# Feature: HTTP Server

## 文件

- `src/plush_agent/server/app.py` — FastAPI 应用工厂
- `src/plush_agent/server/main.py` — uvicorn 启动入口
- `src/plush_agent/server/routes_chat.py` — `/api/chat`
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
      → uvicorn.run(app, host, port)
```

`app.state.approval_handler` 是全局共享的 ApprovalHandler 实例，各路由通过 `request.app.state.approval_handler` 访问。

## 路由总览

| 方法 | 路径 | 处理文件 | 功能 |
|------|------|----------|------|
| POST | `/api/chat` | `routes_chat.py` | 发送消息给 Agent |
| GET | `/api/forms` | `routes_form.py` | 列出待填写表单 |
| GET | `/api/forms/{form_id}` | `routes_form.py` | 获取表单详情 |
| POST | `/api/forms/{form_id}/submit` | `routes_form.py` | 提交表单数据 |
| GET | `/api/approvals` | `routes_approval.py` | 列出待审批操作 |
| GET | `/api/approvals/{approval_id}` | `routes_approval.py` | 获取审批详情 |
| POST | `/api/approvals/{approval_id}/decide` | `routes_approval.py` | 提交审批决策 |

FastAPI 自动生成 `/docs`（Swagger UI）和 `/redoc`（ReDoc）文档。

## 聊天接口详解

### 请求

```
POST /api/chat
Content-Type: application/json

{
  "message": "帮我生成一个用户注册表单",
  "thread_id": "optional-thread-id"
}
```

### 响应

**正常完成：**

```json
{
  "status": "done",
  "content": "好的，我已经生成了表单..."
}
```

**需要审批：**

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

### 流程

1. 从 `request.app.state` 获取 `ApprovalHandler`
2. `thread_id` 默认为 `"default"`
3. `_sanitize_surrogates()` 清理请求消息中的代理字符
4. 调用 `handler.run_with_hitl(message, thread_id)`
5. 直接返回 handler 结果（始终是结构化 dict）

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
