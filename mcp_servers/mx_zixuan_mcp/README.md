# mx_zixuan MCP 对照包

本目录为**独立实验**：把 `data/skills/mx-zixuan/mx_zixuan.py` **拷贝一份**到此处，用 **MCP（Model Context Protocol）** 以 `tools` 形式暴露，便于和「Agent + Skill（read_file + 终端跑脚本）」路径对比。**未修改** `data/skills/mx-zixuan/` 下任何文件。

## 目录说明

| 文件 | 说明 |
|------|------|
| `mx_zixuan.py` | 与仓库内 skill 脚本同内容的拷贝（可自行改本拷贝，不影响原 skill） |
| `server.py` | FastMCP stdio 服务：通过子进程调用 `mx_zixuan.py`，暴露 4 个 MCP tool |
| `requirements.txt` | 本 MCP 进程依赖 |
| `output/` | 运行脚本时 `--output-dir` 指向此处（CSV/JSON 落盘） |

## 安装

```bash
pip install -r mcp_servers/mx_zixuan_mcp/requirements.txt
# 或直接用 api_server 的 requirements（已包含 mcp>=1.2.0）
pip install -r api_server/requirements.txt
```

## 凭据

环境变量 **`MX_APIKEY`** — 与 skill 相同。

填写方式（二选一）：
1. 直接在 `mcp_servers/mcp_servers.json` 的 `env.MX_APIKEY` 字段里填写
2. 在系统环境变量 / `config/.env` 里设置（server.py 启动时会读取 config/.env 兜底）

## api_server 侧如何自动加载

api_server 不再需要针对每个 MCP server 写独立代码。整个 MCP client 逻辑都在：

```
api_server/tools/mcp_client.py
```

服务配置统一写在：

```
mcp_servers/mcp_servers.json
```

### 配置文件格式

```json
{
  "mcpServers": {
    "mx_zixuan": {
      "command": "D:\\anaconda\\envs\\agent\\python.exe",
      "args": ["D:\\miniOpenClaw\\mcp_servers\\mx_zixuan_mcp\\server.py"],
      "env": {
        "MX_APIKEY": "你的key"
      },
      "enabled": true
    },
    "another_server": {
      "command": "python",
      "args": ["/path/to/another_server.py"],
      "enabled": false
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `command` | Python 解释器（或任意可执行文件）的完整路径 |
| `args` | 传给 command 的参数列表 |
| `env` | 额外环境变量（与当前进程 env 合并，此处优先） |
| `enabled` | `false` 时跳过该 server（默认 `true`） |

### api_server 启动时发生什么

1. `get_all_tools()` → `get_mcp_tools()` 读取配置文件
2. 对每个 `enabled=true` 的 server，通过 **stdio** 连接并调用 `list_tools`
3. 每个 tool 被动态包装成 `BaseTool`，与 `terminal/read_file` 等并列注册
4. 之后每次模型调用 tool 时，都临时建立一次 MCP stdio 连接并 `call_tool`

### 手动测试 MCP server

```bash
cd mcp_servers/mx_zixuan_mcp
set MX_APIKEY=你的key
python server.py
```

## Skill vs MCP：差异一览

| 维度 | Skill（`data/skills/mx-zixuan`） | MCP（本目录） |
|------|----------------------------------|---------------|
| 工具如何被发现 | Agent 读 `SKILLS_SNAPSHOT.md` → 再读 `SKILL.md` 文档 → 决定跑脚本 | `api_server` 启动时 `list_tools` → 结构化 tool schema 直接注入 LLM 上下文 |
| 模型如何调用 | 自然语言 + `terminal` / `read_file` 等通用工具 | 模型发 **结构化 tool call**（函数名 + JSON 参数） |
| 进程边界 | TerminalTool / PythonReplTool 子进程 | **独立 MCP server 进程**，stdio 通信 |
| 协议 | 无统一协议（项目内自定义） | **MCP**（list_tools / call_tool） |
| 添加新能力 | 写 SKILL.md + 脚本 | 写 MCP server + 在 mcp_servers.json 增加一行 |
