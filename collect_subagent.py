#!/usr/bin/env python3
"""
真实走代理的"子代理"采集脚本
派发一个明确要求使用子代理(code-auditor)的任务,让 Claude Code 实际
spawn 子代理去分析代码并产出独立会话 jsonl(agent-*.jsonl),
从而为 agentic-coding-analysis 的 --include-subagents 提供真实子代理数据。

用法:
    python3 collect_subagent.py [目标项目目录] [额外任务提示词...]
默认: 让 code-auditor 子代理审查 claude-code-proxy 的 proxy/ Go 代码。
"""
import subprocess, sys, os, json, sqlite3, glob, time

DB = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy/requests.db"
SETTINGS = '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3001"}}'
PROJECT_ROOT = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy"
# 子代理会生成到该目录下的 <父会话>/subagents/agent-*.jsonl
JSONL_BASE = os.path.expanduser("~/.claude/projects")

DEFAULT_TASK = (
    "请调用 code-auditor 这个子代理,让它去审查 proxy/ 目录下的 Go 代码。\n"
    "要求:\n"
    "1. 显式启动 code-auditor 子代理(它必须作为独立子会话运行,不要自己替代它)。\n"
    "2. 让 code-auditor 完整读取 cmd/proxy/main.go、internal/handler/handlers.go"
    "、internal/config/config.go、internal/service/model_router.go、"
    "internal/service/storage_sqlite.go 这五个文件。\n"
    "3. 让它逐个文件给出:功能、潜在 bug、安全风险、性能问题、改进建议。\n"
    "4. 让它把审查报告写到 code-audit-report.md。\n"
    "5. 子代理结束后,你(主会话)再对报告做一次简评。\n"
    "务必确保 code-auditor 作为真实子代理被启动,不要简化成普通回答。"
)


def count_db():
    if not os.path.exists(DB): return 0
    try:
        return sqlite3.connect(DB).execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    except Exception: return 0


def glob_new(path_pattern):
    return glob.glob(path_pattern, recursive=True)


def subagent_jsonls():
    """寻找子代理 jsonl。真实结构: ~/.claude/projects/<项目>/<父uuid>/subagents/agent-*.jsonl
    (也兼容顶层的 agent-*.jsonl)"""
    base = JSONL_BASE
    hits = glob.glob(os.path.join(base, "*", "*", "subagents", "agent-*.jsonl")) + \
           glob.glob(os.path.join(base, "*", "subagents", "agent-*.jsonl")) + \
           glob.glob(os.path.join(base, "*", "agent-*.jsonl"))
    return hits


def newest_jsonl():
    files = glob.glob(os.path.join(JSONL_BASE, "*", "*.jsonl"))
    if not files: return None, None
    newest = max(files, key=os.path.getmtime)
    return newest, os.path.getmtime(newest)


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else PROJECT_ROOT
    task = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else DEFAULT_TASK
    project = os.path.abspath(project)
    workdir = project if os.path.isdir(project) else os.path.dirname(project)

    def count_agent_jsonl():
        return len(subagent_jsonls())

    before_db = count_db()
    before_agent = count_agent_jsonl()
    _, before_j = newest_jsonl()

    print("=" * 64)
    print("【子代理采集】走代理 + 真实 spawn 子代理")
    print(f"  cwd      : {workdir}")
    print(f"  采集前   : requests.db={before_db} 条, agent-jsonl={before_agent} 个")
    print(f"  代理     : {SETTINGS}")
    print("=" * 64)
    print("派发 real 子代理任务...\n")

    cmd = ["claude", "--settings", SETTINGS, "--print",
           "--dangerously-skip-permissions", task]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=1200)
        dur = time.time() - t0
        print(f"Claude 完成,耗时 {dur:.1f}s")
        print("--- 输出(结尾 1500) ---")
        out = (proc.stdout or "").strip()
        print(out[-1500:] if len(out) > 1500 else out)
    except subprocess.TimeoutExpired:
        print("⏰ 超时。已产生的请求/子代理已记录。")
    except Exception as e:
        print(f"❌ {e}"); return

    after_db = count_db()
    after_agent = count_agent_jsonl()
    _, after_j = newest_jsonl()

    print("\n" + "=" * 64)
    print("采集结果")
    print(f"  requests.db : {before_db} → {after_db} 条 (+{after_db-before_db})")
    print(f"  agent-jsonl : {before_agent} → {after_agent} 个")
    agent_files = subagent_jsonls()
    if agent_files:
        for f in agent_files[-5:]:
            print(f"      - {os.path.basename(f)}")
    print(f"  最新 jsonl  : {after_j}")
    print("=" * 64)

    if after_agent > before_agent:
        print("✅ 生成了新的子代理会话 jsonl —— 满足 --include-subagents 分析所需。")
        print("   下一步:在 agentic-coding-analysis 开启 INCLUDE_SUBAGENTS=True 并重跑 run_trace_generation.py。")
    elif after_db > before_db:
        print("⚠️ 有真实请求但未见新 agent-jsonl。可能子代理未 spawn 或走了别的目录。")
    else:
        print("⚠️ 无新增 —— 检查 claude 是否真正走了 3001 代理。")


if __name__ == "__main__":
    main()