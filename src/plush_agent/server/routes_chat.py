from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from plush_agent.server.session import SessionManager

router = APIRouter()
logger = logging.getLogger("plush_agent.server")


def _sanitize_surrogates(text: str) -> str:
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


def _sse_serialize(internal_event: dict) -> dict:
    kind = internal_event["kind"]
    if kind == "text":
        return {
            "event": "text",
            "data": json.dumps(internal_event["payload"], ensure_ascii=False),
        }
    else:
        return {
            "event": "event",
            "data": json.dumps(internal_event["payload"], ensure_ascii=False),
        }


def _get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager


async def _consume_and_push(
    async_gen, queue: asyncio.Queue, message_id: str | None = None
) -> None:
    full_text = ""
    try:
        async for event in async_gen:
            if message_id is not None and event.get("kind") == "text":
                full_text += event.get("payload", "")
            await queue.put(_sse_serialize(event))

        if message_id is not None:
            from plush_agent.hitl.streaming_handler import _envelope

            await queue.put(_sse_serialize({
                "kind": "event",
                "payload": _envelope("message_finish", {
                    "message_id": message_id,
                    "content": full_text,
                }),
            }))
    except asyncio.CancelledError:
        pass
    except Exception as e:
        from plush_agent.hitl.streaming_handler import _envelope

        await queue.put(_sse_serialize({
            "kind": "event",
            "payload": _envelope("error", {
                "code": type(e).__name__.lower(),
                "message": f"{type(e).__name__}: {e}",
            }),
        }))


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class EndSessionRequest(BaseModel):
    thread_id: str


@router.get("/chat/stream")
async def stream(request: Request, thread_id: str = "default"):
    sm = _get_session_manager(request)

    if sm.has_session(thread_id):
        await sm.close(thread_id)

    queue = sm.get_or_create(thread_id)

    from plush_agent.hitl.streaming_handler import _envelope

    await queue.put(_sse_serialize({
        "kind": "event",
        "payload": _envelope("session_start", {"thread_id": thread_id}),
    }))

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield event
                except asyncio.TimeoutError:
                    yield {"event": "keepalive", "data": ""}
        finally:
            await _on_stream_disconnect(request, thread_id)

    return EventSourceResponse(event_generator())


async def _on_stream_disconnect(request: Request, thread_id: str) -> None:
    sm = _get_session_manager(request)
    had_messages = sm.has_sent_message(thread_id)
    await sm.close(thread_id)

    if not had_messages:
        logger.info("No message sent in session, skip summary: %s", thread_id)
        return

    try:
        from plush_agent.tools.memory import save_session_summary

        store = request.app.state.store
        config = request.app.state.config
        checkpointer = request.app.state.checkpointer

        cfg = {"configurable": {"thread_id": thread_id}}
        snapshot = checkpointer.get_tuple(cfg)
        if snapshot:
            messages = snapshot.checkpoint.get("channel_values", {}).get("messages", [])
            if messages:
                save_session_summary(store, messages, config)
                logger.info("Session summary saved for: %s", thread_id)
    except Exception:
        logger.exception("Failed to save session summary for: %s", thread_id)


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    from plush_agent.tools.memory import load_session_summary

    sm = _get_session_manager(request)
    thread_id = req.thread_id or "default"
    message = _sanitize_surrogates(req.message)

    if sm.has_session(thread_id):
        queue = sm.get_or_create(thread_id)
        streaming_handler = request.app.state.streaming_approval_handler

        from plush_agent.hitl.streaming_handler import _envelope

        message_id = f"msg_{uuid4().hex[:12]}"
        await queue.put(_sse_serialize({
            "kind": "event",
            "payload": _envelope("message_start", {"message_id": message_id}),
        }))

        cfg = {"configurable": {"thread_id": thread_id}}
        snapshot = request.app.state.checkpointer.get_tuple(cfg)
        if snapshot is None:
            prev_summary = load_session_summary(request.app.state.store)
            if prev_summary:
                message = f"[上一次对话摘要]\n{prev_summary}\n[当前消息]\n{message}"

        gen = streaming_handler.run_streaming(message, thread_id)
        task = asyncio.create_task(_consume_and_push(gen, queue, message_id))
        sm.register_task(thread_id, task)
        sm.mark_message_sent(thread_id)

        return {"status": "accepted", "thread_id": thread_id}

    handler = request.app.state.approval_handler
    store = request.app.state.store
    checkpointer = request.app.state.checkpointer

    cfg = {"configurable": {"thread_id": thread_id}}
    snapshot = checkpointer.get_tuple(cfg)
    if snapshot is None:
        prev_summary = load_session_summary(store)
        if prev_summary:
            message = f"[上一次对话摘要]\n{prev_summary}\n[当前消息]\n{message}"

    result = await handler.run_with_hitl(message, thread_id)
    return result


@router.post("/chat/end")
async def end_session(req: EndSessionRequest, request: Request):
    from plush_agent.tools.memory import save_session_summary

    sm = _get_session_manager(request)
    if sm.has_session(req.thread_id):
        raise HTTPException(400, "SSE stream is still active. Disconnect the stream first.")

    store = request.app.state.store
    config = request.app.state.config
    checkpointer = request.app.state.checkpointer

    cfg = {"configurable": {"thread_id": req.thread_id}}
    snapshot = checkpointer.get_tuple(cfg)
    if snapshot is None:
        return {"status": "no_conversation", "message": "No conversation found for this thread."}

    messages = snapshot.checkpoint.get("channel_values", {}).get("messages", [])
    if not messages:
        return {"status": "no_messages", "message": "No messages to summarize."}

    try:
        summary = save_session_summary(store, messages, config)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "saved", "summary": summary}
