"""Tests for memory tools (save_memory, search_memory, get_memory, delete_memory)
and the underlying store operations (InMemoryStore and PostgresStore with vector index)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from plush_agent.config import EmbeddingConfig, MemoryConfig, PostgresConfig
from plush_agent.memory.store import create_store
from plush_agent.tools.memory import (
    SESSION_SUMMARY_NAMESPACE,
    SESSION_SUMMARY_KEY,
    _MEMORY_NAMESPACE,
    delete_memory,
    get_memory,
    load_session_summary,
    save_memory,
    save_session_summary,
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
        for ns in [_MEMORY_NAMESPACE, ("test",), ("ns1",), ("ns2",)]:
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
        store.put(("test",), "u1", {"content": "Alice"})
        store.put(("test",), "u2", {"content": "Bob"})
        items = store.search(("test",))
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

    def test_postgres_vector_search(self, pg_config):
        store = create_store(pg_config)

        store.put(("test",), "item1", {"content": "Python is my favorite programming language"})
        store.put(("test",), "item2", {"content": "I love eating sushi and ramen"})
        store.put(("test",), "item3", {"content": "The weather is sunny today"})

        items = store.search(("test",), query="coding and software development", limit=3)
        assert len(items) >= 1
        assert items[0].key == "item1"

    def test_postgres_vector_search_chinese(self, pg_config):
        store = create_store(pg_config)

        store.put(("test",), "vtuber", {"content": "星街彗星、白上吹雪、神乐七奈是我最喜欢的VTuber"})
        store.put(("test",), "food", {"content": "我喜欢吃火锅和烧烤"})
        store.put(("test",), "work", {"content": "今天要完成项目报告"})

        items = store.search(("test",), query="喜欢的虚拟主播", limit=3)
        assert len(items) >= 1
        assert items[0].key == "vtuber"


# ===========================================================================
# Memory-tools unit tests (using InMemoryStore)
# ===========================================================================


class TestMemoryToolsInMemory:
    @pytest.fixture
    def store(self):
        return create_store(MemoryConfig(type="in_memory"))

    @pytest.fixture
    def rt(self, store):
        return _make_runtime(store)

    @staticmethod
    def _save(key, value, rt):
        return save_memory.func(key=key, value=value, runtime=rt)

    @staticmethod
    def _get(key, rt):
        return get_memory.func(key=key, runtime=rt)

    @staticmethod
    def _search(query, rt):
        return search_memory.func(query=query, runtime=rt)

    @staticmethod
    def _delete(key, rt):
        return delete_memory.func(key=key, runtime=rt)

    def test_save_and_get(self, rt, store):
        result = self._save("favorite language", "Python", rt)
        assert "Saved" in result
        item = store.get(_MEMORY_NAMESPACE, "favorite language")
        assert item.value == {"content": "Python"}

    def test_save_overwrites(self, rt, store):
        self._save("k", "v1", rt)
        self._save("k", "v2", rt)
        item = store.get(_MEMORY_NAMESPACE, "k")
        assert item.value == {"content": "v2"}

    def test_get_existing(self, rt, store):
        store.put(_MEMORY_NAMESPACE, "lang", {"content": "Python"})
        result = self._get("lang", rt)
        assert result == "Python"

    def test_get_missing(self, rt):
        result = self._get("nope", rt)
        assert "not found" in result

    def test_search_returns_results(self, rt, store):
        store.put(_MEMORY_NAMESPACE, "n1", {"content": "Buy milk"})
        store.put(_MEMORY_NAMESPACE, "n2", {"content": "Buy eggs"})
        result = self._search("shopping", rt)
        assert "n1" in result
        assert "n2" in result

    def test_search_empty(self, rt):
        result = self._search("anything", rt)
        assert "No memories found" in result

    def test_delete_existing(self, rt, store):
        store.put(_MEMORY_NAMESPACE, "k1", {"content": "to delete"})
        result = self._delete("k1", rt)
        assert "Deleted" in result
        assert store.get(_MEMORY_NAMESPACE, "k1") is None

    def test_delete_missing(self, rt):
        result = self._delete("nope", rt)
        assert "not found" in result


# ===========================================================================
# Memory-tools integration tests (PostgresStore with vector index)
# ===========================================================================


class TestMemoryToolsPostgres:
    @pytest.fixture(autouse=True)
    def cleanup(self, pg_config):
        yield
        store = create_store(pg_config)
        items = store.search(_MEMORY_NAMESPACE)
        for item in items:
            store.delete(_MEMORY_NAMESPACE, item.key)

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
        save_memory.func(key="name", value="Bob", runtime=rt)
        item = store.get(_MEMORY_NAMESPACE, "name")
        assert item is not None
        assert item.value == {"content": "Bob"}

    def test_get_returns_content(self, rt, store):
        store.put(_MEMORY_NAMESPACE, "theme", {"content": "dark"})
        result = get_memory.func(key="theme", runtime=rt)
        assert result == "dark"

    def test_search_semantic(self, rt, store):
        store.put(_MEMORY_NAMESPACE, "meeting", {"content": "Team meeting at 3pm in conference room"})
        store.put(_MEMORY_NAMESPACE, "grocery", {"content": "Buy milk, eggs, and bread from supermarket"})
        result = search_memory.func(query="work schedule", runtime=rt)
        assert "meeting" in result

    def test_delete(self, rt, store):
        store.put(_MEMORY_NAMESPACE, "k1", {"content": "temporary"})
        delete_memory.func(key="k1", runtime=rt)
        assert store.get(_MEMORY_NAMESPACE, "k1") is None

    def test_full_lifecycle(self, rt, store):
        save_memory.func(key="lang", value="Rust", runtime=rt)
        assert get_memory.func(key="lang", runtime=rt) == "Rust"
        assert "lang" in search_memory.func(query="programming language", runtime=rt)
        delete_memory.func(key="lang", runtime=rt)
        assert "not found" in get_memory.func(key="lang", runtime=rt)


# ===========================================================================
# Session summary tests
# ===========================================================================


class TestSessionSummary:
    """Tests for save_session_summary / load_session_summary."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        yield
        store = create_store(self._pg_config())
        item = store.get(SESSION_SUMMARY_NAMESPACE, SESSION_SUMMARY_KEY)
        if item:
            store.delete(SESSION_SUMMARY_NAMESPACE, SESSION_SUMMARY_KEY)

    @staticmethod
    def _pg_config():
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

    @staticmethod
    def _mock_config():
        """Config with a mock LLM that returns a preset summary."""
        from unittest.mock import MagicMock
        config = MagicMock()
        config.model.base_url = "https://example.com/v1"
        config.model.api_key = "fake"
        config.model.model_name = "fake"
        return config

    @staticmethod
    def _make_messages():
        from langchain_core.messages import AIMessage, HumanMessage
        return [
            HumanMessage(content="记住我最喜欢的颜色是蓝色"),
            AIMessage(content="好的，已记住你喜欢蓝色"),
        ]

    def test_empty_messages_returns_none(self):
        store = create_store(MemoryConfig(type="in_memory"))
        result = save_session_summary(store, [], self._mock_config())
        assert result is None

    def test_no_human_ai_messages_returns_none(self):
        store = create_store(MemoryConfig(type="in_memory"))
        result = save_session_summary(store, ["not a message"], self._mock_config())
        assert result is None

    @patch("langchain_openai.ChatOpenAI")
    def test_llm_returns_empty_retries_and_fails(self, mock_chat_cls):
        """If LLM returns empty every time, save_session_summary returns None."""
        mock_response = MagicMock()
        mock_response.content = ""
        mock_chat_cls.return_value.invoke.return_value = mock_response

        store = create_store(MemoryConfig(type="in_memory"))
        result = save_session_summary(store, self._make_messages(), self._mock_config())
        assert result is None
        assert mock_chat_cls.return_value.invoke.call_count == 3

    @patch("langchain_openai.ChatOpenAI")
    def test_llm_returns_empty_then_succeeds(self, mock_chat_cls):
        """Retry: first call empty, second call returns content."""
        empty_resp = MagicMock()
        empty_resp.content = ""
        ok_resp = MagicMock()
        ok_resp.content = "用户喜欢蓝色"
        mock_chat_cls.return_value.invoke.side_effect = [empty_resp, ok_resp]

        store = create_store(MemoryConfig(type="in_memory"))
        result = save_session_summary(store, self._make_messages(), self._mock_config())
        assert result == "用户喜欢蓝色"
        assert mock_chat_cls.return_value.invoke.call_count == 2

    @patch("langchain_openai.ChatOpenAI")
    def test_llm_raises_then_succeeds(self, mock_chat_cls):
        """Retry: first call raises exception, second call succeeds."""
        ok_resp = MagicMock()
        ok_resp.content = "摘要内容"
        mock_chat_cls.return_value.invoke.side_effect = [
            RuntimeError("API error"),
            ok_resp,
        ]

        store = create_store(MemoryConfig(type="in_memory"))
        result = save_session_summary(store, self._make_messages(), self._mock_config())
        assert result == "摘要内容"

    @patch("langchain_openai.ChatOpenAI")
    def test_llm_returns_list_content(self, mock_chat_cls):
        """Handle response.content being a list of content blocks."""
        mock_response = MagicMock()
        mock_response.content = [
            {"type": "text", "text": "用户偏好：蓝色"},
        ]
        mock_chat_cls.return_value.invoke.return_value = mock_response

        store = create_store(MemoryConfig(type="in_memory"))
        result = save_session_summary(store, self._make_messages(), self._mock_config())
        assert result == "用户偏好：蓝色"

    @patch("langchain_openai.ChatOpenAI")
    def test_llm_all_raise_returns_none(self, mock_chat_cls):
        """If LLM raises on every attempt, returns None."""
        mock_chat_cls.return_value.invoke.side_effect = RuntimeError("fail")

        store = create_store(MemoryConfig(type="in_memory"))
        result = save_session_summary(store, self._make_messages(), self._mock_config())
        assert result is None
        assert mock_chat_cls.return_value.invoke.call_count == 3

    def test_roundtrip_with_postgres(self):
        """End-to-end: save summary to PostgresStore with vector index, then load."""
        store = create_store(self._pg_config())

        # Directly store to bypass LLM call (LLM reliability is tested above)
        store.put(
            SESSION_SUMMARY_NAMESPACE,
            SESSION_SUMMARY_KEY,
            {"content": "用户喜欢蓝色和星街彗星", "message_count": 2, "timestamp": 1000.0},
        )

        loaded = load_session_summary(store)
        assert loaded == "用户喜欢蓝色和星街彗星"

        store.delete(SESSION_SUMMARY_NAMESPACE, SESSION_SUMMARY_KEY)

    def test_load_missing_returns_none(self):
        store = create_store(self._pg_config())
        result = load_session_summary(store)
        assert result is None
