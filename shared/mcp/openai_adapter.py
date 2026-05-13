"""
shared/mcp/openai_adapter.py
==============================
把 MCPToolSpec 转换为 OpenAI function schema 格式，供 channel_server 使用。

用法：
    from shared.mcp.openai_adapter import get_openai_mcp_tools, call_mcp_tool_by_name

    # 拿到 OpenAI tools 列表，直接传给 LLM API
    mcp_tools = get_openai_mcp_tools()
    # [
    #   {
    #     "type": "function",
    #     "function": {
    #       "name": "mx_get_stock_info",
    #       "description": "...",
    #       "parameters": { "type": "object", "properties": {...}, "required": [...] }
    #     }
    #   },
    #   ...
    # ]

    # 调用工具（by name）
    result = call_mcp_tool_by_name("mx_get_stock_info", {"code": "600000"})
"""

from __future__ import annotations

import logging
from typing import Any

from shared.mcp.client import MCPToolSpec, call_tool, load_server_tool_specs

logger = logging.getLogger(__name__)

# 模块级缓存：避免每次请求都 list_tools（冷启动较慢）
# 若需要动态刷新，调用 reload_specs() 即可
_SPECS: list[MCPToolSpec] | None = None
_SPEC_BY_NAME: dict[str, MCPToolSpec] = {}


def reload_specs() -> list[MCPToolSpec]:
    """重新加载 server tool specs 并刷新缓存。"""
    global _SPECS, _SPEC_BY_NAME
    try:
        _SPECS = load_server_tool_specs()
        _SPEC_BY_NAME = {s.tool_name: s for s in _SPECS}
        logger.info("[openai_adapter] 加载 %d 个 MCP tools", len(_SPECS))
    except Exception as exc:
        logger.warning("[openai_adapter] load_server_tool_specs 失败: %r", exc)
        _SPECS = []
        _SPEC_BY_NAME = {}
    return _SPECS


def _ensure_loaded() -> list[MCPToolSpec]:
    if _SPECS is None:
        reload_specs()
    return _SPECS or []


# ─── MCPToolSpec → OpenAI function schema ─────────────────────────────────────

def spec_to_openai_function(spec: MCPToolSpec) -> dict[str, Any]:
    """把单个 MCPToolSpec 转换为 OpenAI tool schema。"""
    return {
        "type": "function",
        "function": {
            "name": spec.tool_name,
            "description": spec.description,
            "parameters": spec.input_schema or {
                "type": "object",
                "properties": {},
            },
        },
    }


def get_openai_mcp_tools() -> list[dict[str, Any]]:
    """
    返回所有 MCP tools 的 OpenAI function schema 列表，
    可直接传入 LLM API 的 tools 参数。
    """
    return [spec_to_openai_function(s) for s in _ensure_loaded()]


# ─── 调用工具 ──────────────────────────────────────────────────────────────────

def call_mcp_tool_by_name(tool_name: str, arguments: dict[str, Any]) -> str:
    """
    按工具名同步调用 MCP tool，返回字符串结果。
    工具名不存在时返回错误提示字符串。
    """
    _ensure_loaded()
    spec = _SPEC_BY_NAME.get(tool_name)
    if spec is None:
        logger.warning("[openai_adapter] 未找到 tool: %s", tool_name)
        return f"[MCP error] 未知工具: {tool_name}"
    return call_tool(spec, arguments)
