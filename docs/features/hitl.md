# Feature: Human-in-the-Loop (HITL) 审批系统

## 文件

- `src/plush_agent/agent.py` — `build_agent()` 配置 `HumanInTheLoopMiddleware`
- `src/plush_agent/hitl/approval_handler.py` — `ApprovalHandler` + `PendingApproval`
- `src/plush_agent/hitl/streaming_handler.py` — `StreamingApprovalHandler`（继承 `ApprovalHandler`）
- `src/plush_agent/server/routes_approval.py` — HTTP 审批接口（含 SSE 流式支持）
- `src/plush_agent/server/routes_chat.py` — 通过 ApprovalHandler 调用 agent（含 SSE 流式支持）
- `src/plush_agent/cli.py` — CLI 模式 HITL 交互审批（含 `_should_auto_approve`、`_stream_cli`）

## 架构

HITL 分两层：

1. **LangChain 官方层**：`HumanInTheLoopMiddleware` — 负责中断/恢复生命周期
2. **Plush-Agent 层**：`ApprovalHandler`（HTTP）+ `_should_auto_approve`（CLI） — 负责自动审批过滤

```
              ┌──────────────────────────────────┐
              │    LangChain 官方 SDK             │
              │  HumanInTheLoopMiddleware         │
              │  - after_model hook 拦截 tool_calls│
              │  - interrupt() 暂停执行           │
              │  - Command(resume=decisions) 恢复 │
              │  - interrupt_on={tool: True}      │
              │    所有工具默认拦截，安全优先       │
              └──────────────┬───────────────────┘
                             │
                             ▼
              ┌──────────────────────────────────┐
              │    Plush-Agent 层                 │
              │  ApprovalHandler (HTTP)           │
              │  _should_auto_approve (CLI)       │
              │  - 正则匹配 auto_approve 规则     │
              │  - 匹配 → 自动 approve            │
              │  - 不匹配 → 人工审批              │
              └──────────────┬───────────────────┘
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
              ┌───────────┐   ┌──────────────┐
              │  HTTP 层   │   │   CLI 层      │
              │  /api/     │   │ _handle_cli_  │
              │  approvals │   │ hitl()        │
              └───────────┘   └──────────────┘
```

## Middleware 注册

在 `agent.py` 的 `build_agent()` 中，所有已知工具名被收集后注册到 `interrupt_on`：

```python
all_tool_names = {t.name for t in tools}
all_tool_names.add("load_skill")  # 来自 SkillMiddleware
interrupt_on = {name: True for name in all_tool_names}

hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on=interrupt_on,
    description_prefix="Tool execution pending approval",
)
```

**安全模型：默认拦截所有工具调用**。未列在 `interrupt_on` 中的工具会被 `HumanInTheLoopMiddleware` 自动放行，因此新增工具时必须同步更新 `interrupt_on`。

## 完整生命周期

```
Step 1: HTTP /api/chat {message, thread_id}
        → ApprovalHandler.run_with_hitl(message, thread_id)

Step 2: agent.ainvoke({messages}, config={thread_id}, version="v2")
        → SkillMiddleware 注入 skill 描述
        → LLM 生成回复（可能含 tool_calls）
        → HumanInTheLoopMiddleware 检查 interrupt_on
          → 匹配 → interrupt() → 返回 GraphOutput.interrupts
          → 不匹配 → 直接执行 tool

Step 3: ApprovalHandler._process_interrupt() 解析 interrupt.value
        → action_requests: [{name, args, description}, ...]
        → review_configs: [{action_name, allowed_decisions}, ...]
        → 遍历 action_requests
        → should_auto_approve(name, args)?
          → Yes → decisions[i] = {"type": "approve"}
          → No  → decisions[i] = None, 加入 needs_human
        → 从 review_configs 提取每个 action 的 allowed_decisions

Step 4a: 全部自动审批
        → agent.ainvoke(Command(resume={decisions}), config, version="v2")
        → _extract_done() → {"status": "done", "content": reply}

Step 4b: 部分需要人工
        → 存入 self.pending[thread_id] = PendingApproval(...)
        → 返回 {"status": "pending_approval", "pending_actions": [...]}

Step 5: 用户 POST /api/approvals/{id}/decide {decisions: [...]}
        → ApprovalHandler.submit_decision()
        → 将 human_decisions 填入 decisions 数组对应位置
        → agent.ainvoke(Command(resume={all_decisions}), config, version="v2")
        → 可能产生新的 interrupt → _process_interrupt() 再次处理
        → 无新 interrupt → _extract_done() 返回结果
```

## Interrupt 数据结构

LangChain 官方 `ActionRequest` TypedDict 的 key 是 `args`（不是 `arguments`）：

```python
# interrupt.value 结构
{
    "action_requests": [
        {
            "name": "terminal",           # 工具名
            "args": {"commands": "ls"},    # 注意: key 是 "args"
            "description": "Tool execution pending approval\n\nTool: terminal\nArgs: ..."
        }
    ],
    "review_configs": [
        {
            "action_name": "terminal",
            "allowed_decisions": ["approve", "edit", "reject"]
        }
    ]
}
```

`allowed_decisions` 位于 `review_configs` 中，而非 `action_requests` 中。`_process_interrupt()` 负责从 `review_configs` 中提取每个 action 对应的 `allowed_decisions`。

## 返回值约定

`ApprovalHandler` 的所有公开方法始终返回结构化 dict：

| 方法 | 返回类型 |
|------|----------|
| `run_with_hitl()` | `{"status": "done", "content": "..."}` 或 `{"status": "pending_approval", ...}` |
| `submit_decision()` | 同上，或 `None`（审批不存在） |
| `list_pending()` | `list[dict]` |
| `get_pending()` | `dict` 或 `None` |

`_extract_done()` 是统一的 `GraphOutput` → dict 转换器，确保 HTTP 路由只需直接返回 handler 结果。

## CLI 模式 HITL

CLI 模式通过 `_handle_cli_hitl()` 实现交互式审批，使用 `_should_auto_approve()` 匹配自动审批规则：

```python
def _handle_cli_hitl(agent, result, cfg, config: Config):
    while result.interrupts:
        for action in action_requests:
            if _should_auto_approve(config, name, args):
                decisions.append({"type": "approve"})
                continue
            # 否则交互式审批
            click.echo(f"\n⚠ Tool call requires approval:")
            ...
```

`_should_auto_approve()` 与 `ApprovalHandler.should_auto_approve()` 逻辑一致，共享 `config.yaml` 中的 `hitl.auto_approve` 规则。

## StreamingApprovalHandler（SSE 流式审批）

`StreamingApprovalHandler` 继承自 `ApprovalHandler`，复用基类的 `should_auto_approve()` 和 `_process_interrupt()` 逻辑，使用 `agent.astream()` 替代 `agent.ainvoke()` 执行。

### 核心方法

| 方法 | 说明 |
|------|------|
| `run_streaming(message, thread_id)` | 流式执行 agent，yield SSE 事件 dict |
| `submit_decision_streaming(approval_id, decisions)` | 流式恢复审批，yield SSE 事件 dict |
| `_stream_agent(input, config, tool_call_acc)` | 内部流式循环，处理 messages/updates chunks |
| `_handle_interrupt(interrupts, config, tool_call_acc)` | 处理 interrupt，自动审批则内部 resume，需人工则 yield interrupt 事件 |

### SSE 事件类型（SSEEventType 枚举）

| 事件 | 触发时机 | 数据结构 |
|------|----------|----------|
| `token` | LLM 生成文本片段 | `{"content": "..."}` |
| `tool_call` | 工具调用完成（参数累积后） | `{"name": "...", "args": {...}, "id": "..."}` |
| `tool_result` | 工具执行完成 | `{"name": "...", "content": "..."}` |
| `interrupt` | HITL 需人工审批 | `{"approval_id": "...", "pending_actions": [...], "auto_approved_count": N}` |
| `done` | 流式输出正常结束 | `{"content": ""}` |
| `error` | 发生异常 | `{"message": "..."}` |

### Interrupt 检测

LangGraph v2 流式中，`__interrupt__` 作为 `updates` chunk 的 `data` 顶层键出现：

```python
# chunk 格式
{"type": "updates", "data": {"__interrupt__": (Interrupt(...),)}}

# 检测逻辑（必须在遍历 node outputs 之前检查）
if "__interrupt__" in update_data:
    interrupts = update_data["__interrupt__"]
    # 处理 interrupt...
```

### 自动审批的内部恢复

当所有 action 都匹配自动审批规则时，handler 内部递归调用 `_stream_agent(Command(resume=decisions), ...)` 继续流式输出，不向客户端发送 interrupt 事件。客户端看到无缝的 token 流。

### 工具调用累积

LLM 的 `tool_call_chunks` 以 JSON 片段形式分多次到达。handler 在 `tool_call_acc` dict 中按 tool call ID 累积片段，当工具执行结果到达时（`tool_result` update），一次性解析完整参数并 yield `tool_call` 事件。因此 `tool_call` 事件总是在 `tool_result` 事件之前。

### 独立的 pending 存储

`StreamingApprovalHandler` 使用自己的 `self.streaming_pending` dict，与基类的 `self.pending` 分离。这是因为阻塞模式和流式模式的 pending 审批由不同的 handler 管理。

### CLI 流式 HITL

CLI 的 `_stream_cli()` 使用 `while True` 循环结构：

1. `agent.astream(stream_mode=["messages","updates"])` 流式输出 token
2. 检测到 `__interrupt__` → break 退出内层 async for
3. 自动审批的 action 直接 approve
4. 全部自动审批 → `stream_input = Command(resume=decisions)`，回到 while 循环继续流式输出
5. 需人工审批 → 回退到 `_handle_cli_hitl()` 同步处理剩余流程

## PendingApproval 数据模型

```python
@dataclass
class PendingApproval:
    approval_id: str              # UUID
    thread_id: str                # 对应的对话线程
    config: dict                  # {"configurable": {"thread_id": ...}}
    action_requests: list[dict]   # 官方 middleware 返回的原始 action_requests
    decisions: list[dict | None]  # 已填充 approve + 待填充 None
    needs_human_indices: list[int] # 需要人工决策的索引位置
    created_at: float             # 创建时间戳
```

关键设计：`decisions` 数组长度等于 `action_requests` 长度。自动审批的位置已填充 `{"type": "approve"}`，需要人工的位置为 `None`。`submit_decision()` 将人工决策填入 `needs_human_indices` 指定的位置，然后整体提交。

## 自动审批规则

配置在 `config.yaml` 的 `hitl.auto_approve` 中，CLI 和 HTTP 共享：

```python
def should_auto_approve(self, tool_name: str, tool_args: dict) -> bool:
    for rule in self.auto_approve_rules:
        if rule.tool != tool_name:
            continue
        args_str = str(tool_args)  # 将整个 args dict 转为字符串
        for pattern in rule.args_patterns:
            if re.search(pattern, args_str):
                return True
    return False
```

匹配逻辑：
- 工具名必须完全匹配 `rule.tool`
- 参数匹配是对 `str(tool_args)` 做正则搜索
- 任一 pattern 匹配即通过

当前自动审批的工具：`terminal`（部分命令）、`form_generate`、`load_skill`、`save_memory`、`search_memory`、`get_memory`、`delete_memory`。

## HTTP 接口

### 列出待审批

```
GET /api/approvals
→ [{approval_id, pending_actions, auto_approved_count, created_at}, ...]
```

### 获取单个

```
GET /api/approvals/{approval_id}
→ {approval_id, pending_actions, auto_approved_count, created_at}
```

### 提交决策

```
POST /api/approvals/{approval_id}/decide
Body: {
  "decisions": [
    {"type": "approve"},
    {"type": "edit", "edited_action": {"name": "...", "args": {...}}},
    {"type": "reject", "message": "原因"}
  ]
}
```

`decisions` 数组长度必须等于 `pending_actions` 数量，顺序一致。

## 关键约束

1. **`version="v2"`** 是必须的——只有 v2 格式才返回 `GraphOutput.interrupts`
2. **`thread_id` 必须一致**——interrupt 和 resume 使用同一个 thread_id
3. **`checkpointer` 必须配置**——HITL 依赖 LangGraph checkpointer 保存中断状态
4. **所有 decision 必须一次性提交**——官方 middleware 不支持部分恢复
5. **`interrupt_on` 必须覆盖所有工具**——未配置的工具会被自动放行，存在安全风险
6. **ActionRequest key 是 `args`**——不是 `arguments`，由 LangChain 官方 TypedDict 定义
7. **CLI 和 HTTP 共享 auto_approve 规则**——通过 `config.yaml` 统一配置

## 扩展指南

### 添加新的自动审批规则

在 `config.yaml` 中添加：

```yaml
hitl:
  auto_approve:
    - tool: "my_new_tool"
      args_patterns:
        - "^.*safe_operation.*$"
```

### 添加新工具到 HITL

在 `agent.py` 的 `build_agent()` 中，新工具会自动被加入 `interrupt_on`（因为从 `tools` 列表动态收集）。但如果工具来自 middleware（如 `load_skill`），需要手动 `all_tool_names.add("tool_name")`。

### 集成到新路由

```python
handler = request.app.state.approval_handler
result = await handler.run_with_hitl(message, thread_id)
return result  # 始终是结构化 dict，直接返回
```

## 测试

`tests/test_hitl.py` 覆盖阻塞模式场景：

| 测试类 | 测试内容 |
|--------|----------|
| `TestProcessInterrupt` | 自动审批匹配、不匹配、混合场景、review_configs 解析 |
| `TestShouldAutoApprove` | 正则匹配、不匹配、不同工具名、通配符 |
| `TestRunWithHitl` | 无中断、全自动审批、需要人工 |
| `TestSubmitDecision` | 提交后完成、不存在审批 |
| `TestPendingManagement` | 空列表、查询不存在 |

`tests/test_streaming_handler.py` 覆盖流式模式场景：

| 测试类 | 测试内容 |
|--------|----------|
| `TestStreamingTokens` | 单 token、多 token、空流 |
| `TestStreamingToolCalls` | tool_call 累积 + tool_result 事件顺序 |
| `TestStreamingHITL` | 自动审批内部恢复、人工审批 interrupt 事件、不存在的审批 |
| `TestStreamingErrors` | 异常时 yield error 事件 |

## 调试定位

| 问题 | 定位 |
|------|------|
| 工具总被审批 | 检查 `interrupt_on` 配置和 `auto_approve` 规则 |
| 自动审批不生效（CLI） | `cli.py` 的 `_should_auto_approve()` — 检查 tool name 和 args 正则 |
| 自动审批不生效（HTTP） | `ApprovalHandler.should_auto_approve()` — 同上 |
| interrupt 后无法恢复 | 确认 thread_id 一致、checkpointer 已配置 |
| 多个审批只处理了一个 | `decisions` 数组长度必须匹配所有 action_requests |
| `result.interrupts` 为空 | 确认使用 `version="v2"` 调用 `agent.ainvoke()` |
| KeyError: 'arguments' | ActionRequest 使用 `args` 不是 `arguments` |
| 工具直接执行无审批 | 检查 `HumanInTheLoopMiddleware` 是否在 middleware 列表中 |
| 新工具绕过审批 | 检查 `interrupt_on` 是否包含该工具名 |
| 流式模式工具无审批/interrupt | `_stream_cli()` 或 `_stream_agent()` 中 `__interrupt__` 检测 — 必须在遍历 `update_data.items()` 前检查顶层键 |
| 流式模式工具调用后无回复 | 自动审批后未 resume streaming — 检查 `_stream_agent()` 或 `_stream_cli()` 的 while 循环是否正确设置 `stream_input = Command(resume=...)` |
| SSE 流无事件返回 | 检查请求头 `X-Stream: true` 或 `Accept: text/event-stream`，检查 `app.state.streaming_approval_handler` 是否设置 |
| `awrap_model_call` NotImplementedError | `SkillMiddleware` 需同时实现 `wrap_model_call` 和 `awrap_model_call` — 异步上下文（`astream`/`ainvoke`）需要 async 版本 |
