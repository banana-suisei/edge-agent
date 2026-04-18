# Feature: 配置系统

## 文件

- `src/plush_agent/config.py` — 配置加载与类型定义
- `config.yaml` — 主配置文件
- `mcp_servers.json` — MCP servers 配置

## 配置结构

`Config` dataclass 是顶层配置对象，包含以下子配置：

| 子配置 | 类型 | 职责 |
|--------|------|------|
| `model` | `ModelConfig` | LLM 连接参数 |
| `agent` | `AgentConfig` | Agent 行为参数 |
| `mcp` | `McpConfig` | MCP 配置文件路径 |
| `memory` | `MemoryConfig` | 记忆后端类型 |
| `server` | `ServerConfig` | HTTP 服务参数 |
| `hitl` | `HitlConfig` | 自动审批规则列表 |
| `form` | `FormConfig` | 表单工具超时 |

## ModelConfig

```yaml
model:
  base_url: "https://api.openai.com/v1"   # OpenAI 兼容 API 地址
  api_key_env: "OPENAI_API_KEY"            # 环境变量名，或直接填写 API key 值
  model_name: "gpt-4.1"                    # 模型标识
  temperature: 0.7
  max_tokens: 4096
```

- `api_key_env`：先从 `os.environ.get(api_key_env)` 查找环境变量，若不存在则将 `api_key_env` 值本身作为 API key 使用。通过 `ModelConfig.api_key` property 访问
- `base_url`：可替换为任意 OpenAI 兼容 API（如 Azure、本地 Ollama、各种代理）

## AgentConfig

```yaml
agent:
  system_prompt: "你是一个智能助手..."
  skills_dir: ".skill"          # skills 目录的相对/绝对路径
  max_iterations: 10            # ReAct 最大迭代次数
```

## HitlConfig

```yaml
hitl:
  auto_approve:
    - tool: "terminal"                   # 工具名
      args_patterns:                     # 参数正则列表
        - "^.*ls.*$"                     # 匹配 args 字符串表示
        - "^.*cat.*$"
    - tool: "form_generate"
      args_patterns:
        - ".*"                           # 全部自动通过
    - tool: "load_skill"                 # 只读操作，安全自动通过
      args_patterns:
        - ".*"
```

每条规则包含 `tool`（工具名）和 `args_patterns`（正则列表）。当工具调用的 `str(args)` 匹配任一正则时，自动通过审批。

**注意**：`HumanInTheLoopMiddleware` 在 `agent.py` 中配置 `interrupt_on` 对所有已知工具名设置为 `True`（默认拦截所有）。此处的 `auto_approve` 规则控制的是拦截后的自动放行行为——未匹配规则的工具调用会暂停等待人工审批。新增工具必须同时出现在 `interrupt_on`（`agent.py` 中自动从 tools 列表收集）和可选的 `auto_approve` 规则中。

## 加载逻辑

`load_config(path)` 读取 YAML 文件，对每个 section 进行安全的 `.get()` 解析，缺失字段使用默认值。配置文件本身可选——所有字段都有默认值。

## 扩展指南

新增配置字段时：

1. 在 `config.py` 中对应的 `@dataclass` 添加字段和默认值
2. 在 `load_config()` 的对应 section 中添加 `.get()` 解析
3. 更新 `config.yaml` 示例

## 调试定位

| 问题 | 定位 |
|------|------|
| API key 未设置 | `ModelConfig.api_key` property 返回空字符串；若 `api_key_env` 直接填写了 key 值则不会出现此问题 |
| 配置文件不存在 | `load_config()` 会抛 `FileNotFoundError` |
| YAML 格式错误 | `yaml.safe_load()` 会抛 `YAMLError` |
| 字段值类型错误 | dataclass 初始化时抛 `TypeError` |
