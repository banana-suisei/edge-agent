from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("plush_agent.server")


class SessionManager:
    """Manages long-lived SSE sessions keyed by thread_id.

    Each session has an asyncio.Queue for event distribution and
    tracks the current background agent task for cancellation.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._has_sent_message: dict[str, bool] = {}

    def get_or_create(self, thread_id: str) -> asyncio.Queue[dict[str, Any]]:
        if thread_id not in self._queues:
            self._queues[thread_id] = asyncio.Queue()
        return self._queues[thread_id]

    async def push(self, thread_id: str, event: dict[str, Any]) -> None:
        q = self._queues.get(thread_id)
        if q is not None:
            await q.put(event)

    def has_session(self, thread_id: str) -> bool:
        return thread_id in self._queues

    def register_task(self, thread_id: str, task: asyncio.Task) -> None:
        self._tasks[thread_id] = task

    def mark_message_sent(self, thread_id: str) -> None:
        self._has_sent_message[thread_id] = True

    def has_sent_message(self, thread_id: str) -> bool:
        return self._has_sent_message.get(thread_id, False)

    async def close(self, thread_id: str) -> None:
        task = self._tasks.pop(thread_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._queues.pop(thread_id, None)
        self._has_sent_message.pop(thread_id, None)
        logger.info("Session closed: %s", thread_id)
