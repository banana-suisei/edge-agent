"""Tests for memory tools (save_memory, search_memory, get_memory, delete_memory)
and the underlying store operations (InMemoryStore and PostgresStore with vector index)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from plush_agent.config import EmbeddingConfig, MemoryConfig, PostgresConfig
from plush_agent.memory.store import create_store
from plush_agent.tools.memory import (
    delete_memory,
    get_memory,
    save_memory,
    search_memory,
)


# ---------------------------------------------------------------------------
# Helpers – fake ToolRuntime with a real store
# ---------------------------------------------------------------------------

def _make_runtime(store):
    """Create a fake ToolRuntime that exposes a real store."""
    runtime = MagicMock()
    runtime.store = store
    return runtime


# ===========================================================================
# Store-level tests (InMemoryStore + PostgresStore)
# ===========================================================================


class TestInMemoryStore:
    def test_create_in_memory(self):
        store = create_store(MemoryConfig(type="in_memory"))
        assert store is not None

    def test_in_memory_put_and_get(self):
        store = create_store(MemoryConfig(type="in_memory"))
        store.put(("test",), "key1", {"content": "hello"})
        item = store.get(("test",), "key1")
        assert item is not None
        assert item.value == {"content": "hello"}

    def test_in_memory_search_list(self):
        store = create_store(MemoryConfig(type="in_memory"))
        store.put(("users",), "u1", {"content": "Alice"})
        store.put(("users",), "u2", {"content": "Bob"})
        items = store.search(("users",))
        assert len(items) == 2

    def test_in_memory_delete(self):
        store = create_store(MemoryConfig(type="in_memory"))
        store.put(("test",), "k1", {"content": "data"})
        store.delete(("test",), "k1")
        assert store.get(("test",), "k1") is None

    def test_in_memory_get_missing(self):
        store = create_store(MemoryConfig(type="in_memory"))
        assert store.get(("nonexistent",), "missing") is None


class TestPostgresStore:
    @pytest.fixture(autouse=True)
    def cleanup_test_data(self, pg_config):
        yield
        store = create_store(pg_config)
        for ns in [("test",), ("users",), ("ns1",), ("ns2",), ("prefs",), ("notes",), ("semantic",)]:
            items = store.search(ns)
            for item in items:
                store.delete(ns, item.key)

    @pytest.fixture
    def pg_config(self):
        return MemoryConfig(
            type="postgres",
            postgres=PostgresConfig(
                host="192.168.1.172",
                port=5432,
                user="postgres",
                password="y8@pjqEgJztupe9Z3Dd4",
                database="plush_agent",
                sslmode="disable",
            ),
            embedding=EmbeddingConfig(
                base_url="https://openrouter.ai/api/v1",
                api_key_env="your_api_key_here",
                model_name="qwen/qwen3-embedding-8b",
                dims=4096,
                distance_type="cosine",
            ),
        )

    def test_create_postgres(self, pg_config):
        store = create_store(pg_config)
        assert store is not None

    def test_postgres_put_and_get(self, pg_config):
        store = create_store(pg_config)
        store.put(("test",), "key1", {"content": "hello"})
        item = store.get(("test",), "key1")
        assert item is not None
        assert item.value == {"content": "hello"}

    def test_postgres_search_list(self, pg_config):
        store = create_store(pg_config)
        store.put(("users",), "u1", {"content": "Alice"})
        store.put(("users",), "u2", {"content": "Bob"})
        items = store.search(("users",))
        assert len(items) >= 2

    def test_postgres_delete(self, pg_config):
        store = create_store(pg_config)
        store.put(("test",), "k1", {"content": "data"})
        store.delete(("test",), "k1")
        assert store.get(("test",), "k1") is None

    def test_postgres_overwrite(self, pg_config):
        store = create_store(pg_config)
        store.put(("test",), "k1", {"content": "v1"})
        store.put(("test",), "k1", {"content": "v2"})
        item = store.get(("test",), "k1")
        assert item.value == {"content": "v2"}

    def test_postgres_namespaces_isolated(self, pg_config):
        store = create_store(pg_config)
        store.put(("ns1",), "k", {"content": "from ns1"})
        store.put(("ns2",), "k", {"content": "from ns2"})
        assert store.get(("ns1",), "k").value == {"content": "from ns1"}
        assert store.get(("ns2",), "k").value == {"content": "from ns2"}

    def test_postgres_vector_search(self, pg_config):
        """Verify semantic search returns relevant results by vector similarity."""
        store = create_store(pg_config)

        store.put(("semantic",), "item1", {"content": "Python is my favorite programming language"})
        store.put(("semantic",), "item2", {"content": "I love eating sushi and ramen"})
        store.put(("semantic",), "item3", {"content": "The weather is sunny today"})

        items = store.search(("semantic",), query="coding and software development", limit=3)
        assert len(items) >= 1
        # item1 should rank first for a coding query
        assert items[0].key == "item1"

    def test_postgres_vector_search_chinese(self, pg_config):
        """Verify semantic search works with Chinese content and queries."""
        store = create_store(pg_config)

        store.put(("semantic",), "vtuber", {"content": "星街彗星、白上吹雪、神乐七奈是我最喜欢的VTuber"})
        store.put(("semantic",), "food", {"content": "我喜欢吃火锅和烧烤"})
        store.put(("semantic",), "work", {"content": "今天要完成项目报告"})

        items = store.search(("semantic",), query="喜欢的虚拟主播", limit=3)
        assert len(items) >= 1
        assert items[0].key == "vtuber"


# ===========================================================================
# Memory-tools unit tests (using InMemoryStore)
# ===========================================================================


class TestMemoryToolsInMemory:
    """Unit tests for save_memory / search_memory / get_memory / delete_memory
    backed by InMemoryStore."""

    @pytest.fixture
    def store(self):
        return create_store(MemoryConfig(type="in_memory"))

    @pytest.fixture
    def rt(self, store):
        return _make_runtime(store)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _save(namespace, key, value, rt):
        return save_memory.func(namespace=namespace, key=key, value=value, runtime=rt)

    @staticmethod
    def _get(namespace, key, rt):
        return get_memory.func(namespace=namespace, key=key, runtime=rt)

    @staticmethod
    def _search(namespace, query, rt):
        return search_memory.func(namespace=namespace, query=query, runtime=rt)

    @staticmethod
    def _delete(namespace, key, rt):
        return delete_memory.func(namespace=namespace, key=key, runtime=rt)

    # -- save_memory --------------------------------------------------------

    def test_save_and_get(self, rt, store):
        result = self._save("users/u1", "favorite language", "Python", rt)
        assert "Saved" in result
        item = store.get(("users", "u1"), "favorite language")
        assert item.value == {"content": "Python"}

    def test_save_natural_language_key(self, rt, store):
        result = self._save("prefs", "favorite vtubers", "星街彗星、白上吹雪", rt)
        assert "Saved" in result
        item = store.get(("prefs",), "favorite vtubers")
        assert item is not None

    def test_save_overwrites(self, rt, store):
        self._save("test", "k", "v1", rt)
        self._save("test", "k", "v2", rt)
        item = store.get(("test",), "k")
        assert item.value == {"content": "v2"}

    # -- get_memory ---------------------------------------------------------

    def test_get_existing(self, rt, store):
        store.put(("prefs",), "lang", {"content": "Python"})
        result = self._get("prefs", "lang", rt)
        assert result == "Python"

    def test_get_missing(self, rt):
        result = self._get("missing", "nope", rt)
        assert "not found" in result

    # -- search_memory ------------------------------------------------------

    def test_search_returns_results(self, rt, store):
        store.put(("notes",), "n1", {"content": "Buy milk"})
        store.put(("notes",), "n2", {"content": "Buy eggs"})
        result = self._search("notes", "shopping", rt)
        assert "n1" in result
        assert "n2" in result

    def test_search_empty(self, rt):
        result = self._search("empty", "anything", rt)
        assert "No memories found" in result

    # -- delete_memory ------------------------------------------------------

    def test_delete_existing(self, rt, store):
        store.put(("test",), "k1", {"content": "to delete"})
        result = self._delete("test", "k1", rt)
        assert "Deleted" in result
        assert store.get(("test",), "k1") is None

    def test_delete_missing(self, rt):
        result = self._delete("nope", "nope", rt)
        assert "not found" in result


# ===========================================================================
# Memory-tools integration tests (PostgresStore with vector index)
# ===========================================================================


class TestMemoryToolsPostgres:
    """Integration tests verifying the tools work end-to-end with PostgresStore."""

    @pytest.fixture(autouse=True)
    def cleanup(self, pg_config):
        yield
        store = create_store(pg_config)
        for ns in [("users", "u1"), ("prefs",), ("notes",), ("test",), ("semantic",)]:
            items = store.search(ns)
            for item in items:
                store.delete(ns, item.key)

    @pytest.fixture
    def pg_config(self):
        return MemoryConfig(
            type="postgres",
            postgres=PostgresConfig(
                host="192.168.1.172",
                port=5432,
                user="postgres",
                password="y8@pjqEgJztupe9Z3Dd4",
                database="plush_agent",
                sslmode="disable",
            ),
            embedding=EmbeddingConfig(
                base_url="https://openrouter.ai/api/v1",
                api_key_env="your_api_key_here",
                model_name="qwen/qwen3-embedding-8b",
                dims=4096,
                distance_type="cosine",
            ),
        )

    @pytest.fixture
    def store(self, pg_config):
        return create_store(pg_config)

    @pytest.fixture
    def rt(self, store):
        return _make_runtime(store)

    def test_save_and_retrieve(self, rt, store):
        save_memory.func(namespace="users/u1", key="name", value="Bob", runtime=rt)
        item = store.get(("users", "u1"), "name")
        assert item is not None
        assert item.value == {"content": "Bob"}

    def test_get_returns_content(self, rt, store):
        store.put(("prefs",), "theme", {"content": "dark"})
        result = get_memory.func(namespace="prefs", key="theme", runtime=rt)
        assert result == "dark"

    def test_search_semantic_finds_items(self, rt, store):
        store.put(("notes",), "meeting", {"content": "Team meeting at 3pm in conference room"})
        store.put(("notes",), "grocery", {"content": "Buy milk, eggs, and bread from supermarket"})
        result = search_memory.func(namespace="notes", query="work schedule", runtime=rt)
        assert "meeting" in result

    def test_delete_removes_from_postgres(self, rt, store):
        store.put(("test",), "k1", {"content": "temporary"})
        delete_memory.func(namespace="test", key="k1", runtime=rt)
        assert store.get(("test",), "k1") is None

    def test_full_lifecycle(self, rt, store):
        """save -> get -> search -> delete -> get (not found)."""
        save_memory.func(namespace="users/u1", key="lang", value="Rust", runtime=rt)

        got = get_memory.func(namespace="users/u1", key="lang", runtime=rt)
        assert got == "Rust"

        found = search_memory.func(namespace="users/u1", query="programming language", runtime=rt)
        assert "lang" in found

        delete_memory.func(namespace="users/u1", key="lang", runtime=rt)
        got2 = get_memory.func(namespace="users/u1", key="lang", runtime=rt)
        assert "not found" in got2
