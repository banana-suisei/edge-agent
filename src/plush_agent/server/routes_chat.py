from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


def _sanitize_surrogates(text: str) -> str:
    """Remove or replace lone surrogate codepoints."""
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    handler = request.app.state.approval_handler
    thread_id = req.thread_id or "default"
    message = _sanitize_surrogates(req.message)
    result = await handler.run_with_hitl(message, thread_id)
    return result
