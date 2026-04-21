from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from plush_agent.server.routes_chat import _get_session_manager, _sse_serialize, _consume_and_push

router = APIRouter()


class DecideRequest(BaseModel):
    decisions: list[dict]


@router.get("/approvals")
async def list_approvals(request: Request):
    handler = request.app.state.approval_handler
    return handler.list_pending()


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, request: Request):
    handler = request.app.state.approval_handler
    result = handler.get_pending(approval_id)
    if result is None:
        raise HTTPException(404, "审批请求不存在")
    return result


@router.post("/approvals/{approval_id}/decide")
async def decide(approval_id: str, req: DecideRequest, request: Request):
    sm = _get_session_manager(request)

    streaming_handler = request.app.state.streaming_approval_handler
    pending = None
    for p in streaming_handler.streaming_pending.values():
        if p.approval_id == approval_id:
            pending = p
            break

    if pending and sm.has_session(pending.thread_id):
        queue = sm.get_or_create(pending.thread_id)
        gen = streaming_handler.submit_decision_streaming(approval_id, req.decisions)
        task = asyncio.create_task(_consume_and_push(gen, queue))
        sm.register_task(pending.thread_id, task)
        return {"status": "accepted"}

    handler = request.app.state.approval_handler
    result = await handler.submit_decision(approval_id, req.decisions)
    if result is None:
        raise HTTPException(404, "审批请求不存在或已过期")
    return result
