#!/usr/bin/env python3
"""
MCP stdio server：将复制的 mx_zixuan.py 以 MCP tools 形式暴露，便于与 Skill（read_file + 脚本）路径对比。

不修改 data/skills/mx-zixuan/ 下原文件；本目录内 mx_zixuan.py 为独立拷贝。

运行（需已安装 mcp、requests，且配置 MX_APIKEY）：
  cd mcp_servers/mx_zixuan_mcp
  python server.py

或在 Cursor / Claude Desktop 的 MCP 配置里指向：
  command: python
  args: ["D:/miniOpenClaw/mcp_servers/mx_zixuan_mcp/server.py"]
  env: { "MX_APIKEY": "..." }
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_DIR = Path(__file__).resolve().parent
_SCRIPT = _DIR / "mx_zixuan.py"
_OUTPUT = _DIR / "output"


def _repo_root() -> Path:
    """mcp_servers/mx_zixuan_mcp -> 仓库根 miniOpenClaw"""
    return _DIR.parent.parent


def _maybe_inject_mx_apikey_from_config() -> None:
    """若环境变量未设置，尝试从仓库 config/.env 读取 MX_APIKEY（仅本 server 逻辑，不改拷贝脚本）。"""
    if (os.environ.get("MX_APIKEY") or "").strip():
        return
    env_path = _repo_root() / "config" / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != "MX_APIKEY":
                continue
            val = value.strip().strip('"').strip("'")
            if val:
                os.environ["MX_APIKEY"] = val
            break
    except OSError:
        pass


def _run_mx_zixuan_argv(argv: list[str]) -> str:
    """子进程调用本目录 mx_zixuan.py，合并 stdout/stderr 返回给 MCP client。"""
    _OUTPUT.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(_SCRIPT), *argv, "--output-dir", str(_OUTPUT)]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_DIR),
        env=os.environ.copy(),
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"[exit {proc.returncode}]\n{out}\n{err}".strip()
    return (out + ("\n" + err if err else "")).strip() or "(no output)"


_maybe_inject_mx_apikey_from_config()

mcp = FastMCP(
    "mx_zixuan_mcp",
    instructions="东方财富妙想自选股（与仓库内 data/skills/mx-zixuan 脚本逻辑一致，本包为 MCP 对照实验）。需要 MX_APIKEY。",
)


@mcp.tool()
def mx_zixuan_query() -> str:
    """查询当前账户下的自选股列表（等价于：python mx_zixuan.py query）。"""
    return _run_mx_zixuan_argv(["query"])


@mcp.tool()
def mx_zixuan_add(stock_name_or_code: str) -> str:
    """添加一只股票到自选股（等价于：python mx_zixuan.py add <名称或代码>）。"""
    name = (stock_name_or_code or "").strip()
    if not name:
        return "stock_name_or_code 不能为空"
    return _run_mx_zixuan_argv(["add", name])


@mcp.tool()
def mx_zixuan_delete(stock_name_or_code: str) -> str:
    """从自选股中删除一只股票（等价于：python mx_zixuan.py delete <名称或代码>）。"""
    name = (stock_name_or_code or "").strip()
    if not name:
        return "stock_name_or_code 不能为空"
    return _run_mx_zixuan_argv(["delete", name])


@mcp.tool()
def mx_zixuan_natural_language(instruction: str) -> str:
    """用自然语言查询或管理自选股（整句作为脚本第一个位置参数，例如「查询我的自选股」「把贵州茅台加入自选」）。"""
    text = (instruction or "").strip()
    if not text:
        return "instruction 不能为空"
    return _run_mx_zixuan_argv([text])


if __name__ == "__main__":
    mcp.run()
