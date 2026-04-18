"""Long-term memory tools for reading/writing the LangGraph store."""

from __future__ import annotations

from langchain.tools import ToolRuntime, tool


@tool
def save_memory(namespace: str, key: str, value: str, runtime: ToolRuntime) -> str:
    """Save a piece of information to long-term memory for later recall.

    Use this when the user explicitly asks you to remember something, or when
    you detect important user preferences, facts, or context worth persisting.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: Unique identifier within the namespace, e.g. "language".
        value: The content to store.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    runtime.store.put(ns, key, {"content": value})
    return f"Saved '{key}' under '{namespace}'."


@tool
def search_memory(namespace: str, query: str, runtime: ToolRuntime) -> str:
    """Search for memories in a namespace. Returns matching items.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123".
        query: Search query to filter results.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    items = runtime.store.search(ns, query=query)
    if not items:
        return f"No memories found in '{namespace}'."
    results = []
    for item in items:
        content = item.value.get("content", str(item.value)) if isinstance(item.value, dict) else str(item.value)
        results.append(f"[{item.key}] {content}")
    return "\n".join(results)


@tool
def get_memory(namespace: str, key: str, runtime: ToolRuntime) -> str:
    """Retrieve a specific memory by namespace and key.

    Args:
        namespace: Hierarchical path separated by '/', e.g. "users/user_123/preferences".
        key: The key to look up.
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
        key: The key to delete.
    """
    assert runtime.store is not None
    ns = tuple(namespace.split("/"))
    item = runtime.store.get(ns, key)
    if item is None:
        return f"Memory '{key}' not found in '{namespace}'."
    runtime.store.delete(ns, key)
    return f"Deleted '{key}' from '{namespace}'."


memory_tools = [save_memory, search_memory, get_memory, delete_memory]
