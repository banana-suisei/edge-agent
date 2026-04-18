# Plush-Agent 使用手册

## 安装

### 环境要求

- Python 3.11+
- WSL 或 Linux 环境（ShellTool 不支持 Windows）

### 安装步骤

```bash
cd plush-agent
python -m venv .ubuntuvenv
source .ubuntuvenv/bin/activate
pip install -e .
```

### 验证安装

```bash
plush-agent --help
```

---

## 配置

### 设置 API Key

Plush-Agent 通过环境变量读取 API key。默认读取 `OPENAI_API_KEY`：

```bash
export OPENAI_API_KEY="sk-your-key-here"
```

如果使用其他 OpenAI 兼容 API（如 Azure、Ollama、代理），修改 `config.yaml`：

```yaml
model:
  base_url: "https://your-api-endpoint/v1"
  api_key_env: "YOUR_CUSTOM_ENV_VAR"
  model_name: "your-model-name"
```

### 配置文件结构

编辑 `config.yaml`：

```yaml
# LLM 模型配置
model:
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
  model_name: "gpt-4.1"
  temperature: 0.7
  max_tokens: 4096

# Agent 行为
agent:
  system_prompt: "你是一个智能助手..."
  skills_dir: ".skill"
  max_iterations: 10

# MCP servers 配置文件路径
mcp:
  config_file: "mcp_servers.json"

# 记忆类型
memory:
  type: "in_memory"

# HTTP 服务配置
server:
  host: "0.0.0.0"
  port: 8000

# 自动审批规则
hitl:
  auto_approve:
    - tool: "terminal"
      args_patterns:
        - "^.*ls.*$"
        - "^.*cat.*$"
    - tool: "form_generate"
      args_patterns:
        - ".*"

# 表单工具默认超时
form:
  default_timeout: 300
```

---

## 使用方式

### 方式一：CLI 交互

```bash
source .ubuntuvenv/bin/activate
export OPENAI_API_KEY="sk-..."
plush-agent chat
```

进入交互模式后直接输入消息，`/quit` 退出。

### 方式二：HTTP API 服务

```bash
source .ubuntuvenv/bin/activate
export OPENAI_API_KEY="sk-..."
plush-agent serve
# 或指定参数：
plush-agent serve --host 127.0.0.1 --port 9000
```

启动后访问：
- API 文档：`http://localhost:8000/docs`
- ReDoc 文档：`http://localhost:8000/redoc`

---

## HTTP API 使用

### 聊天

```bash
# 发送消息
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，请介绍一下你自己"}'

# 指定 thread_id 维持对话上下文
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我写一个 SQL 查询", "thread_id": "my-session-1"}'
```

正常响应：

```json
{"status": "done", "content": "好的，请告诉我..."}
```

### 表单功能

Agent 会根据需要生成表单。当 Agent 调用 `form_generate` 工具时：

```bash
# 查看待填写表单
curl http://localhost:8000/api/forms

# 获取特定表单
curl http://localhost:8000/api/forms/form-a1b2c3d4

# 提交表单
curl -X POST http://localhost:8000/api/forms/form-a1b2c3d4/submit \
  -H "Content-Type: application/json" \
  -d '{"fields": {"username": "alice", "email": "alice@example.com"}}'
```

表单 JSON 结构示例：

```json
{
  "formId": "form-a1b2c3d4",
  "formName": "用户注册表",
  "formDescription": "填写注册信息",
  "fields": [
    {
      "fieldId": "username",
      "fieldName": "用户名",
      "fieldType": "text",
      "required": true,
      "placeholder": "请输入用户名",
      "value": null
    },
    {
      "fieldId": "email",
      "fieldName": "邮箱",
      "fieldType": "email",
      "required": true,
      "placeholder": "请输入邮箱地址",
      "value": null
    }
  ]
}
```

支持的 `fieldType`：`text`、`password`、`textarea`、`number`、`select`、`checkbox`、`radio`、`date`、`email`

### 审批功能

当工具调用需要人工审批时，chat 接口返回 `pending_approval`：

```json
{
  "status": "pending_approval",
  "approval_id": "uuid-xxx",
  "pending_actions": [
    {
      "name": "terminal",
      "arguments": {"commands": ["rm -rf /tmp/old"]},
      "description": "工具执行等待审批\nTool: terminal\nArgs: ...",
      "allowed_decisions": ["approve", "edit", "reject"]
    }
  ],
  "auto_approved_count": 0
}
```

处理审批：

```bash
# 查看所有待审批
curl http://localhost:8000/api/approvals

# 查看特定审批详情
curl http://localhost:8000/api/approvals/{approval_id}

# 批准执行
curl -X POST http://localhost:8000/api/approvals/{approval_id}/decide \
  -H "Content-Type: application/json" \
  -d '{"decisions": [{"type": "approve"}]}'

# 拒绝执行
curl -X POST http://localhost:8000/api/approvals/{approval_id}/decide \
  -H "Content-Type: application/json" \
  -d '{"decisions": [{"type": "reject", "message": "不要删除文件"}]}'

# 修改后执行
curl -X POST http://localhost:8000/api/approvals/{approval_id}/decide \
  -H "Content-Type: application/json" \
  -d '{"decisions": [{"type": "edit", "edited_action": {"name": "terminal", "args": {"commands": ["ls /tmp"]}}}]}'
```

多个待审批操作需要一次性提交所有决策：

```bash
curl -X POST http://localhost:8000/api/approvals/{approval_id}/decide \
  -H "Content-Type: application/json" \
  -d '{"decisions": [{"type": "approve"}, {"type": "reject", "message": "危险操作"}]}'
```

---

## Skills 管理

### 添加 Skill

1. 在 `.skill/` 下创建目录：

```bash
mkdir -p .skill/my-skill
```

2. 创建 `SKILL.md`：

```markdown
---
name: my-skill
description: "简要描述此 skill 的用途和触发条件"
---

# My Skill

## 使用场景
何时使用此 skill...

## 操作指南
1. 步骤一
2. 步骤二
```

3. 可选添加脚本和参考：

```bash
mkdir .skill/my-skill/scripts
mkdir .skill/my-skill/references
```

4. 重启 agent，新 skill 自动生效

### SKILL.md 规范

- **name**：1-64 字符，小写字母 + 数字 + 连字符，不能以连字符开头/结尾
- **description**：1-1024 字符，描述用途和触发条件
- body 内容无格式限制，写 agent 应遵循的指令

### name 校验规则

| 有效 | 无效 |
|------|------|
| `sql-assistant` | `SQL-Assistant`（大写） |
| `data-analysis` | `-analysis`（开头连字符） |
| `code-review` | `code--review`（连续连字符） |

---

## MCP 集成

### 配置 MCP Server

编辑 `mcp_servers.json`：

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

不需要 MCP 时保持 `{}`。

---

## 自动审批配置

在 `config.yaml` 中配置自动审批规则，匹配的工具调用将跳过人工审批：

```yaml
hitl:
  auto_approve:
    # 对 terminal 工具，匹配安全命令
    - tool: "terminal"
      args_patterns:
        - "^.*ls.*$"        # 列出文件
        - "^.*cat.*$"       # 查看文件
        - "^.*echo.*$"      # 输出文本
        - "^.*head.*$"      # 查看文件头部
        - "^.*grep.*$"      # 搜索文件内容

    # 对 form_generate 工具，全部自动通过
    - tool: "form_generate"
      args_patterns:
        - ".*"
```

规则匹配逻辑：
1. 工具名必须完全匹配 `tool` 字段
2. 参数值转为字符串后，对 `args_patterns` 中的正则逐一匹配
3. 任一正则匹配即自动通过

---

## 常见问题

### API Key 无效

```bash
# 检查环境变量
echo $OPENAI_API_KEY

# 设置环境变量
export OPENAI_API_KEY="sk-..."
```

### 端口被占用

```bash
plush-agent serve --port 9000
```

### Skill 未加载

1. 确认 `.skill/` 目录在运行目录下
2. 确认 SKILL.md 的 frontmatter 格式正确（`---` 包裹的 YAML）
3. 确认 `name` 和 `description` 字段存在

### Bash 命令执行失败

ShellTool 不支持 Windows 原生命令，需在 WSL 或 Linux 下运行。

### 表单超时

表单默认等待 5 分钟。可在 `config.yaml` 中调整：

```yaml
form:
  default_timeout: 600  # 10 分钟
```

或在 agent 调用时通过参数指定超时。
