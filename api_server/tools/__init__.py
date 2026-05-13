from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool

from api_server.tools.fetch_url_tool import FetchURLTool
from api_server.tools.python_repl_tool import PythonReplTool
from api_server.tools.read_file_tool import ReadFileTool
from api_server.tools.terminal_tool import TerminalTool


def get_all_tools(base_dir: Path) -> list[BaseTool]:
    tools: list[BaseTool] = [
        TerminalTool(root_dir=base_dir),
        PythonReplTool(root_dir=base_dir),
        FetchURLTool(),
        ReadFileTool(root_dir=base_dir),
    ]

    # ── MCP tools ─────────────────────────────────────────────────────────────
    # 读 mcp_servers/mcp_servers.json，自动把每个 server 的 tools 注册进来。
    # 配置文件不存在或 mcp / shared 未安装时静默跳过，不影响启动。
    try:
        from shared.mcp.langchain_adapter import get_mcp_tools
        tools.extend(get_mcp_tools())
    except Exception:
        pass

    # ── memory_module_v2 tools ─────────────────────────────────────────────────
    from shared.memory_module_v2.service.config import get_memory_backend, get_memory_v2_inject_mode
    if get_memory_backend() == "v2" and get_memory_v2_inject_mode() == "tool":
        from shared.memory_module_v2.integrations.tools import get_memory_tools
        tools.extend(get_memory_tools())

    return tools
