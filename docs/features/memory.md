# Feature: 长期记忆

## 文件

- `src/plush_agent/memory/store.py` — Store 工厂（PostgresStore + 向量索引 / InMemoryStore）
- `src/plush_agent/tools/memory.py` — 记忆工具（save / search / get / delete）+ 会话摘要
- `src/plush_agent/agent.py` — 将 store 传入 `create_agent()`，注册 memory_tools

## Store 创建

通过 `create_store(config)` 根据配置创建对应后端：

```python
from plush_agent.memory.store import create_store
from plush_agent.config import MemoryConfig

# config.yaml 中 memory.type = "postgres" 时
store = create_store(config.memory)
# → 构造 ConnectionPool + PostgresStore（含向量索引）
# → 调用 store.setup() 建表（base 表 + 向量索引表）
```

### PostgresStore + 向量索引

`store.py` 构造 `PostgresStore` 时传入 `index=IndexConfig(...)`，启用 pgvector 向量语义搜索：

```python
from langchain_openai import OpenAIEmbeddings
from langgraph.store.postgres import PostgresStore

embeddings = OpenAIEmbeddings(
    base_url=config.embedding.base_url,
    api_key=config.embedding.api_key,
    model=config.embedding.model_name,
    check_embedding_ctx_length=False,
)

index_config = {
    "dims": config.embedding.dims,
    "embed": embeddings,
    "fields": ["content"],       # 只对 value 中的 "content" 字段生成 embedding
    "distance_type": "cosine",
    "ann_index_config": {"kind": "flat"},  # 暴力搜索（兼容高维向量）
}

store = PostgresStore(conn=pool, index=index_config)
store.setup()  # 创建 store 表 + 向量索引表
```

**前置条件**：PostgreSQL 需安装 pgvector 扩展：
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 索引配置说明

| 配置项 | 说明 |
|--------|------|
| `dims` | 向量维度，需匹配 embedding 模型输出（如 4096） |
| `embed` | LangChain `Embeddings` 实例或嵌入函数 |
| `fields` | 从 value JSON 中提取哪些字段做 embedding，`["content"]` 只取 content |
| `distance_type` | 距离度量：`cosine`、`l2`、`inner_product` |
| `ann_index_config` | `{"kind": "flat"}` 暴力搜索（无维度限制）；`{"kind": "hnsw"}` 支持 ≤2000 维 |

### 连接字符串

`PostgresConfig.connection_string` 属性自动拼接，使用 `urllib.parse.quote_plus` 对 user/password 进行 URL 编码（处理密码中的特殊字符如 `@`）：

```python
postgresql://{user}:{password}@{host}:{port}/{database}?sslmode={sslmode}
```

## 数据组织

所有记忆工具使用固定命名空间 `("users", "default")`，避免 LLM 自行选择 namespace 导致保存和召回不一致。

```python
_MEMORY_NAMESPACE = ("users", "default")

# 写入
store.put(("users", "default"), "key", {"content": "text"})

# 向量语义搜索
items = store.search(("users", "default"), query="自然语言查询")

# 精确获取
item = store.get(("users", "default"), "key")

# 删除
store.delete(("users", "default"), "key")
```

## 记忆工具

4 个 `@tool` 函数，通过 `ToolRuntime.store` 访问 store。所有工具使用固定命名空间，不暴露 `namespace` 参数给 LLM。

### save_memory

```python
@tool
def save_memory(key: str, value: str, runtime: ToolRuntime) -> str:
```

- `key`: 简短自然语言标识符（如 `"favorite-vtubers"`）
- `value`: 要保存的内容

### search_memory

```python
@tool
def search_memory(query: str, runtime: ToolRuntime) -> str:
```

- `query`: 自然语言查询，由 PostgresStore 通过向量相似度搜索返回最相关的 10 条结果
- 底层调用 `store.search(namespace, query=query, limit=10)`，由 embedding 模型将 query 向量化后与存储的向量做余弦相似度匹配

### get_memory

```python
@tool
def get_memory(key: str, runtime: ToolRuntime) -> str:
```

- 按 key 精确获取指定记忆

### delete_memory

```python
@tool
def delete_memory(key: str, runtime: ToolRuntime) -> str:
```

- 按 key 精确删除指定记忆

## 会话摘要

每次对话结束时自动生成摘要并持久化，下次启动时加载注入上下文。会话摘要使用固定 key 存取，不涉及向量搜索。

### 保存

`save_session_summary(store, messages, config)` — 非 @tool，由应用层直接调用。

- 从对话消息中提取 human/ai 内容
- 调用 LLM 生成要点摘要（最多重试 3 次）
- 写入固定位置：`store.put(("sessions",), "latest", {...})`
- 调用时机：CLI `/quit` 退出、`POST /api/chat/end`

### 重试机制

LLM 调用可能返回空内容或抛出异常，`save_session_summary` 内置重试：

- 最多重试 3 次
- 空内容（`response.content` 为空字符串或空白）→ 重试
- 异常（API 错误等）→ 等待 1 秒后重试
- 兼容 `response.content` 为 list 格式（content blocks）的情况
- 3 次都失败 → 返回 `None`，不存储空摘要

### 加载

`load_session_summary(store)` → `str | None`

- 读取 `store.get(("sessions",), "latest")`，直接按 key 获取，不触发向量搜索
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
  embedding:
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"       # 环境变量名，或直接填写 API key 值
    model_name: "text-embedding-3-small"
    dims: 1536                          # 需匹配模型输出维度
    distance_type: "cosine"             # cosine / l2 / inner_product
```

`EmbeddingConfig.api_key` property 与 `ModelConfig.api_key` 行为一致：先从环境变量查找，不存在则将 `api_key_env` 值本身作为 API key。

## 调试定位

| 问题 | 定位 |
|------|------|
| store 为 None | `build_agent()` 是否传了 `store=store` |
| 数据库连接失败 | 检查 `config.yaml` 中 postgres 配置、密码中的特殊字符是否被 URL 编码 |
| store 表不存在 | `store.setup()` 是否在 `create_store()` 中被调用 |
| 向量搜索无结果 | 检查 embedding 配置（base_url、api_key、dims）是否正确；PostgreSQL 是否安装 pgvector 扩展 |
| `expected N dimensions, not M` | `dims` 配置与 embedding 模型实际输出维度不匹配 |
| `column cannot have more than 2000 dimensions for hnsw index` | 向量维度超过 HNSW 限制，改用 `ann_index_config: {"kind": "flat"}` |
| 搜索结果不相关 | 检查 `fields` 配置是否正确（推荐 `["content"]`）；embedding 模型质量 |
| 会话摘要内容为空 | LLM 可能返回空内容，内置 3 次重试机制已处理 |
| 会话摘要未加载 | 检查 store 中 `("sessions",) / "latest"` 是否有数据 |
| pool 已关闭 | 不要使用 `from_conn_string()` + `__enter__()`，使用直接构造 `ConnectionPool` + `PostgresStore` |
