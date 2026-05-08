from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import logging

from config import get_settings, runtime_config
from graph.context import RequestContext
from graph.agent_factory import build_agent_config, create_agent_from_config
from service.memory_indexer import memory_indexer
from service.session_manager import SessionManager
from graph.llm import build_llm_config_from_settings, get_llm
from tools import get_all_tools
from shared.memory_module_v2.service.config import get_memory_backend, get_memory_v2_inject_mode
from shared.memory_module_v2.integrations.middleware import build_memory_context

logger = logging.getLogger(__name__)

_LLM_CONTEXT_PATH = "workspace/LLM_CONTEXT.md"


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


class AgentManager:
    def __init__(self) -> None:
        self.base_dir: Path | None = None
        self.session_manager: SessionManager | None = None
        self.tools = []

    def initialize(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.session_manager = SessionManager(base_dir)
        self.tools = get_all_tools(base_dir)

    # 用于generate_title()和summarize_history()
    def _build_chat_model(self):
        settings = get_settings()
        llm_config = build_llm_config_from_settings(settings, temperature=0.0, streaming=False)
        return get_llm(llm_config)

    def _build_agent(self):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")
        config = build_agent_config(
            self.base_dir, self.tools, use_checkpointer=True
        )
        return create_agent_from_config(config)

    def _write_latest_llm_context(
        self,
        *,
        session_id: str,
        system_prompt: str,
        history_messages: list[dict[str, str]],
        turn_messages: list[dict[str, str]],
    ) -> None:
        if self.base_dir is None:
            return

        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        all_messages: list[dict[str, str]] = []
        if system_prompt.strip():
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(history_messages)
        all_messages.extend(turn_messages)

        lines: list[str] = []
        lines.append("# LLM Context (latest turn)")
        lines.append("")
        lines.append(f"- session_id: `{session_id}`")
        lines.append(f"- generated_at: `{now}`")
        lines.append(f"- messages: `{len(all_messages)}`")
        lines.append("")
        lines.append("## Messages (verbatim)")
        lines.append("")
        for idx, msg in enumerate(all_messages, start=1):
            role = str(msg.get("role", ""))
            content = str(msg.get("content", "") or "")
            lines.append(f"### {idx}. {role}")
            lines.append("")
            lines.append("```")
            lines.append(content)
            lines.append("```")
            lines.append("")
        lines.append("## Messages (JSON)")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(all_messages, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

        ctx_dir = (self.base_dir / "workspace" / "llm_context").resolve()
        ctx_dir.mkdir(parents=True, exist_ok=True)
        path = ctx_dir / f"{session_id}.md"
        path.write_text("\n".join(lines), encoding="utf-8")

    def _build_messages(self, history: list[dict[str, Any]]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for item in history:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            messages.append({"role": role, "content": str(item.get("content", ""))})
        return messages

    def _format_retrieval_context(self, results: list[dict[str, Any]]) -> str:
        lines = ["[RAG retrieved memory context]"]
        for idx, item in enumerate(results, start=1):
            text = str(item.get("text", "")).strip()
            source = str(
                item.get(
                    "source",
                    "memory_module_v1/long_term_memory/MEMORY.md",
                )
            )
            lines.append(f"{idx}. Source: {source}\n{text}")
        return "\n\n".join(lines)

    async def astream(
        self,
        message: str,
        history: list[dict[str, Any]],
        context: RequestContext | None = None,
    ):
        if self.base_dir is None:
            raise RuntimeError("AgentManager is not initialized")

        memory_backend = get_memory_backend()
        turn_messages: list[dict[str, str]] = []
        #turn_messages 是每次对话的上下文，包括用户消息和助手消息

        session_id = (context.thread_id if context else "").strip() or "default"

        if memory_backend == "v1":
            # v1: Chroma / MEMORY.md RAG injection
            retrievals = memory_indexer.retrieve(message, top_k=3)
            yield {"type": "retrieval", "query": message, "results": retrievals}
            if retrievals:
                turn_messages.append(
                    {
                        "role": "assistant",
                        "content": self._format_retrieval_context(retrievals),
                    }
                )
        elif memory_backend == "v2" and get_memory_v2_inject_mode() == "always":
            # v2 forced injection: search every turn, prepend to prompt
            try:
                v2_context = build_memory_context(message)
                if v2_context:
                    yield {"type": "retrieval_v2", "query": message, "context": v2_context}
                    turn_messages.append(
                        {"role": "assistant", "content": v2_context}
                    )
            except Exception as v2_exc:
                logger.warning("Memory v2 forced injection failed: %s", v2_exc)
        # When memory_backend == "v2" and inject_mode == "tool":
        #   search_memory is registered as a tool in get_all_tools(),
        #   the agent decides when to call it autonomously.

        turn_messages.append({"role": "user", "content": message})

        # Build config early so we can dump the *exact* context to file
        config = build_agent_config(
            self.base_dir, self.tools,
            use_checkpointer=True,
            channel_type=context.channel_type if context else "",
        )
        agent = create_agent_from_config(config)

        history_for_dump: list[dict[str, str]] = []
        if self.session_manager is not None and session_id != "default":
            try:
                history_for_dump = self.session_manager.load_session_for_agent(session_id)
            except Exception as exc:
                logger.warning("Failed to load session history for LLM context dump: %s", exc)

        try:
            self._write_latest_llm_context(
                session_id=session_id,
                system_prompt=config.system_prompt,
                history_messages=history_for_dump,
                turn_messages=turn_messages,
            )
        except Exception as exc:
            logger.warning("Failed to write latest LLM context: %s", exc)

        run_config: dict[str, Any] = {"configurable": {"thread_id": (context.thread_id if context else "")}}
        if context and context.callbacks:
            run_config["callbacks"] = context.callbacks
        if not run_config["configurable"]["thread_id"]:
            run_config["configurable"]["thread_id"] = "default"

        final_content_parts: list[str] = []
        last_ai_message = ""
        pending_tools: dict[str, dict[str, str]] = {}
        last_usage: dict[str, Any] | None = None

        async for mode, payload in agent.astream(
            {"messages": turn_messages},
            stream_mode=["messages", "updates"],
            config=run_config,
            # stream_options={"include_usage": True}
        ):
            if mode == "messages":
                chunk, metadata = payload
                # 优先从 metadata 中读取 usage（LangGraph 在 include_usage=True 时会放在这里）
                usage_candidate: Any = None
                if isinstance(metadata, dict):
                    usage_candidate = metadata.get("usage")
                if isinstance(usage_candidate, dict):
                    last_usage = usage_candidate

                # 只转发主 agent 节点的 token；跳过 guardian middleware 等非 agent 节点的 LLM 输出
                node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                if node is not None and node != "agent":
                    continue

                text = _stringify_content(getattr(chunk, "content", ""))
                if text:
                    final_content_parts.append(text)
                    yield {"type": "token", "content": text}
                continue

            if mode != "updates":
                continue

            for update in payload.values():
                if not update:
                    continue
                for agent_message in update.get("messages", []):
                    message_type = getattr(agent_message, "type", "")
                    tool_calls = getattr(agent_message, "tool_calls", []) or []

                    if message_type == "ai" and not tool_calls:
                        candidate = _stringify_content(getattr(agent_message, "content", ""))
                        if candidate:
                            last_ai_message = candidate

                    if tool_calls:
                        for tool_call in tool_calls:
                            call_id = str(tool_call.get("id") or tool_call.get("name"))
                            tool_name = str(tool_call.get("name", "tool"))
                            tool_args = tool_call.get("args", "")
                            if not isinstance(tool_args, str):
                                tool_args = json.dumps(tool_args, ensure_ascii=False)
                            pending_tools[call_id] = {
                                "tool": tool_name,
                                "input": str(tool_args),
                            }
                            yield {
                                "type": "tool_start",
                                "tool": tool_name,
                                "input": str(tool_args),
                            }

                    if message_type == "tool":
                        tool_call_id = str(getattr(agent_message, "tool_call_id", ""))
                        pending = pending_tools.pop(
                            tool_call_id,
                            {"tool": getattr(agent_message, "name", "tool"), "input": ""},
                        )
                        output = _stringify_content(getattr(agent_message, "content", ""))
                        yield {
                            "type": "tool_end",
                            "tool": pending["tool"],
                            "output": output,
                        }
                        yield {"type": "new_response"}

        final_content = "".join(final_content_parts).strip() or last_ai_message.strip()
        # 若 LLM 返回了 usage，且本次调用启用了 Langfuse，则在结束时补充 usage 信息，方便在 Langfuse 中显示 tokens
        if last_usage and context and context.callbacks:
            try:
                from langfuse import get_client
                from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

                langfuse_handler: Any | None = None
                for cb in context.callbacks:
                    if isinstance(cb, LangfuseCallbackHandler):
                        langfuse_handler = cb
                        break
                trace_id = getattr(langfuse_handler, "last_trace_id", None) if langfuse_handler else None
                if trace_id:
                    client = get_client()
                    client.trace.update(
                        id=trace_id,
                        usage={
                            "input": last_usage.get("prompt_tokens", 0),
                            "output": last_usage.get("completion_tokens", 0),
                            "total": last_usage.get("total_tokens", 0),
                        },
                    )
            except Exception as exc:
                print("[langfuse] 更新 usage 失败：", repr(exc))
        yield {"type": "done", "content": final_content}

    async def generate_title(self, first_user_message: str) -> str:
        prompt = (
            "请根据用户的第一条消息生成一个中文会话标题。"
            "要求不超过 10 个汉字，不要带引号，不要解释。"
        )
        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": first_user_message},
                ]
            )
            title = _stringify_content(getattr(response, "content", "")).strip()
            return title[:10] or "新会话"
        except Exception:
            return (first_user_message.strip() or "新会话")[:10]

    async def summarize_history(self, messages: list[dict[str, Any]]) -> str:
        prompt = (
            "请将以下对话压缩成中文摘要，控制在 500 字以内。"
            "重点保留用户目标、已完成步骤、重要结论和未解决事项。"
        )
        lines: list[str] = []
        for item in messages:
            role = item.get("role", "assistant")
            content = str(item.get("content", "") or "")
            if content:
                lines.append(f"{role}: {content}")
        transcript = "\n".join(lines)

        try:
            response = await self._build_chat_model().ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ]
            )
            summary = _stringify_content(getattr(response, "content", "")).strip()
            return summary[:500]
        except Exception:
            return transcript[:500]


agent_manager = AgentManager()
