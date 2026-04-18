"""Tests for HITL interrupt handling with correct data structure keys."""

from __future__ import annotations

import pytest

from plush_agent.config import AutoApproveRule, Config, HitlConfig
from plush_agent.hitl.approval_handler import ApprovalHandler


def _make_interrupt_value(action_requests, review_configs=None):
    """Build a realistic interrupt value matching HumanInTheLoopMiddleware's structure."""
    return {
        "action_requests": action_requests,
        "review_configs": review_configs or [
            {"action_name": a["name"], "allowed_decisions": ["approve", "edit", "reject"]}
            for a in action_requests
        ],
    }


def _make_action(name, args, description=""):
    return {"name": name, "args": args, "description": description}


class MockInterrupt:
    def __init__(self, value):
        self.value = value


class MockGraphOutput:
    def __init__(self, interrupts=None, value=None):
        self.interrupts = interrupts or ()
        self.value = value or {"messages": []}


class FakeAgent:
    """Fake agent that tracks calls for assertion."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or []

    async def ainvoke(self, payload, config=None, version=None):
        self.calls.append({"payload": payload, "config": config, "version": version})
        return self.responses[len(self.calls) - 1] if self.calls else MockGraphOutput()


# ── Tests for _process_interrupt ──

class TestProcessInterrupt:
    def setup_method(self):
        cfg = Config(hitl=HitlConfig(auto_approve=[
            AutoApproveRule(tool="terminal", args_patterns=[r"^.*ls.*$"]),
        ]))
        self.handler = ApprovalHandler(None, cfg)

    def test_auto_approve_matching_pattern(self):
        iv = _make_interrupt_value([
            _make_action("terminal", {"commands": "ls -la"}),
        ])
        action_requests, decisions, needs_human, pending_actions = (
            self.handler._process_interrupt(iv)
        )
        assert len(decisions) == 1
        assert decisions[0] == {"type": "approve"}
        assert needs_human == []
        assert pending_actions == []

    def test_needs_human_no_match(self):
        iv = _make_interrupt_value([
            _make_action("terminal", {"commands": "rm -rf /"}),
        ])
        action_requests, decisions, needs_human, pending_actions = (
            self.handler._process_interrupt(iv)
        )
        assert decisions[0] is None
        assert len(needs_human) == 1
        assert len(pending_actions) == 1
        assert pending_actions[0]["name"] == "terminal"
        assert pending_actions[0]["args"] == {"commands": "rm -rf /"}

    def test_mixed_auto_and_human(self):
        iv = _make_interrupt_value([
            _make_action("terminal", {"commands": "ls"}),
            _make_action("terminal", {"commands": "rm -rf /"}),
        ])
        _, decisions, needs_human, pending_actions = self.handler._process_interrupt(iv)
        assert decisions == [{"type": "approve"}, None]
        assert len(needs_human) == 1
        assert pending_actions[0]["args"] == {"commands": "rm -rf /"}

    def test_allowed_decisions_from_review_configs(self):
        iv = _make_interrupt_value(
            [_make_action("terminal", {"commands": "rm"})],
            review_configs=[{"action_name": "terminal", "allowed_decisions": ["approve", "reject"]}],
        )
        _, _, _, pending_actions = self.handler._process_interrupt(iv)
        assert pending_actions[0]["allowed_decisions"] == ["approve", "reject"]


# ── Tests for should_auto_approve ──

class TestShouldAutoApprove:
    def setup_method(self):
        cfg = Config(hitl=HitlConfig(auto_approve=[
            AutoApproveRule(tool="terminal", args_patterns=[r"^.*ls.*$"]),
            AutoApproveRule(tool="form_generate", args_patterns=[r".*"]),
        ]))
        self.handler = ApprovalHandler(None, cfg)

    def test_match(self):
        assert self.handler.should_auto_approve("terminal", {"commands": "ls -la"}) is True

    def test_no_match(self):
        assert self.handler.should_auto_approve("terminal", {"commands": "rm -rf /"}) is False

    def test_different_tool(self):
        assert self.handler.should_auto_approve("unknown_tool", {}) is False

    def test_catch_all_pattern(self):
        assert self.handler.should_auto_approve("form_generate", {"purpose": "test"}) is True


# ── Tests for run_with_hitl ──

@pytest.mark.asyncio
class TestRunWithHitl:
    async def test_no_interrupts(self):
        """When agent finishes without interrupts, return done."""
        cfg = Config()
        handler = ApprovalHandler(
            FakeAgent(responses=[MockGraphOutput(value={"messages": []})]),
            cfg,
        )
        result = await handler.run_with_hitl("hello", "t1")
        assert result["status"] == "done"

    async def test_all_auto_approved(self):
        """All actions match auto-approve rules -> resume automatically."""
        cfg = Config(hitl=HitlConfig(auto_approve=[
            AutoApproveRule(tool="terminal", args_patterns=[r".*"]),
        ]))
        iv = _make_interrupt_value([_make_action("terminal", {"commands": "ls"})])
        agent = FakeAgent(responses=[
            MockGraphOutput(interrupts=[MockInterrupt(iv)]),
            MockGraphOutput(value={"messages": []}),
        ])
        handler = ApprovalHandler(agent, cfg)
        result = await handler.run_with_hitl("list files", "t1")
        assert result["status"] == "done"
        # Agent was called twice: first invoke + resume
        assert len(agent.calls) == 2

    async def test_needs_human_returns_pending(self):
        """Non-matching action -> pending_approval."""
        cfg = Config(hitl=HitlConfig(auto_approve=[
            AutoApproveRule(tool="terminal", args_patterns=[r"^.*ls.*$"]),
        ]))
        iv = _make_interrupt_value([
            _make_action("terminal", {"commands": "rm -rf /"}),
        ])
        agent = FakeAgent(responses=[
            MockGraphOutput(interrupts=[MockInterrupt(iv)]),
        ])
        handler = ApprovalHandler(agent, cfg)
        result = await handler.run_with_hitl("delete all", "t1")
        assert result["status"] == "pending_approval"
        assert len(result["pending_actions"]) == 1
        assert result["pending_actions"][0]["args"] == {"commands": "rm -rf /"}


# ── Tests for submit_decision ──

@pytest.mark.asyncio
class TestSubmitDecision:
    async def test_submit_and_finish(self):
        cfg = Config()
        iv = _make_interrupt_value([_make_action("terminal", {"commands": "rm"})])
        agent = FakeAgent(responses=[
            MockGraphOutput(interrupts=[MockInterrupt(iv)]),
            MockGraphOutput(value={"messages": []}),
        ])
        handler = ApprovalHandler(agent, cfg)

        # First call creates pending
        result = await handler.run_with_hitl("delete", "t1")
        assert result["status"] == "pending_approval"

        # Submit approval
        approval_id = result["approval_id"]
        result = await handler.submit_decision(approval_id, [{"type": "approve"}])
        assert result["status"] == "done"

    async def test_submit_nonexistent(self):
        cfg = Config()
        handler = ApprovalHandler(FakeAgent(), cfg)
        result = await handler.submit_decision("nonexistent", [])
        assert result is None


# ── Tests for list/get pending ──

class TestPendingManagement:
    def test_empty(self):
        cfg = Config()
        handler = ApprovalHandler(None, cfg)
        assert handler.list_pending() == []
        assert handler.get_pending("x") is None
