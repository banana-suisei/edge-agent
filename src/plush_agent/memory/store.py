from __future__ import annotations

from langgraph.store.memory import InMemoryStore


def create_store() -> InMemoryStore:
    return InMemoryStore()
