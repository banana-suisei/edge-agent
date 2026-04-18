"""Store factory — creates InMemoryStore or PostgresStore with vector index support."""

from __future__ import annotations

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from plush_agent.config import MemoryConfig


def create_store(config: MemoryConfig) -> BaseStore:
    if config.type == "postgres":
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        from langchain_openai import OpenAIEmbeddings
        from langgraph.store.postgres import PostgresStore

        pool: ConnectionPool = ConnectionPool(
            config.postgres.connection_string,
            min_size=1,
            max_size=5,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            open=True,
        )

        embeddings = OpenAIEmbeddings(
            base_url=config.embedding.base_url,
            api_key=config.embedding.api_key,
            model=config.embedding.model_name,
            check_embedding_ctx_length=False,
        )

        index_config = {
            "dims": config.embedding.dims,
            "embed": embeddings,
            "fields": ["content"],
            "distance_type": config.embedding.distance_type,
            "ann_index_config": {"kind": "flat"},
        }

        store = PostgresStore(conn=pool, index=index_config)
        store.setup()
        return store

    return InMemoryStore()
