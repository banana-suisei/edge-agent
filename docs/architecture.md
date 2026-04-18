# Plush-Agent 架构文档

## 系统总览

Plush-Agent 是一个基于 LangChain 的 ReAct Agent，使用 OpenAI 接口标准的基座模型。系统通过 FastAPI 提供 HTTP REST API，同时支持 CLI 交互。

```
┌─────────────────────────────────────────────────────────┐
│                     用户交互层                           │
│  ┌──────────┐              ┌──────────────────────────┐ │
│  │   CLI    │              │   FastAPI HTTP Server     │ │
│  │  (click) │              │  /api/chat (SSE)          │ │
│  │          │              │  /api/forms               │ │
│  │          │              │  /api/approvals           │ │
│  └────┬─────┘              └────────────┬─────────────┘ │
└───────┼─────────────────────────────────┼───────────────┘
        │                                 │
        ▼                                 ▼
┌─────────────────────────────────────────────────────────┐
│                   Agent 编排层                            │
│                                                         │
│  ┌──────────────┐  ┌────────────┐  ┌────────────────┐  │
│  │ ApprovalHandler│  │ create_agent │  │ InMemorySaver │  │
│  │ (HITL 自动审批)│  │ (ReAct 循环)│  │ (Checkpointer)│  │
│  └──────┬───────┘  └──────┬─────┘  └────────────────┘  │
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
┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐
│   Tools 层    │  │  记忆层      │  │  Skills 层           │
│              │  │             │  │                      │
│ ┌──────────┐ │  │ PostgresStore│  │ .skill/              │
│ │ terminal │ │  │ (PostgreSQL) │  │ └── <name>/           │
│ │ (Bash)   │ │  │ namespace/  │  │     ├── SKILL.md     │
│ ├──────────┤ │  │ key → JSON  │  │     ├── scripts/     │
│ │form_     │ │  │             │  │     └── references/  │
│ │generate  │ │  └─────────────┘  │                      │
│ ├──────────┤ │                    │  SkillLoader (扫描)  │
│ │load_skill│ │  ┌──────────────┐  │  SkillMiddleware     │
│ ├──────────┤ │  │  MCP 层      │  │  (渐进式披露)        │
│ │memory_   │ │  │             │  └──────────────────────┘
│ │tools (*) │ │  │ MultiServer │
│ │(待实现)  │ │  │ MCPClient   │
│ ├──────────┤ │  │ (from JSON) │
│ │MCP tools │ │  └──────────────┘
│ │(动态加载)│ │
│ └──────────┘ │
└──────────────┘

(*) memory_tools 待实现：save_memory / search_memory / list_memories
    通过 runtime.store 读写 PostgresStore，是长期记忆数据写入的唯一入口
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
│   │   └── memory.py                 # (*) 长期记忆工具 (待实现)
│   ├── mcp/
│   │   └── loader.py                 # MCP 从 JSON 配置加载
│   ├── memory/
│   │   └── store.py                  # Store 工厂 (PostgresStore / InMemoryStore)
│   ├── hitl/
│   │   └── approval_handler.py       # HITL 自动审批 + HTTP 桥接
│   └── server/
│       ├── app.py                    # FastAPI 应用工厂
│       ├── main.py                   # uvicorn 启动入口
│       ├── routes_chat.py            # /api/chat
│       ├── routes_form.py            # /api/forms
│       └── routes_approval.py        # /api/approvals
└── tests/
```

## 核心数据流

### 1. 聊天请求流

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

### 2. 长期记忆流 (当前状态)

```
                    ┌─────────────────────────────────────────┐
                    │           当前状态（缺陷）                │
                    │                                         │
  create_agent()    │  store=store  ✅ 已传入                   │
  ──────────────►   │  PostgresStore ✅ 已创建并 setup()        │
                    │                                         │
                    │  ❌ 没有任何工具调用 runtime.store         │
                    │  ❌ 数据永远不会写入 PostgreSQL            │
                    │                                         │
                    │  根因：缺少 memory_tools                  │
                    │  (save_memory / search_memory 等)        │
                    └─────────────────────────────────────────┘

修复后的预期数据流：

Agent 对话
  → LLM 判断需要记忆信息
  → 调用 save_memory(namespace, key, value)
    → runtime.store.put(namespace, key, value)
    → 写入 PostgresStore → PostgreSQL

Agent 对话
  → LLM 判断需要回忆信息
  → 调用 search_memory(namespace, query)
    → runtime.store.search(namespace, query)
    → 从 PostgresStore 读取 → 返回给 Agent
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
       → server/main.py (serve 模式)

server/main.py → agent.py
               → hitl/approval_handler.py
               → server/app.py

agent.py → config.py
         → skills/loader.py + skills/middleware.py
         → tools/bash.py + tools/form.py + tools/memory.py (*)
         → mcp/loader.py
         → memory/store.py

hitl/approval_handler.py → config.py
                          (依赖 agent 实例，不依赖具体 agent 构建逻辑)

server/routes_*.py → hitl/approval_handler.py
                   → tools/form.py
```

## 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 框架 | LangChain `create_agent` | 官方 ReAct 实现，内置 middleware/tool 支持 |
| HITL 实现 | 官方 `HumanInTheLoopMiddleware` | 不手动实现 interrupt，交给 SDK 管理状态 |
| 自动审批 | HTTP 层实现，不修改 middleware | 官方 middleware 仅支持工具名级别，参数正则需要在调用层处理 |
| Skills 格式 | agentskills.io 规范 | 开放标准，SKILL.md 可读、可审计、易分享 |
| 表单等待 | `asyncio.Event` + 内存 store | 简单可靠，单进程部署足够 |
| 长期记忆存储 | PostgreSQL (PostgresStore) | 生产级持久化，跨进程共享，支持向量搜索 |
| 记忆访问方式 | 通过 `runtime.store` 在 tool 中读写 | LangChain 官方模式：store 传入 create_agent 后，tool 通过 ToolRuntime.store 访问 |
| MCP 加载 | JSON 文件 | MCP 的 MultiServerMCPClient 接受 dict，JSON 映射最直接 |

## 已知问题

### 长期记忆未写入数据

**现象**：PostgreSQL 中 `store` 表存在但无任何数据行。

**根因**：`store` 虽然已正确创建（PostgresStore + ConnectionPool）并传入 `create_agent(store=store)`，但没有任何工具通过 `runtime.store` 执行 put/get/search 操作。LLM 无法自主决定写入记忆——它需要显式的工具（如 `save_memory`、`search_memory`）才能与 store 交互。

**修复方案**：在 `src/plush_agent/tools/memory.py` 中实现记忆工具，并在 `_collect_tools()` 中注册。
