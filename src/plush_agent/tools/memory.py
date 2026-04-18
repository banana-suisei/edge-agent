"""Long-term memory tools for reading/writing the LangGraph store."""

from __future__ import annotations

import time

from langchain.tools import ToolRuntime, tool


@tool
def save_memory(namespace: str, key: str, value: str, runtime: ToolRuntime) -> str:
    """Save a piece of information to long-term memory for later recall.

    Use this when the user explicitly asks you to remember something, or when
    you detect important user preferences, facts, or context worth persisting.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: A short descriptive identifier for this memory.
        value: The content to store.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    runtime.store.put(ns, key, {"content": value})
    return f"Saved '{key}' under '{namespace}'."


@tool
def search_memory(namespace: str, query: str, runtime: ToolRuntime) -> str:
    """Search memories by semantic similarity.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123".
        query: Natural language query describing what you're looking for.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    try:
        items = runtime.store.search(ns, query=query, limit=10)
    except Exception:
        return f"Error searching '{namespace}'."

    if not items:
        return f"No memories found in '{namespace}' for: {query}"

    lines = []
    for item in items:
        content = item.value.get("content", str(item.value)) if isinstance(item.value, dict) else str(item.value)
        lines.append(f"[{item.key}] {content}")
    return "\n".join(lines)


@tool
def get_memory(namespace: str, key: str, runtime: ToolRuntime) -> str:
    """Retrieve a specific memory by namespace and key.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: The identifier used when saving.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    item = runtime.store.get(ns, key)
    if item is None:
        return f"Memory '{key}' not found in '{namespace}'."
    content = item.value.get("content", str(item.value)) if isinstance(item.value, dict) else str(item.value)
    return content


@tool
def delete_memory(namespace: str, key: str, runtime: ToolRuntime) -> str:
    """Delete a specific memory by namespace and key.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: The identifier used when saving.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    item = runtime.store.get(ns, key)
    if item is None:
        return f"Memory '{key}' not found in '{namespace}'."
    runtime.store.delete(ns, key)
    return f"Deleted '{key}' from '{namespace}'."


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
