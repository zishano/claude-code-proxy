# Claude Code Proxy → Trace 完整流程指南

将真实 Claude Code 请求采集（经 3001 代理）→ 用 agentic-coding-analysis 生成带 hash_ids / ttft / subagent 的 trace 的完整流程。

---

## 一、整体链路

```
claude CLI ──(走 3001)──> proxy(3001, 记录 requests.db) ──转发──> DeepSeek 网关(15721)
     │                                                          │
     └─ 写 jsonl 会话 / subagent 会话 ──────────────────────────┘
                              │
                              ↓ (run_trace_generation.py)
                     traces/*.json  (带 ttft / hash_ids / subagent)
```

**关键前提**：`claude` 必须走 **3001 proxy**（`ANTHROPIC_BASE_URL=http://127.0.0.1:3001`），这样请求**同时**进入 requests.db 和 jsonl 会话，id 才能匹配（否则 validate 报 "No matching requests"）。

---

## 二、环境前置

| 项 | 要求 | 检查命令 |
|---|---|---|
| proxy(3001) | 运行中，**带 ttft 的版本** | `ss -tlnp \| grep 3001` |
| claude | 走 3001 | `grep BASE_URL ~/.claude/settings.json` → `http://127.0.0.1:3001` |
| 分析项目 | `PROJECT/agentic-coding-analysis` | `ls run_trace_generation.py` |
| tiktoken 缓存 | 绕开下载被墙 | `ls /tmp/tiktoken-cache/` |

> 注意：有**两个** claude-code-proxy 副本（`project/` 小写 和 `PROJECT/` 大写）。本流程用 **大写 `PROJECT/`** 这一套（脚本路径已对齐到它）。

---

## 三、完整步骤

### Step 0：清理旧数据（每次重跑前必做，否则 validate 报 "No matching"）
```bash
cd /mnt/nvme1n1/data/lmk/PROJECT
# 清空 requests.db（保留表结构）
python3 -c "import sqlite3;c=sqlite3.connect('claude-code-proxy/requests.db');c.execute('DELETE FROM requests');c.commit()"
# 删旧 jsonl 会话（含子代理）
rm -f ~/.claude/projects/-mnt-nvme1n1-data-lmk-PROJECT-claude-code-proxy/*.jsonl
find ~/.claude/projects/-mnt-nvme1n1-data-lmk-PROJECT-claude-code-proxy -name "agent-*.jsonl" -delete
find ~/.claude/projects/-mnt-nvme1n1-data-lmk-PROJECT-claude-code-proxy -type d -name subagents -empty -delete 2>/dev/null
# 删旧 trace
rm -f /mnt/nvme1n1/data/lmk/PROJECT/traces/*.json
```

### Step 1：启动 proxy（终端 1，若已运行可跳过）
```bash
pkill -f "bin/proxy" 2>/dev/null
cd /mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy
cd proxy && go build -o ../bin/proxy cmd/proxy/main.go && cd ..   # 确保带 ttft 的最新二进制
./bin/proxy
```
预期：`Claude Code Monitor Server running on http://localhost:3001`

### Step 2：采集真实数据（终端 2）
```bash
cd /mnt/nvme1n1/data/lmk/PROJECT/claude-code-proxy
python3 collect_real_subagent.py 3
```
- 按 claude **真实行为**采集：主会话流式/非流式混合，子代理天然非流式
- 预期：`requests.db: X → Y 条`，`stream 分布: {'流式(s)': N, '非流式(n)': M}`，`流式请求带 ttft 的: N 条`，`agent-jsonl: 0 → 1 个`

### Step 3：生成 trace（终端 2）
```bash
cd /mnt/nvme1n1/data/lmk/PROJECT/agentic-coding-analysis
python3 run_trace_generation.py
```
- `INCLUDE_STREAMING=True`（追加流式，type=s）、`INCLUDE_SUBAGENTS=True`（含子代理）
- 预期：`生成 trace 文件: N 个`、`Simulated: XX%`
- 输出在 `PROJECT/traces/*.json`

### Step 4：查看结果
```bash
cd /mnt/nvme1n1/data/lmk/PROJECT/traces
ls -la
```
trace 里 request 字段：`type`（s=流式/n=非流式/subagent）、`ttft`（首 token 延迟，仅流式）、`in`/`out`、`hash_ids`。

---

## 四、项目里可用的采集脚本

| 脚本 | 用途 |
|---|---|
| `collect_real_subagent.py` | **推荐**。按 claude 真实行为，多轮主会话 + 子代理，流式/非流式混合 |
| `collect_subagent.py` | 单次 spawn code-auditor 子代理审查代码 |
| `collect_mixed_stream_subagent.py` | HTTP 直发流式/非流式 + 1 次真实子代理 |
| `collect_multi_conversations.py` | 多对话 claude 采集（无子代理）|

---

## 五、关键结论 / 排查记录

1. **`API: 0.0%` 是预期（非 bug）**
   `validate_trace_cache.py` 的 API 命中率从 response 的 `cache_read_input_tokens` 读。**DeepSeek 上游从不返回 cache 字段**（243 条响应验证 0 条有），所以 API=0%。要让 API 有意义需换支持 Anthropic prompt caching 的上游。

2. **`No matching requests in database` 的原因**
   requests.db 被清空重建后，残留的旧 jsonl 会话对应请求已不在 db → validate 匹配不上。解决：Step 0 清掉孤儿 jsonl。

3. **流式请求的 `ttft` 是 proxy 新加的**（已改 Go 代码）
   - `proxy/internal/model/models.go`：`ResponseLog` 加 `TTFT` 字段
   - `proxy/internal/handler/handlers.go`：`handleStreamingResponse` 检测首个 `content_block_delta` 记录 `ttft`
   - `build_minimal_traces.py`：`extract_response_info` 优先读 proxy 的 `ttft`；且当 usage 缺失时用响应 text 计算 `out_tokens`（修 out=0）

4. **流式请求 in 偏大**：长上下文 claude 请求（system-reminder 大）in 会到几十万，是真实数据，非算错。

5. **修改过的文件**（相对上游）
   - `PROJECT/claude-code-proxy/proxy/internal/model/models.go`
   - `PROJECT/claude-code-proxy/proxy/internal/handler/handlers.go`
   - `PROJECT/agentic-coding-analysis/build_minimal_traces.py`
   - `PROJECT/agentic-coding-analysis/run_trace_generation.py`（加 `INCLUDE_STREAMING`）
   - `PROJECT/claude-code-proxy/.env`（`ANTHROPIC_FORWARD_URL=http://127.0.0.1:15721`）
   - `PROJECT/claude-code-proxy/config.yaml`（anthropic base_url 指向 15721）
   - `~/.claude/settings.json`（`ANTHROPIC_BASE_URL=http://127.0.0.1:3001`，备份在 `~/.claude/settings.json.bak.*`）

6. **子代理定义**
   `PROJECT/claude-code-proxy/.claude/agents/code-auditor.md`（从 `project/` 副本复制）—— 没有它 claude 无法 spawn 名为 code-auditor 的子代理。

---

## 六、最终验证示例（某次成功结果）

```
12bf8db9-dc6.json
  type 分布: {s:19(流式带ttft), n:5(非流式), subagent:1}
  subagent_count: 1
  流式 ttft: 1.5~2.5s, out: 正确(非0)
  Simulated: 81.0%
```
