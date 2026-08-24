#!/usr/bin/env python3
"""
Claude Code Proxy — 模拟请求压力/演示脚本
持续向代理(3001)发送模拟的 Claude Code 请求,生成多样化数据,
便于在 5173 面板上观察对话流、工具调用链等效果。

用法:
    python3 loop_sim.py [请求数] [间隔秒]
    例: python3 loop_sim.py 50 1     # 发50条,间隔1秒
    例: python3 loop_sim.py 0 0.5    # 无限发,间隔0.5秒(Ctrl+C 停)
默认: 30条, 间隔0.8秒
"""
import json, random, sys, time, os, urllib.request, urllib.error

# ============================================================
# *** 你使用的 API 与 Model 配置 ****
# ============================================================

# *** API **：走本机代理(3001),转发到 Anthropic。改这里切换目标 API。
PROXY = "http://localhost:3001/v1/messages"

# *** KEY **：真实 key 通过环境变量 ANTHROPIC_API_KEY 传入(不硬编码)。
#    运行前: export ANTHROPIC_API_KEY="sk-ant-你的真实key"
#    未设置则退回假 key(代理照常记录,但真 API 返回 401)。
KEY = os.environ.get("ANTHROPIC_API_KEY", "PROXY_MANAGED")

# *** MODEL **：只发送你指定的模型(去掉下面的注释/填真模型名)。
#    15721 网关认的名字(调用 /v1/messages 时),已在环境变量里验证 200。
MODELS = ["deepseek-v4-flash-0731"]
# ============================================================

PROJECTS = ["web-app", "data-pipeline", "auth-service", "docs-rewrite", "ml-experiment", "cli-tool"]
USER_TEXTS = [
    "帮我看看这个函数的性能问题", "写一个 Go 的 HTTP 处理函数",
    "给这段代码补注释", "修复这个 bug 并加测试",
    "重构这个模块让它更可读", "分析一下这里为什么会超时",
    "把这个 JSON 结构改成更合理的", "帮我设计数据库表结构",
]
TOOLS = [
    {"name": "execute_command", "description": "Run a shell command",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "Read", "description": "Read a file",
     "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}},
    {"name": "Edit", "description": "Edit a file",
     "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}, "old_string": {"type": "string"}}}},
    {"name": "Bash", "description": "Execute a bash command",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
]
TOOL_INPUTS = {
    "execute_command": {"command": "go test ./..."},
    "Read": {"file_path": "/src/main.go"},
    "Edit": {"file_path": "/src/app.py", "old_string": "TODO", "new_string": "fixed"},
    "Bash": {"command": "ls -la"},
}


def make_body():
    n_turns = random.randint(1, 3)
    messages = [{"role": "user",
                 "content": "【项目:" + random.choice(PROJECTS) + "】 " + random.choice(USER_TEXTS)}]
    for _ in range(n_turns - 1):
        tool = random.choice(TOOLS)
        tcall = {"type": "tool_use", "id": f"toolu_{random.randint(0, 10**10)}",
                 "name": tool["name"], "input": dict(TOOL_INPUTS[tool["name"]])}
        messages.append({"role": "assistant", "content": [tcall]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tcall["id"], "content": "ok"}]})
    return {
        "model": random.choice(MODELS),
        "max_tokens": random.choice([1024, 2048, 4096]),
        "temperature": round(random.uniform(0.2, 0.9), 1),
        "stream": random.random() < 0.5,
        "tools": random.sample(TOOLS, random.randint(1, 3)),
        "messages": messages,
    }


def send(body):
    req = urllib.request.Request(PROXY, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json",
        "X-Api-Key": KEY,
        "Anthropic-Version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"ERR:{e}"


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 0.8
    n = 0
    dist = {}
    is_real = KEY and not KEY.startswith("sk-missing")
    mode = "🔑 真实 key(将真实调用 API,会扣费)" if is_real else "⚠️ 假 key(真 API 会返回 401,但代理照常记录)"
    print(f"模式: {mode}")
    print(f"开始模拟(共{limit or '∞'}条, 间隔{interval}s)。Ctrl+C 停止...")
    try:
        while True:
            body = make_body()
            st = send(body)
            dist[body["model"]] = dist.get(body["model"], 0) + 1
            n += 1
            print(f"[{n:>3}] {body['model']:>18}  msgs={len(body['messages'])}  tools={len(body['tools'])}  HTTP{st}")
            if limit and n >= limit:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止。")
    print(f"\n完成,共发送 {n} 条。模型分布: {dist}")


if __name__ == "__main__":
    main()