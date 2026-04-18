# Feature: MCP 集成

## 文件

- `src/plush_agent/mcp/loader.py` — 从 JSON 配置加载 MCP servers
- `mcp_servers.json` — MCP server 配置文件

## 配置格式

`mcp_servers.json` 格式与 `MultiServerMCPClient` 的构造参数一致：

```json
{
  "math": {
    "transport": "stdio",
    "command": "python",
    "args": ["/path/to/math_server.py"]
  },
  "weather": {
    "transport": "http",
    "url": "http://localhost:8001/mcp"
  }
}
```

### 支持的 transport

| Transport | 字段 | 说明 |
|-----------|------|------|
| `stdio` | `command`, `args` | 启动子进程，通过 stdin/stdout 通信 |
| `http` | `url`, 可选 `headers` | HTTP Streamable HTTP 协议 |

### 认证

HTTP transport 支持 `headers` 字段：

```json
{
  "weather": {
    "transport": "http",
    "url": "http://localhost:8001/mcp",
    "headers": {
      "Authorization": "Bearer YOUR_TOKEN"
    }
  }
}
```

## 加载逻辑

```python
async def load_mcp_tools(config_path: str | Path) -> tuple[list, MultiServerMCPClient | None]:
```

1. 读取 JSON 文件
2. 如果为空（`{}`）→ 返回 `([], None)`
3. 创建 `MultiServerMCPClient(servers_config)`
4. 调用 `client.get_tools()` 获取工具列表
5. 返回 `(tools, client)`

**注意**：`client` 需要保持引用，否则 MCP 连接会被清理。

## 集成到 Agent

在 `agent.py` 的 `build_agent()` 中：

```python
tools = _collect_tools(config)
# TODO: async load MCP tools
# mcp_tools, mcp_client = await load_mcp_tools(config.mcp.config_file)
# tools.extend(mcp_tools)
```

当前 MCP 工具需要在启动时异步加载后追加到 tools 列表。由于 `build_agent()` 是同步函数，MCP 加载需要在 `run_server()` 或 `cli chat` 中单独处理。

## 调试定位

| 问题 | 定位 |
|------|------|
| MCP server 连接失败 | 检查 URL/command/args 是否正确 |
| 工具未加载 | JSON 文件是否为空或格式错误 |
| 工具调用超时 | MCP server 响应慢，检查 server 日志 |
