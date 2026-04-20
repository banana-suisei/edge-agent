# Plush-Agent 架构文档

## 系统总览

Plush-Agent 是一个基于 LangChain 的 ReAct Agent，使用 OpenAI 接口标准的基座模型。系统通过 FastAPI 提供 HTTP REST API，同时支持 CLI 交互。

```
┌─────────────────────────────────────────────────────────┐
│                     用户交互层                           │
│  ┌──────────┐              ┌──────────────────────────┐ │
│  │   CLI    │              │   FastAPI HTTP Server     │ │
│  │  (click) │              │  /api/chat (SSE)          │ │
│  │          │              │  /api/chat/end            │ │
│  │          │              │  /api/forms               │ │
│  │          │              │  /api/approvals           │ │
│  └────┬─────┘              └────────────┬─────────────┘ │
└───────┼─────────────────────────────────┼───────────────┘
        │                                 │
        ▼                                 ▼
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
│  │  SkillMiddleware → HumanInTheLoopMiddleware      │   │
│  │  (所有工具默认拦截，interrupt_on={tool:True})      │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
└──────────────────────────┬──────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        ▼                  ▼                      ▼
┌──────────────┐  ┌────────────────────────┐  ┌──────────────────────┐
│   Tools 层    │  │  记忆层                │  │  Skills 层           │
│              │  │                       │  │                      │
│ ┌──────────┐ │  │ PostgresStore          │  │ .skill/              │
│ │ terminal │ │  │ + pgvector (向量搜索)  │  │ └── <name>/           │
│ │ (Bash)   │ │  │ + OpenAIEmbeddings     │  │     ├── SKILL.md     │
│ ├──────────┤ │  │ namespace/             │  │     ├── scripts/     │
│ │form_     │ │  │ key → JSON (含向量)    │  │     └── references/  │
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
│   ├── config.py                     # 配置加载 (YAML → dataclass)
│   ├── agent.py                      # Agent 创建入口 (build_agent)
│   ├── cli.py                        # CLI 入口 (click: chat, serve)
│   ├── skills/
│   │   ├── loader.py                 # SKILL.md 扫描与解析
│   │   └── middleware.py             # SkillMiddleware + load_skill tool
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
│       ├── app.py                    # FastAPI 应用工厂
│       ├── main.py                   # uvicorn 启动入口
│       ├── routes_chat.py            # /api/chat, /api/chat/end
│       ├── routes_form.py            # /api/forms
│       └── routes_approval.py        # /api/approvals
└── tests/
```

## 核心数据流

### 1a. 聊天请求流（阻塞模式）

```
用户消息 → FastAPI /api/chat
         → ApprovalHandler.run_with_hitl()
           → agent.ainvoke(messages, config, version="v2")
             → SkillMiddleware 注入 skill 描述到 system prompt
             → LLM 生成回复 (可能包含 tool_calls)
             → HumanInTheLoopMiddleware 拦截所有 tool_calls
               (interrupt_on={tool_name: True}，所有工具默认拦截)
           ← GraphOutput (含 .interrupts)
         → _process_interrupt() 解析 action_requests + review_configs
         → should_auto_approve() 正则匹配 tool name + args
         → 全部自动通过 → resume → _extract_done() 返回结果
         → 需要人工 → 返回 pending_approval 等待 HTTP 决策
```

### 1b. 聊天请求流（SSE 流式模式）

```
用户消息 → FastAPI /api/chat (X-Stream: true)
         → EventSourceResponse(streaming_handler.run_streaming())
           → agent.astream(messages, stream_mode=["messages","updates"], version="v2")
             → messages chunk → yield SSE token / tool_call 事件
             → updates chunk (tools) → yield SSE tool_result 事件
             → updates chunk (__interrupt__) → _handle_interrupt():
               → 全部自动通过 → 内部 resume streaming → 继续输出
               → 需要人工 → yield SSE interrupt 事件 → 流结束

审批恢复 (X-Stream: true):
POST /api/approvals/{id}/decide
         → EventSourceResponse(streaming_handler.submit_decision_streaming())
           → agent.astream(Command(resume=decisions), ...)
           → 继续流式输出后续 token / tool_call / tool_result 事件
```

```
用户消息 → FastAPI /api/chat
         → ApprovalHandler.run_with_hitl()
           → agent.ainvoke(messages, config, version="v2")
             → SkillMiddleware 注入 skill 描述到 system prompt
             → LLM 生成回复 (可能包含 tool_calls)
             → HumanInTheLoopMiddleware 拦截所有 tool_calls
               (interrupt_on={tool_name: True}，所有工具默认拦截)
           ← GraphOutput (含 .interrupts)
         → _process_interrupt() 解析 action_requests + review_configs
         → should_auto_approve() 正则匹配 tool name + args
         → 全部自动通过 → resume → _extract_done() 返回结果
         → 需要人工 → 返回 pending_approval 等待 HTTP 决策
```

### 2. 长期记忆流

```
Agent 对话中
  → LLM 判断需要记忆信息
  → 调用 save_memory(key, value)
    key 为自然语言标识（如 "user preference for python data analysis"）
    namespace 固定为 ("users", "default")
    → runtime.store.put(("users","default"), key, {"content": value})
    → PostgresStore 自动对 content 字段生成向量嵌入 (OpenAIEmbeddings)
    → 写入 PostgreSQL (pgvector)

Agent 对话中
  → LLM 判断需要回忆信息
  → 调用 search_memory(query)
    query 为自然语言查询（如 "user likes programming"）
    namespace 固定为 ("users", "default")
    → runtime.store.search(("users","default"), query=query)
    → PostgresStore 使用 pgvector 进行余弦相似度向量搜索
    → 返回语义最相关的记忆 → 返回给 Agent

会话结束时（CLI /quit 或 POST /api/chat/end）
  → save_session_summary(store, messages, config)
    → LLM 生成对话摘要（含重试逻辑，最多 3 次尝试避免空响应）
    → store.put(("sessions",), "latest", {"content": summary, ...})
    → 写入 PostgresStore

下次启动（新 thread 首条消息）
  → load_session_summary(store)
    → store.get(("sessions",), "latest")
    → 注入到首条用户消息: "[上一次对话摘要]\n...\n[当前消息]\n..."
```

### 3. 表单工具流

```
Agent 调用 form_generate(purpose, requirements, timeout)
  → LLM 生成表单 JSON schema
  → 存入 _pending_forms[form_id]
  → asyncio.Event 等待
用户 GET /api/forms/{form_id} → 获取表单
用户 POST /api/forms/{form_id}/submit → submit_form() 触发 event.set()
  → tool 返回用户填写数据
超时 → tool 返回 timeout 状态
```

### 4. HITL 审批流

```
ApprovalHandler.run_with_hitl(message, thread_id)
  1. agent.ainvoke(version="v2") → GraphOutput
  2. result.interrupts 为空 → _extract_done() 返回 {"status": "done", ...}
  3. _process_interrupt() 解析 interrupt.value:
     - action_requests: [{name, args, description}, ...]
     - review_configs: [{action_name, allowed_decisions}, ...]
  4. 遍历 action_requests:
     - should_auto_approve(name, args) → decisions[i] = {"type": "approve"}
     - 不匹配 → decisions[i] = None, 加入 needs_human
  5. needs_human 为空 → resume → _extract_done()
  6. needs_human 非空 → 存入 self.pending, 返回 {"status": "pending_approval", ...}

用户 POST /api/approvals/{id}/decide
  → ApprovalHandler.submit_decision()
    → 填充 human_decisions 到 decisions 数组
    → agent.ainvoke(Command(resume=all_decisions))
    → 可能产生新的 interrupt → 再次进入 _process_interrupt() 循环
    → 无新 interrupt → _extract_done() 返回结果
```

## 模块依赖关系

```
config.py ← (被所有模块引用)

cli.py → config.py
       → agent.py (chat 模式)
       → tools/memory.py (save_session_summary, load_session_summary)
       → server/main.py (serve 模式)

server/main.py → agent.py
               → hitl/approval_handler.py
               → hitl/streaming_handler.py
               → server/app.py

agent.py → config.py
         → skills/loader.py + skills/middleware.py
         → tools/bash.py + tools/form.py + tools/memory.py
         → mcp/loader.py
         → memory/store.py

hitl/approval_handler.py → config.py
                          (依赖 agent 实例，不依赖具体 agent 构建逻辑)

server/routes_chat.py → hitl/approval_handler.py
                      → hitl/streaming_handler.py
                      → tools/memory.py (save_session_summary, load_session_summary)
server/routes_approval.py → hitl/streaming_handler.py
server/routes_*.py → tools/form.py
```

## 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 框架 | LangChain `create_agent` | 官方 ReAct 实现，内置 middleware/tool 支持 |
| HITL 实现 | 官方 `HumanInTheLoopMiddleware` | 不手动实现 interrupt，交给 SDK 管理状态 |
| 自动审批 | CLI 和 HTTP 双路径实现 | `_should_auto_approve()` 在 CLI 层，`ApprovalHandler` 在 HTTP 层，共享同一正则匹配逻辑 |
| 流式响应 | 扩展 `ApprovalHandler` 为 `StreamingApprovalHandler` 子类 | 不修改基类，HTTP 流式通过 SSE (`sse-starlette`)，CLI 流式通过 `--stream` 参数启用 `astream()` |
| Skills 格式 | agentskills.io 规范 | 开放标准，SKILL.md 可读、可审计、易分享 |
| 表单等待 | `asyncio.Event` + 内存 store | 简单可靠，单进程部署足够 |
| 长期记忆存储 | PostgreSQL + pgvector (PostgresStore + OpenAIEmbeddings) | 生产级持久化，跨进程共享，原生向量语义搜索支持 |
| 记忆访问方式 | 通过 `runtime.store` 在 tool 中读写 | LangChain 官方模式：store 传入 create_agent 后，tool 通过 ToolRuntime.store 访问 |
| 记忆搜索方式 | 向量语义搜索 (pgvector + Embeddings, cosine similarity) | 自然语言查询，语义匹配比关键词匹配更准确，跨语言无障碍 |
| 记忆命名空间 | 固定 `("users", "default")` | 简化接口，所有记忆工具无需 namespace 参数 |
| 会话摘要 | 固定 key `"latest"` 写入 `("sessions",)` 命名空间 | 简单可靠，每次启动自动加载上次摘要 |
| 记忆工具审批 | 自动通过（`auto_approve: .*`） | 记忆操作无安全风险，无需人工确认 |
| MCP 加载 | JSON 文件 | MCP 的 MultiServerMCPClient 接受 dict，JSON 映射最直接 |
