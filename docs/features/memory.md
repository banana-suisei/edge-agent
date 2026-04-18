# Feature: 长期记忆

## 文件

- `src/plush_agent/memory/store.py` — Store 工厂（PostgresStore / InMemoryStore）
- `src/plush_agent/tools/memory.py` — (*) 记忆工具（待实现）
- `src/plush_agent/agent.py` — 将 store 传入 `create_agent()`

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
store.put(("users",), "user_123", {"name": "Alice", "lang": "zh"})

# 读取
item = store.get(("users",), "user_123")
item.value  # {"name": "Alice", "lang": "zh"}

# 搜索
items = store.search(("users",), query="Alice")

# 删除
store.delete(("users",), "user_123")
```

## Tool 中访问 Store

通过 `runtime.store` 参数（LangChain 官方模式）：

```python
from langchain.tools import tool, ToolRuntime

@tool
def save_memory(key: str, value: str, runtime: ToolRuntime) -> str:
    """Save a memory item."""
    assert runtime.store is not None
    runtime.store.put(("memories",), key, {"content": value})
    return f"Saved memory: {key}"
```

## 当前问题：数据未写入

### 现象

PostgreSQL 中 `store` 表已由 `setup()` 创建，但无任何数据行。

### 根因

`store` 已正确传入 `create_agent(store=store)`，但 **没有任何工具通过 `runtime.store` 执行写操作**。LLM 无法自主写入记忆——它需要显式的工具来与 store 交互。

现有工具（terminal、form_generate、load_skill）均不涉及 store 操作。

### 修复方案

在 `src/plush_agent/tools/memory.py` 中实现记忆工具：

| 工具 | 功能 | store 操作 |
|------|------|-----------|
| `save_memory` | 保存一条记忆 | `store.put()` |
| `search_memory` | 搜索记忆 | `store.search()` |
| `get_memory` | 获取指定记忆 | `store.get()` |
| `delete_memory` | 删除记忆 | `store.delete()` |

实现后需要在 `agent.py` 的 `_collect_tools()` 中注册，并更新 `interrupt_on` 和 `auto_approve` 配置。

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
```

## 调试定位

| 问题 | 定位 |
|------|------|
| store 为 None | `build_agent()` 是否传了 `store=store` |
| 数据库连接失败 | 检查 `config.yaml` 中 postgres 配置、密码中的特殊字符是否被 URL 编码 |
| store 表不存在 | `store.setup()` 是否在 `create_store()` 中被调用 |
| 数据库无数据 | (*) 根因：缺少 memory_tools，store 没有被任何工具写入 |
| pool 已关闭 | 不要使用 `from_conn_string()` + `__enter__()`，使用直接构造 `ConnectionPool` + `PostgresStore` |
| namespace 错误 | 确保 namespace 是 tuple，如 `("users",)` 而非 `"users"` |
