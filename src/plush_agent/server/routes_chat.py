from __future__ import annotations

import json

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

router = APIRouter()


def _sanitize_surrogates(text: str) -> str:
    """Remove or replace lone surrogate codepoints."""
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


def _is_streaming(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    x_stream = request.headers.get("x-stream", "").lower()
    return x_stream == "true" or accept == "text/event-stream"


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class EndSessionRequest(BaseModel):
    thread_id: str


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    from plush_agent.tools.memory import load_session_summary

    handler = request.app.state.approval_handler
    store = request.app.state.store
    checkpointer = request.app.state.checkpointer
    thread_id = req.thread_id or "default"
    message = _sanitize_surrogates(req.message)

    cfg = {"configurable": {"thread_id": thread_id}}
    snapshot = checkpointer.get_tuple(cfg)
    if snapshot is None:
        prev_summary = load_session_summary(store)
        if prev_summary:
            message = f"[上一次对话摘要]\n{prev_summary}\n[当前消息]\n{message}"

    if not _is_streaming(request):
        result = await handler.run_with_hitl(message, thread_id)
        return result

    streaming_handler = request.app.state.streaming_approval_handler

    async def event_generator():
        async for event in streaming_handler.run_streaming(message, thread_id):
            yield {
                "event": event["event"],
                "data": json.dumps(event["data"], ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


@router.post("/chat/end")
async def end_session(req: EndSessionRequest, request: Request):
    """End a session: summarize conversation and save to long-term memory."""
    from plush_agent.tools.memory import save_session_summary

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
