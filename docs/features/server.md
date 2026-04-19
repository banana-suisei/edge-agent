# Feature: HTTP Server

## 文件

- `src/plush_agent/server/app.py` — FastAPI 应用工厂
- `src/plush_agent/server/main.py` — uvicorn 启动入口
- `src/plush_agent/server/routes_chat.py` — `/api/chat`, `/api/chat/end`
- `src/plush_agent/server/routes_form.py` — `/api/forms`
- `src/plush_agent/server/routes_approval.py` — `/api/approvals`

## 应用生命周期

```
plush-agent serve
  → cli.py serve 命令
    → run_server(config)
      → build_agent(config) → agent, store, checkpointer
      → ApprovalHandler(agent, config)
      → create_app() → FastAPI 实例
      → app.state.approval_handler = handler
      → app.state.store = store
      → app.state.checkpointer = checkpointer
      → app.state.config = config
      → uvicorn.run(app, host, port)
```

`app.state` 上的共享对象：

| 属性 | 类型 | 用途 |
|------|------|------|
| `approval_handler` | `ApprovalHandler` | HITL 审批处理 |
| `store` | `BaseStore` | 长期记忆读写 |
| `checkpointer` | `InMemorySaver` | 获取对话历史（会话摘要） |
| `config` | `Config` | LLM 配置（摘要生成） |

## 路由总览

| 方法 | 路径 | 处理文件 | 功能 |
|------|------|----------|------|
| POST | `/api/chat` | `routes_chat.py` | 发送消息给 Agent |
| POST | `/api/chat/end` | `routes_chat.py` | 结束会话并保存摘要 |
| GET | `/api/forms` | `routes_form.py` | 列出待填写表单 |
| GET | `/api/forms/{form_id}` | `routes_form.py` | 获取表单详情 |
| POST | `/api/forms/{form_id}/submit` | `routes_form.py` | 提交表单数据 |
| GET | `/api/approvals` | `routes_approval.py` | 列出待审批操作 |
| GET | `/api/approvals/{approval_id}` | `routes_approval.py` | 获取审批详情 |
| POST | `/api/approvals/{approval_id}/decide` | `routes_approval.py` | 提交审批决策 |

FastAPI 自动生成 `/docs`（Swagger UI）和 `/redoc`（ReDoc）文档。

## 聊天接口详解

### POST /api/chat

**请求：**

```json
{
  "message": "帮我生成一个用户注册表单",
  "thread_id": "optional-thread-id"
}
```

**新 thread 摘要注入：**

当 `checkpointer.get_tuple(cfg)` 返回 `None`（新 thread）时，自动加载上次会话摘要并注入到消息中：

```python
prev_summary = load_session_summary(store)
if prev_summary:
    message = f"[上一次对话摘要]\n{prev_summary}\n[当前消息]\n{message}"
```

**响应：**

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

### POST /api/chat/end

结束会话，生成对话摘要并保存到长期记忆。

**请求：**

```json
{
  "thread_id": "my-thread-id"
}
```

**响应：**

```json
{
  "status": "saved",
  "summary": "- 用户询问了Python数据分析方案\n- 推荐使用pandas..."
}
```

无对话或无消息时返回：

```json
{"status": "no_conversation", "message": "No conversation found for this thread."}
```

流程：
1. 从 `checkpointer.get_tuple()` 获取对话历史
2. 提取 messages
3. 调用 `save_session_summary(store, messages, config)`（内置 3 次重试机制）
4. 返回摘要内容

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
