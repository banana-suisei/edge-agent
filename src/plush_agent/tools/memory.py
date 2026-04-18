"""Long-term memory tools for reading/writing the LangGraph store."""

from __future__ import annotations

import time

from langchain.tools import ToolRuntime, tool

_MEMORY_NAMESPACE = ("users", "default")


@tool
def save_memory(key: str, value: str, runtime: ToolRuntime) -> str:
    """Save a piece of information to long-term memory for later recall.

    Use this when the user explicitly asks you to remember something, or when
    you detect important user preferences, facts, or context worth persisting.

    Args:
        key: A short descriptive identifier for this memory.
        value: The content to store.
    """
    assert runtime.store is not None
    runtime.store.put(_MEMORY_NAMESPACE, key, {"content": value})
    return f"Saved '{key}'."


@tool
def search_memory(query: str, runtime: ToolRuntime) -> str:
    """Search memories by semantic similarity.

    Args:
        query: Natural language query describing what you're looking for.
    """
    assert runtime.store is not None
    try:
        items = runtime.store.search(_MEMORY_NAMESPACE, query=query, limit=10)
    except Exception:
        return "Error searching memories."

    if not items:
        return f"No memories found for: {query}"

    lines = []
    for item in items:
        content = item.value.get("content", str(item.value)) if isinstance(item.value, dict) else str(item.value)
        lines.append(f"[{item.key}] {content}")
    return "\n".join(lines)


@tool
def get_memory(key: str, runtime: ToolRuntime) -> str:
    """Retrieve a specific memory by key.

    Args:
        key: The identifier used when saving.
    """
    assert runtime.store is not None
    item = runtime.store.get(_MEMORY_NAMESPACE, key)
    if item is None:
        return f"Memory '{key}' not found."
    content = item.value.get("content", str(item.value)) if isinstance(item.value, dict) else str(item.value)
    return content


@tool
def delete_memory(key: str, runtime: ToolRuntime) -> str:
    """Delete a specific memory by key.

    Args:
        key: The identifier used when saving.
    """
    assert runtime.store is not None
    item = runtime.store.get(_MEMORY_NAMESPACE, key)
    if item is None:
        return f"Memory '{key}' not found."
    runtime.store.delete(_MEMORY_NAMESPACE, key)
    return f"Deleted '{key}'."


memory_tools = [save_memory, search_memory, get_memory, delete_memory]

SESSION_SUMMARY_NAMESPACE = ("sessions",)
SESSION_SUMMARY_KEY = "latest"


def load_session_summary(store) -> str | None:
    """Load the previous session summary from the store, if any."""
    item = store.get(SESSION_SUMMARY_NAMESPACE, SESSION_SUMMARY_KEY)
    if item is None:
        return None
    return item.value.get("content") if isinstance(item.value, dict) else str(item.value)


def save_session_summary(store, messages: list, config) -> str | None:
    """Summarize a conversation and persist it to the long-term memory store.

    Called by CLI / Server when a session ends. Directly writes to the store,
    bypassing the agent tool pipeline.
    """
    if not messages:
        return None

    parts = []
    for m in messages:
        role = getattr(m, "type", str(getattr(m, "role", "unknown")))
        content = getattr(m, "content", "")
        if role in ("human", "ai") and content:
            parts.append(f"{role}: {content}")

    if not parts:
        return None

    text = "\n".join(parts)
    if len(text) > 8000:
        text = text[-8000:]

    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        base_url=config.model.base_url,
        api_key=config.model.api_key,
        model=config.model.model_name,
        temperature=0.3,
        max_tokens=512,
    )

    response = llm.invoke([
        SystemMessage(content="你是一个对话摘要助手。请用简洁的要点形式总结对话中的关键信息。"),
        HumanMessage(content=(
            "请总结以下对话的关键信息，包括用户需求、结论、重要偏好和上下文：\n\n"
            + text
        )),
    ])

    summary = response.content

    store.put(
        SESSION_SUMMARY_NAMESPACE,
        SESSION_SUMMARY_KEY,
        {
            "content": summary,
            "message_count": len(messages),
            "timestamp": time.time(),
        },
    )
    return summary
