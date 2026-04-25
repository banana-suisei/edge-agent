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
    streaming_handler = request.app.state.streaming_approval_handler
    result = handler.list_pending()
    # Include streaming-mode pending approvals (keyed by thread_id in a separate dict)
    for p in streaming_handler.streaming_pending.values():
        actions = []
        for idx in p.needs_human_indices:
            a = p.action_requests[idx]
            actions.append({
                "name": a["name"],
                "args": a["args"],
                "description": a.get("description", ""),
            })
        result.append({
            "approval_id": p.approval_id,
            "pending_actions": actions,
            "auto_approved_count": len(p.action_requests) - len(p.needs_human_indices),
            "created_at": p.created_at,
        })
    return result


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, request: Request):
    handler = request.app.state.approval_handler
    result = handler.get_pending(approval_id)
    if result is None:
        streaming_handler = request.app.state.streaming_approval_handler
        for p in streaming_handler.streaming_pending.values():
            if p.approval_id == approval_id:
                actions = []
                for idx in p.needs_human_indices:
                    a = p.action_requests[idx]
                    actions.append({
                        "name": a["name"],
                        "args": a["args"],
                        "description": a.get("description", ""),
                    })
                result = {
                    "approval_id": p.approval_id,
                    "pending_actions": actions,
                    "auto_approved_count": len(p.action_requests) - len(p.needs_human_indices),
                    "created_at": p.created_at,
                }
                break
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
        message_id = sm.get_message_id(pending.thread_id)
        task = asyncio.create_task(_consume_and_push(
            gen, queue, message_id, sm, pending.thread_id,
        ))
        sm.register_task(pending.thread_id, task)
        return {"status": "accepted"}

    handler = request.app.state.approval_handler
    result = await handler.submit_decision(approval_id, req.decisions)
    if result is None:
        raise HTTPException(404, "审批请求不存在或已过期")
    return result
