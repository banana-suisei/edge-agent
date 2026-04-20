from __future__ import annotations

import json
from enum import Enum
from uuid import uuid4

from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.types import Command

from plush_agent.hitl.approval_handler import ApprovalHandler, PendingApproval
from plush_agent.config import Config


class SSEEventType(str, Enum):
    TOKEN = "token"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    INTERRUPT = "interrupt"
    DONE = "done"
    ERROR = "error"


class StreamingApprovalHandler(ApprovalHandler):
    def __init__(self, agent, config: Config) -> None:
        super().__init__(agent, config)
        self.streaming_pending: dict[str, PendingApproval] = {}

    def _process_tool_call_chunks(self, token, tool_call_acc):
        """Accumulate tool call fragments and return completed tool calls."""
        completed = []
        for tc in token.tool_call_chunks:
            tc_id = tc.get("id")
            if not tc_id:
                continue
            entry = tool_call_acc.setdefault(tc_id, {"name": "", "args_str": ""})
            if tc.get("name"):
                entry["name"] = tc["name"]
            if tc.get("args"):
                entry["args_str"] += tc["args"]
        return completed

    def _flush_completed_tool_calls(self, tool_call_acc, tool_call_id=None):
        """Emit completed tool_call events for IDs whose results have arrived."""
        events = []
        if tool_call_id and tool_call_id in tool_call_acc:
            acc = tool_call_acc.pop(tool_call_id)
            try:
                args = json.loads(acc["args_str"])
            except json.JSONDecodeError:
                args = acc["args_str"]
            events.append(
                {"event": SSEEventType.TOOL_CALL, "data": {"name": acc["name"], "args": args, "id": tool_call_id}}
            )
        return events

    async def _stream_agent(self, input_or_command, config, tool_call_acc):
        """Core streaming loop. Yields SSE event dicts."""
        async for chunk in self.agent.astream(
            input_or_command,
            config=config,
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            chunk_type = chunk.get("type")

            if chunk_type == "messages":
                token, metadata = chunk["data"]
                if not isinstance(token, AIMessageChunk):
                    continue
                if token.content:
                    yield {"event": SSEEventType.TOKEN, "data": {"content": token.content}}
                self._process_tool_call_chunks(token, tool_call_acc)

            elif chunk_type == "updates":
                update_data = chunk.get("data", {})
                if not isinstance(update_data, dict):
                    continue

                # Top-level __interrupt__ (LangGraph emits this as
                # {"__interrupt__": (Interrupt(...),)} in updates mode)
                if "__interrupt__" in update_data:
                    async for evt in self._handle_interrupt(
                        update_data["__interrupt__"], config, tool_call_acc
                    ):
                        yield evt
                    return

                for node_name, node_output in update_data.items():
                    if not isinstance(node_output, dict):
                        continue

                    # Tool results — emit tool_call first, then tool_result
                    if node_name == "tools":
                        msgs = node_output.get("messages", [])
                        for m in msgs:
                            if isinstance(m, ToolMessage):
                                for evt in self._flush_completed_tool_calls(tool_call_acc, m.tool_call_id):
                                    yield evt
                                yield {
                                    "event": SSEEventType.TOOL_RESULT,
                                    "data": {"name": m.name, "content": m.content},
                                }

    async def _handle_interrupt(self, interrupts, config, tool_call_acc):
        """Process an interrupt: auto-approve or yield interrupt event."""
        # interrupts can be a tuple/list of Interrupt objects or a single dict
        interrupt = interrupts[0] if isinstance(interrupts, (tuple, list)) else interrupts
        action_requests = interrupt.value.get("action_requests", [])
        review_configs = interrupt.value.get("review_configs", [])

        decisions: list[dict | None] = []
        needs_human: list[tuple[int, dict]] = []

        for i, action in enumerate(action_requests):
            if self.should_auto_approve(action["name"], action["args"]):
                decisions.append({"type": "approve"})
            else:
                decisions.append(None)
                needs_human.append((i, action))

        if not needs_human:
            async for evt in self._stream_agent(
                Command(resume={"decisions": decisions}), config, tool_call_acc
            ):
                yield evt
            return

        pending_actions = []
        for idx, action in needs_human:
            allowed = ["approve", "edit", "reject"]
            for rc in review_configs:
                if rc.get("action_name") == action["name"]:
                    allowed = rc.get("allowed_decisions", allowed)
                    break
            pending_actions.append({
                "name": action["name"],
                "args": action["args"],
                "description": action.get("description", ""),
                "allowed_decisions": allowed,
            })

        approval_id = str(uuid4())
        thread_id = config["configurable"]["thread_id"]
        self.streaming_pending[thread_id] = PendingApproval(
            approval_id=approval_id,
            thread_id=thread_id,
            config=config,
            action_requests=action_requests,
            decisions=decisions,
            needs_human_indices=[i for i, _ in needs_human],
        )

        yield {
            "event": SSEEventType.INTERRUPT,
            "data": {
                "approval_id": approval_id,
                "pending_actions": pending_actions,
                "auto_approved_count": len(action_requests) - len(needs_human),
            },
        }

    async def run_streaming(self, message: str, thread_id: str):
        """Stream agent response, yielding SSE event dicts."""
        config = {"configurable": {"thread_id": thread_id}}
        tool_call_acc: dict[str, dict] = {}

        try:
            async for evt in self._stream_agent(
                {"messages": [{"role": "user", "content": message}]},
                config,
                tool_call_acc,
            ):
                yield evt

            yield {"event": SSEEventType.DONE, "data": {"content": ""}}
        except Exception as e:
            yield {"event": SSEEventType.ERROR, "data": {"message": f"{type(e).__name__}: {e}"}}

    async def submit_decision_streaming(
        self, approval_id: str, human_decisions: list[dict]
    ):
        """Resume after HITL approval with streaming, yielding SSE event dicts."""
        pending = None
        thread_id = None
        for tid, p in self.streaming_pending.items():
            if p.approval_id == approval_id:
                pending = p
                thread_id = tid
                break

        if pending is None:
            yield {"event": SSEEventType.ERROR, "data": {"message": "审批请求不存在或已过期"}}
            return

        for j, idx in enumerate(pending.needs_human_indices):
            pending.decisions[idx] = human_decisions[j]

        self.streaming_pending.pop(thread_id, None)
        tool_call_acc: dict[str, dict] = {}

        try:
            async for evt in self._stream_agent(
                Command(resume={"decisions": pending.decisions}),
                pending.config,
                tool_call_acc,
            ):
                yield evt

            yield {"event": SSEEventType.DONE, "data": {"content": ""}}
        except Exception as e:
            yield {"event": SSEEventType.ERROR, "data": {"message": f"{type(e).__name__}: {e}"}}
