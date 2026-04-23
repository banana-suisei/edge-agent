# Plush-Agent 架构文档

## 系统总览

Plush-Agent 是一个基于 LangChain 的 ReAct Agent，使用 OpenAI 接口标准的基座模型。系统通过 FastAPI 提供 HTTP REST API，同时支持 CLI 交互和 UDS (Unix Domain Socket) 通道。

```
┌───────────────────────────────────────────────────────────────┐
│                        用户交互层                              │
│  ┌──────────┐  ┌──────────────────────────┐  ┌────────────┐ │
│  │   CLI    │  │   FastAPI HTTP Server     │  │  UDS Server│ │
│  │  (click) │  │  /api/chat/stream (SSE)   │  │ JSON-Line  │ │
│  │          │  │  /api/chat (POST)         │  │ 短连接协议  │ │
│  │          │  │  /api/chat/end            │  │ getForms   │ │
│  │          │  │  /api/forms               │  │ submitForm │ │
│  │          │  │  /api/approvals           │  │ getPending │ │
│  └────┬─────┘  └────────────┬─────────────┘  │ Approvals  │ │
│       │                     │                 │ submitDeci-│ │
│       │                     │                 │ sion       │ │
│       │                     │                 └─────┬──────┘ │
└───────┼─────────────────────┼───────────────────────┼────────┘
        │                     │                       │
        v                     v                       v
┌─────────────────────────────────────────────────────────┐
│                   Agent 编排层                            │
│                                                         │
│  ┌──────────────┐  ┌────────────────────┐  ┌────────────────┐  │
│  │ ApprovalHandler│  │StreamingApprovalHandler│  │ InMemorySaver │  │
│  │ (HITL 自动审批)│  │ (SSE 流式审批)     │  │ (Checkpointer)│  │
│  └──────┬───────┘  └──────┬─────────────┘  └────────────────┘  │
│         │                 │                              │
│  ┌──────┴─────────────────┴─────────────────────────┐   │
│  │              Middleware Pipeline                  │   │
│  │  SkillMiddleware -> HumanInTheLoopMiddleware      │   │
│  │  (所有工具默认拦截，interrupt_on={tool:True})      │   │
│  │  SkillMiddleware 仅注入 prompt，不注册 tools       │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  UDS Server (可选，通过 config.uds.enabled 启用)        │
│  UdsServer 持有 approval_handler / streaming_handler    │
│  引用，通过 asyncio.start_unix_server 监听 socket       │
│                                                         │
└──────────────────────────┬──────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        v                  v                      |
┌──────────────┐  ┌────────────────────────┐  ┌──────────────────────┐
│   Tools 层    │  │  记忆层                │  │  Skills 层           │
│              │  │                       │  │                      │
│ ┌──────────┐ │  │ PostgresStore          │  │ .skill/              │
│ │ terminal │ │  │ + pgvector (向量搜索)  │  │ └── <name>/           │
│ │ (Bash)   │ │  │ + OpenAIEmbeddings     │  │     ├── SKILL.md     │
│ ├──────────┤ │  │ namespace/             │  │     ├── scripts/     │
│ │form_     │ │  │ key -> JSON (含向量)    │  │     └── references/  │
│ │generate  │ │  │                       │  │                      │
│ ├──────────┤ │  └────────────────────────┘  │  SkillLoader (扫描)  │
│ │load_skill│ │                               │  SkillMiddleware     │
│ ├──────────┤ │  ┌──────────────┐            │  (渐进式披露)        │
│ │memory_   │ │  │  MCP 层      │            └──────────────────────┘
│ │tools     │ │  │             │
│ ├──────────┤ │  │ MultiServer │
│ │MCP tools │ │  │ MCPClient   │
│ │(动态加载)│ │  │ (from JSON) │
│ └──────────┘ │  └──────────────┘
└──────────────┘

memory_tools 通过 runtime.store 读写 PostgresStore，是长期记忆数据写入的唯一入口。
store.setup() 自动创建 pgvector 向量索引表；需要 PostgreSQL 预装 pgvector 扩展。
search_memory 使用 store.search(ns, query=...) 进行向量语义搜索。
会话摘要使用固定 key "latest" 写入 ("sessions",) 命名空间，下次启动时自动加载。
```

## 目录结构

```
plush-agent/
├── config.yaml                       # 主配置文件 (YAML)
├── mcp_servers.json                  # MCP servers 配置 (JSON)
├── pyproject.toml                    # 项目元数据与依赖
├── .skill/                           # Skills 目录 (agentskills.io 规范)
│   └── <skill-name>/
│       ├── SKILL.md                  # 必需：YAML frontmatter + Markdown 指令
│       ├── scripts/                  # 可选：可执行脚本
│       ├── references/               # 可选：参考文档
│       └── assets/                   # 可选：模板、资源
├── src/plush_agent/
│   ├── config.py                     # 配置加载 (YAML -> dataclass)
│   ├── agent.py                      # Agent 创建入口 (build_agent)
│   ├── cli.py                        # CLI 入口 (click: chat, serve)
│   ├── skills/
│   │   ├── loader.py                 # SKILL.md 扫描与解析
│   │   └── middleware.py             # SkillMiddleware (prompt injection) + load_skill tool
│   ├── tools/
│   │   ├── bash.py                   # ShellTool 封装
│   │   ├── form.py                   # 表单生成 tool + HTTP 接口
│   │   └── memory.py                 # 长期记忆工具 + 会话摘要
│   ├── mcp/
│   │   └── loader.py                 # MCP 从 JSON 配置加载
│   ├── memory/
│   │   └── store.py                  # Store 工厂 (PostgresStore / InMemoryStore)
│   ├── hitl/
│   │   ├── approval_handler.py       # HITL 自动审批 + HTTP 桥接
│   │   └── streaming_handler.py      # SSE 流式审批处理
│   └── server/
│       ├── app.py                    # FastAPI 应用工厂（含 lifespan 管理 UDS 启停）
│       ├── main.py                   # uvicorn 启动入口
│       ├── session.py                # SessionManager（长连接 SSE 会话管理）
│       ├── routes_chat.py            # /api/chat, /api/chat/stream, /api/chat/end
│       ├── routes_form.py            # /api/forms
│       ├── routes_approval.py        # /api/approvals
│       └── uds_server.py             # UDS 审批/表单通道（JSON-Line 协议）
└── tests/
```

## 核心数据流

### 1a. 聊天请求流（阻塞模式）

```
用户消息 -> FastAPI /api/chat
         -> ApprovalHandler.run_with_hitl()
           -> agent.ainvoke(messages, config, version="v2")
             -> SkillMiddleware 注入 skill 描述到 system prompt
             -> LLM 生成回复 (可能包含 tool_calls)
             -> HumanInTheLoopMiddleware 拦截所有 tool_calls
               (interrupt_on={tool_name: True}，所有工具默认拦截)
           ← GraphOutput (含 .interrupts)
         -> _process_interrupt() 解析 action_requests + review_configs
         -> should_auto_approve() 正则匹配 tool name + args
         -> 全部自动通过 -> resume -> _extract_done() 返回结果
         -> 需要人工 -> 返回 pending_approval 等待 HTTP 决策
```

### 1b. 聊天请求流（SSE 长连接模式）

SSE 按 thread_id 建立长连接，通过 SessionManager + asyncio.Queue 桥接 HTTP 输入和 SSE 输出。
详见 [sse-protocol.md](features/sse-protocol.md)。

```
1. 客户端建立 SSE 连接:
   GET /api/chat/stream?thread_id=t1
   -> SessionManager.get_or_create("t1") -> asyncio.Queue
   -> SSE 返回 session_start 事件
   -> 长连接，从 queue 读取事件并推送

2. 客户端发送消息:
   POST /api/chat {message, thread_id}
   -> 检测到 SSE 连接 -> 返回 {"status": "accepted"}
   -> 后台任务: streaming_handler.run_streaming(message, thread_id)
     -> agent.astream(messages, stream_mode=["messages","updates"], version="v2")
       -> messages chunk -> token.text -> yield {"kind": "text", "payload": "..."}
       -> messages chunk -> token.tool_call_chunks -> 累积工具调用参数
       -> updates chunk (tools) -> yield {"kind": "event", "type": "tool_result", ...}
       -> updates chunk (__interrupt__) -> _handle_interrupt():
         -> 全部自动通过 -> Command(resume={...}) 内部 resume
         -> 需要人工 -> yield {"kind": "event", "type": "approval_request", ...}
     -> handler yield 事件 -> _consume_and_push() -> queue.put()
       -> _consume_and_push 累积所有 text payload，流结束后发送 message_finish 事件
     -> SSE 从 queue 读取并推送 (event: text 或 event: event)

3. 客户端提交审批:
   POST /api/approvals/{id}/decide {decisions}
   -> 检测到 SSE 连接 -> 返回 {"status": "accepted"}
   -> 后台任务: streaming_handler.submit_decision_streaming(id, decisions)
   -> LangGraph resume -> agent 继续输出 -> 通过原 SSE 推送

4. 客户端断开 SSE:
   -> 取消后台 agent 任务
   -> 若本会话发送过消息 -> save_session_summary() 保存摘要
   -> 若本会话未发送过消息 -> 跳过摘要保存
   -> SessionManager.close() 清理资源
```

### 1c. UDS 审批/表单通道

通过 Unix Domain Socket 暴露审批和表单接口，供边缘客户端（如 Rust edge）使用。协议为 JSON-Line 短连接（一次请求/响应）。需在 `config.yaml` 中启用 `uds.enabled: true`。

```
1. 服务启动:
   config.uds.enabled == true
   -> UdsServer 持有 approval_handler / streaming_handler / session_manager 引用
   -> lifespan 启动 asyncio.start_unix_server(socket_path)
   -> 清理旧 socket 文件

2. 客户端查询审批:
   连接 socket -> 发送 {"action": "getPendingApprovals", "interruptId": ""}
   -> UdsServer._dispatch() -> _handle_get_pending_approvals()
   -> 汇总 approval_handler.pending + streaming_handler.streaming_pending
   -> 映射为 UDS 格式 [{interruptId, actionName, argsJson, allowedDecisions, ...}]
   -> 返回 JSON-Line 响应 -> 关闭连接

3. 客户端提交审批决策:
   连接 socket -> 发送 {"action": "submitApprovalDecision", "interruptId": "xxx", "decision": "approve"}
   -> UdsServer._dispatch() -> _handle_submit_approval_decision()
   -> 查找 pending（streaming_handler 优先，再查 approval_handler）
   -> 统一 decision 映射为内部 decisions 数组（每个 needs_human index 应用相同决策）
   -> streaming 模式: submit_decision_streaming() -> 事件经 _sse_serialize() 推送到 SSE queue
   -> blocking 模式: submit_decision() -> 直接返回
   -> 返回 JSON-Line 响应 -> 关闭连接

4. 表单查询/提交（robotId 校验）:
   getForms -> get_pending_forms() -> 映射为 UDS forms 数组
   submitForm -> submit_form(form_id, responses) -> 触发 asyncio.Event

5. 服务关闭:
   -> lifespan 关闭 UDS server -> 清理 socket 文件
```

### 2. 长期记忆流

```
Agent 对话中
  -> LLM 判断需要记忆信息
  -> 调用 save_memory(key, value)
    key 为自然语言标识（如 "user preference for python data analysis"）
    namespace 固定为 ("users", "default")
    -> runtime.store.put(("users","default"), key, {"content": value})
    -> PostgresStore 自动对 content 字段生成向量嵌入 (OpenAIEmbeddings)
    -> 写入 PostgreSQL (pgvector)

Agent 对话中
  -> LLM 判断需要回忆信息
  -> 调用 search_memory(query)
    query 为自然语言查询（如 "user likes programming"）
    namespace 固定为 ("users", "default")
    -> runtime.store.search(("users","default"), query=query)
    -> PostgresStore 使用 pgvector 进行余弦相似度向量搜索
    -> 返回语义最相关的记忆 -> 返回给 Agent

会话结束时（CLI /quit 或 POST /api/chat/end）
  -> save_session_summary(store, messages, config)
    -> LLM 生成对话摘要（含重试逻辑，最多 3 次尝试避免空响应）
    -> store.put(("sessions",), "latest", {"content": summary, ...})
    -> 写入 PostgresStore

下次启动（新 thread 首条消息）
  -> load_session_summary(store)
    -> store.get(("sessions",), "latest")
    -> 注入到首条用户消息: "[上一次对话摘要]\n...\n[当前消息]\n..."
```

### 3. 表单工具流

```
Agent 调用 form_generate(purpose, requirements, timeout)
  -> LLM 生成表单 JSON schema
  -> 存入 _pending_forms[form_id]
  -> asyncio.Event 等待
用户 GET /api/forms/{form_id} -> 获取表单
用户 POST /api/forms/{form_id}/submit -> submit_form() 触发 event.set()
  -> tool 返回用户填写数据
超时 -> tool 返回 timeout 状态
```

### 4. HITL 审批流

**阻塞模式**（无 SSE 连接时）：

```
ApprovalHandler.run_with_hitl(message, thread_id)
  1. agent.ainvoke(version="v2") -> GraphOutput
  2. result.interrupts 为空 -> _extract_done() 返回 {"status": "done", ...}
  3. _process_interrupt() 解析 interrupt.value:
     - action_requests: [{name, args, description}, ...]
     - review_configs: [{action_name, allowed_decisions}, ...]
  4. 遍历 action_requests:
     - should_auto_approve(name, args) -> decisions[i] = {"type": "approve"}
     - 不匹配 -> decisions[i] = None, 加入 needs_human
  5. needs_human 为空 -> Command(resume={interrupt.id: {"decisions": ...}}) -> _extract_done()
  6. needs_human 非空 -> 存入 self.pending(interrupt_id=interrupt.id), 返回 {"status": "pending_approval", ...}

用户 POST /api/approvals/{id}/decide
  -> ApprovalHandler.submit_decision()
    -> 填充 human_decisions 到 decisions 数组
    -> agent.ainvoke(Command(resume={interrupt_id: {"decisions": all_decisions}}))
    -> 可能产生新的 interrupt -> 再次进入 _process_interrupt() 循环
    -> 无新 interrupt -> _extract_done() 返回结果
```

**SSE 长连接模式**：

```
StreamingApprovalHandler._handle_interrupt(interrupts, config, tool_call_acc)
  -> should_auto_approve() 过滤
  -> 全部自动通过 -> 内部递归 _stream_agent(Command(resume=...))
  -> 需要人工 -> 存入 self.streaming_pending[thread_id]
  -> yield {"kind": "event", "type": "approval_request", ...}

用户 POST /api/approvals/{id}/decide
  -> 检测 thread_id 有 SSE 连接 -> 后台 submit_decision_streaming()
  -> LangGraph resume -> 通过原 SSE 推送后续输出
```

**双 handler 待审批查询**：

`main.py` 中 `ApprovalHandler` 和 `StreamingApprovalHandler` 是两个独立实例，分别维护各自的 pending dict。
`GET /api/approvals` 和 `GET /api/approvals/{id}` 同时查询两个 handler：
- `approval_handler.pending`（阻塞模式）
- `streaming_approval_handler.streaming_pending`（SSE 模式）
```

## 模块依赖关系

```
config.py ← (被所有模块引用)

cli.py -> config.py
       -> agent.py (chat 模式)
       -> tools/memory.py (save_session_summary, load_session_summary)
       -> server/main.py (serve 模式)

server/main.py -> agent.py
               -> hitl/approval_handler.py
               -> hitl/streaming_handler.py
               -> server/app.py
               -> server/uds_server.py (当 config.uds.enabled 时)

server/app.py -> server/session.py (SessionManager)
              -> server/routes_*.py
    lifespan 管理 UdsServer 启停（通过 app.state.uds_server）

server/uds_server.py -> config.py
                      -> hitl/approval_handler.py (ApprovalHandler, PendingApproval)
                      -> hitl/streaming_handler.py (StreamingApprovalHandler)
                      -> server/routes_chat.py (_sse_serialize)
                      -> server/session.py (SessionManager)
                      -> tools/form.py (get_pending_forms, get_form, submit_form)

agent.py -> config.py
         -> skills/loader.py + skills/middleware.py
         -> tools/bash.py + tools/form.py + tools/memory.py
         -> mcp/loader.py
         -> memory/store.py

hitl/approval_handler.py -> config.py
                          (依赖 agent 实例，不依赖具体 agent 构建逻辑)

server/routes_chat.py -> server/session.py (SessionManager)
                      -> hitl/streaming_handler.py
                      -> tools/memory.py (save_session_summary, load_session_summary)
server/routes_approval.py -> server/routes_chat.py (_sse_serialize, _consume_and_push, _get_session_manager)
                          -> hitl/approval_handler.py (阻塞模式 pending)
                          -> hitl/streaming_handler.py (SSE 模式 streaming_pending)
    GET /api/approvals, GET /api/approvals/{id} 同时查两个 handler 的 pending dict
    POST /api/approvals/{id}/decide 按是否有 SSE 连接分流
server/routes_*.py -> tools/form.py
```

## 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 框架 | LangChain `create_agent` | 官方 ReAct 实现，内置 middleware/tool 支持 |
| HITL 实现 | 官方 `HumanInTheLoopMiddleware` | 不手动实现 interrupt，交给 SDK 管理状态 |
| 自动审批 | CLI 和 HTTP 双路径实现，共享正则匹配逻辑 | `_should_auto_approve()` 在 CLI 层，`ApprovalHandler` 在 HTTP 层；正则使用词边界 `\b` 避免子串误匹配 |
| 流式响应 | 长连接 SSE + `SessionManager` + `asyncio.Queue` | SSE 按 thread_id 绑定会话，通过 Queue 桥接 POST 输入和 SSE 输出；审批期间连接保持，利用 LangChain interrupt/resume 管理状态 |
| SSE 事件格式 | 两种 SSE event type：`text` + `event`（统一信封） | 文本和事件分离，信封内 `type` 字段分发，新增事件类型无需改协议层 |
| Skills 格式 | agentskills.io 规范 | 开放标准，SKILL.md 可读、可审计、易分享 |
| 表单等待 | `asyncio.Event` + 内存 store | 简单可靠，单进程部署足够 |
| 长期记忆存储 | PostgreSQL + pgvector (PostgresStore + OpenAIEmbeddings) | 生产级持久化，跨进程共享，原生向量语义搜索支持 |
| 记忆访问方式 | 通过 `runtime.store` 在 tool 中读写 | LangChain 官方模式：store 传入 create_agent 后，tool 通过 ToolRuntime.store 访问 |
| 记忆搜索方式 | 向量语义搜索 (pgvector + Embeddings, cosine similarity) | 自然语言查询，语义匹配比关键词匹配更准确，跨语言无障碍 |
| 记忆命名空间 | 固定 `("users", "default")` | 简化接口，所有记忆工具无需 namespace 参数 |
| 会话摘要 | 固定 key `"latest"` 写入 `("sessions",)` 命名空间 | CLI 和 HTTP 均通过 checkpointer 获取消息，不依赖运行时变量追踪 |
| 记忆工具审批 | 自动通过（`auto_approve: .*`） | 记忆操作无安全风险，无需人工确认 |
| MCP 加载 | JSON 文件 | MCP 的 MultiServerMCPClient 接受 dict，JSON 映射最直接 |
| UDS 通道 | `asyncio.start_unix_server` + JSON-Line 短连接 | 无外部依赖，复用同一事件循环；审批/表单数据直接从 handler 和 form 模块读取 |
| UDS 与 SSE 共享队列 | UDS `_consume_and_push` 使用 `_sse_serialize` 序列化后推入 SSE queue | UDS 提交 streaming 审批决策时，事件经 `_sse_serialize` 转为 `{event, data}` 格式，与 SSE 路由层一致，避免 `ServerSentEvent` 崩溃 |
| UDS 审批语义 | 节点级（不过滤 robotId），表单语义为机器人级（robotId 校验） | 审批面向整个 agent 实例，表单面向特定机器人，与 mock 协议规范一致 |
| UDS 决策映射 | 统一决策（单个 decision 应用到所有 needs_human action） | 简化边缘客户端逻辑，一个 UDS 决策覆盖整个 interrupt 的所有待审批 action |
