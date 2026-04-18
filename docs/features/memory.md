# Feature: 长期记忆

## 文件

- `src/plush_agent/memory/store.py` — Store 工厂（PostgresStore / InMemoryStore）
- `src/plush_agent/tools/memory.py` — 记忆工具（save / search / get / delete）+ 会话摘要
- `src/plush_agent/agent.py` — 将 store 传入 `create_agent()`，注册 memory_tools

## Store 创建

通过 `create_store(config)` 根据配置创建对应后端：

```python
from plush_agent.memory.store import create_store
from plush_agent.config import MemoryConfig

# config.yaml 中 memory.type = "postgres" 时
store = create_store(config.memory)
# → 直接构造 ConnectionPool + PostgresStore
# → 调用 store.setup() 建表
```

### PostgresStore 实现

`store.py` 直接使用 `psycopg_pool.ConnectionPool` 构造 `PostgresStore`，而非 `from_conn_string()` 上下文管理器（后者返回的生成器会被 GC 回收导致连接池关闭）：

```python
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from langgraph.store.postgres import PostgresStore

pool = ConnectionPool(
    connection_string,
    min_size=1, max_size=5,
    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
)
store = PostgresStore(conn=pool)
store.setup()  # 创建 store 表
```

### 连接字符串

`PostgresConfig.connection_string` 属性自动拼接，使用 `urllib.parse.quote_plus` 对 user/password 进行 URL 编码（处理密码中的特殊字符如 `@`）：

```python
postgresql://{user}:{password}@{host}:{port}/{database}?sslmode={sslmode}
```

## 数据组织

PostgresStore 使用 `(namespace_tuple, key)` 组织数据：

```python
# 写入
store.put(("users",), "user_123", {"content": "Alice"})

# 读取
item = store.get(("users",), "user_123")
item.value  # {"content": "Alice"}

# 搜索
items = store.search(("users",), query="Alice")

# 删除
store.delete(("users",), "user_123")
```

## 记忆工具

4 个 `@tool` 函数，通过 `ToolRuntime.store` 访问 store。所有记忆工具已配置 `auto_approve` 自动通过 HITL 审批。

### save_memory

```python
@tool
def save_memory(namespace: str, key: str, value: str, runtime: ToolRuntime) -> str:
```

- `namespace`: 层级路径，用 `/` 分隔，如 `"users/user_123/preferences"`
- `key`: **3-5 个逗号分隔的英文关键词**，如 `"python,data-analysis,preference"`。用于搜索召回。
- `value`: 要保存的内容

### search_memory

```python
@tool
def search_memory(namespace: str, query: str, runtime: ToolRuntime) -> str:
```

- `namespace`: 层级路径
- `query`: **3-5 个空格分隔的英文关键词**，如 `"programming plan schedule"`。模型需将用户的中文意图翻译为英文关键词。

### get_memory

```python
@tool
def get_memory(namespace: str, key: str, runtime: ToolRuntime) -> str:
```

- 按 key 精确获取指定记忆

### delete_memory

```python
@tool
def delete_memory(namespace: str, key: str, runtime: ToolRuntime) -> str:
```

- 按 key 精确删除指定记忆

### 关键词设计

记忆搜索使用英文关键词而非中文，原因是：
- 内容存储为中文，但中文单词汇在 store.search() 中召回率极低
- `key` 列存储逗号分隔的英文关键词，`search_memory` 的 `query` 使用空格分隔的英文关键词
- 基座模型的 tool description 指示其生成英文关键词

## 会话摘要

每次对话结束时自动生成摘要并持久化，下次启动时加载注入上下文。

### 保存

`save_session_summary(store, messages, config)` — 非 @tool，由应用层直接调用。

- 从对话消息中提取 human/ai 内容
- 调用 LLM 生成要点摘要
- 写入固定位置：`store.put(("sessions",), "latest", {...})`
- 调用时机：CLI `/quit` 退出、`POST /api/chat/end`

### 加载

`load_session_summary(store)` → `str | None`

- 读取 `store.get(("sessions",), "latest")`
- 调用时机：CLI 首条消息前、HTTP 新 thread 首条消息
- 注入格式：`[上一次对话摘要]\n...\n[当前消息]\n...`

### 常量

```python
SESSION_SUMMARY_NAMESPACE = ("sessions",)
SESSION_SUMMARY_KEY = "latest"
```

## 配置

```yaml
memory:
  type: "postgres"       # "postgres" 或 "in_memory"
  postgres:
    host: "localhost"
    port: 5432
    user: "postgres"
    password: "postgres"
    database: "plush_agent"
    sslmode: "disable"

hitl:
  auto_approve:
    - tool: "save_memory"
      args_patterns: [".*"]
    - tool: "search_memory"
      args_patterns: [".*"]
    - tool: "get_memory"
      args_patterns: [".*"]
    - tool: "delete_memory"
      args_patterns: [".*"]
```

## 调试定位

| 问题 | 定位 |
|------|------|
| store 为 None | `build_agent()` 是否传了 `store=store` |
| 数据库连接失败 | 检查 `config.yaml` 中 postgres 配置、密码中的特殊字符是否被 URL 编码 |
| store 表不存在 | `store.setup()` 是否在 `create_store()` 中被调用 |
| 搜索无结果 | 检查 key 是否使用了英文关键词，query 是否为英文关键词 |
| 会话摘要未加载 | 检查 store 中 `("sessions",) / "latest"` 是否有数据 |
| pool 已关闭 | 不要使用 `from_conn_string()` + `__enter__()`，使用直接构造 `ConnectionPool` + `PostgresStore` |
| namespace 错误 | 确保 namespace 是 tuple，如 `("users",)` 而非 `"users"` |
