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
│  └──────────────────────────────────────────────────┘   │
│                                                         │
└──────────────────────────┬──────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        ▼                  ▼                      ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐
│   Tools 层    │  │  记忆层      │  │  Skills 层           │
│              │  │             │  │                      │
│ ┌──────────┐ │  │ InMemoryStore│  │ .skill/              │
│ │ terminal │ │  │             │  │ └── <name>/           │
│ │ (Bash)   │ │  │ namespace/  │  │     ├── SKILL.md     │
│ ├──────────┤ │  │ key → JSON  │  │     ├── scripts/     │
│ │form_     │ │  │             │  │     └── references/  │
│ │generate  │ │  └─────────────┘  │                      │
│ ├──────────┤ │                    │  SkillLoader (扫描)  │
│ │load_skill│ │  ┌──────────────┐  │  SkillMiddleware     │
│ ├──────────┤ │  │  MCP 层      │  │  (渐进式披露)        │
│ │MCP tools │ │  │             │  └──────────────────────┘
│ │(动态加载)│ │  │ MultiServer │
│ └──────────┘ │  │ MCPClient   │
└──────────────┘  │ (from JSON) │
                  └──────────────┘
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
│   │   └── form.py                   # 表单生成 tool + HTTP 接口
│   ├── mcp/
│   │   └── loader.py                 # MCP 从 JSON 配置加载
│   ├── memory/
│   │   └── store.py                  # InMemoryStore 工厂
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
             → HumanInTheLoopMiddleware 拦截需要审批的 tool_calls
           ← GraphOutput (含 .interrupts)
         → 自动审批过滤 (正则匹配 tool name + args)
         → 全部自动通过 → 立即 resume
         → 需要人工 → 返回 pending_approval 等待 HTTP 决策
```

### 2. 表单工具流

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

### 3. HITL 审批流

```
ApprovalHandler.run_with_hitl(message, thread_id)
  1. agent.ainvoke() → GraphOutput
  2. result.interrupts 为空 → 直接返回
  3. 遍历 action_requests:
     - 匹配 auto_approve 规则 → decisions[i] = {"type": "approve"}
     - 不匹配 → decisions[i] = None, 加入 needs_human
  4. needs_human 为空 → agent.ainvoke(Command(resume=decisions))
  5. needs_human 非空 → 存入 self.pending, 返回 pending_approval

用户 POST /api/approvals/{id}/decide
  → ApprovalHandler.submit_decision()
    → 填充 human_decisions 到 decisions 数组
    → agent.ainvoke(Command(resume=all_decisions))
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
         → tools/bash.py + tools/form.py
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
| 配置格式 | YAML | 人类可读，支持注释，适合手动编辑 |
| MCP 加载 | JSON 文件 | MCP 的 MultiServerMCPClient 接受 dict，JSON 映射最直接 |
