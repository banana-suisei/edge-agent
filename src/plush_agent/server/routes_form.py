from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plush_agent.tools.form import get_form, get_pending_forms, submit_form

router = APIRouter()


class FormSubmitRequest(BaseModel):
    fields: dict


@router.get("/forms")
async def list_forms():
    return get_pending_forms()


@router.get("/forms/{form_id}")
async def get_form_detail(form_id: str):
    form = get_form(form_id)
    if form is None:
        raise HTTPException(404, "表单不存在")
    return form


@router.post("/forms/{form_id}/submit")
async def submit_form_endpoint(form_id: str, req: FormSubmitRequest):
    ok = submit_form(form_id, req.fields)
    if not ok:
        raise HTTPException(404, "表单不存在或已过期")
    return {"status": "ok"}
