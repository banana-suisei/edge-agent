# Feature: CLI

## 文件

- `src/plush_agent/cli.py` — click 命令定义

## 命令

### plush-agent chat

启动交互式命令行对话。

```bash
plush-agent chat
plush-agent chat --stream
plush-agent --config my-config.yaml chat
```

`--stream` 启用流式输出：LLM 生成的 token 实时打印到终端，无需等待完整响应。

流程：
1. 加载配置文件
2. `build_agent(config)` 创建 agent（含 `HumanInTheLoopMiddleware`）
3. `load_session_summary(store)` 加载上次对话摘要（如有）
4. 使用固定 `thread_id = "cli-session"` 维持对话上下文
5. 循环读取用户输入，`_sanitize_surrogates()` 清理编码后发送给 agent
6. **阻塞模式**：`agent.invoke(version="v2")` 获取回复，`_handle_cli_hitl()` 处理中断，`_print_reply()` 显示
7. **流式模式** (`--stream`)：`_stream_cli()` 使用 `agent.astream(stream_mode=["messages","updates"])` 实时输出 token
   - HITL 中断检测：`__interrupt__` 位于 `updates` chunk 的 `data` 顶层键
   - 自动审批：匹配规则的工具自动通过，循环调用 `astream(Command(resume=...))` 继续流式输出
   - 人工审批：中断流式输出，回退到 `_handle_cli_hitl()` 同步处理后续
8. 输入 `/quit` 或 Ctrl+C/EOF 退出
9. 退出前 `save_session_summary()` 保存对话摘要到 store

### 会话记忆加载

首条消息前，自动加载上次的对话摘要并注入：

```python
prev_summary = load_session_summary(store)
if prev_summary and not all_messages:
    enriched = f"[上一次对话摘要]\n{prev_summary}\n[当前消息]\n{user_input}"
```

后续消息不再注入摘要，仅包含用户原始输入。

### 会话记忆保存

退出时（`/quit`、Ctrl+C、EOF）自动保存：

```python
finally:
    if all_messages:
        save_session_summary(store, all_messages, config)
```

摘要写入固定位置 `("sessions",) / "latest"`。

### HITL 交互审批

CLI 模式下 `HumanInTheLoopMiddleware` 同样生效。`_handle_cli_hitl()` 先检查自动审批规则，匹配则直接通过，不匹配才弹交互提示：

```
⚠ Tool call requires approval:
  Tool: terminal
  Args: {"commands": "rm -rf /tmp"}
  Approve? [y/r(eject)]: r
  Rejection reason: 不要删除文件
```

自动审批通过 `_should_auto_approve(config, tool_name, tool_args)` 实现，与 HTTP 模式的 `ApprovalHandler.should_auto_approve()` 共享相同的正则匹配逻辑和 `config.yaml` 规则。

记忆工具（save_memory / search_memory / get_memory / delete_memory）如需自动通过，需在 `config.yaml` 的 `hitl.auto_approve` 中配置对应规则。

### WSL2 编码处理

`_sanitize_surrogates(text)` 清除 WSL2 + Windows Terminal 中文 IME 输入时可能泄漏的孤立 UTF-16 代理字符（surrogate codepoints）。这些字符会导致 OpenAI 客户端 JSON 序列化时抛出 `UnicodeEncodeError: surrogates not allowed`。

```python
def _sanitize_surrogates(text: str) -> str:
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
```

在用户输入传入 agent 前调用，对正常文本无影响。

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
4. 创建 FastAPI 应用，设置 `app.state`（approval_handler、store、checkpointer、config）
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
| 记忆工具仍需手动审批 | 检查 `config.yaml` 中 `hitl.auto_approve` 是否包含记忆工具 |
| 上次对话摘要未加载 | 检查 store 中 `("sessions",) / "latest"` 是否有数据 |
| 流式模式工具调用后无回复 | `_stream_cli()` 中 `__interrupt__` 检测或自动审批 resume 逻辑 |
| `awrap_model_call` NotImplementedError | `SkillMiddleware` 需要 async 版本支持 `astream()` |
