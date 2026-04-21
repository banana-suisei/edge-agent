from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

from langgraph.types import Command

from plush_agent.config import Config

logger = logging.getLogger("plush_agent.hitl")


@dataclass
class PendingApproval:
    approval_id: str
    thread_id: str
    config: dict
    action_requests: list[dict]
    decisions: list[dict | None]
    needs_human_indices: list[int]
    interrupt_id: str = ""
    created_at: float = field(default_factory=time.time)


class ApprovalHandler:
    def __init__(self, agent, config: Config) -> None:
        self.agent = agent
        self.auto_approve_rules = config.hitl.auto_approve
        self.pending: dict[str, PendingApproval] = {}

    def should_auto_approve(self, tool_name: str, tool_args: dict) -> bool:
        for rule in self.auto_approve_rules:
            if rule.tool != tool_name:
                continue
            args_str = str(tool_args)
            for pattern in rule.args_patterns:
                if re.search(pattern, args_str):
                    logger.debug("auto-approve matched: tool=%s, pattern=%s, args_str=%s", tool_name, pattern, args_str)
                    return True
        return False

    @staticmethod
    def _extract_done(result) -> dict:
        state = result.value if hasattr(result, "value") else result
        messages = state.get("messages", []) if isinstance(state, dict) else []
        reply = ""
        for m in reversed(messages):
            if hasattr(m, "content") and m.type == "ai":
                reply = m.content
                break
        return {"status": "done", "content": reply}

    def _process_interrupt(self, interrupt_value: dict):
        """Process interrupt value, return (decisions, needs_human, pending_actions)."""
        action_requests = interrupt_value["action_requests"]
        review_configs = interrupt_value.get("review_configs", [])

        decisions: list[dict | None] = []
        needs_human: list[tuple[int, dict]] = []

        for i, action in enumerate(action_requests):
            if self.should_auto_approve(action["name"], action["args"]):
                decisions.append({"type": "approve"})
            else:
                decisions.append(None)
                needs_human.append((i, action))

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

        return action_requests, decisions, needs_human, pending_actions

    async def run_with_hitl(self, message: str, thread_id: str):
        config = {"configurable": {"thread_id": thread_id}}

        result = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            version="v2",
        )

        if not result.interrupts:
            return self._extract_done(result)

        interrupt = result.interrupts[0]
        action_requests, decisions, needs_human, pending_actions = (
            self._process_interrupt(interrupt.value)
        )

        if not needs_human:
            result = await self.agent.ainvoke(
                Command(resume={interrupt.id: {"decisions": decisions}}),
                config=config,
                version="v2",
            )
            return self._extract_done(result)

        approval_id = str(uuid4())
        self.pending[thread_id] = PendingApproval(
            approval_id=approval_id,
            thread_id=thread_id,
            config=config,
            action_requests=action_requests,
            decisions=decisions,
            needs_human_indices=[i for i, _ in needs_human],
            interrupt_id=interrupt.id,
        )

        return {
            "status": "pending_approval",
            "approval_id": approval_id,
            "pending_actions": pending_actions,
            "auto_approved_count": len(action_requests) - len(needs_human),
        }

    async def submit_decision(self, approval_id: str, human_decisions: list[dict]):
        pending = None
        thread_id = None
        for tid, p in self.pending.items():
            if p.approval_id == approval_id:
                pending = p
                thread_id = tid
                break

        if pending is None:
            return None

        for j, idx in enumerate(pending.needs_human_indices):
            pending.decisions[idx] = human_decisions[j]

        result = await self.agent.ainvoke(
            Command(resume={pending.interrupt_id: {"decisions": pending.decisions}}),
            config=pending.config,
            version="v2",
        )

        self.pending.pop(thread_id, None)

        if not result.interrupts:
            return self._extract_done(result)

        new_interrupt = result.interrupts[0]
        new_action_requests, new_decisions, new_needs_human, new_pending_actions = (
            self._process_interrupt(new_interrupt.value)
        )

        if not new_needs_human:
            result = await self.agent.ainvoke(
                Command(resume={new_interrupt.id: {"decisions": new_decisions}}),
                config=pending.config,
                version="v2",
            )
            return self._extract_done(result)

        new_approval_id = str(uuid4())
        self.pending[thread_id] = PendingApproval(
            approval_id=new_approval_id,
            thread_id=thread_id,
            config=pending.config,
            action_requests=new_action_requests,
            decisions=new_decisions,
            needs_human_indices=[i for i, _ in new_needs_human],
            interrupt_id=new_interrupt.id,
        )

        return {
            "status": "pending_approval",
            "approval_id": new_approval_id,
            "pending_actions": new_pending_actions,
            "auto_approved_count": len(new_action_requests) - len(new_needs_human),
        }

    def list_pending(self) -> list[dict]:
        out = []
        for p in self.pending.values():
            actions = []
            for idx in p.needs_human_indices:
                a = p.action_requests[idx]
                actions.append({
                    "name": a["name"],
                    "args": a["args"],
                    "description": a.get("description", ""),
                })
            out.append({
                "approval_id": p.approval_id,
                "pending_actions": actions,
                "auto_approved_count": len(p.action_requests) - len(p.needs_human_indices),
                "created_at": p.created_at,
            })
        return out

    def get_pending(self, approval_id: str) -> dict | None:
        for p in self.pending.values():
            if p.approval_id == approval_id:
                actions = []
                for idx in p.needs_human_indices:
                    a = p.action_requests[idx]
                    actions.append({
                        "name": a["name"],
                        "args": a["args"],
                        "description": a.get("description", ""),
                    })
                return {
                    "approval_id": p.approval_id,
                    "pending_actions": actions,
                    "auto_approved_count": len(p.action_requests) - len(p.needs_human_indices),
                    "created_at": p.created_at,
                }
        return None

