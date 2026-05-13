"""Request-scoped context for agent calls: thread_id, callbacks, optional metadata."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


def _build_langfuse_callbacks() -> list[BaseCallbackHandler]:
    """若已配置 Langfuse，返回包含 Langfuse handler 的列表；否则返回空列表。不抛错。"""
    logger = logging.getLogger(__name__)
    secret = (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    public = (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
    logger.info(
        "[langfuse] probe: secret=%s public=%s host=%s",
        "set" if bool(secret) else "missing",
        "set" if bool(public) else "missing",
        (os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or "").strip() or "missing",
    )
    if not secret or not public:
        logger.warning(
            "[langfuse] LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY 未配置，跳过 Langfuse callback。"
        )
        return []
    # LANGFUSE_HOST 优先，LANGFUSE_BASE_URL 作为兼容别名，v4 SDK 读 LANGFUSE_HOST
    host = (os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or "").strip()
    if host:
        os.environ.setdefault("LANGFUSE_HOST", host)
    try:
        # Langfuse 的 LangChain 回调类名在不同版本中不一致：
        # - 新版常见：LangchainCallbackHandler
        # - 旧版常见：CallbackHandler
        try:
            from langfuse.langchain import LangchainCallbackHandler as _Handler  # type: ignore
            handler_name = "LangchainCallbackHandler"
        except Exception:
            from langfuse.langchain import CallbackHandler as _Handler  # type: ignore
            handler_name = "CallbackHandler"

        handler = _Handler()
        logger.info(
            "[langfuse] handler created: %s host=%s",
            handler_name,
            os.getenv("LANGFUSE_HOST"),
        )
        return [handler]
    except Exception as exc:
        logger.exception("[langfuse] 初始化 Langfuse handler 失败：%r", exc)
        return []


@dataclass
class RequestContext:
    """单次请求的上下文，供 API 与 graph 共享。"""

    thread_id: str
    """与 session_id 一致，用于 checkpointer 与 trace 关联。"""
    callbacks: list[BaseCallbackHandler] = field(default_factory=list)
    """本请求使用的 callback（如 Langfuse），在 invoke/astream 时传入。"""
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    channel_type: str = ""
    """消息来源渠道（如 weixin / feishu），为空表示来自 web 前端。"""

    def with_langfuse(self) -> RequestContext:
        """在现有 callbacks 基础上追加 Langfuse handler（若已配置）。返回新实例。"""
        extra = _build_langfuse_callbacks()
        if not extra:
            return self
        return RequestContext(
            thread_id=self.thread_id,
            callbacks=[*self.callbacks, *extra],
            request_id=self.request_id,
            metadata=dict(self.metadata),
            channel_type=self.channel_type,
        )


def build_request_context(
    thread_id: str,
    *,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    include_langfuse: bool = True,
    channel_type: str = "",
) -> RequestContext:
    """构造 RequestContext，可选自动加入 Langfuse。"""
    logger = logging.getLogger(__name__)
    ctx = RequestContext(
        thread_id=thread_id,
        callbacks=[],
        request_id=request_id,
        metadata=metadata or {},
        channel_type=channel_type,
    )
    if include_langfuse:
        ctx = ctx.with_langfuse()
        logger.info("[langfuse] build_request_context: include_langfuse=true callbacks=%d", len(ctx.callbacks))
    else:
        logger.info("[langfuse] build_request_context: include_langfuse=false callbacks=%d", len(ctx.callbacks))
    return ctx
