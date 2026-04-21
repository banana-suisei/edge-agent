# Feature: Skills 系统

## 文件

- `src/plush_agent/skills/loader.py` — `SkillLoader`：扫描 .skill/ 目录，解析 SKILL.md
- `src/plush_agent/skills/middleware.py` — `SkillMiddleware` + `load_skill` tool

## 规范

遵循 [agentskills.io](https://agentskills.io/specification) 规范。每个 skill 是 `.skill/` 下的一个目录，包含必需的 `SKILL.md`。

### SKILL.md 格式

```markdown
---
name: my-skill
description: "何时使用此 skill 的描述"
license: Apache-2.0                    # 可选
compatibility: "Requires Python 3.12+"  # 可选
metadata:                              # 可选
  author: team-a
  version: "1.0"
allowed-tools: Bash(git:*) Read        # 可选（实验性）
---

# Skill 指令正文

这里写 agent 加载 skill 后应遵循的详细指令...
```

**必需字段**：`name`（1-64 字符，小写+连字符）、`description`（1-1024 字符）
**可选目录**：`scripts/`、`references/`、`assets/`

### name 校验规则

- 仅允许小写字母 `a-z`、数字、连字符 `-`
- 不能以连字符开头或结尾
- 不能有连续连字符 `--`
- 必须匹配父目录名

## 渐进式披露 (Progressive Disclosure)

```
启动时                    按需加载
───────                  ─────────
SkillLoader 扫描          Agent 调用 load_skill tool
  → 仅读取 frontmatter     → 返回完整 SKILL.md 内容
  → 提取 name+description    → 含 body 指令
                            → 可引用 scripts/、references/

SkillMiddleware            load_skill tool
  → 注入到 system prompt    → 将内容作为 ToolMessage 返回
  → 仅 name+description       → agent 在后续对话中使用
```

## SkillLoader

```python
class SkillLoader:
    def __init__(self, skills_dir: str | Path)
    @property
    def skills(self) -> dict[str, SkillMeta]          # name → meta 映射
    def load_skill_content(self, skill_name: str) -> str | None  # 完整内容
    def skill_descriptions_text(self) -> str           # Markdown 列表
```

**初始化时**扫描 `skills_dir` 下所有子目录，检查是否存在 `SKILL.md`，解析 YAML frontmatter。格式错误或缺少必需字段的 skill 会被静默跳过。

**`load_skill_content()`** 按需读取完整 SKILL.md 内容（含 frontmatter 和 body）。

## SkillMiddleware

```python
class SkillMiddleware(AgentMiddleware):

    def _inject_skills_prompt(self, request: ModelRequest) -> ModelRequest:
        # 在 system prompt 末尾追加 skill 描述列表（共享逻辑）

    def wrap_model_call(self, request, handler) -> ModelResponse:
        # 同步路径：handler 是同步函数
        return handler(self._inject_skills_prompt(request))

    async def awrap_model_call(self, request, handler) -> ModelResponse:
        # 异步路径：handler 是 async 函数
        return await handler(self._inject_skills_prompt(request))
```

`SkillMiddleware` 仅负责注入 skill 描述到 system prompt，不声明 `tools`。`load_skill` 工具通过 `_collect_tools()` 作为常规工具注册到 `create_agent`，确保走标准 tool node 被 `HumanInTheLoopMiddleware` 拦截审批。

`wrap_model_call` 和 `awrap_model_call` 在每次 LLM 调用前执行，将所有 skill 的 name+description 追加到 system message 的 content blocks 中。两者共享 `_inject_skills_prompt()` 逻辑。异步版本 (`awrap_model_call`) 是必须的——使用 `astream()` 或 `ainvoke()` 时 LangGraph 调用 async 版本，未实现会抛出 `NotImplementedError`。追加的文本会通过 `_sanitize_surrogates()` 清理，防止代理字符污染 API 请求。

## load_skill tool

```python
@tool
def load_skill(skill_name: str) -> str:
    """加载指定 skill 的完整内容"""
```

- 成功：返回 `"Loaded skill: {name}\n\n{full SKILL.md content}"`（经 `_sanitize_surrogates()` 清理）
- 未找到：返回 `"Skill '{name}' not found. Available skills: a, b, c"`

`load_skill` 在 `agent.py` 的 `_collect_tools()` 中作为常规工具注册，会正常被 `HumanInTheLoopMiddleware` 拦截。`_loader` 是模块级全局变量，通过 `_set_loader()` 在 `SkillMiddleware.__init__` 时设置。这是因为 `@tool` 装饰的函数无法通过构造函数注入依赖。

## 添加新 Skill

1. 在 `.skill/` 下创建目录：`mkdir .skill/my-new-skill`
2. 创建 `SKILL.md`：
   ```markdown
   ---
   name: my-new-skill
   description: "描述此 skill 做什么以及何时使用"
   ---

   # 指令内容
   ...
   ```
3. 可选添加 `scripts/`、`references/`、`assets/`
4. 重启 agent，新 skill 自动出现在 system prompt

## 调试定位

| 问题 | 定位 |
|------|------|
| Skill 未被发现 | `SkillLoader._scan_skills()` — 检查目录名、SKILL.md 存在性、frontmatter 格式 |
| Frontmatter 解析失败 | `SkillLoader._parse_frontmatter()` — YAML 语法错误或缺少 name/description |
| load_skill 返回 not found | `load_skill()` — 检查 name 与目录名是否一致 |
| 描述未出现在 system prompt | `SkillMiddleware.wrap_model_call()` — 检查 `_skills_prompt` 是否为空 |
| `UnicodeEncodeError: surrogates not allowed` | `_sanitize_surrogates()` 清理 skill 描述和内容中的代理字符 |
