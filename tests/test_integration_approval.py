"""Integration test: streaming approval visibility via GET /api/approvals.

Reproduces the bug: approval_request emitted via SSE, but
GET /api/approvals returns [] and GET /api/approvals/{id} returns 404.

Root cause: main.py creates ApprovalHandler and StreamingApprovalHandler
as TWO SEPARATE instances. The routes must check both handlers' pending dicts.
"""
import pytest
import httpx

from plush_agent.config import Config
from plush_agent.server.app import create_app
from plush_agent.hitl.approval_handler import ApprovalHandler, PendingApproval
from plush_agent.hitl.streaming_handler import StreamingApprovalHandler


def _make_app():
    config = Config()

    class FakeAgent:
        async def astream(self, *a, **kw):
            return
            yield

        async def ainvoke(self, *a, **kw):
            return type("R", (), {"interrupts": [], "value": {"messages": []}})()

    app = create_app()
    agent = FakeAgent()
    app.state.approval_handler = ApprovalHandler(agent, config)
    app.state.streaming_approval_handler = StreamingApprovalHandler(agent, config)
    app.state.store = None
    app.state.checkpointer = type("CP", (), {"get_tuple": lambda s, c: None})()
    app.state.config = config
    return app


def _inject_streaming_pending(streaming_handler, approval_id, thread_id):
    pending = PendingApproval(
        approval_id=approval_id,
        thread_id=thread_id,
        config={"configurable": {"thread_id": thread_id}},
        action_requests=[
            {"name": "terminal", "args": {"commands": "rm -rf /"}, "description": "dangerous"}
        ],
        decisions=[None],
        needs_human_indices=[0],
        interrupt_id="test-int-id",
    )
    streaming_handler.streaming_pending[thread_id] = pending
    return pending


@pytest.mark.asyncio
async def test_list_approvals_finds_streaming_pending():
    """GET /api/approvals should list streaming-mode pending approvals."""
    app = _make_app()
    sh = app.state.streaming_approval_handler
    _inject_streaming_pending(sh, "approval-abc", "t1")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/approvals")
        body = resp.json()
        ids = [a["approval_id"] for a in body]
        assert "approval-abc" in ids, f"approval-abc not found in {ids}"


@pytest.mark.asyncio
async def test_get_approval_by_id_finds_streaming_pending():
    """GET /api/approvals/{id} should find streaming-mode pending approval."""
    app = _make_app()
    sh = app.state.streaming_approval_handler
    _inject_streaming_pending(sh, "approval-xyz", "t2")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/approvals/approval-xyz")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        assert resp.json()["approval_id"] == "approval-xyz"


@pytest.mark.asyncio
async def test_cleanup_after_submit():
    """After submit removes from streaming_pending, GET should return 404."""
    app = _make_app()
    sh = app.state.streaming_approval_handler
    _inject_streaming_pending(sh, "approval-cleanup", "t3")
    sh.streaming_pending.pop("t3", None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/approvals/approval-cleanup")
        assert resp.status_code == 404
