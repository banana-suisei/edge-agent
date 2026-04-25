# Hardware Client Integration Guide

本文档面向硬件客户端开发者，仅涵盖 SSE 流式接收与消息发送两个核心接口。

---

## 1. 接口概览

硬件客户端需要对接的接口共两个：

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/chat/stream` | 建立 SSE 长连接，接收 Agent 的所有输出 |
| POST | `/api/chat` | 向 Agent 发送用户消息 |

**交互模式**：先建立 SSE 长连接，再通过 POST 发送消息。Agent 的回复全部通过 SSE 推送，POST 接口本身不返回回复内容。

---

## 2. SSE 连接

### 2.1 建立连接

```
GET /api/chat/stream?thread_id=<thread_id>
Accept: text/event-stream
```

**参数**：

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `thread_id` | query | string | 是 | 会话标识符，客户端自行生成，建议使用 UUID |

**连接建立后**，服务端立即推送一个 `session_start` 事件（见第 4.1 节），之后保持长连接等待后续事件。

### 2.2 连接规则

- **一对一**：同一 `thread_id` 同时只允许一条 SSE 连接。如果用相同 `thread_id` 再建一条连接，旧连接会被服务端自动关闭。
- **心跳保活**：服务端每 30 秒发送一次 `keepalive` 事件，客户端应忽略即可，但不要因此判定连接断开。
- **断开行为**：
  - 发送过消息的会话断开后，服务端自动保存对话摘要到长期记忆。
  - 未发送过消息的空会话断开后，仅清理资源，不触发摘要保存。

### 2.3 原始数据传输格式

SSE 基于 HTTP 长连接，使用 `text/event-stream` Content-Type。每条消息由 `event:` 和 `data:` 两行组成：

```
event: <事件名>
data: <JSON 字符串>

```

（每条消息后有一个空行分隔）

---

## 3. SSE 事件总览

SSE 传输层有 **三种** `event` 名称：

| SSE event 名 | 说明 | 出现时机 |
|--------------|------|----------|
| `text` | LLM 生成的文本片段 | 每生成一个 token 推送一次 |
| `event` | 结构化事件（信封格式） | 工具调用、审批、状态变更等 |
| `keepalive` | 心跳 | 每 30 秒一次 |

**客户端解析逻辑**：

```
收到 SSE 消息:
  if event == "text":
      文本片段 = JSON.parse(data)    // data 是 JSON 编码的字符串
      拼接到当前回复缓冲区

  elif event == "event":
      信封 = JSON.parse(data)        // data 是 JSON 对象
      switch (信封.type):
          "session_start"    -> 处理连接确认
          "message_start"    -> 标记新消息开始
          "tool_call"        -> 展示工具调用
          "tool_result"      -> 展示工具结果
          "approval_request" -> 进入审批流程
          "message_finish"   -> 获取完整消息内容
          "message_done"     -> 标记消息结束，可发送下一条
          "error"            -> 处理错误
          其他               -> 忽略（兼容未来新增事件）

  elif event == "keepalive":
      忽略
```

---

## 4. 结构化事件详细说明

所有结构化事件共享统一信封格式：

```json
{
  "id": "evt_a1b2c3d4e5f6",
  "type": "事件类型名",
  "timestamp": 1713686400.0,
  "data": { ... }
}
```

**信封字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一事件 ID。格式：`evt_` + 12 位十六进制字符。用于日志追踪和去重。 |
| `type` | string | 事件类型名，客户端据此分发处理。 |
| `timestamp` | float | Unix 时间戳（秒，含小数），事件产生时刻。 |
| `data` | object | 事件载荷，具体结构随 `type` 不同而不同。 |

---

### 4.1 session_start

**含义**：SSE 连接建立成功。

**时序**：连接建立后立即发送，是客户端收到的第一个事件。

**data 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | string | 当前会话标识符，与请求参数一致 |

**示例**：

```
event: event
data: {"id":"evt_a1b2c3d4e5f6","type":"session_start","timestamp":1713686399.0,"data":{"thread_id":"my-session-1"}}
```

**客户端处理建议**：记录 `thread_id`，标记连接就绪状态，此后可以发送消息。

---

### 4.2 message_start

**含义**：Agent 开始处理一条新消息。

**时序**：客户端 POST `/api/chat` 后，SSE 推送的第一个事件。

**data 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | string | 消息唯一 ID。格式：`msg_` + 12 位十六进制字符。用于关联后续的 `message_finish` 事件 |

**示例**：

```
event: event
data: {"id":"evt_b2c3d4e5f6a1","type":"message_start","timestamp":1713686399.5,"data":{"message_id":"msg_abc123def456"}}
```

**客户端处理建议**：清空文本缓冲区，记录 `message_id`，准备接收后续 `text` 事件。

---

### 4.3 text（文本片段）

**含义**：LLM 生成的一个文本 token 片段。

**时序**：在 `message_start` 之后、`message_finish` 之前，持续推送。

**格式**：

```
event: text
data: "文本片段"
```

**注意**：`data` 字段是一个 **JSON 编码的字符串**（包含引号和转义字符），不是裸文本。客户端需要先 `JSON.parse(data)` 才能获得实际文本。

**示例**：

```
event: text
data: "Hello"
```

```
event: text
data: " world"
```

```
event: text
data: "!\nLet me"
```

经过三次 `JSON.parse()` 后分别得到：`Hello`、` world`、`!\nLet me`，拼接结果为 `Hello world!\nLet me`。

**客户端处理建议**：逐片段追加到文本缓冲区并实时渲染，实现打字机效果。

---

### 4.4 tool_call

**含义**：Agent 决定调用一个工具。

**时序**：在文本输出过程中穿插出现。Agent 可能先输出部分文本，再调用工具，工具执行完后继续输出文本。

**data 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `call_id` | string | 工具调用唯一标识，用于关联后续的 `tool_result` |
| `name` | string | 工具名称，如 `terminal`、`save_memory` 等 |
| `args` | object | 传给工具的参数，结构因工具而异 |

**示例**：

```
event: event
data: {"id":"evt_c3d4e5f6a1b2","type":"tool_call","timestamp":1713686400.5,"data":{"call_id":"c1","name":"terminal","args":{"commands":"ls -la"}}}
```

**客户端处理建议**：可选展示。可使用 `call_id` 等待对应的 `tool_result`。

---

### 4.5 tool_result

**含义**：工具执行完成，返回结果。

**时序**：在对应的 `tool_call` 之后推送，通过 `call_id` 关联。

**data 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `call_id` | string | 对应的 `tool_call` 的 `call_id` |
| `name` | string | 工具名称，与 `tool_call` 一致 |
| `content` | string | 工具执行结果文本 |
| `is_error` | bool | 工具执行是否出错 |

**示例**：

```
event: event
data: {"id":"evt_d4e5f6a1b2c3","type":"tool_result","timestamp":1713686401.2,"data":{"call_id":"c1","name":"terminal","content":"file1\nfile2\nfile3","is_error":false}}
```

**客户端处理建议**：可选展示。通过 `call_id` 关联到之前的 `tool_call`，`is_error` 为 `true` 时可高亮显示。

---

### 4.6 approval_request

**含义**：Agent 执行的工具需要人工审批，暂停等待决策。

**时序**：在 `tool_call` 之后推送。**收到此事件后 SSE 连接保持不断开**，Agent 处于暂停状态，等待客户端通过 HTTP POST 提交审批决策。

**data 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `approval_id` | string (UUID) | 审批请求唯一标识，提交决策时需要此 ID |
| `thread_id` | string | 当前会话 ID |
| `pending_actions` | array | 待审批的操作列表（详见下表） |
| `auto_approved_count` | int | 已自动通过的操作数量（供展示用） |

**pending_actions 中每个操作的字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `index` | int | 操作在所有操作中的位置索引（0 开始） |
| `name` | string | 工具名称 |
| `args` | object | 工具参数 |
| `description` | string | 操作的自然语言描述 |
| `allowed_decisions` | string[] | 该操作允许的决策类型，取值为 `approve`、`edit`、`reject` 的子集 |

**示例**：

```
event: event
data: {"id":"evt_e5f6a1b2c3d4","type":"approval_request","timestamp":1713686402.0,"data":{"approval_id":"550e8400-e29b-41d4-a716-446655440000","thread_id":"my-session-1","pending_actions":[{"index":0,"name":"terminal","args":{"commands":"rm -rf /tmp/old"},"description":"Tool execution pending approval\n\nTool: terminal\nArgs: ...","allowed_decisions":["approve","edit","reject"]}],"auto_approved_count":1}}
```

**客户端处理建议**：

1. 展示审批 UI，列出所有 `pending_actions` 及其描述。
2. 用户做出决策后，调用 `POST /api/approvals/{approval_id}/decide`（见第 5 节）。
3. 提交后 Agent 自动恢复，后续输出通过同一 SSE 连接继续推送。
4. **在收到审批决策结果前，不要发送新消息。**

---

### 4.7 message_finish

**含义**：流式文本传输结束，包含该轮消息的完整内容。

**时序**：所有 `text` 事件发送完毕后推送。仅在初始消息发送时产生（`POST /api/chat` 触发），审批恢复后的流式输出 **不会** 发送此事件。

**data 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | string | 与 `message_start` 中的 `message_id` 一致 |
| `content` | string | 该轮消息的完整文本内容（所有 `text` 事件拼接的结果） |

**示例**：

```
event: event
data: {"id":"evt_f6a1b2c3d4e5","type":"message_finish","timestamp":1713686403.0,"data":{"message_id":"msg_abc123def456","content":"Let me check that for you.\n\nHere are the files:\n- file1\n- file2\n- file3"}}
```

**客户端处理建议**：

- 如果客户端已经自行拼接了所有 `text` 事件，可忽略此事件。
- 如果客户端需要一次性获取完整回复（而非实时拼接），可直接使用 `content` 字段。
- 可通过 `message_id` 校验与 `message_start` 的对应关系。

---

### 4.8 message_done

**含义**：Agent 完成一轮消息的完整处理，客户端可以发送下一条消息。

**时序**：一轮消息的最后一个事件。如果中途出现了 `approval_request`，则 `message_done` 会在审批通过、后续处理全部完成后才发送。

**data 结构**：空对象 `{}`，无额外数据。

**示例**：

```
event: event
data: {"id":"evt_a1f6b2c3d4e5","type":"message_done","timestamp":1713686403.5,"data":{}}
```

**客户端处理建议**：收到此事件后，标记当前消息处理完毕，允许用户发送下一条消息。**在收到 `message_done` 之前发送新消息会导致 Agent 状态冲突。**

---

### 4.9 error

**含义**：处理过程中发生异常。

**时序**：可能在任何时刻出现。收到 `error` 后该轮消息处理结束，**不会有 `message_finish` 和 `message_done`**。

**data 结构**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 错误类型，通常是异常类名的小写形式，如 `runtimeerror`、`timeouterror` |
| `message` | string | 错误的详细信息 |

**示例**：

```
event: event
data: {"id":"evt_b2a1c3d4e5f6","type":"error","timestamp":1713686405.0,"data":{"code":"runtimeerror","message":"RuntimeError: boom"}}
```

**客户端处理建议**：展示错误信息给用户，标记当前消息处理结束（等同 `message_done` 的效果），允许用户重新发送消息。

---

## 5. 发送消息接口

### 5.1 POST /api/chat

发送用户消息给 Agent。Agent 的回复通过 SSE 推送，HTTP 响应仅返回接收确认。

**请求**：

```
POST /api/chat
Content-Type: application/json
```

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户消息文本 |
| `thread_id` | string | 是 | 会话标识符，必须与 SSE 连接使用的 `thread_id` 一致 |

**请求示例**：

```json
{
  "message": "帮我列出当前目录的文件",
  "thread_id": "my-session-1"
}
```

**响应（SSE 模式）**：

当对应 `thread_id` 存在活跃 SSE 连接时，返回：

```json
{
  "status": "accepted",
  "thread_id": "my-session-1"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 固定为 `"accepted"`，表示消息已被接收，回复将通过 SSE 推送 |
| `thread_id` | string | 确认的会话 ID |

**响应（阻塞模式）**：

如果对应 `thread_id` 没有 SSE 连接，走阻塞模式，Agent 直接在 HTTP 响应中返回结果：

```json
{
  "status": "done",
  "content": "好的，以下是文件列表..."
}
```

**硬件客户端应始终先建立 SSE 连接再发送消息**，确保走 SSE 模式。

### 5.2 POST /api/approvals/{approval_id}/decide

提交审批决策。仅当收到 `approval_request` 事件后需要调用。

**请求**：

```
POST /api/approvals/<approval_id>/decide
Content-Type: application/json
```

URL 中的 `<approval_id>` 替换为 `approval_request` 事件中的 `approval_id` 字段值。

**请求体**：

```json
{
  "decisions": [
    {"type": "approve"}
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `decisions` | array | 是 | 决策数组，长度必须等于 `pending_actions` 的数量，顺序一一对应 |

**每个决策对象**：

| type 值 | 含义 | 额外字段 |
|---------|------|----------|
| `approve` | 批准执行 | 无 |
| `reject` | 拒绝执行 | `message`（string，可选）：拒绝原因 |
| `edit` | 修改参数后批准 | `edited_action`（object，必填）：`{"name": "工具名", "args": {修改后的参数}}` |

**响应**：

```json
{"status": "accepted"}
```

提交后 Agent 自动恢复执行，后续输出通过 SSE 连接继续推送。

---

## 6. 完整交互流程

### 6.1 简单对话（无审批）

```
┌────────┐                                    ┌────────┐
│ Client │                                    │ Server │
└───┬────┘                                    └───┬────┘
    │                                             │
    │  ① GET /api/chat/stream?thread_id=t1        │
    │────────────────────────────────────────────>│
    │  SSE: session_start                         │
    │<────────────────────────────────────────────│
    │                                             │
    │  ② POST /api/chat {"message":"...",         │
    │       "thread_id":"t1"}                     │
    │────────────────────────────────────────────>│
    │  HTTP 200: {"status":"accepted"}            │
    │<────────────────────────────────────────────│
    │                                             │
    │  SSE: message_start                         │
    │<────────────────────────────────────────────│
    │  SSE: text "Hello"                          │
    │<────────────────────────────────────────────│
    │  SSE: text " world"                         │
    │<────────────────────────────────────────────│
    │  SSE: text "!"                              │
    │<────────────────────────────────────────────│
    │  SSE: message_finish                        │
    │<────────────────────────────────────────────│
    │  SSE: message_done                          │
    │<────────────────────────────────────────────│
    │                                             │
    │  ③ (可发送下一条消息)                         │
    │  ...                                        │
```

### 6.2 带审批的对话

```
┌────────┐                                    ┌────────┐
│ Client │                                    │ Server │
└───┬────┘                                    └───┬────┘
    │  ...已建立 SSE 连接...                      │
    │                                             │
    │  POST /api/chat {"message":"...", ...}      │
    │────────────────────────────────────────────>│
    │  HTTP 200: {"status":"accepted"}            │
    │<────────────────────────────────────────────│
    │                                             │
    │  SSE: message_start                         │
    │<────────────────────────────────────────────│
    │  SSE: text "Let me"                         │
    │<────────────────────────────────────────────│
    │  SSE: text " check that."                   │
    │<────────────────────────────────────────────│
    │  SSE: tool_call                             │
    │<────────────────────────────────────────────│
    │  SSE: approval_request    ← Agent 暂停      │
    │<────────────────────────────────────────────│
    │                                             │
    │  (展示审批 UI，等待用户决策)                   │
    │                                             │
    │  POST /api/approvals/{id}/decide            │
    │  {"decisions":[{"type":"approve"}]}         │
    │────────────────────────────────────────────>│
    │  HTTP 200: {"status":"accepted"}            │
    │<────────────────────────────────────────────│
    │                                             │
    │  SSE: tool_result        ← Agent 恢复       │
    │<────────────────────────────────────────────│
    │  SSE: text "Done!"                          │
    │<────────────────────────────────────────────│
    │  SSE: message_finish                        │
    │<────────────────────────────────────────────│
    │  SSE: message_done                          │
    │<────────────────────────────────────────────│
    │                                             │
    │  (可发送下一条消息)                           │
```

### 6.3 发生错误

```
┌────────┐                                    ┌────────┐
│ Client │                                    │ Server │
└───┬────┘                                    └───┬────┘
    │  ...已建立 SSE 连接并发送消息...             │
    │                                             │
    │  SSE: message_start                         │
    │<────────────────────────────────────────────│
    │  SSE: text "Let me"                         │
    │<────────────────────────────────────────────│
    │  SSE: error                                 │
    │<────────────────────────────────────────────│
    │                                             │
    │  (注意：不会有 message_finish 或 message_done)│
    │  (可重新发送消息)                             │
```

---

## 7. 事件时序状态机

客户端可按以下状态机管理消息处理流程：

```
                    SSE 连接建立
                        │
                        v
                 ┌─────────────┐
                 │   CONNECTED │  收到 session_start
                 └──────┬──────┘
                        │
              POST /api/chat
                        │
                        v
                 ┌─────────────┐
                 │   WAITING    │  收到 message_start
                 └──────┬──────┘
                        │
                        v
                 ┌─────────────┐
              ┌─>│  STREAMING  │  收到 text / tool_call / tool_result
              │  └──────┬──────┘
              │         │
              │         │ 收到 approval_request
              │         v
              │  ┌─────────────┐
              │  │  APPROVAL   │  等待用户决策
              │  └──────┬──────┘
              │         │ 提交决策后回到 STREAMING
              │         │
              └─────────┘
                        │
                        │ 收到 message_finish
                        v
                 ┌─────────────┐
                 │   FINISHED   │  收到 message_done
                 └──────┬──────┘
                        │
                        v
                 可发送下一条消息

    任意时刻收到 error → 回到 CONNECTED（可重新发消息）
```

---

## 8. 一轮消息的完整事件序列

以一次包含工具调用和审批的完整交互为例，客户端收到的 SSE 事件序列如下：

```
1.  event: event    type: session_start       ← 连接确认
2.                  (等待客户端发送消息)
3.  event: event    type: message_start       ← 新消息开始
4.  event: text     data: "Let me"            ← 文本片段 1
5.  event: text     data: " check that."      ← 文本片段 2
6.  event: event    type: tool_call           ← Agent 调用工具
7.  event: event    type: approval_request    ← 需要审批，Agent 暂停
    (客户端提交审批决策)
8.  event: event    type: tool_result         ← 工具执行完成
9.  event: text     data: "Here are"          ← 文本片段 3
10. event: text     data: " the files."       ← 文本片段 4
11. event: event    type: message_finish      ← 完整消息内容
12. event: event    type: message_done        ← 处理完成
    (客户端可发送下一条消息)
```

**说明**：
- 步骤 4-5 和 9-10 的 `text` 事件数量不固定，取决于 LLM 生成的 token 数。
- 步骤 6-7 和步骤 8 是工具调用/审批/结果的三段式，可能多次出现。
- 步骤 11 `message_finish` 的 `data.content` 等于所有 `text` 事件的拼接结果：`"Let me check that.Here are the files."`。
- 步骤 12 `message_done` 之后才能发下一条消息。

---

## 9. 错误处理

### 9.1 HTTP 错误

| HTTP 状态码 | 含义 | 处理建议 |
|------------|------|----------|
| 400 | 请求参数错误 | 检查请求体格式和字段 |
| 404 | 审批请求不存在或已过期 | 不要重试，审批可能已超时被清理 |
| 500 | 服务端内部错误 | 可重试，建议指数退避 |

### 9.2 SSE 错误

通过 `error` 事件（`type: "error"`）推送。收到后该轮消息处理结束，**不会有** `message_finish` 或 `message_done`。

常见 `code` 值：

| code | 含义 |
|------|------|
| `runtimeerror` | Agent 运行时异常 |
| `timeouterror` | 处理超时 |
| `not_found` | 审批请求不存在（仅审批提交时） |

### 9.3 连接中断

| 场景 | 处理 |
|------|------|
| 客户端主动断开 | 服务端自动清理，发送过消息的会话自动保存摘要 |
| 网络中断 | 使用相同 `thread_id` 重新建立 SSE 连接 |
| 旧连接被踢 | 同一 `thread_id` 建新连接时，旧连接自动关闭 |

---

## 10. 实现注意事项

1. **必须先建 SSE 再发消息**。否则 POST `/api/chat` 会走阻塞模式，响应格式不同。
2. **严格遵守 message_done 门控**。收到 `message_done` 或 `error` 后才能发送下一条消息，否则会导致 Agent 状态冲突。
3. **text 的 data 需要二次解析**。`data` 字段是 JSON 编码的字符串，不是裸文本，必须先 `JSON.parse()` 再拼接。
4. **忽略未知事件类型**。未来可能新增事件类型，客户端遇到未知的 `type` 应安全跳过。
5. **approval_request 期间保持连接**。收到审批请求时 SSE 不会断开，不要关闭连接，提交决策后事件会继续推送。
6. **message_finish 可选使用**。如果客户端已自行拼接 `text` 事件，可忽略 `message_finish`；如果需要一次性获取完整回复，直接用其 `content` 字段。
7. **message_id 的关联**。`message_start` 中的 `message_id` 与 `message_finish` 中的一致，可用于校验。
8. **工具事件可选展示**。`tool_call` 和 `tool_result` 对硬件客户端可能是内部细节，可视需求选择性展示或忽略。

---

## 11. 快速验证（curl）

在两个终端中分别执行：

**终端 1** — 建立 SSE 连接并观察输出：

```bash
curl -N "http://localhost:8000/api/chat/stream?thread_id=test1"
```

**终端 2** — 发送消息：

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "thread_id": "test1"}'
```

终端 1 中将依次看到 `session_start`、`message_start`、若干 `text`、`message_finish`、`message_done` 事件。
