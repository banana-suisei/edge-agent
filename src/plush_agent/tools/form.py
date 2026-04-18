from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from plush_agent.config import Config

_pending_forms: dict[str, dict] = {}
_form_events: dict[str, asyncio.Event] = {}
_config: Config | None = None


def _set_config(cfg: Config) -> None:
    global _config
    _config = cfg


FORM_FIELD_TYPES = [
    "text", "password", "textarea", "number",
    "select", "checkbox", "radio", "date", "email",
]

FORM_GENERATION_SYSTEM = """You are a form schema generator. Given a form purpose and field requirements,
generate a JSON form definition with this exact structure:

{
  "formName": "<descriptive form name>",
  "formDescription": "<brief description>",
  "fields": [
    {
      "fieldId": "<unique_id>",
      "fieldName": "<display name>",
      "fieldType": "<one of: text, password, textarea, number, select, checkbox, radio, date, email>",
      "required": <true/false>,
      "placeholder": "<hint text>"
    }
  ]
}

Rules:
- fieldId must be a lowercase snake_case identifier
- Generate 2-8 fields appropriate for the purpose
- Choose appropriate fieldTypes
- Make placeholders descriptive
- Return ONLY the JSON, no markdown fences or explanation"""


def _generate_form_sync(model: ChatOpenAI, purpose: str, requirements: str) -> dict:
    response = model.invoke([
        SystemMessage(content=FORM_GENERATION_SYSTEM),
        HumanMessage(content=f"Form purpose: {purpose}\nField requirements: {requirements}"),
    ])
    text = response.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    schema = json.loads(text)
    form_id = f"form-{uuid.uuid4().hex[:8]}"
    schema["formId"] = form_id
    for f in schema.get("fields", []):
        f["value"] = None
    return schema


def create_form_generate_tool(cfg: Config):
    _set_config(cfg)

    @tool
    async def form_generate(
        form_purpose: str,
        field_requirements: str,
        timeout: int = cfg.form.default_timeout,
    ) -> dict:
        """Generate a structured form and wait for user to fill it.

        Creates a form based on the purpose and requirements, exposes it via HTTP API,
        then waits until the user submits or timeout expires.

        Args:
            form_purpose: Description of what the form is for.
            field_requirements: Description of fields to collect.
            timeout: Seconds to wait for user submission (default from config).
        """
        model = ChatOpenAI(
            base_url=cfg.model.base_url,
            api_key=cfg.model.api_key,
            model=cfg.model.model_name,
            temperature=0.3,
            max_tokens=1024,
        )

        schema = await asyncio.to_thread(_generate_form_sync, model, form_purpose, field_requirements)
        form_id = schema["formId"]

        _pending_forms[form_id] = {"schema": schema, "submitted": None}
        event = asyncio.Event()
        _form_events[form_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            data = _pending_forms.pop(form_id, {})
            _form_events.pop(form_id, None)
            return {
                "status": "submitted",
                "formId": form_id,
                "formData": data.get("submitted", {}),
            }
        except asyncio.TimeoutError:
            _pending_forms.pop(form_id, None)
            _form_events.pop(form_id, None)
            return {
                "status": "timeout",
                "formId": form_id,
                "message": "用户未在规定时间内填写表单",
            }

    return form_generate


def get_pending_forms() -> dict[str, dict]:
    return {fid: {"schema": v["schema"]} for fid, v in _pending_forms.items()}


def get_form(form_id: str) -> dict | None:
    entry = _pending_forms.get(form_id)
    if entry is None:
        return None
    return entry["schema"]


def submit_form(form_id: str, fields: dict[str, Any]) -> bool:
    entry = _pending_forms.get(form_id)
    if entry is None:
        return False
    entry["submitted"] = fields
    event = _form_events.get(form_id)
    if event:
        event.set()
    return True
