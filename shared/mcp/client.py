"""
shared/mcp/client.py
====================
纯 MCP stdio client 核心，**不依赖** LangChain 或任何框架。

对外暴露：
- MCPToolSpec   数据类，描述一个 MCP tool 的完整信息（连接参数 + schema）
- load_server_tool_specs()  读配置文件，对每个 enabled server 调 list_tools，
                             返回 MCPToolSpec 列表
- call_tool()   调用单个 tool，返回字符串结果

配置文件默认路径：<repo_root>/mcp_servers/mcp_servers.json
可通过环境变量 MCP_SERVERS_CONFIG 覆盖。

配置格式：
{
  "mcpServers": {
    "server_name": {
      "command": "/path/to/python",
      "args": ["/path/to/server.py"],
      "env": {"KEY": "value"},
      "enabled": true
    }
  }
}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── 配置路径 ──────────────────────────────────────────────────────────────────

def _default_config_path() -> Path:
    env = os.getenv("MCP_SERVERS_CONFIG", "").strip()
    if env:
        return Path(env)
    # shared/ 在 repo_root/shared/，所以 parents[2] = repo_root
    return Path(__file__).resolve().parents[2] / "mcp_servers" / "mcp_servers.json"


# ─── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class MCPToolSpec:
    """单个 MCP tool 的完整描述，包含连接信息和 JSON Schema。"""

    # --- 工具元数据 ---
    tool_name: str
    description: str
    input_schema: dict[str, Any]   # 原始 JSON Schema（inputSchema）

    # --- 连接参数（指向所属 server） ---
    server_name: str
    command: str
    args: list[str] = field(default_factory=list)
    env_override: dict[str, str] = field(default_factory=dict)


# ─── 配置加载 ──────────────────────────────────────────────────────────────────

def _load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or _default_config_path()
    if not path.is_file():
        logger.debug("[mcp.client] 配置文件不存在: %s", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[mcp.client] 读取配置失败: %s", exc)
        return {}


def _enabled_servers(config_path: Path | None = None) -> list[tuple[str, dict[str, Any]]]:
    cfg = _load_config(config_path)
    result = []
    for name, spec in cfg.get("mcpServers", {}).items():
        if not spec.get("enabled", True):
            logger.debug("[mcp.client] 跳过已禁用 server: %s", name)
            continue
        result.append((name, spec))
    return result


# ─── 异步核心：list_tools / call_tool ─────────────────────────────────────────

async def _async_list_tools(
    command: str,
    args: list[str],
    env_override: dict[str, str],
) -> list[Any]:
    """向 MCP server 发送 list_tools 请求，返回原始 mcp.types.Tool 列表。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    merged_env = {**os.environ, **env_override}
    params = StdioServerParameters(command=command, args=args, env=merged_env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
    return result.tools


async def _async_call_tool(
    command: str,
    args: list[str],
    env_override: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """调用 MCP server 的单个 tool，返回字符串结果。"""
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    merged_env = {**os.environ, **env_override}
    params = StdioServerParameters(command=command, args=args, env=merged_env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)

    if result.content:
        block = result.content[0]
        if isinstance(block, types.TextContent):
            return block.text
    if result.structuredContent is not None:
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return str(result)


# ─── 同步公开 API ──────────────────────────────────────────────────────────────

def load_server_tool_specs(config_path: Path | None = None) -> list[MCPToolSpec]:
    """
    读取配置文件，对每个 enabled server 发 list_tools，
    返回 MCPToolSpec 列表。失败的 server 仅打印警告，不抛错。
    """
    specs: list[MCPToolSpec] = []

    for server_name, spec in _enabled_servers(config_path):
        command: str = spec.get("command") or sys.executable
        args: list[str] = spec.get("args") or []
        env_override: dict[str, str] = {
            k: str(v) for k, v in (spec.get("env") or {}).items()
        }

        logger.info("[mcp.client] 加载 server: %s  command=%s", server_name, command)

        try:
            raw_tools = asyncio.run(_async_list_tools(command, args, env_override))
        except Exception as exc:
            logger.warning(
                "[mcp.client] list_tools 失败，跳过 server '%s': %r", server_name, exc
            )
            continue

        for t in raw_tools:
            specs.append(
                MCPToolSpec(
                    tool_name=t.name,
                    description=t.description or t.name,
                    input_schema=t.inputSchema or {},
                    server_name=server_name,
                    command=command,
                    args=args,
                    env_override=env_override,
                )
            )
            logger.info(
                "[mcp.client]   tool: %s  (server=%s)", t.name, server_name
            )

    return specs


def call_tool(spec: MCPToolSpec, arguments: dict[str, Any]) -> str:
    """
    同步调用 MCPToolSpec 描述的工具，返回字符串结果。
    可在普通（非 async）代码中直接使用。
    """
    try:
        return asyncio.run(
            _async_call_tool(
                command=spec.command,
                args=spec.args,
                env_override=spec.env_override,
                tool_name=spec.tool_name,
                arguments=arguments,
            )
        )
    except Exception as exc:
        logger.error("[mcp.client] call_tool %s error: %r", spec.tool_name, exc)
        return f"[MCP error] {exc}"


async def async_call_tool(spec: MCPToolSpec, arguments: dict[str, Any]) -> str:
    """
    异步调用 MCPToolSpec 描述的工具（供 async 上下文使用）。
    """
    try:
        return await _async_call_tool(
            command=spec.command,
            args=spec.args,
            env_override=spec.env_override,
            tool_name=spec.tool_name,
            arguments=arguments,
        )
    except Exception as exc:
        logger.error("[mcp.client] async_call_tool %s error: %r", spec.tool_name, exc)
        return f"[MCP error] {exc}"
