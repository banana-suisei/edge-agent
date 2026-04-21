"""Tests for StreamingApprovalHandler — SSE streaming with HITL support."""
from __future__ import annotations

import pytest

from langchain_core.messages import AIMessageChunk, ToolMessage

from plush_agent.config import AutoApproveRule, Config, HitlConfig
from plush_agent.hitl.streaming_handler import StreamingApprovalHandler


def _make_interrupt_value(action_requests, review_configs=None):
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
    def __init__(self, value, id="mock-interrupt-id"):
        self.value = value
        self.id = id


class FakeStreamingAgent:
    """Fake agent whose astream yields pre-configured v2 chunks per call."""

    def __init__(self, chunk_sequences=None, invoke_responses=None):
        self.chunk_sequences = list(chunk_sequences or [[]])
        self.invoke_responses = invoke_responses or []
        self.invoke_calls = []
        self._call_idx = 0

    async def astream(self, input_or_command, config=None, stream_mode=None, version=None):
        idx = min(self._call_idx, len(self.chunk_sequences) - 1)
        self._call_idx += 1
        for chunk in self.chunk_sequences[idx]:
            yield chunk

    async def ainvoke(self, payload, config=None, version=None):
        self.invoke_calls.append(payload)
        return self.invoke_responses[len(self.invoke_calls) - 1] if self.invoke_responses else None


def _token_chunk(content):
    return {
        "type": "messages",
        "data": (AIMessageChunk(content=content), {}),
    }


def _tool_call_chunk(name, args_fragment, tc_id="c1"):
    return {
        "type": "messages",
        "data": (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": name, "args": args_fragment,
                    "id": tc_id, "index": 0, "type": "tool_call_chunk",
                }],
            ),
            {},
        ),
    }


def _tool_result_update(tool_name, content, tool_call_id="c1"):
    return {
        "type": "updates",
        "data": {
            "tools": {
                "messages": [ToolMessage(content=content, name=tool_name, tool_call_id=tool_call_id)],
            },
        },
    }


def _interrupt_update(action_requests, review_configs=None):
    iv = _make_interrupt_value(action_requests, review_configs)
    return {
        "type": "updates",
        "data": {
            "__interrupt__": (MockInterrupt(iv),),
        },
    }


def _config_with_auto_approve():
    return Config(hitl=HitlConfig(auto_approve=[
        AutoApproveRule(tool="terminal", args_patterns=[r"^.*ls.*$"]),
    ]))


def _is_text_event(e):
    return e["kind"] == "text"


def _is_structured_event(e, event_type=None):
    if e["kind"] != "event":
        return False
    if event_type is None:
        return True
    return e["payload"]["type"] == event_type


def _envelope_keys(e):
    assert e["kind"] == "event"
    p = e["payload"]
    return set(p.keys())


# ── Tests for basic token streaming ──

@pytest.mark.asyncio
class TestStreamingTokens:
    async def test_single_token(self):
        agent = FakeStreamingAgent(chunk_sequences=[[_token_chunk("hello")]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        text_events = [e for e in events if _is_text_event(e)]
        assert len(text_events) == 1
        assert text_events[0]["payload"] == "hello"

    async def test_multiple_tokens(self):
        agent = FakeStreamingAgent(chunk_sequences=[[
            _token_chunk("hello "),
            _token_chunk("world"),
        ]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        text_events = [e for e in events if _is_text_event(e)]
        assert len(text_events) == 2
        assert text_events[0]["payload"] == "hello "
        assert text_events[1]["payload"] == "world"

    async def test_empty_stream(self):
        agent = FakeStreamingAgent(chunk_sequences=[[]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        assert len(events) == 0


# ── Tests for tool calls ──

@pytest.mark.asyncio
class TestStreamingToolCalls:
    async def test_tool_call_and_result(self):
        agent = FakeStreamingAgent(chunk_sequences=[[
            _tool_call_chunk("terminal", '{"comm', tc_id="c1"),
            _tool_call_chunk(None, 'ands": "ls"}', tc_id="c1"),
            _tool_result_update("terminal", "file1\nfile2", tool_call_id="c1"),
            _token_chunk("Here are the files."),
        ]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("list files", "t1"):
            events.append(evt)

        tc_events = [e for e in events if _is_structured_event(e, "tool_call")]
        tr_events = [e for e in events if _is_structured_event(e, "tool_result")]
        text_events = [e for e in events if _is_text_event(e)]

        assert len(tc_events) == 1
        assert len(tr_events) == 1
        assert len(text_events) == 1

        tc_idx = events.index(tc_events[0])
        tr_idx = events.index(tr_events[0])
        assert tc_idx < tr_idx

        tc_data = tc_events[0]["payload"]["data"]
        assert tc_data["name"] == "terminal"
        assert tc_data["args"] == {"commands": "ls"}
        assert tc_data["call_id"] == "c1"

        tr_data = tr_events[0]["payload"]["data"]
        assert tr_data["call_id"] == "c1"
        assert tr_data["is_error"] is False

    async def test_envelope_fields(self):
        agent = FakeStreamingAgent(chunk_sequences=[[_token_chunk("hi")]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        # text events just have kind + payload string
        assert events[0]["kind"] == "text"
        assert isinstance(events[0]["payload"], str)


# ── Tests for HITL interrupt handling ──

@pytest.mark.asyncio
class TestStreamingHITL:
    async def test_auto_approved_interrupt_resumes(self):
        cfg = _config_with_auto_approve()
        agent = FakeStreamingAgent(chunk_sequences=[
            [
                _token_chunk("Let me check."),
                _tool_call_chunk("terminal", '{"commands": "ls"}', tc_id="c1"),
                _interrupt_update([_make_action("terminal", {"commands": "ls"})]),
            ],
            [
                _tool_result_update("terminal", "file1", tool_call_id="c1"),
                _token_chunk("Done."),
            ],
        ])
        handler = StreamingApprovalHandler(agent, cfg)

        events = []
        async for evt in handler.run_streaming("list", "t1"):
            events.append(evt)

        approval_events = [e for e in events if _is_structured_event(e, "approval_request")]
        assert len(approval_events) == 0

        text_events = [e for e in events if _is_text_event(e)]
        assert any(e["payload"] == "Let me check." for e in text_events)
        assert any(e["payload"] == "Done." for e in text_events)

    async def test_human_needed_interrupt_yields_event(self):
        cfg = _config_with_auto_approve()
        agent = FakeStreamingAgent(chunk_sequences=[[
            _token_chunk("Let me check."),
            _interrupt_update([_make_action("terminal", {"commands": "rm -rf /"})]),
        ]])
        handler = StreamingApprovalHandler(agent, cfg)

        events = []
        async for evt in handler.run_streaming("delete", "t1"):
            events.append(evt)

        approval_events = [e for e in events if _is_structured_event(e, "approval_request")]
        assert len(approval_events) == 1

        ap = approval_events[0]
        assert _envelope_keys(ap) == {"id", "type", "timestamp", "data"}
        ap_data = ap["payload"]["data"]
        assert ap_data["approval_id"]
        assert ap_data["thread_id"] == "t1"
        assert len(ap_data["pending_actions"]) == 1
        assert ap_data["pending_actions"][0]["name"] == "terminal"
        assert ap_data["pending_actions"][0]["index"] == 0

    async def test_submit_decision_nonexistent(self):
        agent = FakeStreamingAgent(chunk_sequences=[[]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.submit_decision_streaming("nonexistent", []):
            events.append(evt)

        error_events = [e for e in events if _is_structured_event(e, "error")]
        assert len(error_events) == 1
        assert "不存在" in error_events[0]["payload"]["data"]["message"]


# ── Tests for error handling ──

@pytest.mark.asyncio
class TestStreamingErrors:
    async def test_exception_yields_error_event(self):
        class BrokenAgent:
            async def astream(self, *a, **kw):
                raise RuntimeError("boom")
                yield  # noqa: unreachable — makes this an async generator

        handler = StreamingApprovalHandler(BrokenAgent(), Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        error_events = [e for e in events if _is_structured_event(e, "error")]
        assert len(error_events) == 1
        err_data = error_events[0]["payload"]["data"]
        assert "boom" in err_data["message"]
        assert err_data["code"] == "runtimeerror"
