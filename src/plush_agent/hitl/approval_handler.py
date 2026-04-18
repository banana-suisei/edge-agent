from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

from langgraph.types import Command

from plush_agent.config import Config


@dataclass
class PendingApproval:
    approval_id: str
    thread_id: str
    config: dict
    action_requests: list[dict]
    decisions: list[dict | None]
    needs_human_indices: list[int]
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
                    return True
        return False

    async def run_with_hitl(self, message: str, thread_id: str):
        config = {"configurable": {"thread_id": thread_id}}

        result = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            version="v2",
        )

        if not result.interrupts:
            return result

        interrupt = result.interrupts[0]
        action_requests = interrupt.value["action_requests"]

        decisions: list[dict | None] = []
        needs_human: list[tuple[int, dict]] = []

        for i, action in enumerate(action_requests):
            if self.should_auto_approve(action["name"], action["arguments"]):
                decisions.append({"type": "approve"})
            else:
                decisions.append(None)
                needs_human.append((i, action))

        if not needs_human:
            return await self.agent.ainvoke(
                Command(resume={"decisions": decisions}),
                config=config,
                version="v2",
            )

        approval_id = str(uuid4())
        self.pending[thread_id] = PendingApproval(
            approval_id=approval_id,
            thread_id=thread_id,
            config=config,
            action_requests=action_requests,
            decisions=decisions,
            needs_human_indices=[i for i, _ in needs_human],
        )

        return {
            "status": "pending_approval",
            "approval_id": approval_id,
            "pending_actions": [
                {
                    "name": a["name"],
                    "arguments": a["arguments"],
                    "description": a.get("description", ""),
                    "allowed_decisions": a.get("allowed_decisions", ["approve", "edit", "reject"]),
                }
                for _, a in needs_human
            ],
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
            Command(resume={"decisions": pending.decisions}),
            config=pending.config,
            version="v2",
        )

        self.pending.pop(thread_id, None)
        return result

    def list_pending(self) -> list[dict]:
        out = []
        for p in self.pending.values():
            actions = []
            for idx in p.needs_human_indices:
                a = p.action_requests[idx]
                actions.append({
                    "name": a["name"],
                    "arguments": a["arguments"],
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
                        "arguments": a["arguments"],
                        "description": a.get("description", ""),
                    })
                return {
                    "approval_id": p.approval_id,
                    "pending_actions": actions,
                    "auto_approved_count": len(p.action_requests) - len(p.needs_human_indices),
                    "created_at": p.created_at,
                }
        return None

