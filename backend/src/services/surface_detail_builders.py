"""Tab builder functions for surface detail modal.

Each builder fetches grounded data from existing services and returns
a DetailTabResponse with collapsible A2UI sections. The detail API
dispatches to builders via the TAB_BUILDERS registry keyed on (kind, tab_id).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailSection, DetailTabResponse

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────


def _section(
    sid: str,
    title: str,
    children: list[A2UIComponent],
    collapsed: bool = True,
) -> DetailSection:
    return DetailSection(id=sid, title=title, collapsed=collapsed, children=children)


def _empty_tab(tab_id: str, message: str = "No data available.") -> DetailTabResponse:
    return DetailTabResponse(
        tab_id=tab_id,
        sections=[_section("empty", "Info", [r.text("empty_msg", message)], collapsed=False)],
    )


def _get_payload(surface: Any) -> dict:
    return getattr(surface, "payload", None) or {}


def _extract_run_id(surface: Any) -> str | None:
    payload = _get_payload(surface)
    meta = payload.get("metadata", {})
    return payload.get("source_run_id") or meta.get("source_run_id") or meta.get("run_id")


def _extract_approval_id(surface: Any) -> str | None:
    surface_id = getattr(surface, "surface_id", "") or ""
    if surface_id.startswith("approval_"):
        return surface_id.removeprefix("approval_")
    if surface_id.startswith("notif_surf_"):
        payload = _get_payload(surface)
        return payload.get("metadata", {}).get("approval_id")
    return None


def _extract_briefing_id(surface: Any) -> str | None:
    surface_id = getattr(surface, "surface_id", "") or ""
    if surface_id.startswith("briefing_"):
        return surface_id.removeprefix("briefing_")
    payload = _get_payload(surface)
    return payload.get("metadata", {}).get("briefing_id")


def _get_step_desc(step: Any) -> str:
    input_data = getattr(step, "input_data", None)
    if isinstance(input_data, dict):
        return input_data.get("description", "")
    return ""


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_ts(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


# ── Plan builders ───────────────────────────────────────────────


async def build_plan_overview(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan overview — TaskRun + TaskSteps with statuses."""
    from src.models.task_graph import TaskRun, TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("overview", "No linked execution run.")

    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        return _empty_tab("overview", f"Run {run_id[:16]}... not found.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())

    run_children: list[A2UIComponent] = [
        r.badge("run_status", run.status or "unknown"),
        r.text("run_source", f"Source: {run.source or 'unknown'}"),
    ]
    if run.started_at:
        run_children.append(r.caption("run_started", f"Started: {_format_ts(run.started_at)}"))
    if run.completed_at:
        run_children.append(r.caption("run_done", f"Completed: {_format_ts(run.completed_at)}"))

    step_children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        variant = "success" if step.status == "completed" else "default"
        step_children.append(
            r.row(
                f"step_{i}",
                [
                    r.badge(f"step_{i}_st", step.status or "pending", variant=variant),
                    r.text(f"step_{i}_name", step.name or step.step_type or f"Step {i + 1}"),
                    r.caption(f"step_{i}_desc", _truncate(_get_step_desc(step), 100)),
                ],
            )
        )

    completed = sum(1 for s in steps if s.status == "completed")
    total = len(steps)
    sections = [_section("summary", "Run Summary", run_children, collapsed=False)]
    if step_children:
        sections.append(
            _section("steps", f"Steps ({completed}/{total})", step_children, collapsed=False)
        )
    return DetailTabResponse(tab_id="overview", sections=sections)


async def build_plan_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan context — memories, entities from the context pack."""
    from src.models.task_graph import TaskRun

    run_id = _extract_run_id(surface)
    sections: list[DetailSection] = []

    if run_id:
        run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = run_result.scalar_one_or_none()
        ctx = (run.context_pack_json if run else None) or {}

        if ctx.get("memories"):
            mem_children = [
                r.memory_card(
                    f"ctx_mem_{i}",
                    m.get("fact_text", ""),
                    m.get("memory_type", "factual"),
                    source=m.get("source", ""),
                )
                for i, m in enumerate(ctx["memories"][:10])
            ]
            sections.append(_section("memories", "Related Memories", mem_children))

        if ctx.get("entities"):
            ent_children = [
                r.entity_card(
                    f"ctx_ent_{i}",
                    e.get("name", ""),
                    e.get("entity_type", "unknown"),
                )
                for i, e in enumerate(ctx["entities"][:10])
            ]
            sections.append(_section("entities", "Related Entities", ent_children))

    if not sections:
        return _empty_tab("context", "No context data available for this plan.")
    return DetailTabResponse(tab_id="context", sections=sections)


async def build_plan_execution(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan execution trace — step-by-step tool calls, results, timings."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("execution", "No linked execution run.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("execution", "No execution steps recorded.")

    trace_events = []
    for step in steps:
        event: dict[str, str] = {
            "label": step.name or step.step_type or "step",
            "status": step.status or "pending",
            "description": _truncate(_get_step_desc(step), 80),
        }
        if step.output_data and isinstance(step.output_data, dict):
            event["result"] = _truncate(str(step.output_data), 200)
        trace_events.append(event)

    return DetailTabResponse(
        tab_id="execution",
        sections=[
            _section(
                "trace",
                "Execution Trace",
                [r.execution_trace("exec_trace", trace_events)],
                collapsed=False,
            )
        ],
    )


# ── Summary builders ────────────────────────────────────────────


async def build_summary_overview(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    payload = _get_payload(surface)
    text_content = payload.get("response_preview", "") or ""
    children: list[A2UIComponent] = [
        r.text("summary_text", text_content or "No summary content available.")
    ]
    return DetailTabResponse(
        tab_id="overview",
        sections=[_section("content", "Summary", children, collapsed=False)],
    )


async def build_summary_sources(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Summary sources — recent perception events from this workspace."""
    from src.models.events import NormalizedEvent

    run_id = _extract_run_id(surface)
    # Try to get workspace_id from surface
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        payload = _get_payload(surface)
        ws_id = payload.get("workspace_id")

    if not ws_id and not run_id:
        return _empty_tab("sources", "No source data available.")

    # Fetch recent events (last 24h, up to 20)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    query = (
        select(NormalizedEvent)
        .where(NormalizedEvent.occurred_at >= cutoff)
        .order_by(NormalizedEvent.occurred_at.desc())
        .limit(20)
    )
    if ws_id:
        query = query.where(NormalizedEvent.workspace_id == ws_id)

    result = await db.execute(query)
    events = list(result.scalars().all())

    if not events:
        return _empty_tab("sources", "No recent perception events.")

    event_children: list[A2UIComponent] = []
    for i, evt in enumerate(events):
        event_children.append(
            r.row(
                f"evt_{i}",
                [
                    r.badge(f"evt_{i}_src", evt.source or "unknown"),
                    r.text(f"evt_{i}_title", _truncate(evt.title or evt.event_type or "", 80)),
                    r.caption(f"evt_{i}_time", _format_ts(evt.occurred_at)),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="sources",
        sections=[
            _section("events", f"Recent Events ({len(events)})", event_children, collapsed=False)
        ],
    )


async def build_summary_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Summary context — memories that may have informed the summary."""
    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("context", "No linked run for context lookup.")

    # Check context pack on the run
    from src.models.task_graph import TaskRun

    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
    run = run_result.scalar_one_or_none()
    ctx = (run.context_pack_json if run else None) or {}

    sections: list[DetailSection] = []
    if ctx.get("memories"):
        mem_children = [
            r.memory_card(
                f"ctx_mem_{i}",
                m.get("fact_text", ""),
                m.get("memory_type", "factual"),
                source=m.get("source", ""),
            )
            for i, m in enumerate(ctx["memories"][:10])
        ]
        sections.append(_section("memories", "Related Memories", mem_children))

    if not sections:
        return _empty_tab("context", "No context data available.")
    return DetailTabResponse(tab_id="context", sections=sections)


# ── Briefing builders ───────────────────────────────────────────


async def build_briefing_priorities(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    from src.models.briefings import Briefing

    briefing_id = _extract_briefing_id(surface)
    if not briefing_id:
        return _empty_tab("priorities", "No linked briefing found.")

    result = await db.execute(select(Briefing).where(Briefing.briefing_id == briefing_id))
    briefing = result.scalar_one_or_none()
    if not briefing:
        return _empty_tab("priorities", "Briefing not found.")

    priorities = briefing.top_priorities or []
    children: list[A2UIComponent] = []
    for i, p in enumerate(priorities):
        title = p.get("title", "") if isinstance(p, dict) else str(p)
        why = p.get("why", "") if isinstance(p, dict) else ""
        children.append(r.text(f"pri_{i}_title", title))
        if why:
            children.append(r.caption(f"pri_{i}_why", why))
        if i < len(priorities) - 1:
            children.append(r.divider(f"pri_{i}_div"))

    if not children:
        return _empty_tab("priorities", "No priorities in today's briefing.")
    return DetailTabResponse(
        tab_id="priorities",
        sections=[_section("priorities", "Top Priorities", children, collapsed=False)],
    )


async def build_briefing_events(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Briefing events — recent perception events from the last 24h."""
    from src.models.events import NormalizedEvent

    # Get workspace_id from surface or query from briefing
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        briefing_id = _extract_briefing_id(surface)
        if briefing_id:
            from src.models.briefings import Briefing

            br = await db.execute(
                select(Briefing.workspace_id).where(Briefing.briefing_id == briefing_id)
            )
            row = br.first()
            ws_id = row[0] if row else None

    if not ws_id:
        return _empty_tab("events", "Could not resolve workspace for events.")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(NormalizedEvent)
        .where(
            NormalizedEvent.workspace_id == ws_id,
            NormalizedEvent.occurred_at >= cutoff,
        )
        .order_by(NormalizedEvent.occurred_at.desc())
        .limit(30)
    )
    events = list(result.scalars().all())

    if not events:
        return _empty_tab("events", "No events in the last 24 hours.")

    # Group by source
    by_source: dict[str, list] = {}
    for evt in events:
        by_source.setdefault(evt.source or "unknown", []).append(evt)

    sections: list[DetailSection] = []
    for source, source_events in by_source.items():
        children: list[A2UIComponent] = []
        for i, evt in enumerate(source_events[:10]):
            children.append(
                r.row(
                    f"{source}_{i}",
                    [
                        r.text(
                            f"{source}_{i}_title",
                            _truncate(evt.title or evt.event_type or "event", 80),
                        ),
                        r.caption(f"{source}_{i}_time", _format_ts(evt.occurred_at)),
                    ],
                )
            )
        sections.append(
            _section(f"src_{source}", f"{source} ({len(source_events)})", children, collapsed=False)
        )

    return DetailTabResponse(tab_id="events", sections=sections)


async def build_briefing_actions(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    from src.models.briefings import Briefing

    briefing_id = _extract_briefing_id(surface)
    if not briefing_id:
        return _empty_tab("actions", "No linked briefing found.")

    result = await db.execute(select(Briefing).where(Briefing.briefing_id == briefing_id))
    briefing = result.scalar_one_or_none()
    if not briefing:
        return _empty_tab("actions", "Briefing not found.")

    actions = briefing.recommended_actions or []
    children: list[A2UIComponent] = []
    for i, action in enumerate(actions):
        title = action.get("title", "") if isinstance(action, dict) else str(action)
        desc = action.get("description", "") if isinstance(action, dict) else ""
        children.append(r.text(f"act_{i}_title", title))
        if desc:
            children.append(r.caption(f"act_{i}_desc", desc))
        if i < len(actions) - 1:
            children.append(r.divider(f"act_{i}_div"))

    if not children:
        return _empty_tab("actions", "No recommended actions.")
    return DetailTabResponse(
        tab_id="actions",
        sections=[_section("actions", "Recommended Actions", children, collapsed=False)],
    )


# ── Approval builders ──────────────────────────────────────────


async def build_approval_request(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    from src.models.approvals import Approval

    approval_id = _extract_approval_id(surface)
    if not approval_id:
        return _empty_tab("request", "No linked approval.")

    result = await db.execute(select(Approval).where(Approval.approval_id == approval_id))
    apr = result.scalar_one_or_none()
    if not apr:
        return _empty_tab("request", f"Approval {approval_id[:16]}... not found.")

    children: list[A2UIComponent] = [
        r.text("apr_title", apr.title or "Approval Request"),
        r.text("apr_summary", apr.summary or "No details available."),
    ]
    if apr.artifact_refs and isinstance(apr.artifact_refs, dict):
        tool_name = apr.artifact_refs.get("tool_name", "")
        if tool_name:
            children.append(r.badge("apr_tool", f"Tool: {tool_name}"))
        tool_params = apr.artifact_refs.get("tool_params")
        if tool_params:
            children.append(r.code_block("apr_params", str(tool_params), language="json"))

    risk_variant = "warning" if apr.risk_level in ("high", "critical") else "default"
    children.append(r.badge("apr_risk", apr.risk_level or "medium", variant=risk_variant))

    if apr.status == "pending":
        children.append(
            r.row(
                "apr_actions",
                [
                    r.button(
                        f"approve_{apr.approval_id}",
                        "Approve",
                        variant="primary",
                        action_payload={"action": "approve", "id": apr.approval_id},
                    ),
                    r.button(
                        f"reject_{apr.approval_id}",
                        "Reject",
                        variant="danger",
                        action_payload={"action": "reject", "id": apr.approval_id},
                    ),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="request",
        sections=[_section("details", "Request Details", children, collapsed=False)],
    )


async def build_approval_risk(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    from src.models.approvals import Approval

    approval_id = _extract_approval_id(surface)
    if not approval_id:
        return _empty_tab("risk", "No linked approval.")

    result = await db.execute(select(Approval).where(Approval.approval_id == approval_id))
    apr = result.scalar_one_or_none()
    if not apr:
        return _empty_tab("risk", "Approval not found.")

    children: list[A2UIComponent] = []
    risk_variant = "danger" if apr.risk_level in ("high", "critical") else "warning"
    children.append(r.badge("risk_level", apr.risk_level or "medium", variant=risk_variant))

    if apr.summary:
        children.append(r.text("risk_just", apr.summary))
    if apr.decision_reason:
        children.append(r.text("risk_reason", apr.decision_reason))
    if apr.status and apr.status != "pending":
        children.append(r.badge("risk_decision", apr.status))

    return DetailTabResponse(
        tab_id="risk",
        sections=[_section("assessment", "Risk Assessment", children, collapsed=False)],
    )


async def build_approval_history(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Approval history — past approvals of the same type."""
    from src.models.approvals import Approval

    approval_id = _extract_approval_id(surface)
    if not approval_id:
        return _empty_tab("history", "No linked approval.")

    # Get the current approval to find its type
    curr = await db.execute(select(Approval).where(Approval.approval_id == approval_id))
    current = curr.scalar_one_or_none()
    if not current:
        return _empty_tab("history", "Approval not found.")

    # Find past approvals of the same type
    result = await db.execute(
        select(Approval)
        .where(
            Approval.workspace_id == current.workspace_id,
            Approval.approval_type == current.approval_type,
            Approval.approval_id != approval_id,
            Approval.status.in_(["approved", "rejected"]),
        )
        .order_by(Approval.decided_at.desc())
        .limit(10)
    )
    past = list(result.scalars().all())

    if not past:
        return _empty_tab("history", "No prior approvals of this type.")

    children: list[A2UIComponent] = []
    for i, apr in enumerate(past):
        variant = "success" if apr.status == "approved" else "danger"
        children.append(
            r.row(
                f"hist_{i}",
                [
                    r.badge(f"hist_{i}_st", apr.status, variant=variant),
                    r.text(f"hist_{i}_title", _truncate(apr.title or "", 60)),
                    r.caption(f"hist_{i}_time", _format_ts(apr.decided_at)),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="history",
        sections=[_section("past", f"Past Decisions ({len(past)})", children, collapsed=False)],
    )


# ── Recommendation builders ─────────────────────────────────────


async def build_recommendation_overview(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    payload = _get_payload(surface)
    text_content = payload.get("response_preview", "")
    preview = payload.get("preview", {})
    title = preview.get("title", "") if isinstance(preview, dict) else ""

    children: list[A2UIComponent] = [
        r.text("rec_text", text_content or title or "No recommendation content.")
    ]
    return DetailTabResponse(
        tab_id="overview",
        sections=[_section("content", "Recommendation", children, collapsed=False)],
    )


async def build_recommendation_context(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Recommendation context — related memories."""
    from src.models.memory import Memory

    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        return _empty_tab("context", "No workspace context available.")

    # Fetch recent active memories as general context
    result = await db.execute(
        select(Memory)
        .where(
            Memory.workspace_id == ws_id,
            Memory.status == "active",
        )
        .order_by(Memory.last_accessed_at.desc().nullslast())
        .limit(10)
    )
    memories = list(result.scalars().all())

    if not memories:
        return _empty_tab("context", "No related memories found.")

    children = [
        r.memory_card(
            f"mem_{i}",
            mem.fact_text or "",
            mem.memory_type or "factual",
            source="memory",
            confidence=mem.confidence or 0.5,
        )
        for i, mem in enumerate(memories)
    ]
    return DetailTabResponse(
        tab_id="context",
        sections=[_section("memories", "Related Memories", children, collapsed=False)],
    )


# ── Alert builder ───────────────────────────────────────────────


async def build_alert_overview(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Alert overview — run details for blocked/priority alerts."""
    from src.models.task_graph import TaskRun

    run_id = _extract_run_id(surface)
    children: list[A2UIComponent] = []

    if run_id:
        result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if run:
            children.append(r.badge("alert_status", run.status or "unknown", variant="warning"))
            children.append(r.text("alert_source", f"Source: {run.source or 'unknown'}"))
            if run.error and isinstance(run.error, dict):
                err_msg = run.error.get("message", str(run.error))
                children.append(
                    r.alert("alert_err", _truncate(str(err_msg), 200), severity="error")
                )
            if run.started_at:
                children.append(
                    r.caption("alert_started", f"Started: {_format_ts(run.started_at)}")
                )

    if not children:
        # Fallback to preview data
        payload = _get_payload(surface)
        preview = payload.get("preview", {})
        title = preview.get("title", "Alert") if isinstance(preview, dict) else "Alert"
        subtitle = preview.get("subtitle", "") if isinstance(preview, dict) else ""
        children.append(r.text("alert_title", title))
        if subtitle:
            children.append(r.caption("alert_detail", subtitle))

    return DetailTabResponse(
        tab_id="overview",
        sections=[_section("details", "Alert Details", children, collapsed=False)],
    )


# ── Registry ────────────────────────────────────────────────────

TAB_BUILDERS: dict[tuple[str, str], Any] = {
    ("plan", "overview"): build_plan_overview,
    ("plan", "context"): build_plan_context,
    ("plan", "execution"): build_plan_execution,
    ("summary", "overview"): build_summary_overview,
    ("summary", "sources"): build_summary_sources,
    ("summary", "context"): build_summary_context,
    ("briefing", "priorities"): build_briefing_priorities,
    ("briefing", "events"): build_briefing_events,
    ("briefing", "actions"): build_briefing_actions,
    ("approval", "request"): build_approval_request,
    ("approval", "risk"): build_approval_risk,
    ("approval", "history"): build_approval_history,
    ("recommendation", "overview"): build_recommendation_overview,
    ("recommendation", "context"): build_recommendation_context,
    ("alert", "overview"): build_alert_overview,
}
