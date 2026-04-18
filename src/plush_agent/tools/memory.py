"""Long-term memory tools for reading/writing the LangGraph store."""

from __future__ import annotations

import re
import time

from langchain.tools import ToolRuntime, tool

_RE_KEYWORD = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


def _normalize_keywords(raw: str) -> list[str]:
    """Normalize keyword string: convert spaces/slashes to commas, lowercase, validate.

    Returns only keywords matching [a-z0-9][a-z0-9-]* (lowercase English / Roman-numeral style).
    """
    normalized = raw.replace(" ", ",").replace("/", ",")
    keywords = [kw.strip().lower() for kw in normalized.split(",")]
    return [kw for kw in keywords if kw and _RE_KEYWORD.match(kw)]


@tool
def save_memory(namespace: str, key: str, value: str, runtime: ToolRuntime) -> str:
    """Save a piece of information to long-term memory for later recall.

    Use this when the user explicitly asks you to remember something, or when
    you detect important user preferences, facts, or context worth persisting.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: 3-5 comma-separated lowercase English keywords summarizing the content.
            Spaces and slashes are automatically converted to commas.
            Example: "python,data-analysis,preference".
        value: The content to store.
    """
    assert runtime.store is not None
    keywords = _normalize_keywords(key)
    if not keywords:
        return "Invalid key: must contain at least one lowercase English keyword (letters, digits, hyphens)."
    normalized_key = ",".join(keywords)
    ns = tuple(namespace.split("/"))
    runtime.store.put(ns, normalized_key, {"content": value})
    return f"Saved '{normalized_key}' under '{namespace}'."


@tool
def search_memory(namespace: str, query: str, runtime: ToolRuntime) -> str:
    """Search for memories in a namespace. Returns matching items sorted by keyword relevance.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123".
        query: 3-5 comma-separated lowercase English keywords.
            Spaces and slashes are automatically converted to commas.
            Returns memories matching ANY keyword, sorted by match count (most first).
            Example: "python,programming" finds memories tagged with either keyword.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    query_keywords = list(dict.fromkeys(_normalize_keywords(query)))  # deduplicated
    if not query_keywords:
        return "Invalid query: must contain at least one lowercase English keyword."

    query_set = set(query_keywords)

    # Single DB call: fetch all items in namespace (no query = list mode)
    try:
        all_items = runtime.store.search(ns, limit=1000)
    except Exception:
        return f"Error searching '{namespace}'."

    if not all_items:
        return f"No memories found in '{namespace}'."

    # Filter & score: matched_query_keywords / total_query_keywords
    scored = []
    for item in all_items:
        item_keywords = set(_normalize_keywords(item.key))
        matched_count = len(query_set & item_keywords)
        if matched_count == 0:
            continue
        score = matched_count / len(query_set)
        scored.append((item, score, matched_count))

    if not scored:
        return f"No memories found in '{namespace}' for keywords: {', '.join(query_keywords)}."

    scored.sort(key=lambda x: (x[1], x[2]), reverse=True)
    scored = scored[:50]

    lines = []
    for item, _score, _count in scored:
        content = item.value.get("content", str(item.value)) if isinstance(item.value, dict) else str(item.value)
        lines.append(f"[{item.key}] {content}")
    return "\n".join(lines)


@tool
def get_memory(namespace: str, key: str, runtime: ToolRuntime) -> str:
    """Retrieve a specific memory by namespace and key.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: Comma-separated lowercase English keywords used when saving.
    """
    assert runtime.store is not None
    keywords = _normalize_keywords(key)
    normalized_key = ",".join(keywords) if keywords else key
    ns = tuple(namespace.split("/"))
    item = runtime.store.get(ns, normalized_key)
    if item is None:
        return f"Memory '{normalized_key}' not found in '{namespace}'."
    content = item.value.get("content", str(item.value)) if isinstance(item.value, dict) else str(item.value)
    return content


@tool
def delete_memory(namespace: str, key: str, runtime: ToolRuntime) -> str:
    """Delete a specific memory by namespace and key.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: Comma-separated lowercase English keywords used when saving.
    """
    assert runtime.store is not None
    keywords = _normalize_keywords(key)
    normalized_key = ",".join(keywords) if keywords else key
    ns = tuple(namespace.split("/"))
    item = runtime.store.get(ns, normalized_key)
    if item is None:
        return f"Memory '{normalized_key}' not found in '{namespace}'."
    runtime.store.delete(ns, normalized_key)
    return f"Deleted '{normalized_key}' from '{namespace}'."


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
