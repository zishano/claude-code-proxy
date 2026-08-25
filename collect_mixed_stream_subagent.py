#!/usr/bin/env python3
"""
综合"流式+非流式+子代理"采集脚本
============================================================
同时完成两件事,让 trace 里出现 type=s(流式)、type=n(非流式)、type=subagent:
  1) HTTP 直接向 3001 代理发"流式/非流式交替"请求 → 产生 type=s 与 type=n 真实请求
  2) 派发一个真实 claude 任务,要求 spawn 子代理 → 产生 type=subagent 节点 + 父会话
所有请求均经 3001 代理被监控记录到 requests.db 及 ~/.claude/projects 的 jsonl。

用法:
    python3 collect_mixed_stream_subagent.py [每轮HTTP条数] [轮数]
    默认: HTTP 每轮 4 条(2流式+2非流式), 1 轮; + 1 次真实子代理任务。
    例: python3 collect_mixed_stream_subagent.py 6 2   # HTTP 12条 + 子代理
"""
import subprocess, sys, os, json, sqlite3, urllib.request, urllib.error, time, glob

# ============ 配置 ============
DB = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy/requests.db"
PROXY = "http://127.0.0.1:3001/v1/messages"
KEY = os.environ.get("ANTHROPIC_API_KEY", "PROXY_MANAGED")
MODEL = "DeepSeek-V4-Flash"
ANTHROPIC_VERSION = "2023-06-01"
SETTINGS = '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3001"}}'
PROJECT_ROOT = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy"
# ==============================

HTTP_PROMPTS = [
    "请简要说明这个项目 proxy/ 目录的用途。",
    "请列出 main.go 里注册的主要路由端点。",
    "请解释 model_router 的作用。",
    "请简述 requests.db 存储了哪些请求信息。",
    "请总结 config.yaml 的可配置项。",
    "请说明 subagent.enable 配置的作用。",
]

SUBAGENT_TASK = (
    "请调用 code-auditor 这个子代理,让它去审查 proxy/internal/config/config.go 这个文件。\n"
    "要求:\n"
    "1. 显式启动 code-auditor 子代理(它必须作为独立子会话运行,不要自己替代它)。\n"
    "2. 让 code-auditor 完整读取并分析该文件的配置加载逻辑,指出配置优先级与潜在问题。\n"
    "3. 让它输出一段简短的审查结论。\n"
    "4. 子代理结束后,你(主会话)再用一句话总结。\n"
    "务必确保 code-auditor 作为真实子代理被启动。"
)


def count_db():
    if not os.path.exists(DB):
        return 0
    try:
        return sqlite3.connect(DB).execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    except Exception:
        return 0


def send_http(prompt, stream):
    """向代理发一条 HTTP 请求,stream 控制流式/非流式。返回(status, ok, mode)"""
    body = {
        "model": MODEL,
        "max_tokens": 100,
        "stream": stream,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(PROXY, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json",
        "X-Api-Key": KEY,
        "Anthropic-Version": ANTHROPIC_VERSION,
    })
    mode = "流式" if stream else "非流式"
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read().decode("utf-8", errors="replace")
            return r.status, True, mode
    except urllib.error.HTTPError as e:
        return e.code, False, mode
    except Exception as e:
        return "ERR", False, mode


def run_subagent():
    """派发真实 claude 子代理任务。返回新 agent-jsonl 数/结果"""
    cmd = ["claude", "--settings", SETTINGS, "--print",
           "--dangerously-skip-permissions", SUBAGENT_TASK]
    print("派发子代理任务...\n")
    try:
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=1500)
        out = (proc.stdout or "").strip()
        print(f"Claude 完成, rc={proc.returncode}, 耗时", end=" ")
        print(out[-300:] if out else "(空输出)")
        return proc.returncode
    except subprocess.TimeoutExpired:
        print("⏰ 超时。已产生的请求/子代理已记录。")
        return "TIMEOUT"
    except Exception as e:
        print(f"❌ {e}")
        return "ERR"


def count_agent_jsonl():
    return glob.glob(os.path.expanduser("~/.claude/projects/-mnt-nvme1n1-data-lmk-PROJECT-claude-code-proxy/*/subagents/agent-*.jsonl"))


def main():
    per_http = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    before_db = count_db()
    before_agent = len(count_agent_jsonl())

    print("=" * 64)
    print("【流式+非流式+子代理】综合采集(真实请求,经 3001 代理)")
    print(f"  HTTP 部分 : {rounds} 轮 × {per_http} 条(交替流式/非流式)")
    print(f"  子代理部分: 1 次真实 claude spawn 子代理任务")
    print(f"  采集前    : requests.db={before_db} 条, agent-jsonl={before_agent} 个")
    print("=" * 64)

    # ---- Part 1: 交替 HTTP 流式/非流式 ----
    http_ok = 0
    http_fail = 0
    print("\n>>> [Part 1] HTTP 交替流式/非流式请求 ---")
    for rnd in range(1, rounds + 1):
        for i in range(per_http):
            stream = (i % 2 == 0)          # 偶=流式, 奇=非流式
            prompt = HTTP_PROMPTS[(rnd - 1) * per_http + i]  # 轮流内容
            status, ok, mode = send_http(prompt, stream)
            mark = "✓" if ok else "✗"
            if ok:
                http_ok += 1
            else:
                http_fail += 1
            print(f"  [HTTP {rnd}-{i+1}] {mode} HTTP{status} {mark}")
            time.sleep(0.5)
        time.sleep(1.5)
    print(f"  → HTTP 部分: 成功 {http_ok}, 失败 {http_fail}")

    # ---- Part 2: 真实子代理 ----
    print("\n>>> [Part 2] 真实 claude 子代理任务 ---")
    rc = run_subagent()

    after_db = count_db()
    after_agent = len(count_agent_jsonl())
    print("\n" + "=" * 64)
    print("采集结果")
    print(f"  requests.db  : {before_db} → {after_db} 条 (+{after_db-before_db})")
    print(f"  agent-jsonl  : {before_agent} → {after_agent} 个")
    print("=" * 64)
    if after_db - before_db > 0 or after_agent > before_agent:
        print("✅ 已采集。可重跑 run_trace_generation.py(INCLUDE_SUBAGENTS=True)")
        print("   期望 trace 里同时出现 type=s(流式)、type=n(非流式)、type=subagent。")
    else:
        print("⚠️ 无新增 —— 检查 proxy(3001) 是否运行、claude 是否走 3001。")


if __name__ == "__main__":
    main()
