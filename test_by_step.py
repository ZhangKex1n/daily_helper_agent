"""
单步调试脚本：模拟前端向 api_server 发送"查询九江天气"

用法：
  python test_by_step.py

调试断点建议（在 IDE 里打断点后用 Debug 模式启动）：
  - chat.py         L58  build_request_context(...)        ← 查看 request_context 与 callbacks
  - agent.py        L141 memory_backend = get_memory_backend()  ← 进入 astream
  - agent.py        L173 turn_messages.append(...)         ← 查看拼装好的消息列表
  - agent.py        L200 run_config = ...                  ← 查看传给 LangGraph 的配置
  - agent.py        L211 async for mode, payload in agent.astream(...)  ← 每个 LangGraph 事件
  - agent.py        L231 text = _stringify_content(...)    ← 每个 token 流
  - agent.py        L252 if tool_calls:                    ← LLM 决定调工具
  - agent.py        L269 if message_type == "tool":        ← 工具结果回来
  - read_file_tool.py L38 file_path = self._resolve_path(path)  ← 工具内部执行
  - context.py      L44 handler = _Handler()               ← Langfuse handler 创建

前提：api_server 已在 8002 端口启动
  conda activate agent
  uvicorn api_server.app:app --host 0.0.0.0 --port 8002 --reload --access-log --log-level info
"""

import json
import urllib.request
import urllib.error

API_BASE = "http://127.0.0.1:8002/api"
MESSAGE = "查询南昌天气"
SESSION_ID = "debug-step-003"


# ── 颜色输出 ────────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def cyan(t):   return _c("36", t)
def red(t):    return _c("31", t)
def bold(t):   return _c("1",  t)


# ── Step 0：检查服务器是否可达 ────────────────────────────────────────────────

def step0_health_check():
    print(bold("\n[Step 0] 健康检查 GET /health"))
    # ← 断点 A：进入这里，确认 http 请求能打通
    req = urllib.request.Request(f"{API_BASE.replace('/api', '')}/health")
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        body = json.loads(resp.read().decode())
        print(green(f"  ✓ 服务器正常: {body}"))
    except Exception as exc:
        print(red(f"  ✗ 服务器不可达: {exc}"))
        print(red("  请先启动：uvicorn api_server.app:app --host 0.0.0.0 --port 8002"))
        raise SystemExit(1)


# ── Step 1：非流式请求（stream=false），同步拿到最终结果，便于单步 ────────────

def step1_non_stream():
    print(bold(f"\n[Step 1] 非流式 POST /api/chat  (stream=false)"))
    print(f"  message   = {cyan(MESSAGE)}")
    print(f"  session_id = {SESSION_ID}")

    payload = {
        "message": MESSAGE,
        "session_id": SESSION_ID,
        "stream": False,
        "channel_type": "",
    }

    # ← 断点 B：payload 构造完毕，即将发出 HTTP 请求
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    # ← 断点 C：urlopen 阻塞期间，api_server 内部正在执行 astream；
    #           此时切到 api_server 进程侧设置的断点观察
    print("  → 等待 api_server 完整处理（非流式）…")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(red(f"  ✗ HTTP {exc.code}: {exc.read().decode()}"))
        return
    except Exception as exc:
        print(red(f"  ✗ 请求失败: {exc}"))
        return

    # ← 断点 D：response 已回来，body 就是服务器的完整输出
    print(green("\n  ✓ 收到回复："))
    content = body.get("content", "")
    print(f"  {content}\n")

    # 解析出 SSE 里嵌入的各类事件（stream=false 时内容是原始 SSE 文本）
    _parse_sse_events(content)


def _parse_sse_events(raw: str):
    """把 stream=false 时返回的 SSE 文本按 event 类型拆开打印。"""
    if not raw:
        return
    events = raw.strip().split("\n\n")
    print(bold("  --- SSE 事件拆解 ---"))
    for block in events:
        lines = block.strip().splitlines()
        event_type = "message"
        data_lines = []
        for line in lines:
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            continue
        try:
            data = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            data = {"raw": "\n".join(data_lines)}

        if event_type == "token":
            print(f"  [token]     {data.get('content','')!r}")
        elif event_type == "tool_start":
            print(yellow(f"  [tool_start] tool={data.get('tool')}  input={data.get('input','')[:80]}"))
        elif event_type == "tool_end":
            print(yellow(f"  [tool_end]   tool={data.get('tool')}  output={str(data.get('output',''))[:120]}"))
        elif event_type == "done":
            print(green(f"  [done]      {data.get('content','')[:200]}"))
        elif event_type == "error":
            print(red(f"  [error]     {data.get('error')}"))
        else:
            print(f"  [{event_type}]  {str(data)[:120]}")


# ── Step 2：流式请求（stream=true），实时打印每个 SSE 事件 ───────────────────

def step2_stream():
    print(bold(f"\n[Step 2] 流式 POST /api/chat  (stream=true)"))
    print(f"  message   = {cyan(MESSAGE)}")
    print(f"  session_id = {SESSION_ID}-stream")

    payload = {
        "message": MESSAGE,
        "session_id": f"{SESSION_ID}-stream",
        "stream": True,
        "channel_type": "",
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print("  → 实时 SSE 事件流：")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except Exception as exc:
        print(red(f"  ✗ 请求失败: {exc}"))
        return

    buffer = ""
    event_count = {"token": 0, "tool_start": 0, "tool_end": 0, "done": 0, "other": 0}

    # ← 断点 E：进入流式读取循环，每次 read 都可能返回一段 SSE
    for chunk in iter(lambda: resp.read(512), b""):
        buffer += chunk.decode("utf-8", errors="replace")
        # 按双换行切 SSE block
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            lines = block.strip().splitlines()
            event_type = "message"
            data_lines = []
            for line in lines:
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not data_lines:
                continue
            try:
                event_data = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                event_data = {"raw": "\n".join(data_lines)}

            # ← 断点 F：每个 SSE event 到达这里
            if event_type == "token":
                print(cyan(event_data.get("content", "")), end="", flush=True)
                event_count["token"] += 1
            elif event_type == "tool_start":
                print(yellow(f"\n  ↳ [tool_start] {event_data.get('tool')} ({event_data.get('input','')[:60]})"))
                event_count["tool_start"] += 1
            elif event_type == "tool_end":
                out = str(event_data.get("output", ""))[:100]
                print(yellow(f"  ↳ [tool_end]  {event_data.get('tool')} → {out}"))
                event_count["tool_end"] += 1
            elif event_type == "done":
                print(green(f"\n  ✓ [done]"))
                event_count["done"] += 1
            elif event_type == "error":
                print(red(f"\n  ✗ [error] {event_data.get('error')}"))
                event_count["other"] += 1
            else:
                event_count["other"] += 1

    print()
    print(bold("  --- 事件统计 ---"))
    for k, v in event_count.items():
        if v:
            print(f"    {k}: {v}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ← 总断点 G：脚本入口，从这里往下 F10 单步

    step0_health_check()

    # Step 1：非流式 — 用于单步调试 api_server 内部流程（只要在 api_server 代码打断点）
    step1_non_stream()

    # Step 2：流式 — 用于观察实时事件顺序（需要禁用 api_server 侧断点，否则会超时）
    # step2_stream()   # ← 取消注释可同时跑流式版
