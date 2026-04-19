# Feature: Tools

## 文件

- `src/plush_agent/tools/bash.py` — Bash/Shell 工具
- `src/plush_agent/tools/form.py` — 表单生成工具 + HTTP 接口函数
- `src/plush_agent/tools/memory.py` — 长期记忆工具 + 会话摘要函数

## 工具注册

在 `agent.py` 的 `_collect_tools()` 中统一收集，传入 `create_agent(tools=...)`：

```python
from plush_agent.tools.memory import memory_tools

tools = [bash_tool, load_skill, create_form_generate_tool(config), *memory_tools]
# + MCP tools (async loaded)
```

所有注册的工具自动被加入 `HumanInTheLoopMiddleware` 的 `interrupt_on` 配置（默认拦截所有工具调用）。来自 middleware 的工具（如 `load_skill`）需手动添加到 `all_tool_names`。

```python
all_tool_names = {t.name for t in tools}
all_tool_names.add("load_skill")
interrupt_on = {name: True for name in all_tool_names}
```

---

## Bash Tool (`terminal`)

封装 `langchain_community.tools.ShellTool`，注册名为 `terminal`。

```python
from langchain_community.tools import ShellTool
bash_tool = ShellTool()
```

- 输入：`{"commands": ["echo hello"]}`
- 输出：命令的标准输出 + 标准错误
- 受 HITL 审批控制，危险命令需人工确认

### 依赖

需要 `langchain-experimental` 包，否则 `ShellTool()` 初始化报错。

### 安全注意事项

- ShellTool 没有内置安全防护
- 通过 HITL 中间件控制哪些命令需要审批
- 在 `config.yaml` 的 `hitl.auto_approve` 中配置安全命令的正则

---

## Form Tool (`form_generate`)

### 工厂模式

`create_form_generate_tool(cfg)` 返回一个闭包 tool，捕获配置对象用于：
- 获取 LLM 连接参数（复用 agent 模型）
- 获取默认超时时间

### Tool 签名

```python
@tool
async def form_generate(
    form_purpose: str,         # 表单用途描述
    field_requirements: str,   # 字段需求描述
    timeout: int = 300,        # 等待超时（秒）
) -> dict:
```

### 执行流程

```
1. 创建 ChatOpenAI 实例（复用 agent 配置，temperature=0.3）
2. asyncio.to_thread(_generate_form_sync) → LLM 生成表单 JSON
3. 添加 formId，所有字段 value=null
4. 存入 _pending_forms[form_id]，创建 asyncio.Event
5. await asyncio.wait_for(event.wait(), timeout=timeout)
6a. 事件触发 → 返回 {"status": "submitted", "formData": {...}}
6b. 超时 → 返回 {"status": "timeout", "message": "..."}
```

### LLM 表单生成

`_generate_form_sync()` 向 LLM 发送 system prompt + 用户需求，要求返回 JSON：

```json
{
  "formName": "用户注册表",
  "formDescription": "填写注册信息",
  "fields": [
    {
      "fieldId": "username",
      "fieldName": "用户名",
      "fieldType": "text",
      "required": true,
      "placeholder": "请输入用户名"
    }
  ]
}
```

支持的 `fieldType`：`text`、`password`、`textarea`、`number`、`select`、`checkbox`、`radio`、`date`、`email`

### 模块级状态

```python
_pending_forms: dict[str, dict] = {}     # form_id → {schema, submitted}
_form_events: dict[str, asyncio.Event] = {}  # form_id → Event
```

这些是进程内全局状态，由 HTTP 路由层的函数直接操作。

### HTTP 接口函数

| 函数 | 用途 | 调用者 |
|------|------|--------|
| `get_pending_forms()` | 列出所有待填写表单 | `GET /api/forms` |
| `get_form(form_id)` | 获取单个表单 schema | `GET /api/forms/{id}` |
| `submit_form(form_id, fields)` | 提交数据，触发 Event | `POST /api/forms/{id}/submit` |

`submit_form()` 设置 `entry["submitted"] = fields` 并调用 `event.set()`，唤醒正在等待的 tool。

---

## Memory Tools

长期记忆工具，通过 `runtime.store` 读写 PostgresStore（含向量索引）。使用固定命名空间 `("users", "default")`，不暴露 namespace 参数给 LLM。

| 工具 | 功能 | store 操作 |
|------|------|-----------|
| `save_memory` | 保存记忆 | `store.put(namespace, key, {"content": value})` |
| `search_memory` | 语义搜索记忆 | `store.search(namespace, query=query, limit=10)` |
| `get_memory` | 按 key 精确获取 | `store.get(namespace, key)` |
| `delete_memory` | 按 key 精确删除 | `store.delete(namespace, key)` |

### 向量语义搜索

`search_memory` 不再使用关键词匹配，而是通过 pgvector 做向量语义搜索：

```python
# 保存时 — store 自动为 value["content"] 生成 embedding 向量
save_memory(key="favorite-vtubers", value="星街彗星、白上吹雪、神乐七奈")

# 搜索时 — query 被 embedding 后与存储的向量做余弦相似度匹配
search_memory(query="喜欢的虚拟主播")
```

### 会话摘要函数（非 @tool）

`memory.py` 还提供两个应用层函数，由 CLI/Server 直接调用：

- `save_session_summary(store, messages, config)` — 生成对话摘要（含重试机制），写入 `("sessions",) / "latest"`
- `load_session_summary(store)` — 读取上次摘要（直接 key 查找，不走向量搜索）

详见 [memory.md](memory.md)。

---

## 扩展指南

添加新 tool：

1. 在 `src/plush_agent/tools/` 下创建新模块
2. 定义 `@tool` 函数
3. 在 `agent.py` 的 `_collect_tools()` 中导入并添加到列表
4. 新工具自动被加入 `interrupt_on`（从 tools 列表动态收集）
5. 如需自动审批，在 `config.yaml` 的 `hitl.auto_approve` 中添加规则

## 调试定位

| 问题 | 定位 |
|------|------|
| Bash tool 初始化失败 | 缺少 `langchain-experimental` |
| LLM 返回非 JSON | `_generate_form_sync()` 的 markdown fence 剥离逻辑 |
| 表单提交后 tool 无响应 | `submit_form()` 是否正确调用 `event.set()` |
| 表单超时 | 检查 `timeout` 参数和 `config.form.default_timeout` |
| 记忆搜索无结果 | embedding 配置是否正确、pgvector 是否安装、dims 是否匹配模型输出 |
| 会话摘要内容为空 | 内置 3 次重试机制；检查 LLM 是否可用 |
