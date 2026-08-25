#!/usr/bin/env python3
"""
Claude Code Proxy — 真实走代理的多会话 loop 脚本
持续向代理(3001)发送真实请求(经 15721 → DeepSeek 返回 200),
每个请求带 metadata.conversation_id,实现"多会话"数据清洗。

用法:
    python3 agent_loop.py [每会话条数] [会话数] [间隔秒]
    例: python3 agent_loop.py 10 3 0.5   # 3 个会话各 10 条(共30条),间隔0.5s
    例: python3 agent_loop.py 5 2 0.3    # 2 会话各 5 条
默认: 10 3 0.5   (3 会话各 10 条, 共 30 条)
"""
import json, random, sys, time, os, urllib.request, urllib.error

# ============================================================
# *** 你使用的 API 与 Model 配置 ****
# ============================================================

# *** API **：走本机代理(3001),被 claude-code-proxy 记录,再转发到 15721(DeepSeek)。
PROXY = "http://127.0.0.1:3001/v1/messages"

# *** KEY **：15721 网关认 PROXY_MANAGED(代理透传给网关)。不必 export。
KEY = os.environ.get("ANTHROPIC_API_KEY", "PROXY_MANAGED")

# *** MODEL **：15721 网关认的名字(已在环境变量里验证 200)。
MODEL = "deepseek-v4-flash-0731"
# ============================================================

# 流式开关:
#   True  = 流式 → trace 里 type="s" 且带 ttft 字段(注意:DeepSeek 流式 usage 恒 0,in/out 会是 0)
#   False = 非流式 → type="n",无 ttft(上游返回真实 usage, hash 分块更准)
# 若要看 trace 里的 s + ttft,设 STREAM = True
STREAM = True

# 让每个会话的内容不同,但会话内保持一定主题(便于 hash 前缀重叠的观察)
TOPICS = {
    1: "写一个 Go 的 HTTP 服务",
    2: "分析 Python 性能优化方案",
    3: "设计数据库表结构和索引",
}
QUESTIONS = [
    "请给出思路并解释",
    "能提供代码示例吗",
    "有哪些注意事项",
    "再详细展开一下",
    "和另一种方案比较",
    "给出边界处理",
    "如何测试和验证",
    "有没有更优的做法",
    "贴出可运行代码",
    "总结一下要点",
]


def build_messages(session_id, turn):
    """构造『真实』多轮对话:首轮主题问题 + 后续追问,确保前缀单调递增(缓存可命中)。"""
    msgs = [{"role": "user", "content": "【会话%d 第%d轮】 %s %s" % (session_id, turn, TOPICS[session_id], QUESTIONS[turn-1])}]
    # 每条是全新 user 消息(不带历史),等价于新请求; hash 分块可基于内容
    return msgs


def build_body(session_id):
    turn = random.randint(1, len(QUESTIONS))
    return {
        "model": MODEL,
        "max_tokens": 128,
        "temperature": 0.7,
        "stream": STREAM,
        "metadata": {"conversation_id": "session-%d" % session_id},
        "messages": build_messages(session_id, turn),
    }


def send(body):
    req = urllib.request.Request(PROXY, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json",
        "X-Api-Key": KEY,
        "Anthropic-Version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return "ERR:%s" % e


def main():
    per_session = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    n_sessions = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    is_real = KEY and not KEY.startswith("sk-missing")
    mode = "🔑 KEY=%s" % KEY
    print("发送 %d 会话 × 各 %d 条 = %d 条, 间隔%ss, 走代理 %s" % (n_sessions, per_session, n_sessions*per_session, interval, PROXY))
    print("模式: %s" % mode)
    ok = 0
    total = 0
    try:
        for sid in range(1, n_sessions + 1):
            for i in range(1, per_session + 1):
                body = build_body(sid)
                st = send(body)
                total += 1
                if st == 200:
                    ok += 1
                conv = body["metadata"]["conversation_id"]
                print("  [%2d] %s HTTP%s" % (total, conv, st))
                time.sleep(interval)
        print("完成: %d 条, 200成功 %d" % (total, ok))
    except KeyboardInterrupt:
        print("\n已停止, 本节发送 %d 条." % total)


if __name__ == "__main__":
    main()