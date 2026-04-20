from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage
from langchain.tools import tool

from plush_agent.skills.loader import SkillLoader


def _sanitize_surrogates(text: str) -> str:
    """Remove or replace lone surrogate codepoints."""
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")

_loader: SkillLoader | None = None


def _set_loader(loader: SkillLoader) -> None:
    global _loader
    _loader = loader


@tool
def load_skill(skill_name: str) -> str:
    """Load the full content of a skill into the agent's context.

    Use this when you need detailed instructions for handling a specific type of request.

    Args:
        skill_name: The name of the skill to load.
    """
    if _loader is None:
        return "Skill system not initialized."
    content = _loader.load_skill_content(skill_name)
    if content is None:
        available = ", ".join(_loader.skills.keys())
        return f"Skill '{skill_name}' not found. Available skills: {available}"
    return _sanitize_surrogates(f"Loaded skill: {skill_name}\n\n{content}")


class SkillMiddleware(AgentMiddleware):
    tools = [load_skill]

    def __init__(self, loader: SkillLoader) -> None:
        _set_loader(loader)
        self._loader = loader
        self._skills_prompt = loader.skill_descriptions_text()

    def _inject_skills_prompt(self, request: ModelRequest) -> ModelRequest:
        if not self._skills_prompt:
            return request
        addendum = _sanitize_surrogates(
            "\n\n## Available Skills\n\n"
            f"{self._skills_prompt}\n\n"
            "Use the load_skill tool when you need detailed information "
            "about handling a specific type of request."
        )
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": addendum},
        ]
        new_system = SystemMessage(content=new_content)
        return request.override(system_message=new_system)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._inject_skills_prompt(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._inject_skills_prompt(request))
