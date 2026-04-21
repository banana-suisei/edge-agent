from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from plush_agent.config import Config
from plush_agent.hitl.approval_handler import ApprovalHandler, PendingApproval
from plush_agent.hitl.streaming_handler import StreamingApprovalHandler
from plush_agent.server.routes_chat import _sse_serialize
from plush_agent.server.session import SessionManager
from plush_agent.tools.form import get_form, get_pending_forms, submit_form

logger = logging.getLogger("plush_agent.uds")

VERSION = 1


def _base_response(action: str, robot_id: str, success: bool) -> dict:
    return {
        "version": VERSION,
        "action": action,
        "robotId": robot_id,
        "timestamp": int(time.time()),
        "success": success,
    }


def _pending_to_uds(p: PendingApproval) -> dict:
    """Map a PendingApproval to a single UDS approval item."""
    first_idx = p.needs_human_indices[0] if p.needs_human_indices else 0
    first_action = p.action_requests[first_idx]

    allowed = ["approve", "edit", "reject"]
    for rc in p.review_configs:
        if rc.get("action_name") == first_action["name"]:
            allowed = rc.get("allowed_decisions", allowed)
            break

    return {
        "interruptId": p.approval_id,
        "actionName": first_action["name"],
        "description": first_action.get("description", ""),
        "argsJson": json.dumps(first_action.get("args", {}), ensure_ascii=False),
        "allowedDecisions": allowed,
        "createdAt": int(p.created_at),
        "reviewConfigJson": json.dumps(p.review_configs, ensure_ascii=False),
    }


def _collect_all_approvals(
    approval_handler: ApprovalHandler,
    streaming_handler: StreamingApprovalHandler,
) -> list[dict]:
    approvals = []
    for p in approval_handler.pending.values():
        approvals.append(_pending_to_uds(p))
    for p in streaming_handler.streaming_pending.values():
        approvals.append(_pending_to_uds(p))
    return approvals


def _map_uds_decision(
    decision: str, reason: str, edited_args: dict | None, pending: PendingApproval,
) -> list[dict]:
    """Map a single UDS decision to internal decisions array for all human-needed actions."""
    decisions = []
    for _ in pending.needs_human_indices:
        if decision == "approve":
            decisions.append({"type": "approve"})
        elif decision == "reject":
            decisions.append({"type": "reject", "message": reason})
        elif decision == "edit":
            if edited_args:
                action = pending.action_requests[pending.needs_human_indices[0]]
                decisions.append({
                    "type": "edit",
                    "edited_action": {"name": action["name"], "args": edited_args},
                })
            else:
                decisions.append({"type": "approve"})
        else:
            decisions.append({"type": "approve"})
    return decisions


class UdsServer:
    def __init__(
        self,
        config: Config,
        approval_handler: ApprovalHandler,
        streaming_handler: StreamingApprovalHandler,
        session_manager: SessionManager,
    ) -> None:
        self.config = config
        self.robot_id = config.robot_id
        self.approval_handler = approval_handler
        self.streaming_handler = streaming_handler
        self.session_manager = session_manager
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        socket_path = self.config.uds.socket_path
        if os.path.exists(socket_path):
            os.unlink(socket_path)

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=socket_path,
        )
        logger.info("UDS server listening on %s (robotId=%s)", socket_path, self.robot_id)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        socket_path = self.config.uds.socket_path
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        logger.info("UDS server stopped")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=30)
            if not data:
                return

            request = json.loads(data.decode("utf-8"))
            response = await self._dispatch(request)

            resp_bytes = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
            writer.write(resp_bytes)
            await writer.drain()
        except json.JSONDecodeError as e:
            logger.error("UDS JSON parse error: %s", e)
            try:
                err = _base_response("error", self.robot_id, False)
                err["error"] = f"Invalid JSON: {e}"
                writer.write((json.dumps(err) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        except asyncio.TimeoutError:
            logger.warning("UDS connection timed out")
        except Exception as e:
            logger.error("UDS connection error: %s", e)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, request: dict) -> dict:
        action = request.get("action", "")

        if action == "getForms":
            return await self._handle_get_forms(request)
        elif action == "submitForm":
            return await self._handle_submit_form(request)
        elif action == "getPendingApprovals":
            return await self._handle_get_pending_approvals(request)
        elif action == "submitApprovalDecision":
            return await self._handle_submit_approval_decision(request)
        else:
            resp = _base_response(action, self.robot_id, False)
            resp["error"] = f"Unknown action: {action}"
            return resp

    async def _handle_get_forms(self, request: dict) -> dict:
        req_robot_id = request.get("robotId", "")
        if req_robot_id and req_robot_id != self.robot_id:
            resp = _base_response("getForms", self.robot_id, False)
            resp["error"] = f"robotId mismatch: expected={self.robot_id}, got={req_robot_id}"
            return resp

        form_id_filter = request.get("formId", "")
        if form_id_filter:
            schema = get_form(form_id_filter)
            if schema is None:
                resp = _base_response("getForms", self.robot_id, False)
                resp["error"] = f"Form not found: {form_id_filter}"
                return resp
            forms = [schema]
        else:
            all_forms = get_pending_forms()
            forms = [v["schema"] for v in all_forms.values()]

        resp = _base_response("getForms", self.robot_id, True)
        resp["forms"] = forms
        return resp

    async def _handle_submit_form(self, request: dict) -> dict:
        req_robot_id = request.get("robotId", "")
        if req_robot_id and req_robot_id != self.robot_id:
            resp = _base_response("submitForm", self.robot_id, False)
            resp["error"] = f"robotId mismatch: expected={self.robot_id}, got={req_robot_id}"
            return resp

        form_id = request.get("formId", "")
        responses = request.get("responses", {})
        ok = submit_form(form_id, responses)
        if not ok:
            resp = _base_response("submitForm", self.robot_id, False)
            resp["error"] = f"Form not found or already submitted: {form_id}"
            return resp

        resp = _base_response("submitForm", self.robot_id, True)
        resp["formId"] = form_id
        return resp

    async def _handle_get_pending_approvals(self, request: dict) -> dict:
        approvals = _collect_all_approvals(self.approval_handler, self.streaming_handler)

        interrupt_id_filter = request.get("interruptId", "")
        if interrupt_id_filter:
            approvals = [a for a in approvals if a["interruptId"] == interrupt_id_filter]

        resp = _base_response("getPendingApprovals", self.robot_id, True)
        resp["approvals"] = approvals
        return resp

    async def _handle_submit_approval_decision(self, request: dict) -> dict:
        interrupt_id = request.get("interruptId", "")
        decision = request.get("decision", "approve")
        reason = request.get("reason", "")
        edited_args = request.get("editedArgs")

        # Search streaming handler first
        pending = None
        handler_type = None
        for p in self.streaming_handler.streaming_pending.values():
            if p.approval_id == interrupt_id:
                pending = p
                handler_type = "streaming"
                break

        if pending is None:
            for p in self.approval_handler.pending.values():
                if p.approval_id == interrupt_id:
                    pending = p
                    handler_type = "blocking"
                    break

        if pending is None:
            resp = _base_response("submitApprovalDecision", self.robot_id, False)
            resp["error"] = f"Interrupt '{interrupt_id}' not found or already resolved"
            return resp

        human_decisions = _map_uds_decision(decision, reason, edited_args, pending)

        if handler_type == "streaming" and self.session_manager.has_session(pending.thread_id):
            queue = self.session_manager.get_or_create(pending.thread_id)
            gen = self.streaming_handler.submit_decision_streaming(interrupt_id, human_decisions)
            task = asyncio.create_task(self._consume_and_push(gen, queue))
            self.session_manager.register_task(pending.thread_id, task)
        else:
            result = await self.approval_handler.submit_decision(interrupt_id, human_decisions)
            if result is None:
                resp = _base_response("submitApprovalDecision", self.robot_id, False)
                resp["error"] = f"Failed to submit decision for '{interrupt_id}'"
                return resp

        resp = _base_response("submitApprovalDecision", self.robot_id, True)
        return resp

    @staticmethod
    async def _consume_and_push(async_gen, queue: asyncio.Queue) -> None:
        try:
            async for item in async_gen:
                await queue.put(_sse_serialize(item))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("UDS consume_and_push error: %s", e)
            from plush_agent.hitl.streaming_handler import _envelope

            await queue.put(_sse_serialize({
                "kind": "event",
                "payload": _envelope("error", {
                    "code": type(e).__name__.lower(),
                    "message": f"{type(e).__name__}: {e}",
                }),
            }))
