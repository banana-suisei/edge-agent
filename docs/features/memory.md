# Feature: 长期记忆

## 文件

- `src/plush_agent/memory/store.py` — `InMemoryStore` 工厂
- `src/plush_agent/agent.py` — 将 store 传入 `create_agent()`

## 实现

使用 `langgraph.store.memory.InMemoryStore`：

```python
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()
agent = create_agent(model, tools=tools, store=store, ...)
```

## 数据组织

InMemoryStore 使用 `(namespace_tuple, key)` 组织数据：

```python
# 写入
store.put(("users",), "user_123", {"name": "Alice", "lang": "zh"})

# 读取
item = store.get(("users",), "user_123")
item.value  # {"name": "Alice", "lang": "zh"}

# 搜索
items = store.search(("users",), query="Alice")
```

## Tool 中访问 Store

通过 `runtime.store` 参数：

```python
from langchain.tools import tool, ToolRuntime

@tool
def save_preference(key: str, value: str, runtime: ToolRuntime) -> str:
    """Save a user preference."""
    runtime.store.put(("preferences",), key, {"value": value})
    return f"Saved {key}={value}"
```

## 局限性

- **InMemoryStore 是内存存储**：进程重启后数据丢失
- 适用于开发、测试、单次会话场景
- 生产环境应替换为 `PostgresStore`（`langgraph-checkpoint-postgres`）

## 扩展到持久化存储

1. 安装 `langgraph-checkpoint-postgres`
2. 在 `config.yaml` 中 `memory.type` 设为 `"postgres"`
3. 在 `memory/store.py` 中根据 type 创建对应的 store

## 调试定位

| 问题 | 定位 |
|------|------|
| store 为 None | `build_agent()` 是否传了 `store=store` |
| 数据丢失 | InMemoryStore 特性，检查是否重启了进程 |
| namespace 错误 | 确保 namespace 是 tuple，如 `("users",)` 而非 `"users"` |
