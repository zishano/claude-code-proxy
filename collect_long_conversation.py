#!/usr/bin/env python3
"""
真实走代理的长对话采集脚本
通过 `claude --print` 派发一个多步骤复杂任务,让 Claude 自动产生多轮、
多工具的真实请求(读文件、编辑、跑命令、写报告),从而:
  - requests.db 记录大量真实请求(经 3001 代理)
  - ~/.claude/projects 生成 rich jsonl(真实缓存前缀累积)
供 agentic-coding-analysis 生成带 hash_ids 的真实缓存命中 trace。

用法:
    python3 collect_long_conversation.py [目标项目目录] [额外任务提示词...]
    例: python3 collect_long_conversation.py /path/to/foo "请完整分析这个项目"
默认: claude-code-proxy 项目根,执行"Go 源码多文件走查+报告"任务。
"""
import subprocess, sys, os, json, sqlite3, glob, time

DB = "/mnt/nvme1n1/data/lmk/project/claude-code-proxy/requests.db"
SETTINGS = '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3001"}}'

# 默认分析的项目根目录 = claude-code-proxy 本身
DEFAULT_PROJECT = "/mnt/nvme1n1/data/lmk/project/claude-code-proxy"

DEFAULT_TASK = (
    "请对这个项目做一次完整而深入的代码走查,请充分使用工具:\n"
    "1. 先列出仓库顶层结构(用 ls / Bash 工具)\n"
    "2. 进入 proxy/ 目录,读取 cmd/proxy/main.go, internal/handler/handlers.go, "
    "internal/handler/utils.go, internal/config/config.go, internal/service/model_router.go, "
    "internal/service/storage_sqlite.go 等核心文件\n"
    "3. 逐个文件分析:功能、潜在 bug、边界问题、可优化点、安全风险\n"
    "4. 特别关注:请求流转、路由、SQLite 存储、并发安全、错误处理\n"
    "5. 多次 Read/多次分析后,写一份 code_review.md 到项目根,汇总你发现的问题和改进建议\n"
    "6. 每读完一个文件都给出你的点评,最后做一个整体总结\n"
    "请务必大量阅读文件、充分使用工具,拉长对话;不要只读一个文件就结束。"
)


def count_db():
    if not os.path.exists(DB):
        return 0
    try:
        return sqlite3.connect(DB).execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    except Exception:
        return 0


def newest_jsonl():
    files = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    if not files:
        return None, None
    newest = max(files, key=os.path.getmtime)
    return newest, os.path.getmtime(newest)


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT
    task = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else DEFAULT_TASK

    # 要分析的项目根:若给了文件/目录,取其所在目录作为 cwd
    project = os.path.abspath(project)
    workdir = project if os.path.isdir(project) else os.path.dirname(project)

    before_db = count_db()
    _, before_j = newest_jsonl()

    print("=" * 60)
    print("长对话采集任务")
    print(f"  cwd(分析根目录): {workdir}")
    print(f"  采集前 requests.db: {before_db} 条")
    print(f"  走代理: {SETTINGS}")
    print("=" * 60)
    print("派发 Claude Code 任务(多步骤,多工具)...")
    print()

    cmd = ["claude", "--settings", SETTINGS, "--print",
           "--dangerously-skip-permissions", task]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=900)
        dur = time.time() - t0
        print(f"Claude 完成,耗时 {dur:.1f}s")
        print("--- Claude 输出(结尾 2000 字符) ---")
        out = (proc.stdout or "").strip()
        print(out[-2000:] if len(out) > 2000 else out)
        err = (proc.stderr or "").strip()
        if err:
            print("--- stderr(如有) ---")
            print(err[-600:])
    except subprocess.TimeoutExpired:
        print("⏰ 超时(可能任务很长)。已采集的请求仍已记录。")
    except Exception as e:
        print(f"❌ 失败: {e}")
        return

    after_db = count_db()
    _, after_j = newest_jsonl()
    print("\n" + "=" * 60)
    print("采集结果")
    print(f"  requests.db: {before_db} → {after_db} 条 (+{after_db-before_db})")
    print(f"  最新 jsonl:  {after_j}")
    print("=" * 60)
    if after_db - before_db > 0:
        print("✅ 真实请求已采集,可跑 agentic-coding-analysis 生成 trace。")
        print("   下一句: cd project/agentic-coding-analysis && 按它 README 跑四步。")
    else:
        print("⚠️ 没有新增请求 —— 检查 claude 是否真的走了 3001 代理。")


if __name__ == "__main__":
    main()