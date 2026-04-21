# Feature: HTTP Server

## 文件

- `src/plush_agent/server/app.py` — FastAPI 应用工厂
- `src/plush_agent/server/main.py` — uvicorn 启动入口
- `src/plush_agent/server/session.py` — SessionManager（长连接 SSE 会话管理）
- `src/plush_agent/server/routes_chat.py` — `/api/chat`, `/api/chat/stream`, `/api/chat/end`
- `src/plush_agent/server/routes_form.py` — `/api/forms`
- `src/plush_agent/server/routes_approval.py` — `/api/approvals`

## 应用生命周期

```
plush-agent serve
  → cli.py serve 命令
    → run_server(config)
      → build_agent(config) → agent, store, checkpointer
      → ApprovalHandler(agent, config)
      → StreamingApprovalHandler(agent, config)
      → create_app() → FastAPI 实例
        → SessionManager() 初始化
      → app.state.approval_handler = handler
      → app.state.streaming_approval_handler = streaming_handler
      → app.state.store = store
      → app.state.checkpointer = checkpointer
      → app.state.config = config
      → uvicorn.run(app, host, port)
```

`app.state` 上的共享对象：

| 属性 | 类型 | 用途 |
|------|------|------|
| `approval_handler` | `ApprovalHandler` | HITL 审批处理（阻塞模式） |
| `streaming_approval_handler` | `StreamingApprovalHandler` | HITL 审批处理（SSE 流式模式） |
| `session_manager` | `SessionManager` | 长连接 SSE 会话管理 |
| `store` | `BaseStore` | 长期记忆读写 |
| `checkpointer` | `InMemorySaver` | 获取对话历史（会话摘要） |
| `config` | `Config` | LLM 配置（摘要生成） |

## 路由总览

| 方法 | 路径 | 处理文件 | 功能 |
|------|------|----------|------|
| GET | `/api/chat/stream` | `routes_chat.py` | 建立 SSE 长连接 |
| POST | `/api/chat` | `routes_chat.py` | 发送消息给 Agent |
| POST | `/api/chat/end` | `routes_chat.py` | 结束会话并保存摘要 |
| GET | `/api/forms` | `routes_form.py` | 列出待填写表单 |
| GET | `/api/forms/{form_id}` | `routes_form.py` | 获取表单详情 |
| POST | `/api/forms/{form_id}/submit` | `routes_form.py` | 提交表单数据 |
| GET | `/api/approvals` | `routes_approval.py` | 列出待审批操作 |
| GET | `/api/approvals/{approval_id}` | `routes_approval.py` | 获取审批详情 |
| POST | `/api/approvals/{approval_id}/decide` | `routes_approval.py` | 提交审批决策 |

FastAPI 自动生成 `/docs`（Swagger UI）和 `/redoc`（ReDoc）文档。

## SSE 长连接模式

### GET /api/chat/stream

按 `thread_id` 建立 SSE 长连接，推送该会话的所有 agent 输出。

```
GET /api/chat/stream?thread_id=my-session-1
Accept: text/event-stream
```

**特性**：
- 连接建立时发送 `session_start` 事件
- 每 30 秒发送 `keepalive` 心跳
- 同一 `thread_id` 只允许一个连接，新连接自动踢掉旧连接
- 断开时自动保存会话摘要到长期记忆
- 审批期间连接保持不断开

**SSE 数据格式**：详见 [sse-protocol.md](sse-protocol.md)

### POST /api/chat（SSE 模式）

当对应 `thread_id` 已有 SSE 连接时，POST 请求触发后台 agent 执行，输出通过 SSE 推送。

```json
{
  "message": "帮我列出当前目录的文件",
  "thread_id": "my-session-1"
}
```

**响应**：
```json
{"status": "accepted", "thread_id": "my-session-1"}
```

**新 thread 摘要注入**：

当 `checkpointer.get_tuple(cfg)` 返回 `None`（新 thread）时，自动加载上次会话摘要并注入到消息中：

```python
prev_summary = load_session_summary(store)
if prev_summary:
    message = f"[上一次对话摘要]\n{prev_summary}\n[当前消息]\n{message}"
```

### POST /api/chat（阻塞模式）

当对应 `thread_id` 没有 SSE 连接时，走原有阻塞模式，直接在 HTTP 响应中返回结果。

**响应**：

正常完成：
```json
{
  "status": "done",
  "content": "好的，我已经生成了表单..."
}
```

需要审批：
```json
{
  "status": "pending_approval",
  "approval_id": "uuid-xxx",
  "pending_actions": [
    {
      "name": "terminal",
      "args": {"commands": ["rm -rf /tmp/old"]},
      "description": "Tool execution pending approval\n\nTool: terminal\nArgs: ...",
      "allowed_decisions": ["approve", "edit", "reject"]
    }
  ],
  "auto_approved_count": 1
}
```

### POST /api/approvals/{id}/decide（SSE 模式）

当审批对应的 `thread_id` 有活跃 SSE 连接时，提交决策后 agent 自动恢复，后续输出通过 SSE 推送。

```json
{
  "decisions": [
    {"type": "approve"}
  ]
}
```

**响应**：
```json
{"status": "accepted"}
```

### GET /api/approvals（双模式查询）

同时查询阻塞模式（`approval_handler.pending`）和 SSE 模式（`streaming_approval_handler.streaming_pending`）的待审批数据。无论审批请求来自哪种模式，都能被查到。

### GET /api/approvals/{id}（双模式查询）

同上，按 approval_id 查找时依次搜索阻塞模式 pending 和 SSE 模式 streaming_pending。

### POST /api/chat/end

结束会话，生成对话摘要并保存到长期记忆。**仅在无 SSE 连接时可用**。

```json
{
  "thread_id": "my-thread-id"
}
```

**响应**：

```json
{
  "status": "saved",
  "summary": "- 用户询问了Python数据分析方案\n- 推荐使用pandas..."
}
```

## 启动参数

```bash
plush-agent serve                        # 使用 config.yaml 默认配置
plush-agent serve --host 127.0.0.1       # 指定监听地址
plush-agent serve --port 9000            # 指定端口
plush-agent --config prod.yaml serve     # 指定配置文件
```

## 调试定位

| 问题 | 定位 |
|------|------|
| 启动失败 | 检查端口占用、config.yaml 格式 |
| 404 | 路由前缀 `/api`，检查 URL 拼写 |
| 500 | 查看 uvicorn 日志输出 |
| chat 返回空回复 | `routes_chat.py` 中提取 AI message 的逻辑 |
| `UnicodeEncodeError: surrogates not allowed` | `routes_chat.py` 中 `_sanitize_surrogates()` 清理输入 |
| 摘要未注入 | 检查 checkpointer 返回值和 store 中 "latest" 数据 |
| /chat/end 返回 no_conversation | thread_id 是否正确，对话是否已发生 |
| SSE 无事件返回 | 检查 `session_manager` 是否注册，检查 queue 是否创建 |
| SSE 审批后无输出 | 检查 `streaming_pending` 中是否有对应 approval_id |
| GET /api/approvals 返回空 | 确认路由同时查了 `approval_handler.pending` 和 `streaming_approval_handler.streaming_pending` |
| SSE 断开后摘要未保存 | 检查 `_on_stream_disconnect` 日志 |
| 新连接未踢掉旧连接 | `SessionManager.close()` 是否正确清理 |
