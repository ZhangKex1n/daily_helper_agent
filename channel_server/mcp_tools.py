"""
channel_server/mcp_tools.py
============================
channel_server 侧的 MCP tool 执行助手。

当 LLM 通过 call_with_tools() 返回一个 tool_call（function name 匹配 MCP tool），
agent 框架应调用本模块的 execute_mcp_tool() 来执行，并把结果作为 tool 消息追回给 LLM。

示例（agent 框架伪代码）：
    from mcp_tools import execute_mcp_tool, is_mcp_tool

    response = bot.call_with_tools(messages, tools=my_tools)
    tool_calls = response["choices"][0]["message"].get("tool_calls", [])
    for tc in tool_calls:
        name = tc["function"]["name"]
        args = json.loads(tc["function"]["arguments"])
        if is_mcp_tool(name):
            result = execute_mcp_tool(name, args)
        else:
            result = my_local_tool_handler(name, args)
        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    # 再次调用 bot.call_with_tools(messages) 获取最终回复
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_adapter():
    """延迟导入 openai_adapter，避免在 mcp 未安装时影响 channel_server 启动。"""
    try:
        from shared.mcp.openai_adapter import (
            get_openai_mcp_tools,
            call_mcp_tool_by_name,
            reload_specs,
        )
        return get_openai_mcp_tools, call_mcp_tool_by_name, reload_specs
    except ImportError as exc:
        raise RuntimeError(
            "shared.mcp 不可用，请确认 shared/ 目录在 PYTHONPATH 中，且已安装 mcp 依赖。"
        ) from exc


def get_mcp_tool_schemas() -> list[dict[str, Any]]:
    """
    返回所有 MCP tools 的 OpenAI function schema 列表。
    供 agent 框架手动合并进 tools 参数（call_with_tools 已自动注入，一般无需单独调用）。
    """
    get_openai_mcp_tools, _, _ = _get_adapter()
    return get_openai_mcp_tools()


def is_mcp_tool(tool_name: str) -> bool:
    """判断一个工具名是否属于 MCP tools（已在配置中注册）。"""
    schemas = get_mcp_tool_schemas()
    mcp_names = {s["function"]["name"] for s in schemas}
    return tool_name in mcp_names


def execute_mcp_tool(tool_name: str, arguments: dict[str, Any] | str) -> str:
    """
    执行一个 MCP tool 并返回字符串结果。

    Args:
        tool_name:  工具名（与 LLM tool_call 中的 function.name 对应）
        arguments:  工具参数，dict 或 JSON 字符串均可

    Returns:
        工具执行结果字符串（失败时返回错误提示）
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}

    _, call_mcp_tool_by_name, _ = _get_adapter()
    logger.info("[mcp_tools] 执行 MCP tool: %s  args=%s", tool_name, arguments)
    result = call_mcp_tool_by_name(tool_name, arguments)
    logger.info("[mcp_tools] 结果: %s", str(result)[:200])
    return result


def reload_mcp_tools() -> int:
    """
    强制重新加载 MCP server 配置（list_tools）并刷新缓存。
    返回加载到的 tool 数量。用于运行时热更新（如新增了 server 配置后）。
    """
    _, _, reload_specs = _get_adapter()
    specs = reload_specs()
    logger.info("[mcp_tools] 重新加载完成，共 %d 个 MCP tools", len(specs))
    return len(specs)
