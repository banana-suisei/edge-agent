from __future__ import annotations

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from plush_agent.config import Config
from plush_agent.skills.loader import SkillLoader
from plush_agent.skills.middleware import SkillMiddleware

from pydantic import SecretStr


def build_agent(config: Config):
    model = ChatOpenAI(
        base_url=config.model.base_url,
        api_key=SecretStr(config.model.api_key),
        model=config.model.model_name,
        temperature=config.model.temperature,
        max_tokens=config.model.max_tokens,
    )

    skills_loader = SkillLoader(config.agent.skills_dir)
    skill_middleware = SkillMiddleware(skills_loader)

    tools = _collect_tools(config)

    store = InMemoryStore()
    checkpointer = InMemorySaver()

    agent = create_agent(
        model,
        tools=tools,
        system_prompt=config.agent.system_prompt,
        middleware=[skill_middleware],
        store=store,
        checkpointer=checkpointer,
    )

    return agent, store, checkpointer


def _collect_tools(config: Config):
    from plush_agent.tools.bash import bash_tool
    from plush_agent.tools.form import create_form_generate_tool
    from plush_agent.skills.middleware import load_skill

    tools = [bash_tool, load_skill, create_form_generate_tool(config)]
    return tools
