#!/usr/bin/env python3
"""
真实走代理的多对话采集脚本
用 `claude --settings --print` 派发 2 个独立对话,每个对话用 --continue 精确续到
5 条请求记录。所有请求经 3001 代理转发,从而:
  - requests.db 记录 2 对话 × 5 条 = 10 条真实请求(经 3001 代理)
  - ~/.claude/projects 生成对应 2 个对话的 rich jsonl(真实缓存前缀累积)

用法:
    python3 collect_multi_conversations.py [每对话续接条数]
    默认每对话 5 条(即 1 新建 + 4 次续接),2 个对话共 10 条。
    例: python3 collect_multi_conversations.py 5   # 每对话 5 条
"""
import subprocess, sys, os, json, sqlite3, glob, time, re

# ============ 配置 ============
# 采集到哪个 requests.db(对齐 PROJECT 副本)
DB = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy/requests.db"
# 让 claude 走 3001 代理,从而被 proxy 监控记录
SETTINGS = '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3001"}}'

# 2 个对话,每个一个主题任务(内容不同便于区分)
CONVERSATIONS = [
    {
        "name": "frontend-review",
        "first": "请审查这个项目的前端(web/)目录,列出主要组件和路由结构,简述每个文件职责。",
        "continue_prompts": [
            "继续:针对上面提到的组件,指出潜在的性能问题并给出改进建议。",
            "继续:再分析样式/布局相关的实现是否有可优化点。",
            "继续:总结前端整体架构,列出一份精简的 code_review 摘要。",
            "继续:最后给出前端部分最值得优先修复的 3 个问题。",
        ],
    },
    {
        "name": "backend-review",
        "first": "请审查这个项目的后端(proxy/)目录,梳理请求处理的主链路(入口→handler→service)。",
        "continue_prompts": [
            "继续:重点分析 model_router 的路由逻辑与异常处理。",
            "继续:检查存储层(SQLite)的并发安全与错误处理。",
            "继续:总结后端核心流程,标注风险点与改进建议。",
            "继续:最后给出后端部分最值得优先修复的 3 个问题。",
        ],
    },
]

# 默认分析的项目根目录(与代理对齐的 PROJECT 副本)
DEFAULT_PROJECT = "/mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy"
# ==============================


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


def run_claude(args, cwd):
    """执行一次 claude --print,返回 (返回码, stdout)"""
    cmd = ["claude", "--settings", SETTINGS, "--print",
           "--dangerously-skip-permissions"] + args
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)
        return proc.returncode, (proc.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""


def main():
    per_conv = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    project = os.path.abspath(DEFAULT_PROJECT)
    workdir = project if os.path.isdir(project) else os.path.dirname(project)

    before_db = count_db()
    _, before_j = newest_jsonl()

    print("=" * 60)
    print("多对话采集任务(真实 claude 请求,走 3001 代理)")
    print(f"  cwd: {workdir}")
    print(f"  对话数: {len(CONVERSATIONS)}, 每对话 {per_conv} 条(1 新建 + {per_conv-1} 续接)")
    print(f"  采集前 requests.db: {before_db} 条")
    print(f"  SETTINGS: {SETTINGS}")
    print("=" * 60)

    total_ok = 0
    total_fail = 0
    for ci, conv in enumerate(CONVERSATIONS, 1):
        print(f"\n--- 对话 {ci}/{len(CONVERSATIONS)} [{conv['name']}] ---")
        # rot: 循环使用 per_conv 个提示词,先用 first,不足则循环 continue_prompts
        prompts = [conv["first"]] + (conv["continue_prompts"] * 10)[:per_conv - 1]

        conv_ok = 0
        for i, prompt in enumerate(prompts):
            # 第一条:新建会话;后续:--continue 续接同一会话
            is_continue = i > 0
            label = "新建" if not is_continue else f"续接{i}"
            args = []
            if is_continue:
                args.append("--continue")
            args.append(prompt)

            rc, out = run_claude(args, workdir)
            tail = out[-60:].replace("\n", " ")
            if rc == 0:
                conv_ok += 1
                total_ok += 1
                print(f"  [{label:>5}] ✓ rc=0  {tail}")
            else:
                total_fail += 1
                print(f"  [{label:>5}] ✗ rc={rc}  {tail}")
            time.sleep(1)  # 避免过密,方便 proxy 逐条落库

        print(f"  → 对话 {conv['name']} 成功 {conv_ok}/{per_conv} 条")
        # 对话之间留出间隔,确保 --continue 只续当前对话(串行)
        time.sleep(2)

    after_db = count_db()
    _, after_j = newest_jsonl()
    print("\n" + "=" * 60)
    print("采集结果")
    print(f"  requests.db: {before_db} → {after_db} 条 (+{after_db-before_db})")
    print(f"  claude 成功请求: {total_ok}, 失败: {total_fail}")
    print(f"  最新 jsonl:  {after_j}")
    print("=" * 60)
    if after_db - before_db > 0:
        print("✅ 已采集真实请求,可到 5173 面板或 /api/requests 查看多条 Request History。")
    else:
        print("⚠️ 没有新增请求 —— 检查 proxy(3001) 是否在运行、claude 是否真的走了 3001 代理。")


if __name__ == "__main__":
    main()
