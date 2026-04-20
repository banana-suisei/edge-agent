from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from plush_agent.server.routes_chat import _is_streaming

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
    handler = request.app.state.approval_handler

    if _is_streaming(request):
        streaming_handler = request.app.state.streaming_approval_handler

        async def event_generator():
            async for event in streaming_handler.submit_decision_streaming(
                approval_id, req.decisions
            ):
                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"], ensure_ascii=False),
                }

        return EventSourceResponse(event_generator())

    result = await handler.submit_decision(approval_id, req.decisions)
    if result is None:
        raise HTTPException(404, "审批请求不存在或已过期")
    return result
