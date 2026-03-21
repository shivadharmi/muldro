"""Trace explanation service — user-visible trace summaries.

Translates raw traces into user-understandable explanations including
route reasoning, agent involvement, and consequences.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.traces import ModelCall, Trace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentInvolvement:
    agent_name: str
    call_count: int
    total_tokens: int
    cost_usd: float
    duration_ms: int
    tools_used: list[str]
    decisions: list[str]
    errors: list[str]


@dataclass(frozen=True)
class TraceExplanation:
    trace_id: str
    trigger: str
    status: str
    duration_ms: int | None
    summary: str
    route_reason: str
    agents: list[AgentInvolvement]
    tools_called: list[str]
    consequences: list[str]
    error_summary: str | None
    total_cost_usd: float


class TraceExplanationService:
    def __init__(self, db: AsyncSession, workspace_id: str) -> None:
        self._db = db
        self._workspace_id = workspace_id

    async def explain(self, trace_id: str) -> TraceExplanation | None:
        """Build a user-facing explanation of a trace."""
        result = await self._db.execute(
            select(Trace).where(
                Trace.trace_id == trace_id,
                Trace.workspace_id == self._workspace_id,
            )
        )
        trace = result.scalar_one_or_none()
        if not trace:
            return None

        # Get model calls for agent breakdown
        calls_result = await self._db.execute(
            select(ModelCall).where(ModelCall.trace_id == trace_id).order_by(ModelCall.created_at)
        )
        model_calls = calls_result.scalars().all()

        # Build agent involvement
        agent_map: dict[str, list[ModelCall]] = {}
        for call in model_calls:
            agent_map.setdefault(call.agent_name, []).append(call)

        agents = []
        for agent_name, calls in agent_map.items():
            tools: list[str] = []
            decisions: list[str] = []
            errors: list[str] = []
            for c in calls:
                if c.tools_called:
                    tools.extend(c.tools_called)
                if c.decision:
                    decisions.append(c.decision)
                if c.error:
                    errors.append(c.error[:200])

            agents.append(
                AgentInvolvement(
                    agent_name=agent_name,
                    call_count=len(calls),
                    total_tokens=sum(c.input_tokens + c.output_tokens for c in calls),
                    cost_usd=sum(c.cost_usd for c in calls),
                    duration_ms=sum(c.duration_ms for c in calls),
                    tools_used=sorted(set(tools)),
                    decisions=decisions,
                    errors=errors,
                )
            )

        # Build route reason
        route_reason = self._explain_route(trace, agents)

        # Build consequences
        consequences = self._derive_consequences(trace, agents)

        # Build summary
        summary = self._build_summary(trace, agents)

        # Error summary
        error_summary = None
        if trace.error_count and trace.error_count > 0:
            all_errors = [e for a in agents for e in a.errors]
            if all_errors:
                error_summary = f"{len(all_errors)} error(s): {all_errors[0]}"

        return TraceExplanation(
            trace_id=trace_id,
            trigger=trace.trigger,
            status=trace.status,
            duration_ms=trace.duration_ms,
            summary=summary,
            route_reason=route_reason,
            agents=agents,
            tools_called=trace.tools_called or [],
            consequences=consequences,
            error_summary=error_summary,
            total_cost_usd=trace.total_cost_usd,
        )

    def _explain_route(self, trace: Trace, agents: list[AgentInvolvement]) -> str:
        """Explain why this route was chosen."""
        agent_names = [a.agent_name for a in agents]
        trigger = trace.trigger

        if trigger == "user_message":
            return f"User message routed through {', '.join(agent_names)}"
        elif trigger.startswith("perception_"):
            source = trigger.replace("perception_", "")
            return f"New activity detected from {source}, processed by {', '.join(agent_names)}"
        elif trigger.startswith("scheduled_"):
            return f"Scheduled task triggered {', '.join(agent_names)}"
        elif trigger == "trigger_fired":
            return f"Automation trigger activated {', '.join(agent_names)}"
        return f"Route: {trigger} → {', '.join(agent_names)}"

    def _derive_consequences(self, trace: Trace, agents: list[AgentInvolvement]) -> list[str]:
        """Derive user-visible consequences from the trace."""
        consequences: list[str] = []

        if trace.memory_writes and trace.memory_writes > 0:
            consequences.append(f"Updated {trace.memory_writes} memories")

        if trace.approval_ids:
            consequences.append(f"Created {len(trace.approval_ids)} approval request(s)")

        all_tools = trace.tools_called or []
        send_tools = [t for t in all_tools if "send" in t.lower()]
        if send_tools:
            consequences.append(f"Sent {len(send_tools)} message(s)")

        create_tools = [t for t in all_tools if "create" in t.lower()]
        if create_tools:
            consequences.append(f"Created {len(create_tools)} item(s)")

        if trace.status == "failed":
            consequences.append("Task failed — may need retry")

        if not consequences:
            if trace.status == "completed":
                consequences.append("Completed successfully")
            else:
                consequences.append(f"Status: {trace.status}")

        return consequences

    def _build_summary(self, trace: Trace, agents: list[AgentInvolvement]) -> str:
        """Build a concise summary of what happened."""
        if trace.final_result:
            return trace.final_result[:500]

        parts: list[str] = []
        if trace.context_summary:
            parts.append(trace.context_summary[:200])

        agent_str = ", ".join(a.agent_name for a in agents[:3])
        if agents:
            parts.append(f"Agents: {agent_str}")

        tool_count = len(trace.tools_called or [])
        if tool_count:
            parts.append(f"{tool_count} tool calls")

        return "; ".join(parts) if parts else f"Trace {trace.trace_id} ({trace.status})"

    async def list_recent(self, limit: int = 20) -> list[TraceExplanation]:
        """List recent trace explanations."""
        result = await self._db.execute(
            select(Trace)
            .where(Trace.workspace_id == self._workspace_id)
            .order_by(Trace.started_at.desc())
            .limit(limit)
        )
        traces = result.scalars().all()
        explanations = []
        for trace in traces:
            explanation = await self.explain(trace.trace_id)
            if explanation:
                explanations.append(explanation)
        return explanations
