# Feature: HTTP Server

## 文件

- `src/plush_agent/server/app.py` — FastAPI 应用工厂（含 lifespan 管理 UDS 启停）
- `src/plush_agent/server/main.py` — uvicorn 启动入口
- `src/plush_agent/server/session.py` — SessionManager（长连接 SSE 会话管理）
- `src/plush_agent/server/routes_chat.py` — `/api/chat`, `/api/chat/stream`, `/api/chat/end`
- `src/plush_agent/server/routes_form.py` — `/api/forms`
- `src/plush_agent/server/routes_approval.py` — `/api/approvals`
- `src/plush_agent/server/uds_server.py` — UDS 审批/表单通道（JSON-Line 协议）

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
      → (if config.uds.enabled)
        → UdsServer(config, handler, streaming_handler, session_manager)
        → app.state.uds_server = uds
      → uvicorn.run(app, host, port)
        → lifespan startup: uds_server.start() (创建 Unix socket)
        → ... 运行 ...
        → lifespan shutdown: uds_server.stop() (关闭并清理 socket)
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
| `uds_server` | `UdsServer` (可选) | UDS 审批/表单通道（仅 `uds.enabled` 时存在） |

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
- 断开时：若会话中发送过消息则自动保存摘要到长期记忆，未发送过消息则跳过摘要保存
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
| SSE 断开后摘要未保存 | 检查 `_on_stream_disconnect` 日志，确认会话是否发送过消息（未发送消息的空会话会跳过摘要） |
| 新连接未踢掉旧连接 | `SessionManager.close()` 是否正确清理 |

## UDS 审批/表单通道

可选的 Unix Domain Socket 通道，供边缘客户端（如 Rust edge）查询审批和表单。通过 `config.yaml` 中 `uds.enabled` 控制启用。

### 协议

JSON-Line 短连接：客户端连接 socket，发送一行 JSON 请求，接收一行 JSON 响应，关闭连接。

**协议版本**：`1`

**请求格式**：
```json
{"action": "getPendingApprovals", "robotId": "robot-0", "interruptId": ""}
```

**响应信封**：
```json
{"version": 1, "action": "getPendingApprovals", "robotId": "robot-0", "timestamp": 1713686400, "success": true, ...}
```

错误响应增加 `"error"` 字段。

### Actions

| Action | 级别 | 说明 |
|--------|------|------|
| `getForms` | 机器人级 | 查询待填写表单，可选 `formId` 过滤，需 `robotId` 匹配 |
| `submitForm` | 机器人级 | 提交表单数据 `{formId, responses}`，需 `robotId` 匹配 |
| `getPendingApprovals` | 节点级 | 查询待审批操作，可选 `interruptId` 过滤，不过滤 `robotId` |
| `submitApprovalDecision` | 节点级 | 提交审批决策 `{interruptId, decision, reason?, editedArgs?}`，不过滤 `robotId` |

**机器人级** vs **节点级**：表单操作面向特定机器人，审批操作面向整个 agent 实例。

### 审批数据映射

内部 `PendingApproval` 映射为 UDS 格式：

```json
{
  "interruptId": "approval-uuid",
  "actionName": "terminal",
  "description": "删除文件",
  "argsJson": "{\"commands\": \"rm -rf /\"}",
  "allowedDecisions": ["approve", "edit", "reject"],
  "createdAt": 1713686400,
  "reviewConfigJson": "[{\"action_name\": \"terminal\", \"allowed_decisions\": [...]}]"
}
```

取第一个 `needs_human` action 的信息作为主字段。`allowedDecisions` 从 `review_configs` 中提取。

### 决策映射

UDS 单个 `decision`（approve/reject/edit）统一应用到该 `PendingApproval` 下所有 `needs_human` 的 action。

| UDS decision | 内部映射 |
|-------------|---------|
| `approve` | `{"type": "approve"}` × N |
| `reject` | `{"type": "reject", "message": reason}` × N |
| `edit` | `{"type": "edit", "edited_action": {...}}` × N |

### SSE 队列共享

当 UDS 提交 streaming 模式的审批决策时，事件通过 `_sse_serialize()` 序列化后推入 SSE `asyncio.Queue`，与 HTTP 路由层行为一致。直接推入原始内部事件（含 `kind` key）会导致 `ServerSentEvent` 崩溃。

### 配置

```yaml
robot_id: "robot-0"        # 机器人标识，用于表单操作的 robotId 校验

uds:
  enabled: false            # 是否启用 UDS 通道
  socket_path: "/tmp/agent-gateway.sock"  # socket 文件路径
```
