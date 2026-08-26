#!/usr/bin/env python3
"""
真实 claude Code 采集脚本(按 claude 真实行为)
============================================================
按方案2:忠实反映 claude Code 的真实行为来采集,不强行控制 stream。
  - 主会话请求:claude 发什么就什么(流式请求会带 ttft → type=s)
  - 子代理请求:claude 天然发非流式(经验证 → type=n)
派发一个"多轮 + spawn 子代理"的真实任务,让 trace 里同时含:
  type=s(流式,带 ttft) + type=n(非流式) + type=subagent(子代理)

用法:
    python3 collect_real_subagent.py [每对话轮数]
    默认 3 轮主会话,每轮让 claude 决定是否/何时使用子代理。
"""
import subprocess, sys, os, json, sqlite3, glob, time, re

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


def analyze_stream_report():
    """统计 db 里最近请求的 stream/ttft 分布 + agent jsonl 数"""
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT body,response FROM requests ORDER BY timestamp DESC").fetchall()
    from collections import Counter
    c = Counter()
    ttft_flow = 0
    for body, resp in rows:
        try:
            b = json.loads(body); r = json.loads(resp) if resp else {}
        except Exception:
            continue
        if b.get('stream') is True:
            c['流式(s)'] += 1
            if r.get('ttft') is not None:
                ttft_flow += 1
        else:
            c['非流式(n)'] += 1
    conn.close()
    return dict(c), ttft_flow, len(agent_jsonls())


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    before_db = count_db()
    before_agent = len(agent_jsonls())
    print("=" * 64)
    print("真实 claude Code 采集(按 claude 真实行为)")
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
        if rc == 0:
            total_ok += 1
            print(f"  ✓ 轮次{i} 完成 rc=0")
        else:
            print(f"  ✗ 轮次{i} rc={rc}")
        time.sleep(1.5)

    after_db = count_db()
    dist, ttft_flow, after_agent = analyze_stream_report()
    print("\n" + "=" * 64)
    print("采集结果")
    print(f"  requests.db      : {before_db} → {after_db} 条 (+{after_db-before_db})")
    print(f"  stream 分布       : {dist}")
    print(f"  流式请求带 ttft 的 : {ttft_flow} 条")
    print(f"  agent-jsonl      : {before_agent} → {after_agent} 个")
    print("=" * 64)
    if after_agent > before_agent and dist.get('流式(s)', 0) > 0:
        print("✅ 采集成功:有流式(带ttft) + 有子代理(非流式)。可跑 run_trace_generation.py 生成 trace。")
    else:
        print("⚠️ 检查:是否产生子代理、以及流式请求。可重试或检查 claude 是否走了 3001。")


if __name__ == "__main__":
    main()
