#!/usr/bin/env python3
"""
主会话 + 子代理 的 stream 画像采集脚本(按 claude 真实行为)
============================================================
不强制 stream:完全按 Claude Code 客户端真实行为采集。
  - 主会话请求:claude 发什么就什么(默认可能带 stream=true → type=s)
  - 子代理请求:claude 天然发非流式(经验证 → type=n)
这个脚本派发一个"多轮 + spawn 子代理"的真实任务,采集后**如实分类统计**
requests.db 里新增请求的 stream 分布,让你看清主会话 / 子代理各自的 type。

用法:
    python3 collect_stream_profile_subagent.py [每对话轮数]
    默认 3 轮主会话,每轮让 claude 决定是否/何时使用子代理。
"""
import subprocess, sys, os, json, sqlite3, glob, time
from collections import Counter

# ============ 配置 ============
DB = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy/requests.db"
SETTINGS = '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3001"}}'
PROJECT_ROOT = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy"
JSONL_BASE = os.path.expanduser("~/.claude/projects")
PROJECT_JSONL = os.path.join(JSONL_BASE, "-mnt-nvme1n1-data-lmk-PROJECT-claude-code-proxy")
# ==============================

TASK = (
    "请对这个项目做一次多轮的、会用到子代理的走查:\n"
    "1. 先调用 Explore(或 code-auditor)子代理,让它审查 proxy/internal/config/config.go。\n"
    "2. 子代理结束后,请你(主会话)总结它的发现,并给出你的点评。\n"
    "3. 然后你再作为主会话,分析 config.yaml 的配置项,补上你的补充意见。\n"
    "4. 最后小结这次走查的关键点。\n"
    "请保持真实自然的对话节奏。"
)


def count_db():
    if not os.path.exists(DB):
        return 0
    try:
        return sqlite3.connect(DB).execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    except Exception:
        return 0


def agent_jsonls():
    return glob.glob(os.path.join(PROJECT_JSONL, "*/subagents/agent-*.jsonl")) + \
           glob.glob(os.path.join(PROJECT_JSONL, "agent-*.jsonl"))


def run_claude(args):
    cmd = ["claude", "--settings", SETTINGS, "--print", "--dangerously-skip-permissions"] + args
    try:
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=1200)
        return proc.returncode, (proc.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""
    except Exception as e:
        return "ERR", str(e)


def stream_profile(limit=0):
    """统计 db 里(最近 limit 条;limit=0 表示全部)请求的 stream 分布。
    返回 (Counter, 带 ttft 的流式条数).
    口径:body.stream 为 true → '流式(s)';否则 → '非流式(n)'。"""
    conn = sqlite3.connect(DB)
    q = "SELECT body FROM requests"
    if limit:
        q += " ORDER BY timestamp DESC LIMIT ?"
        rows = conn.execute(q, (limit,)).fetchall()
    else:
        rows = conn.execute(q).fetchall()
    conn.close()

    c = Counter()
    ttft_flow = 0
    for (body,) in rows:
        try:
            b = json.loads(body)
        except Exception:
            continue
        if b.get("stream") is True:
            c["流式(s)"] += 1
            # 流式响应如果存了 ttft 另计
        else:
            c["非流式(n)"] += 1
    return c


def subagent_request_count():
    """子代理请求数量的一种近似口径:统计每个 agent-jsonl 里 assistant 回合数
    (每回合一次模型调用)。jsonl 不直接存 stream,故只能算调用次数,
    流式/非流式以 db 里的 body.stream 为准。"""
    total = 0
    for f in agent_jsonls():
        try:
            with open(f, encoding="utf-8") as fh:
                total += sum(
                    1 for line in fh
                    if line.strip() and json.loads(line).get("type") == "assistant"
                )
        except Exception:
            continue
    return total


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    before_db = count_db()
    before_agent = len(agent_jsonls())
    print("=" * 64)
    print("主会话 + 子代理 的 stream 画像采集(按 claude 真实行为)")
    print(f"  主会话轮次 : {rounds} 轮")
    print(f"  采集前     : requests.db={before_db} 条, agent-jsonl={before_agent} 个")
    print("=" * 64)

    total_ok = 0
    for i in range(1, rounds + 1):
        first_round = (i == 1)
        print(f"\n--- 主会话轮次 {i}/{rounds} ---")
        args = []
        if not first_round:
            args.append("--continue")  # 续接同一主会话
        args.append(TASK if first_round else "继续:请基于前面的讨论,再补充你的看法。")
        rc, out = run_claude(args)
        total_ok += 1 if rc == 0 else 0
        print(f"  {'✓' if rc == 0 else '✗'} 轮次{i} rc={rc}")
        time.sleep(1.5)

    after_db = count_db()
    after_agent = len(agent_jsonls())
    dist = stream_profile()                      # 全体新增请求的 stream 画像(整个 db)
    delta_dist = stream_profile(limit=after_db - before_db)  # 按新增条数取最近 N 条
    sub_requests = subagent_request_count()

    print("\n" + "=" * 64)
    print("采集结果")
    print(f"  requests.db       : {before_db} → {after_db} 条 (+{after_db-before_db})")
    print(f"  agent-jsonl(子代理): {before_agent} → {after_agent} 个")
    print(f"  子代理 assistant 回合(≈子代理请求数): {sub_requests}")
    print(f"  本轮新增请求 stream 画像 : {dict(delta_dist)}")
    print(f"  db 全体 stream 画像      : {dict(dist)}")
    print("=" * 64)

    s = delta_dist.get("流式(s)", 0)
    n = delta_dist.get("非流式(n)", 0)
    if after_agent > before_agent and after_db > before_db:
        print(f"✅ 采集到主会话+子代理真实请求: 流式(s)={s}, 非流式(n)={n}。")
        print("   非流式(n) 预期主要来自子代理;主会话是否流式看 claude 实际行为(未强制)。")
        print("   下一步: 在 agentic-coding-analysis 开启分析并重跑 run_trace_generation.py 生成 trace。")
    else:
        print("⚠️ 检查:是否产生新的子代理 jsonl / db 是否有新增,确认 claude 是否走了 3001。")


if __name__ == "__main__":
    main()
