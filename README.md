# claude-code-proxy 使用指南（本环境适配版）

透明代理 + 可视化面板，把 Claude Code 的请求转发到**本机 DeepSeek 网关**，并记录到 SQLite，供面板（5173）实时查看。同时可作为 **agentic-coding-analysis 的数据采集源**（产生 `requests.db` 供生成带 hash_ids 的 trace）。

> 原仓库通用 README 见 `README.original.md`；本文件针对本机（DeepSeek 后端）实际部署验证过。

## 架构

```
claude (ANTHROPIC_BASE_URL=3001)
   → 3001 claude-code-proxy (记录 requests.db)
       → 15721 DeepSeek 网关 (Anthropic 兼容)
           → DeepSeek-v4-flash
浏览器 → 5173 面板 (展示记录)
```

## 关键端口

| 端口 | 角色 | 说明 |
|---|---|---|
| **3001** | 代理 API | claude / 脚本发的请求走这里，转发+记录 |
| **5173** | 面板 | 浏览器看的界面 |
| **15721** | DeepSeek 网关 | 代理转发的上游（Anthropic 兼容，认 `PROXY_MANAGED`）|

> ⚠️ 分清：**3001 是给 claude 连的**；**5173 是给浏览器看的**。claude 绝不连 5173。

## 配置

`.env` 已配好（.gitignore，不入库）：
```
ANTHROPIC_FORWARD_URL=http://127.0.0.1:15721   # 转发到 DeepSeek 网关
ANTHROPIC_VERSION=2023-06-01
PORT=3001
DATABASE_PATH=requests.db
```
- **转发目标**：`127.0.0.1:15721`（可用 cc-switch 的同一网关）
- **认证**：网关认 `PROXY_MANAGED`（15721 自己管理 key，代理原样透传）
- **模型名**：`deepseek-v4-flash-0731`（15721 认的名字）

## 启动

```bash
cd /mnt/nvme1n1/data/lmk/project/claude-code-proxy
./run.sh          # 前端 shell 用 ./run.sh (会 go build + 起 3001/5173)
```
或分开：
```bash
go build -o bin/proxy proxy/cmd/proxy/main.go && ./bin/proxy   # 起 3001
cd web && npm run dev                                           # 起 5173
```

## 让真实 Claude Code 走代理（采集数据）

**关键**：你的 cc-switch 把 `ANTHROPIC_BASE_URL` 设成了 `15721`（settings.json），优先级比 shell export 高，**用环境变量 export 无法覆盖**。要用 **`--settings` 命令行参数**（优先级最高）：

```bash
claude --settings '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3001"}}'
```

> 这样就：claude 发请求到 **3001** → 被代理记录进 `requests.db` → 转发 15721 → DeepSeek 返回 200。
> 验证：进 claude 问一句"在吗"能回复，且 `requests.db` 记录数增长。
> ⚠️ 别用 `unset/export`，那是无效的（settings.json 优先级更高）。

## 生成多条 Request History（压测）

项目带 `gen_requests.py`（仿 claude-3 loop_sim 的多会话模拟脚本）：
```bash
cd /mnt/nvme1n1/data/lmk/project/claude-code-proxy
python3 gen_requests.py 30 0.5     # 30 条,间隔0.5s
python3 gen_requests.py 0 0.3      # 无限发
```
- 每条请求带 `metadata.conversation_id`（模拟会话切换，每 10 条切一个）
- 面板按会话累积 Request History
- 注意：带假工具调用/流式的请求，DeepSeek 可能返回空 content 或流式 usage=0（上游行为，非代理 bug）

## 看数据

- **面板**：浏览器 `http://localhost:5173`（Requests / Conversations）
- **API**：`http://localhost:3001/api/requests`
- **健康**：`http://localhost:3001/health`

## 与其他仓库配合

```
claude-code-proxy  (采集 requests.db)
        ↓
agentic-coding-analysis  (生成带 hash_ids 的 trace)
        ↓
kv-cache-tester  (压测推理服务器)
```

采集足够真实会话后，跑 `agentic-coding-analysis` 的四步（见它那份 README）。

## 常见坑

| 现象 | 原因 | 解决 |
|---|---|---|
| claude 请求没进 requests.db | cc-switch 把 base_url 盖成 15721 | 用 `claude --settings` 指定 3001 |
| 面板没数据 | 没走代理 / requests.db 空 | 确认真实 claude 走 3001 并对话 |
| `UI not available` (3001/) | 那是简易界面，不影响转发 | 看面板用 5173，不是 3001 |
| token 都是 0 | 流式时 DeepSeek usage 恒 0 | 用非流式(stream:false)，或看 t 字段 |