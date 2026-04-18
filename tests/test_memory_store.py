"""Tests for memory store creation (InMemoryStore and PostgresStore)."""

from __future__ import annotations

import pytest

from plush_agent.config import MemoryConfig, PostgresConfig
from plush_agent.memory.store import create_store


class TestInMemoryStore:
    def test_create_in_memory(self):
        config = MemoryConfig(type="in_memory")
        store = create_store(config)
        assert store is not None

    def test_in_memory_put_and_get(self):
        config = MemoryConfig(type="in_memory")
        store = create_store(config)
        store.put(("test",), "key1", {"value": "hello"})
        item = store.get(("test",), "key1")
        assert item is not None
        assert item.value == {"value": "hello"}

    def test_in_memory_search(self):
        config = MemoryConfig(type="in_memory")
        store = create_store(config)
        store.put(("users",), "u1", {"name": "Alice"})
        store.put(("users",), "u2", {"name": "Bob"})
        items = store.search(("users",))
        assert len(items) == 2

    def test_in_memory_delete(self):
        config = MemoryConfig(type="in_memory")
        store = create_store(config)
        store.put(("test",), "k1", {"data": 1})
        store.delete(("test",), "k1")
        assert store.get(("test",), "k1") is None

    def test_in_memory_get_missing(self):
        config = MemoryConfig(type="in_memory")
        store = create_store(config)
        assert store.get(("nonexistent",), "missing") is None


class TestPostgresStore:
    @pytest.fixture(autouse=True)
    def cleanup_test_data(self, pg_config):
        yield
        store = create_store(pg_config)
        for ns in [("test",), ("users",), ("ns1",), ("ns2",)]:
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
        )

    def test_create_postgres(self, pg_config):
        store = create_store(pg_config)
        assert store is not None

    def test_postgres_put_and_get(self, pg_config):
        store = create_store(pg_config)
        store.put(("test",), "key1", {"value": "hello"})
        item = store.get(("test",), "key1")
        assert item is not None
        assert item.value == {"value": "hello"}

    def test_postgres_search(self, pg_config):
        store = create_store(pg_config)
        store.put(("users",), "u1", {"name": "Alice"})
        store.put(("users",), "u2", {"name": "Bob"})
        items = store.search(("users",))
        assert len(items) >= 2

    def test_postgres_delete(self, pg_config):
        store = create_store(pg_config)
        store.put(("test",), "k1", {"data": 1})
        store.delete(("test",), "k1")
        assert store.get(("test",), "k1") is None

    def test_postgres_overwrite(self, pg_config):
        store = create_store(pg_config)
        store.put(("test",), "k1", {"version": 1})
        store.put(("test",), "k1", {"version": 2})
        item = store.get(("test",), "k1")
        assert item.value == {"version": 2}

    def test_postgres_namespaces_isolated(self, pg_config):
        store = create_store(pg_config)
        store.put(("ns1",), "k", {"from": "ns1"})
        store.put(("ns2",), "k", {"from": "ns2"})
        assert store.get(("ns1",), "k").value == {"from": "ns1"}
        assert store.get(("ns2",), "k").value == {"from": "ns2"}
