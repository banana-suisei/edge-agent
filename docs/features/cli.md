# Feature: CLI

## 文件

- `src/plush_agent/cli.py` — click 命令定义

## 命令

### plush-agent chat

启动交互式命令行对话。

```bash
plush-agent chat
plush-agent --config my-config.yaml chat
```

流程：
1. 加载配置文件
2. `build_agent(config)` 创建 agent
3. 使用固定 `thread_id = "cli-session"` 维持对话上下文
4. 循环读取用户输入，`_sanitize_surrogates()` 清理编码后，`agent.invoke()` 获取回复
5. 输入 `/quit` 或空行退出

### WSL2 编码处理

`_sanitize_surrogates(text)` 清除 WSL2 + Windows Terminal 中文 IME 输入时可能泄漏的孤立 UTF-16 代理字符（surrogate codepoints）。这些字符会导致 OpenAI 客户端 JSON 序列化时抛出 `UnicodeEncodeError: surrogates not allowed`。

```python
def _sanitize_surrogates(text: str) -> str:
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
```

在用户输入传入 agent 前调用，对正常文本无影响。

**注意**：CLI 模式下不支持 HITL 审批（不经过 ApprovalHandler），tool 直接执行。

### plush-agent serve

启动 HTTP 服务。

```bash
plush-agent serve
plush-agent serve --host 127.0.0.1 --port 9000
plush-agent --config prod.yaml serve
```

流程：
1. 加载配置文件
2. `build_agent(config)` 创建 agent
3. 创建 `ApprovalHandler(agent, config)`
4. 创建 FastAPI 应用，设置 `app.state.approval_handler`
5. `uvicorn.run()` 启动服务

### --config 选项

全局选项，指定配置文件路径，默认 `config.yaml`。

## 安装后使用

```bash
pip install -e .
plush-agent --help
plush-agent chat --help
plush-agent serve --help
```

## 调试定位

| 问题 | 定位 |
|------|------|
| 命令未找到 | 检查 `pip install -e .` 是否成功 |
| API key 错误 | 环境变量是否设置（`echo $OPENAI_API_KEY`） |
| 配置文件找不到 | 使用 `--config` 指定绝对路径 |
| `UnicodeEncodeError: surrogates not allowed` | WSL2 中文输入编码问题，`_sanitize_surrogates()` 已处理 |
