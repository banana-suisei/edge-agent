from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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
    result = await handler.submit_decision(approval_id, req.decisions)
    if result is None:
        raise HTTPException(404, "审批请求不存在或已过期")
    return result
