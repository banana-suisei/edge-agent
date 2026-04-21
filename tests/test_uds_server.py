"""Tests for UDS server: approval/form protocol and SSE queue serialization.

Key regression test: events pushed to SSE queue must be serialized via
_sse_serialize() (producing {event, data} dicts), NOT raw internal events
with {kind, payload} keys. Raw events cause ServerSentEvent crash.
"""
import asyncio
import json

import pytest

from plush_agent.config import Config
from plush_agent.hitl.approval_handler import ApprovalHandler, PendingApproval
from plush_agent.hitl.streaming_handler import StreamingApprovalHandler
from plush_agent.server.routes_chat import _sse_serialize
from plush_agent.server.session import SessionManager
from plush_agent.server.uds_server import (
    UdsServer,
    VERSION,
    _base_response,
    _collect_all_approvals,
    _map_uds_decision,
    _pending_to_uds,
)
from plush_agent.tools.form import get_pending_forms, submit_form


def _make_server(socket_path: str | None = None) -> UdsServer:
    config = Config()
    if socket_path:
        config.uds.socket_path = socket_path

    class FakeAgent:
        async def astream(self, *a, **kw):
            return
            yield

        async def ainvoke(self, *a, **kw):
            return type("R", (), {"interrupts": [], "value": {"messages": []}})()

    agent = FakeAgent()
    handler = ApprovalHandler(agent, config)
    streaming_handler = StreamingApprovalHandler(agent, config)
    sm = SessionManager()
    return UdsServer(config, handler, streaming_handler, sm)


def _inject_pending(
    handler: ApprovalHandler, approval_id: str, thread_id: str,
) -> PendingApproval:
    p = PendingApproval(
        approval_id=approval_id,
        thread_id=thread_id,
        config={"configurable": {"thread_id": thread_id}},
        action_requests=[
            {"name": "terminal", "args": {"commands": "rm -rf /"}, "description": "删除文件"},
        ],
        decisions=[None],
        needs_human_indices=[0],
        interrupt_id="test-int-id",
        review_configs=[{"action_name": "terminal", "allowed_decisions": ["approve", "reject"]}],
    )
    handler.pending[thread_id] = p
    return p


def _inject_streaming_pending(
    handler: StreamingApprovalHandler, approval_id: str, thread_id: str,
) -> PendingApproval:
    p = PendingApproval(
        approval_id=approval_id,
        thread_id=thread_id,
        config={"configurable": {"thread_id": thread_id}},
        action_requests=[
            {"name": "execute_command", "args": {"command": "reboot"}, "description": "重启服务"},
        ],
        decisions=[None],
        needs_human_indices=[0],
        interrupt_id="test-stream-int-id",
        review_configs=[{"action_name": "execute_command", "allowed_decisions": ["approve", "reject", "edit"]}],
    )
    handler.streaming_pending[thread_id] = p
    return p


# ── Unit tests ──────────────────────────────────────────────


class TestBaseResponse:
    def test_ok_response(self):
        resp = _base_response("getForms", "robot-0", True)
        assert resp["version"] == VERSION
        assert resp["action"] == "getForms"
        assert resp["robotId"] == "robot-0"
        assert resp["success"] is True
        assert "timestamp" in resp

    def test_error_response(self):
        resp = _base_response("getForms", "robot-0", False)
        resp["error"] = "something failed"
        assert resp["success"] is False
        assert resp["error"] == "something failed"


class TestPendingToUds:
    def test_single_action(self):
        p = _inject_pending(ApprovalHandler(None, Config()), "a1", "t1")
        uds = _pending_to_uds(p)
        assert uds["interruptId"] == "a1"
        assert uds["actionName"] == "terminal"
        assert uds["description"] == "删除文件"
        assert json.loads(uds["argsJson"]) == {"commands": "rm -rf /"}
        assert uds["allowedDecisions"] == ["approve", "reject"]
        assert "createdAt" in uds
        review = json.loads(uds["reviewConfigJson"])
        assert len(review) == 1


class TestCollectAllApprovals:
    def test_collects_from_both_handlers(self):
        server = _make_server()
        _inject_pending(server.approval_handler, "a1", "t1")
        _inject_streaming_pending(server.streaming_handler, "a2", "t2")

        approvals = _collect_all_approvals(server.approval_handler, server.streaming_handler)
        ids = [a["interruptId"] for a in approvals]
        assert "a1" in ids
        assert "a2" in ids


class TestMapUdsDecision:
    def test_approve(self):
        p = _inject_pending(ApprovalHandler(None, Config()), "a1", "t1")
        decisions = _map_uds_decision("approve", "", None, p)
        assert len(decisions) == 1
        assert decisions[0] == {"type": "approve"}

    def test_reject_with_reason(self):
        p = _inject_pending(ApprovalHandler(None, Config()), "a1", "t1")
        decisions = _map_uds_decision("reject", "too dangerous", None, p)
        assert decisions[0]["type"] == "reject"
        assert decisions[0]["message"] == "too dangerous"

    def test_edit_with_args(self):
        p = _inject_pending(ApprovalHandler(None, Config()), "a1", "t1")
        decisions = _map_uds_decision("edit", "", {"commands": "ls"}, p)
        assert decisions[0]["type"] == "edit"
        assert decisions[0]["edited_action"]["args"] == {"commands": "ls"}

    def test_edit_without_args_approves(self):
        p = _inject_pending(ApprovalHandler(None, Config()), "a1", "t1")
        decisions = _map_uds_decision("edit", "", None, p)
        assert decisions[0]["type"] == "approve"


# ── SSE serialization regression test ──────────────────────


class TestSseSerialization:
    """Ensure events pushed to SSE queue have no 'kind' key.

    ServerSentEvent(**data) raises TypeError on unexpected keyword 'kind'.
    All events must be serialized to {event, data} format first.
    """

    def test_text_event_serialized(self):
        raw = {"kind": "text", "payload": "hello"}
        result = _sse_serialize(raw)
        assert "kind" not in result
        assert "event" in result
        assert "data" in result
        assert result["event"] == "text"

    def test_approval_event_serialized(self):
        raw = {"kind": "event", "payload": {"type": "approval_request", "data": "x"}}
        result = _sse_serialize(raw)
        assert "kind" not in result
        assert result["event"] == "event"

    def test_sse_starlette_accepts_serialized(self):
        """Simulate what sse_starlette does: ServerSentEvent(**data)."""
        from sse_starlette.event import ServerSentEvent

        raw = {"kind": "text", "payload": "hello"}
        serialized = _sse_serialize(raw)
        # This must NOT raise TypeError
        sse = ServerSentEvent(**serialized)
        assert sse is not None

    def test_raw_event_would_crash(self):
        """Verify the original bug: raw events DO crash ServerSentEvent."""
        from sse_starlette.event import ServerSentEvent

        raw = {"kind": "text", "payload": "hello"}
        with pytest.raises(TypeError, match="kind"):
            ServerSentEvent(**raw)


# ── UDS integration tests (dispatch) ──────────────────────


class TestUdsDispatch:
    """Test UDS action handlers via _dispatch (no real socket needed)."""

    @pytest.mark.asyncio
    async def test_get_forms_empty(self):
        server = _make_server()
        resp = await server._dispatch({
            "action": "getForms",
            "robotId": server.robot_id,
        })
        assert resp["success"] is True
        assert resp["action"] == "getForms"
        assert resp["forms"] == []

    @pytest.mark.asyncio
    async def test_get_forms_robot_id_mismatch(self):
        server = _make_server()
        resp = await server._dispatch({
            "action": "getForms",
            "robotId": "wrong-robot",
        })
        assert resp["success"] is False
        assert "mismatch" in resp["error"]

    @pytest.mark.asyncio
    async def test_get_pending_approvals_empty(self):
        server = _make_server()
        resp = await server._dispatch({"action": "getPendingApprovals"})
        assert resp["success"] is True
        assert resp["approvals"] == []

    @pytest.mark.asyncio
    async def test_get_pending_approvals_with_data(self):
        server = _make_server()
        _inject_pending(server.approval_handler, "a1", "t1")
        _inject_streaming_pending(server.streaming_handler, "a2", "t2")
        resp = await server._dispatch({"action": "getPendingApprovals"})
        assert resp["success"] is True
        ids = [a["interruptId"] for a in resp["approvals"]]
        assert "a1" in ids
        assert "a2" in ids

    @pytest.mark.asyncio
    async def test_get_pending_approvals_filter_by_id(self):
        server = _make_server()
        _inject_pending(server.approval_handler, "a1", "t1")
        _inject_streaming_pending(server.streaming_handler, "a2", "t2")
        resp = await server._dispatch({
            "action": "getPendingApprovals",
            "interruptId": "a2",
        })
        assert resp["success"] is True
        assert len(resp["approvals"]) == 1
        assert resp["approvals"][0]["interruptId"] == "a2"
        assert resp["approvals"][0]["actionName"] == "execute_command"

    @pytest.mark.asyncio
    async def test_submit_approval_decision_blocking(self):
        server = _make_server()
        _inject_pending(server.approval_handler, "a1", "t1")
        resp = await server._dispatch({
            "action": "submitApprovalDecision",
            "interruptId": "a1",
            "decision": "approve",
        })
        assert resp["success"] is True
        assert "a1" not in [p.approval_id for p in server.approval_handler.pending.values()]

    @pytest.mark.asyncio
    async def test_submit_approval_decision_not_found(self):
        server = _make_server()
        resp = await server._dispatch({
            "action": "submitApprovalDecision",
            "interruptId": "nonexistent",
            "decision": "approve",
        })
        assert resp["success"] is False
        assert "not found" in resp["error"]

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        server = _make_server()
        resp = await server._dispatch({"action": "bogus"})
        assert resp["success"] is False
        assert "Unknown action" in resp["error"]
