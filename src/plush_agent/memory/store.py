from __future__ import annotations

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from plush_agent.config import MemoryConfig


def create_store(config: MemoryConfig) -> BaseStore:
    if config.type == "postgres":
        from langgraph.store.postgres import PostgresStore  # type: ignore[import-not-found]
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            config.postgres.connection_string,
            min_size=1,
            max_size=5,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        store = PostgresStore(conn=pool)
        store.setup()
        return store

    return InMemoryStore()
