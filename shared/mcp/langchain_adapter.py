"""
shared/mcp/langchain_adapter.py
================================
把 MCPToolSpec 包装成 LangChain BaseTool，供 api_server 使用。

用法：
    from shared.mcp.langchain_adapter import get_mcp_tools
    tools = get_mcp_tools()          # → list[BaseTool]
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Type

from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model

from shared.mcp.client import MCPToolSpec, async_call_tool, load_server_tool_specs

logger = logging.getLogger(__name__)


# ─── Pydantic schema 生成 ──────────────────────────────────────────────────────

def _build_args_schema(spec: MCPToolSpec) -> Type[BaseModel]:
    """
    从 MCPToolSpec.input_schema（JSON Schema）生成最小 Pydantic model。
    仅处理 string / integer / number / boolean，其余退化为 str。
    """
    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
    }

    props: dict[str, Any] = spec.input_schema.get("properties", {})
    required: list[str] = spec.input_schema.get("required", [])

    if not props:
        return create_model(f"{spec.tool_name}_Input")

    fields: dict[str, Any] = {}
    for field_name, field_schema in props.items():
        py_type = type_map.get(field_schema.get("type", "string"), str)
        desc = field_schema.get("description", field_name)
        if field_name in required:
            fields[field_name] = (py_type, Field(..., description=desc))
        else:
            fields[field_name] = (py_type | None, Field(default=None, description=desc))

    return create_model(f"{spec.tool_name}_Input", **fields)


# ─── 动态 LangChain BaseTool ──────────────────────────────────────────────────

class _MCPDynamicTool(BaseTool):
    """每个 MCPToolSpec 对应一个 LangChain tool 实例。"""

    name: str
    description: str
    args_schema: Type[BaseModel]

    _spec: MCPToolSpec

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, *, spec: MCPToolSpec, args_schema: Type[BaseModel]) -> None:
        super().__init__(
            name=spec.tool_name,
            description=spec.description,
            args_schema=args_schema,
        )
        self._spec = spec

    def _run(self, run_manager: CallbackManagerForToolRun | None = None, **kwargs: Any) -> str:
        return asyncio.run(self._arun(run_manager=None, **kwargs))

    async def _arun(
        self,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> str:
        arguments = {k: v for k, v in kwargs.items() if v is not None}
        result = await async_call_tool(self._spec, arguments)
        return result


# ─── 公开入口 ──────────────────────────────────────────────────────────────────

def get_mcp_tools() -> list[BaseTool]:
    """
    读取 mcp_servers/mcp_servers.json，把所有 enabled server 的 tools
    包装成 LangChain BaseTool 列表返回。

    失败时仅打印警告，不影响 api_server 正常启动。
    """
    tools: list[BaseTool] = []

    try:
        specs = load_server_tool_specs()
    except Exception as exc:
        logger.warning("[langchain_adapter] load_server_tool_specs 失败: %r", exc)
        return tools

    for spec in specs:
        schema = _build_args_schema(spec)
        tool = _MCPDynamicTool(spec=spec, args_schema=schema)
        tools.append(tool)
        logger.info(
            "[langchain_adapter] 注册 LangChain tool: %s  (server=%s)",
            spec.tool_name,
            spec.server_name,
        )

    return tools
