# SSE Streaming Protocol — 客户端对接指南

## 概述

Plush-Agent 使用长连接 SSE（Server-Sent Events）按会话（thread_id）推送 agent 的所有输出。客户端通过 HTTP POST 发送消息和审批决策，通过 SSE 接收文本和事件。

**核心设计**：
- SSE 按 `thread_id` 建立长连接，一个会话一条连接
- 审批期间 SSE 保持连接，客户端提交审批后 agent 自动恢复
- SSE 断开时自动保存会话摘要到长期记忆
- 同一 `thread_id` 只允许一个 SSE 连接，新连接踢掉旧连接

## 连接建立

```
GET /api/chat/stream?thread_id=<thread_id>
Accept: text/event-stream
```

连接建立后服务端立即发送 `session_start` 事件，之后保持长连接等待数据推送。

**thread_id**：客户端自定义的会话标识符，用于关联对话上下文。建议使用 UUID。

## 数据格式

SSE 层面只有两种事件类型：`text` 和 `event`。

### text 事件 — LLM 文本片段

逐 token 推送 LLM 生成的文本。客户端拼接所有 `text` 事件重建完整回复。

```
event: text
data: "Hello"
```

`data` 是 JSON 编码的字符串（含引号和转义）。客户端需 `JSON.parse(data)` 获取实际文本。

### event 事件 — 结构化事件

所有非文本输出通过 `event` 事件推送，使用统一信封格式：

```
event: event
data: {"id":"evt_a1b2c3","type":"tool_call","timestamp":1713686400.0,"data":{...}}
```

**信封字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一事件 ID，格式 `evt_` + 12位十六进制，用于追踪和去重 |
| `type` | string | 事件类型名，客户端按此字段分发处理 |
| `timestamp` | float | Unix 时间戳（秒） |
| `data` | object | 事件载荷，结构随 `type` 变化 |

**客户端应忽略未知的 `type`**，以兼容未来新增的事件类型。

### keepalive 事件

服务端每 30 秒发送一次心跳，保持连接活跃：

```
event: keepalive
data: 
```

客户端无需处理，仅用于防止连接超时。

## 事件类型目录

### session_start

SSE 连接建立时发送的第一个事件。

```json
{
  "id": "evt_s1",
  "type": "session_start",
  "timestamp": 1713686399.0,
  "data": {
    "thread_id": "my-session-1"
  }
}
```

### message_start

Agent 开始处理一条消息时发送。

```json
{
  "id": "evt_ms1",
  "type": "message_start",
  "timestamp": 1713686399.5,
  "data": {
    "message_id": "msg_abc123"
  }
}
```

### tool_call

Agent 调用工具时发送。`call_id` 可用于关联后续的 `tool_result`。

```json
{
  "id": "evt_tc1",
  "type": "tool_call",
  "timestamp": 1713686400.5,
  "data": {
    "call_id": "c1",
    "name": "terminal",
    "args": {"commands": "ls -la"}
  }
}
```

### tool_result

工具执行完成后发送。通过 `call_id` 关联到对应的 `tool_call`。

```json
{
  "id": "evt_tr1",
  "type": "tool_result",
  "timestamp": 1713686401.2,
  "data": {
    "call_id": "c1",
    "name": "terminal",
    "content": "file1\nfile2",
    "is_error": false
  }
}
```

### approval_request

Agent 需要人工审批时发送。**SSE 连接保持不断开**，agent 暂停等待决策。

```json
{
  "id": "evt_ap1",
  "type": "approval_request",
  "timestamp": 1713686402.0,
  "data": {
    "approval_id": "550e8400-e29b-41d4-a716-446655440000",
    "thread_id": "my-session-1",
    "pending_actions": [
      {
        "index": 0,
        "name": "terminal",
        "args": {"commands": "rm -rf /tmp/old"},
        "description": "Tool execution pending approval\n\nTool: terminal\nArgs: ...",
        "allowed_decisions": ["approve", "edit", "reject"]
      }
    ],
    "auto_approved_count": 1
  }
}
```

`pending_actions` 可能包含多个待审批操作。每个操作的字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `index` | int | 在所有操作中的位置索引 |
| `name` | string | 工具名 |
| `args` | object | 工具参数 |
| `description` | string | 操作描述 |
| `allowed_decisions` | string[] | 允许的决策类型：`approve`、`edit`、`reject` |

### message_done

Agent 完成一轮消息的完整处理后发送。此时客户端可以发送下一条消息。

```json
{
  "id": "evt_md1",
  "type": "message_done",
  "timestamp": 1713686403.0,
  "data": {}
}
```

**注意**：如果出现 `approval_request`，`message_done` 会在审批通过并完成后续处理后发送。

### message_finish

流式文本传输结束后发送，包含完整的消息内容。客户端可用于获取完整回复而无需自行拼接 text 事件。

```json
{
  "id": "evt_mf1",
  "type": "message_finish",
  "timestamp": 1713686403.0,
  "data": {
    "message_id": "msg_abc123",
    "content": "Hello! Let me check that for you."
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | string | 与 `message_start` 中的 `message_id` 一致 |
| `content` | string | 该轮消息的完整文本内容（所有 text 事件的拼接） |

**注意**：仅在初始消息发送时产生（POST /api/chat 触发），审批恢复后的流式输出不发送此事件。

### error

发生异常时发送。

```json
{
  "id": "evt_e1",
  "type": "error",
  "timestamp": 1713686405.0,
  "data": {
    "code": "runtimeerror",
    "message": "RuntimeError: boom"
  }
}
```

## API 接口

### 发送消息

```
POST /api/chat
Content-Type: application/json

{
  "message": "帮我列出当前目录的文件",
  "thread_id": "my-session-1"
}
```

**响应**：

```json
{"status": "accepted", "thread_id": "my-session-1"}
```

Agent 的回复通过 SSE 推送，而非 HTTP 响应体。**必须先建立 SSE 连接**，否则走阻塞模式直接返回。

### 提交审批决策

```
POST /api/approvals/<approval_id>/decide
Content-Type: application/json

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

`decisions` 数组长度必须等于 `pending_actions` 的数量，顺序一致。

决策类型：

| type | 说明 | 额外字段 |
|------|------|----------|
| `approve` | 批准执行 | 无 |
| `edit` | 修改参数后批准 | `edited_action`: `{"name": "...", "args": {...}}` |
| `reject` | 拒绝执行 | `message`: 拒绝原因（可选） |

提交后 agent 自动恢复，后续输出通过原 SSE 连接推送。

### 结束会话（手动）

```
POST /api/chat/end
Content-Type: application/json

{"thread_id": "my-session-1"}
```

仅在 SSE 未连接时可用。SSE 连接中断时会自动保存摘要，无需手动调用。

### 查询待审批

```
GET /api/approvals
GET /api/approvals/<approval_id>
```

用于在客户端重启后恢复未完成的审批状态。

## 完整交互流程序列图

```
┌────────┐                              ┌────────┐
│ Client │                              │ Server │
└───┬────┘                              └───┬────┘
    │                                       │
    │  GET /api/chat/stream?thread_id=t1    │
    │──────────────────────────────────────>│
    │  SSE: session_start                   │
    │<──────────────────────────────────────│
    │                                       │
    │  POST /api/chat {message, thread_id}  │
    │──────────────────────────────────────>│
    │  HTTP 200: {status: "accepted"}       │
    │<──────────────────────────────────────│
    │                                       │
    │  SSE: message_start                   │
    │<──────────────────────────────────────│
    │  SSE: text "Let me"                   │
    │<──────────────────────────────────────│
    │  SSE: text " check that."             │
    │<──────────────────────────────────────│
    │  SSE: event tool_call                 │
    │<──────────────────────────────────────│
    │  SSE: event approval_request          │
    │<──────────────────────────────────────│
    │                                       │
    │  (客户端展示审批 UI)                    │
    │                                       │
    │  POST /api/approvals/{id}/decide      │
    │  {decisions: [{type: "approve"}]}     │
    │──────────────────────────────────────>│
    │  HTTP 200: {status: "accepted"}       │
    │<──────────────────────────────────────│
    │                                       │
    │  SSE: event tool_result               │
    │<──────────────────────────────────────│
    │  SSE: text "Done!"                    │
    │<──────────────────────────────────────│
    │  SSE: event message_finish            │
    │<──────────────────────────────────────│
    │  SSE: event message_done              │
    │<──────────────────────────────────────│
    │                                       │
    │  (客户端可发送下一条消息)                │
    │                                       │
    │  POST /api/chat {message, thread_id}  │
    │──────────────────────────────────────>│
    │  ...                                  │
    │                                       │
    │  (客户端断开 SSE)                      │
    │────────x                               │
    │                                       │
    │                            (自动保存摘要)│
```

## 错误处理

### HTTP 层级错误

| 状态码 | 说明 |
|--------|------|
| 400 | 请求参数错误（SSE 仍活跃时调用 /chat/end） |
| 404 | 审批请求不存在 |
| 500 | 服务端内部错误 |

### SSE 层级错误

`error` 事件通过 SSE 推送，包含 `code` 和 `message` 字段。收到 `error` 后该轮消息处理结束（不会有 `message_done`）。

### 连接中断

- 客户端主动断开：若会话中发送过消息，服务端自动保存会话摘要；未发送过消息则跳过摘要保存，仅清理资源
- 网络中断：客户端应实现自动重连，使用相同 `thread_id` 重新建立 SSE 连接
- 新连接踢旧连接：如果同一 `thread_id` 建立新 SSE，旧连接被关闭

## 代码示例

### JavaScript (EventSource)

```javascript
const threadId = crypto.randomUUID();
const baseUrl = 'http://localhost:8000';

// 1. 建立 SSE 连接
const eventSource = new EventSource(
  `${baseUrl}/api/chat/stream?thread_id=${threadId}`
);

let fullText = '';

eventSource.addEventListener('text', (e) => {
  const chunk = JSON.parse(e.data);
  fullText += chunk;
  process.stdout.write(chunk);
});

eventSource.addEventListener('event', (e) => {
  const event = JSON.parse(e.data);

  switch (event.type) {
    case 'session_start':
      console.log('Session started:', event.data.thread_id);
      break;

    case 'message_start':
      fullText = '';
      break;

    case 'tool_call':
      console.log(`Tool call: ${event.data.name}`, event.data.args);
      break;

    case 'tool_result':
      console.log(`Tool result: ${event.data.name}`, event.data.content);
      break;

    case 'approval_request':
      handleApproval(event.data);
      break;

    case 'message_finish':
      console.log('Full message:', event.data.content);
      break;

    case 'message_done':
      console.log('\n--- Message complete ---');
      break;

    case 'error':
      console.error('Error:', event.data.message);
      break;
  }
});

eventSource.onerror = () => {
  console.log('SSE disconnected');
  eventSource.close();
};

// 2. 发送消息
async function sendMessage(text) {
  const res = await fetch(`${baseUrl}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, thread_id: threadId }),
  });
  return res.json();
}

// 3. 提交审批
async function handleApproval(data) {
  console.log('Approval needed:', data.pending_actions);

  // 示例：自动批准所有操作
  const decisions = data.pending_actions.map(() => ({ type: 'approve' }));

  const res = await fetch(
    `${baseUrl}/api/approvals/${data.approval_id}/decide`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decisions }),
    }
  );
  return res.json();
}

// 发送第一条消息
sendMessage('帮我列出当前目录的文件');
```

### Python (httpx + SSE)

```python
import httpx
import json
import threading

BASE_URL = "http://localhost:8000"
THREAD_ID = "my-session-1"

def listen_sse():
    """在后台线程中监听 SSE 事件"""
    with httpx.stream("GET", f"{BASE_URL}/api/chat/stream",
                       params={"thread_id": THREAD_ID},
                       timeout=None) as resp:
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()

                if event_type == "text":
                    print(json.loads(data), end="", flush=True)
                elif event_type == "event":
                    event = json.loads(data)
                    etype = event["type"]
                    edata = event["data"]

                    if etype == "session_start":
                        print(f"\n[Session: {edata['thread_id']}]")
                    elif etype == "message_start":
                        print()  # new line for new message
                    elif etype == "approval_request":
                        handle_approval(edata)
                    elif etype == "message_finish":
                        print(f"\n[Finish] {edata['message_id']}: {edata['content'][:100]}")
                    elif etype == "message_done":
                        print("\n[Done]")
                    elif etype == "error":
                        print(f"\n[Error] {edata['message']}")
                    elif etype == "tool_call":
                        print(f"\n[Tool] {edata['name']}({edata['args']})")
                    elif etype == "tool_result":
                        status = "ERROR" if edata.get("is_error") else "OK"
                        print(f"[Result:{status}] {edata['content'][:100]}")

def handle_approval(data):
    """处理审批请求"""
    print(f"\n[Approval] {data['approval_id']}")
    for action in data["pending_actions"]:
        print(f"  - {action['name']}: {action['args']}")

    decisions = [{"type": "approve"}] * len(data["pending_actions"])
    resp = httpx.post(
        f"{BASE_URL}/api/approvals/{data['approval_id']}/decide",
        json={"decisions": decisions},
    )
    print(f"  Decision: {resp.json()}")

def send_message(text):
    """发送消息"""
    resp = httpx.post(
        f"{BASE_URL}/api/chat",
        json={"message": text, "thread_id": THREAD_ID},
    )
    return resp.json()

# 启动 SSE 监听
sse_thread = threading.Thread(target=listen_sse, daemon=True)
sse_thread.start()

# 发送消息
send_message("帮我列出当前目录的文件")
sse_thread.join()
```

### curl 测试

```bash
# 终端 1：建立 SSE 连接
curl -N "http://localhost:8000/api/chat/stream?thread_id=test1"

# 终端 2：发送消息
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "thread_id": "test1"}'

# 终端 2：提交审批（使用 SSE 中收到的 approval_id）
curl -X POST http://localhost:8000/api/approvals/<approval_id>/decide \
  -H "Content-Type: application/json" \
  -d '{"decisions": [{"type": "approve"}]}'
```

## 客户端实现注意事项

1. **先建 SSE 再发消息**：POST /api/chat 在有 SSE 连接时走异步模式，无 SSE 时走阻塞模式
2. **等待 message_done**：收到 `message_done` 后才能发送下一条消息，避免 agent 状态冲突
3. **处理多操作审批**：`pending_actions` 可能包含多个操作，`decisions` 数组长度必须匹配
4. **心跳忽略**：`keepalive` 事件无需处理，仅用于连接保活
5. **未知事件忽略**：未来可能新增事件类型，客户端应安全忽略未知的 `type`
6. **重连恢复**：网络中断后用相同 `thread_id` 重连，之前未完成的审批仍可通过 GET /api/approvals 查询
