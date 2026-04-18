# Feature: Human-in-the-Loop (HITL) 审批系统

## 文件

- `src/plush_agent/hitl/approval_handler.py` — `ApprovalHandler` + `PendingApproval`
- `src/plush_agent/server/routes_approval.py` — HTTP 审批接口
- `src/plush_agent/server/routes_chat.py` — 通过 ApprovalHandler 调用 agent

## 架构

HITL 分两层：

1. **LangChain 官方层**：`HumanInTheLoopMiddleware` — 负责中断/恢复生命周期
2. **Plush-Agent 层**：`ApprovalHandler` — 负责自动审批过滤 + HTTP 桥接

```
              ┌──────────────────────────────────┐
              │    LangChain 官方 SDK             │
              │  HumanInTheLoopMiddleware         │
              │  - after_model hook 拦截 tool_calls│
              │  - interrupt() 暂停执行           │
              │  - Command(resume=decisions) 恢复 │
              └──────────────┬───────────────────┘
                             │
                             ▼
              ┌──────────────────────────────────┐
              │    Plush-Agent 层                 │
              │  ApprovalHandler                  │
              │  - should_auto_approve() 正则匹配  │
              │  - run_with_hitl() 处理 interrupt  │
              │  - submit_decision() HTTP 决策入口 │
              └──────────────┬───────────────────┘
                             │
                             ▼
              ┌──────────────────────────────────┐
              │    HTTP 层                        │
              │  /api/approvals                   │
              │  /api/approvals/{id}/decide       │
              └──────────────────────────────────┘
```

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

Step 3: ApprovalHandler 处理 interrupt
        → 遍历 action_requests
        → should_auto_approve(name, arguments)?
          → Yes → decisions[i] = {"type": "approve"}
          → No  → decisions[i] = None, 加入 needs_human

Step 4a: 全部自动审批
        → agent.ainvoke(Command(resume={decisions}), config, version="v2")
        → 返回最终结果

Step 4b: 部分需要人工
        → 存入 self.pending[thread_id] = PendingApproval(...)
        → HTTP 返回 {"status": "pending_approval", "pending_actions": [...]}

Step 5: 用户 POST /api/approvals/{id}/decide {decisions: [...]}
        → ApprovalHandler.submit_decision()
        → 将 human_decisions 填入 decisions 数组对应位置
        → agent.ainvoke(Command(resume={all_decisions}), config, version="v2")
        → 返回最终结果
```

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

配置在 `config.yaml` 的 `hitl.auto_approve` 中：

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

### 集成到新路由

```python
handler = request.app.state.approval_handler
result = await handler.run_with_hitl(message, thread_id)
```

## 调试定位

| 问题 | 定位 |
|------|------|
| 工具总被审批 | 检查 `interrupt_on` 配置和 `auto_approve` 规则 |
| 自动审批不生效 | `should_auto_approve()` — 检查 tool name 和 args 正则 |
| interrupt 后无法恢复 | 确认 thread_id 一致、checkpointer 已配置 |
| 多个审批只处理了一个 | `decisions` 数组长度必须匹配所有 action_requests |
| `result.interrupts` 为空 | 确认使用 `version="v2"` 调用 `agent.ainvoke()` |
