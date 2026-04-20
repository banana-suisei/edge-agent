"""Tests for StreamingApprovalHandler — SSE streaming with HITL support."""
from __future__ import annotations

import pytest

from langchain_core.messages import AIMessageChunk, ToolMessage

from plush_agent.config import AutoApproveRule, Config, HitlConfig
from plush_agent.hitl.streaming_handler import SSEEventType, StreamingApprovalHandler


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
    def __init__(self, value):
        self.value = value


class FakeStreamingAgent:
    """Fake agent whose astream yields pre-configured v2 chunks per call."""

    def __init__(self, chunk_sequences=None, invoke_responses=None):
        # chunk_sequences: list of lists — each astream() call consumes one list
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


# ── Tests for basic token streaming ──

@pytest.mark.asyncio
class TestStreamingTokens:
    async def test_single_token(self):
        agent = FakeStreamingAgent(chunk_sequences=[[ _token_chunk("hello") ]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        assert events[0]["event"] == SSEEventType.TOKEN
        assert events[0]["data"]["content"] == "hello"
        assert events[-1]["event"] == SSEEventType.DONE

    async def test_multiple_tokens(self):
        agent = FakeStreamingAgent(chunk_sequences=[[
            _token_chunk("hello "),
            _token_chunk("world"),
        ]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        token_events = [e for e in events if e["event"] == SSEEventType.TOKEN]
        assert len(token_events) == 2
        assert token_events[0]["data"]["content"] == "hello "
        assert token_events[1]["data"]["content"] == "world"

    async def test_empty_stream(self):
        agent = FakeStreamingAgent(chunk_sequences=[[]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.run_streaming("hi", "t1"):
            events.append(evt)

        assert len(events) == 1
        assert events[0]["event"] == SSEEventType.DONE


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

        types = [e["event"] for e in events]
        assert SSEEventType.TOOL_CALL in types
        assert SSEEventType.TOOL_RESULT in types
        assert SSEEventType.TOKEN in types

        # tool_call event should appear before tool_result
        tc_idx = types.index(SSEEventType.TOOL_CALL)
        tr_idx = types.index(SSEEventType.TOOL_RESULT)
        assert tc_idx < tr_idx

        # Verify tool_call data
        tc_event = events[tc_idx]
        assert tc_event["data"]["name"] == "terminal"
        assert tc_event["data"]["args"] == {"commands": "ls"}


# ── Tests for HITL interrupt handling ──

@pytest.mark.asyncio
class TestStreamingHITL:
    async def test_auto_approved_interrupt_resumes(self):
        """All actions auto-approved → stream resumes internally, no interrupt event."""
        cfg = _config_with_auto_approve()
        # First astream call: token + interrupt; second call: more tokens
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

        types = [e["event"] for e in events]
        assert SSEEventType.INTERRUPT not in types
        # Should have tokens from both calls
        token_events = [e for e in events if e["event"] == SSEEventType.TOKEN]
        assert any(e["data"]["content"] == "Let me check." for e in token_events)
        assert any(e["data"]["content"] == "Done." for e in token_events)

    async def test_human_needed_interrupt_yields_event(self):
        """Non-matching action → interrupt event emitted."""
        cfg = _config_with_auto_approve()
        agent = FakeStreamingAgent(chunk_sequences=[[
            _token_chunk("Let me check."),
            _interrupt_update([_make_action("terminal", {"commands": "rm -rf /"})]),
        ]])
        handler = StreamingApprovalHandler(agent, cfg)

        events = []
        async for evt in handler.run_streaming("delete", "t1"):
            events.append(evt)

        types = [e["event"] for e in events]
        assert SSEEventType.INTERRUPT in types

        interrupt_evt = [e for e in events if e["event"] == SSEEventType.INTERRUPT][0]
        assert interrupt_evt["data"]["approval_id"]
        assert len(interrupt_evt["data"]["pending_actions"]) == 1
        assert interrupt_evt["data"]["pending_actions"][0]["name"] == "terminal"

    async def test_submit_decision_nonexistent(self):
        agent = FakeStreamingAgent(chunk_sequences=[[]])
        handler = StreamingApprovalHandler(agent, Config())

        events = []
        async for evt in handler.submit_decision_streaming("nonexistent", []):
            events.append(evt)

        assert events[0]["event"] == SSEEventType.ERROR
        assert "不存在" in events[0]["data"]["message"]


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

        assert events[0]["event"] == SSEEventType.ERROR
        assert "boom" in events[0]["data"]["message"]
