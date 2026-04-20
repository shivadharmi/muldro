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

    # Trust context
    if apr.artifact_refs and isinstance(apr.artifact_refs, dict):
        cap = apr.artifact_refs.get("tool_name")
        if cap:
            from src.models.trust_state import TrustState

            trust_result = await db.execute(
                select(TrustState).where(
                    TrustState.workspace_id == apr.workspace_id,
                    TrustState.capability == cap,
                    TrustState.risk_level == (apr.risk_level or "low"),
                )
            )
            trust_state = trust_result.scalar_one_or_none()
            if trust_state:
                level = trust_state.trust_level
                count = trust_state.approved_count
                if level == "first_use":
                    children.append(r.badge("apr_trust", "First time", variant="default"))
                elif level == "learning":
                    remaining = max(0, 10 - count)
                    children.append(
                        r.text(
                            "apr_trust_hint",
                            f"Similar to {count} prior approvals — "
                            f"{remaining} more to auto-approve",
                        )
                    )
                else:
                    children.append(
                        r.badge(
                            "apr_trust",
                            level.title(),
                            variant="success",
                        )
                    )
            else:
                children.append(r.badge("apr_trust", "First time", variant="default"))

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


# ── Checklist builders ─────────────────────────────────────────


async def build_checklist_items(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Checklist items — structured items from payload or TaskSteps fallback."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    items = surface_data.get("items", []) if isinstance(surface_data, dict) else []

    if items:
        children: list[A2UIComponent] = []
        for i, item in enumerate(items):
            status = item.get("status", "pending") if isinstance(item, dict) else "pending"
            title = item.get("title", str(item)) if isinstance(item, dict) else str(item)
            variant = "success" if status == "completed" else "default"
            children.append(
                r.row(
                    f"cl_item_{i}",
                    [
                        r.badge(f"cl_item_{i}_st", status, variant=variant),
                        r.text(f"cl_item_{i}_title", _truncate(title, 100)),
                    ],
                )
            )
        return DetailTabResponse(
            tab_id="items",
            sections=[_section("items", f"Items ({len(items)})", children, collapsed=False)],
        )

    # Fallback: use TaskSteps from linked run
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("items", "No checklist items available.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("items", "No checklist items available.")

    children = []
    for i, step in enumerate(steps):
        variant = "success" if step.status == "completed" else "default"
        children.append(
            r.row(
                f"cl_step_{i}",
                [
                    r.badge(f"cl_step_{i}_st", step.status or "pending", variant=variant),
                    r.text(f"cl_step_{i}_name", step.name or step.step_type or f"Step {i + 1}"),
                ],
            )
        )
    return DetailTabResponse(
        tab_id="items",
        sections=[_section("items", f"Items ({len(steps)})", children, collapsed=False)],
    )


async def build_checklist_context(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Checklist context — delegates to plan context builder."""
    result = await build_plan_context(db, surface, **kwargs)
    return DetailTabResponse(tab_id="context", sections=result.sections)


# ── Comparison builders ────────────────────────────────────────


async def build_comparison_options(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Comparison options — each option as a card with pros and cons."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    options = surface_data.get("options", []) if isinstance(surface_data, dict) else []

    if options:
        children: list[A2UIComponent] = []
        for i, opt in enumerate(options):
            if not isinstance(opt, dict):
                continue
            name = opt.get("name", f"Option {i + 1}")
            desc = opt.get("description", "")
            pros = opt.get("pros", [])
            cons = opt.get("cons", [])

            card_children: list[A2UIComponent] = [
                r.text(f"opt_{i}_name", name),
            ]
            if desc:
                card_children.append(r.caption(f"opt_{i}_desc", desc))
            for j, pro in enumerate(pros):
                card_children.append(r.badge(f"opt_{i}_pro_{j}", str(pro), variant="success"))
            for j, con in enumerate(cons):
                card_children.append(r.badge(f"opt_{i}_con_{j}", str(con), variant="danger"))
            children.append(r.card(f"opt_{i}", card_children))

        return DetailTabResponse(
            tab_id="options",
            sections=[_section("options", f"Options ({len(options)})", children, collapsed=False)],
        )

    # Fallback to response_preview
    preview = payload.get("response_preview", "")
    return DetailTabResponse(
        tab_id="options",
        sections=[
            _section(
                "options",
                "Options",
                [r.text("opt_fallback", preview or "No comparison data available.")],
                collapsed=False,
            )
        ],
    )


async def build_comparison_criteria(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Comparison criteria — renders criteria as badge list."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    criteria = surface_data.get("criteria", []) if isinstance(surface_data, dict) else []

    if not criteria:
        return _empty_tab("criteria", "No criteria defined.")

    children: list[A2UIComponent] = [
        r.badge(f"crit_{i}", str(c), variant="default") for i, c in enumerate(criteria)
    ]
    return DetailTabResponse(
        tab_id="criteria",
        sections=[_section("criteria", "Criteria", children, collapsed=False)],
    )


# ── Timeline builders ──────────────────────────────────────────


async def build_timeline_events(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Timeline events — renders events via r.timeline() or falls back to briefing events."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    events = surface_data.get("events", []) if isinstance(surface_data, dict) else []

    if events:
        return DetailTabResponse(
            tab_id="events",
            sections=[
                _section(
                    "timeline",
                    f"Events ({len(events)})",
                    [r.timeline("tl_events", events)],
                    collapsed=False,
                )
            ],
        )

    # Fallback to briefing events builder
    result = await build_briefing_events(db, surface, **kwargs)
    return DetailTabResponse(tab_id="events", sections=result.sections)


async def build_timeline_context(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Timeline context — delegates to plan context builder."""
    result = await build_plan_context(db, surface, **kwargs)
    return DetailTabResponse(tab_id="context", sections=result.sections)


# ── Table builders ─────────────────────────────────────────────


async def build_table_data(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Table data — renders columns and rows via r.table()."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})

    columns = surface_data.get("columns", []) if isinstance(surface_data, dict) else []
    rows = surface_data.get("rows", []) if isinstance(surface_data, dict) else []

    if columns and rows:
        return DetailTabResponse(
            tab_id="data",
            sections=[
                _section(
                    "table",
                    f"Data ({len(rows)} rows)",
                    [r.table("tbl_data", columns, rows)],
                    collapsed=False,
                )
            ],
        )

    # Fallback to response_preview
    preview = payload.get("response_preview", "")
    return DetailTabResponse(
        tab_id="data",
        sections=[
            _section(
                "table",
                "Data",
                [r.text("tbl_fallback", preview or "No table data available.")],
                collapsed=False,
            )
        ],
    )


async def build_table_sources(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Table sources — TaskSteps for the linked run with step types and timing."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("sources", "No linked run for source lookup.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("sources", "No source steps found.")

    children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        step_children: list[A2UIComponent] = [
            r.badge(f"ts_{i}_type", step.step_type or "unknown"),
            r.text(f"ts_{i}_name", step.name or f"Step {i + 1}"),
        ]
        if step.started_at:
            step_children.append(
                r.caption(f"ts_{i}_start", f"Started: {_format_ts(step.started_at)}")
            )
        if step.completed_at:
            step_children.append(
                r.caption(f"ts_{i}_done", f"Completed: {_format_ts(step.completed_at)}")
            )
        children.append(r.row(f"ts_{i}", step_children))

    return DetailTabResponse(
        tab_id="sources",
        sections=[_section("sources", f"Source Steps ({len(steps)})", children, collapsed=False)],
    )


# ── Activity builders ──────────────────────────────────────────


async def build_activity_runs(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Activity runs — recent TaskRuns for the workspace (last 24h)."""
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        return _empty_tab("runs", "No workspace context for activity lookup.")

    from src.models.task_graph import TaskRun

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(TaskRun)
        .where(TaskRun.workspace_id == ws_id, TaskRun.created_at >= cutoff)
        .order_by(TaskRun.created_at.desc())
        .limit(20)
    )
    runs = list(result.scalars().all())

    if not runs:
        return _empty_tab("runs", "No runs in the last 24 hours.")

    children: list[A2UIComponent] = []
    for i, run in enumerate(runs):
        variant = "success" if run.status == "completed" else "default"
        if run.status == "failed":
            variant = "danger"
        children.append(
            r.row(
                f"run_{i}",
                [
                    r.badge(f"run_{i}_st", run.status or "pending", variant=variant),
                    r.text(f"run_{i}_src", run.source or "unknown"),
                    r.caption(f"run_{i}_time", _format_ts(run.created_at)),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="runs",
        sections=[_section("runs", f"Recent Runs ({len(runs)})", children, collapsed=False)],
    )


async def build_activity_stats(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Activity stats — aggregated run counts for the workspace."""
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        return _empty_tab("stats", "No workspace context for stats.")

    from sqlalchemy import func

    from src.models.task_graph import TaskRun

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    base_filter = [TaskRun.workspace_id == ws_id, TaskRun.created_at >= cutoff]

    total_result = await db.execute(select(func.count(TaskRun.run_id)).where(*base_filter))
    total = total_result.scalar() or 0

    completed_result = await db.execute(
        select(func.count(TaskRun.run_id)).where(*base_filter, TaskRun.status == "completed")
    )
    completed = completed_result.scalar() or 0

    failed_result = await db.execute(
        select(func.count(TaskRun.run_id)).where(*base_filter, TaskRun.status == "failed")
    )
    failed = failed_result.scalar() or 0

    children: list[A2UIComponent] = [
        r.metric("stat_total", "Total Runs (24h)", total),
        r.metric("stat_completed", "Completed", completed),
        r.metric("stat_failed", "Failed", failed),
    ]
    if total > 0:
        pct = round((completed / total) * 100, 1)
        children.append(r.progress("stat_success_rate", pct, label=f"Success Rate: {pct}%"))

    return DetailTabResponse(
        tab_id="stats",
        sections=[_section("stats", "Run Statistics", children, collapsed=False)],
    )


# ── Proactive Insight builders ─────────────────────────────────


async def build_insight_signal(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Insight signal — source, summary, relevance score and reasoning."""
    payload = _get_payload(surface)
    insight_data = payload.get("insight_data", {})

    if not insight_data:
        return _empty_tab("signal", "No insight data available.")

    children: list[A2UIComponent] = []
    signal_source = insight_data.get("signal_source", "")
    if signal_source:
        children.append(r.badge("ins_source", signal_source))

    signal_summary = insight_data.get("signal_summary", "")
    if signal_summary:
        children.append(r.text("ins_summary", signal_summary))

    relevance_score = insight_data.get("relevance_score")
    if relevance_score is not None:
        children.append(r.metric("ins_relevance", "Relevance", relevance_score))

    relevance_reasoning = insight_data.get("relevance_reasoning", "")
    if relevance_reasoning:
        children.append(r.caption("ins_reasoning", relevance_reasoning))

    if not children:
        return _empty_tab("signal", "No signal details available.")

    return DetailTabResponse(
        tab_id="signal",
        sections=[_section("signal", "Signal Details", children, collapsed=False)],
    )


async def build_insight_actions(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Insight actions — suggested actions with descriptions and execute buttons."""
    payload = _get_payload(surface)
    insight_data = payload.get("insight_data", {})
    actions = insight_data.get("suggested_actions", [])

    if not actions:
        return _empty_tab("actions", "No suggested actions.")

    children: list[A2UIComponent] = []
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        desc = action.get("description", "")
        capability = action.get("capability", "")
        card_children: list[A2UIComponent] = []
        if desc:
            card_children.append(r.text(f"act_{i}_desc", desc))
        if capability:
            card_children.append(r.badge(f"act_{i}_cap", capability))
        card_children.append(
            r.button(
                f"act_{i}_exec",
                "Execute",
                variant="primary",
                action_payload={
                    "action": "execute_insight_action",
                    "index": i,
                    "capability": capability,
                },
            )
        )
        children.append(r.card(f"act_{i}", card_children))

    if not children:
        return _empty_tab("actions", "No suggested actions.")

    title = f"Suggested Actions ({len(children)})"
    return DetailTabResponse(
        tab_id="actions",
        sections=[_section("actions", title, children, collapsed=False)],
    )


async def build_insight_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Insight context — related goals from insight data."""
    payload = _get_payload(surface)
    insight_data = payload.get("insight_data", {})
    goals = insight_data.get("related_goals", [])

    if not goals:
        return _empty_tab("context", "No related goals.")

    children: list[A2UIComponent] = [r.text(f"goal_{i}", str(goal)) for i, goal in enumerate(goals)]
    return DetailTabResponse(
        tab_id="context",
        sections=[_section("goals", "Related Goals", children, collapsed=False)],
    )


# ── Enhanced existing builders ─────────────────────────────────


async def build_recommendation_evidence(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Recommendation evidence — failed runs or open circuit breakers depending on title."""
    payload = _get_payload(surface)
    preview = payload.get("preview", {})
    title = (preview.get("title", "") if isinstance(preview, dict) else "").lower()

    sections: list[DetailSection] = []

    if "failed" in title or "fail" in title:
        from src.models.task_graph import TaskRun

        ws_id = getattr(surface, "workspace_id", None)
        if ws_id:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            result = await db.execute(
                select(TaskRun)
                .where(
                    TaskRun.workspace_id == ws_id,
                    TaskRun.status == "failed",
                    TaskRun.created_at >= cutoff,
                )
                .order_by(TaskRun.created_at.desc())
                .limit(10)
            )
            failed_runs = list(result.scalars().all())
            if failed_runs:
                children: list[A2UIComponent] = []
                for i, run in enumerate(failed_runs):
                    err_msg = ""
                    if run.error and isinstance(run.error, dict):
                        err_msg = run.error.get("message", str(run.error))
                    run_children: list[A2UIComponent] = [
                        r.badge(f"fr_{i}_st", "failed", variant="danger"),
                        r.text(f"fr_{i}_src", f"Source: {run.source or 'unknown'}"),
                    ]
                    if err_msg:
                        run_children.append(r.caption(f"fr_{i}_err", _truncate(str(err_msg), 150)))
                    children.append(r.row(f"fr_{i}", run_children))
                sections.append(_section("failures", f"Failed Runs ({len(failed_runs)})", children))

    if "source" in title or "failing" in title:
        from src.models.perception_state import PerceptionState

        ws_id = getattr(surface, "workspace_id", None)
        if ws_id:
            result = await db.execute(
                select(PerceptionState).where(
                    PerceptionState.workspace_id == ws_id,
                    PerceptionState.circuit_state == "open",
                )
            )
            open_sources = list(result.scalars().all())
            if open_sources:
                children = []
                for i, ps in enumerate(open_sources):
                    children.append(
                        r.row(
                            f"ps_{i}",
                            [
                                r.badge(f"ps_{i}_src", ps.source, variant="danger"),
                                r.text(
                                    f"ps_{i}_err",
                                    _truncate(ps.last_error or "No error details", 120),
                                ),
                                r.caption(
                                    f"ps_{i}_fail",
                                    f"Failures: {ps.consecutive_failures}",
                                ),
                            ],
                        )
                    )
                sections.append(
                    _section("circuits", f"Open Circuit Breakers ({len(open_sources)})", children)
                )

    if not sections:
        return _empty_tab("evidence", "No evidence data available.")
    return DetailTabResponse(tab_id="evidence", sections=sections)


async def build_alert_diagnostics(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Alert diagnostics — failed/blocked/timed_out steps with error details."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("diagnostics", "No linked run for diagnostics.")

    steps_result = await db.execute(
        select(TaskStep)
        .where(
            TaskStep.run_id == run_id,
            TaskStep.status.in_(["failed", "blocked", "timed_out"]),
        )
        .order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())

    if not steps:
        return _empty_tab("diagnostics", "No failed or blocked steps found.")

    children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        variant = "danger" if step.status == "failed" else "warning"
        step_children: list[A2UIComponent] = [
            r.badge(f"diag_{i}_st", step.status or "unknown", variant=variant),
            r.text(f"diag_{i}_name", step.name or step.step_type or f"Step {i + 1}"),
        ]
        if step.error and isinstance(step.error, dict):
            err_msg = step.error.get("message", str(step.error))
            step_children.append(
                r.alert(f"diag_{i}_err", _truncate(str(err_msg), 200), severity="error")
            )
        if step.started_at:
            step_children.append(
                r.caption(f"diag_{i}_start", f"Started: {_format_ts(step.started_at)}")
            )
        children.append(r.row(f"diag_{i}", step_children))

    return DetailTabResponse(
        tab_id="diagnostics",
        sections=[_section("diag", f"Problem Steps ({len(steps)})", children, collapsed=False)],
    )


# ── Run/Summary unified tab builders ─────────────────────────────
#
# Compose detail content from ``ui/units`` so the run surface detail tabs
# render the same fragments as the workspace card — a single source of
# truth for run visualization.


async def build_run_steps_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Steps tab for a run surface: ordered list with status, duration, output."""
    from src.models.task_graph import TaskRun, TaskStep
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        # Run surfaces use id format run_{run_id}
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("steps", "No linked run.")

    run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one_or_none()
    if not run:
        return _empty_tab("steps", f"Run {run_id[:16]}... not found.")

    steps = list(
        (
            await db.execute(
                select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
            )
        )
        .scalars()
        .all()
    )

    step_states = [
        {
            "step_id": s.step_id,
            "description": s.name or (s.input_data or {}).get("description", "") or s.step_id,
            "status": s.status or "pending",
            "output_summary": (
                _truncate(str((s.output_data or {}).get("result", "")), 240)
                if s.output_data
                else None
            ),
            "duration_ms": (
                int((s.completed_at - s.started_at).total_seconds() * 1000)
                if (s.started_at and s.completed_at)
                else None
            ),
        }
        for s in steps
    ]

    return DetailTabResponse(
        tab_id="steps",
        sections=[
            _section(
                "steps",
                f"Steps ({len(steps)})",
                [units.step_list(steps=step_states, run_id=run_id)],
                collapsed=False,
            )
        ],
    )


async def build_run_plan_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan tab: goal, reasoning, success criteria, priority, trigger from the linked Plan row."""
    from src.models.plans import Plan
    from src.models.task_graph import TaskRun
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("plan", "No linked run.")

    run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one_or_none()
    if not run or not run.plan_id:
        return _empty_tab("plan", "Run has no linked plan.")

    plan = (await db.execute(select(Plan).where(Plan.plan_id == run.plan_id))).scalar_one_or_none()
    if not plan:
        return _empty_tab("plan", "Plan not found.")

    return DetailTabResponse(
        tab_id="plan",
        sections=[
            _section(
                "plan",
                "Plan",
                [
                    units.plan_summary(
                        goal=plan.goal or "",
                        reasoning=plan.reasoning or "",
                        success_criteria=plan.success_criteria or "",
                        priority=plan.priority or "",
                        trigger_type=plan.trigger_type or "",
                        run_id=run_id,
                    )
                ],
                collapsed=False,
            )
        ],
    )


async def build_run_events_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Events tab: RuntimeEvent timeline ordered by occurred_at."""
    from src.models.runtime_event import RuntimeEvent
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("events", "No linked run.")

    events = list(
        (
            await db.execute(
                select(RuntimeEvent)
                .where(RuntimeEvent.run_id == run_id)
                .order_by(RuntimeEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )

    event_dicts = [
        {
            "timestamp": e.occurred_at.isoformat() if e.occurred_at else "",
            "event_type": e.event_type,
            "description": (e.payload or {}).get("summary", "") if e.payload else "",
        }
        for e in events
    ]

    return DetailTabResponse(
        tab_id="events",
        sections=[
            _section(
                "events",
                f"Events ({len(events)})",
                [units.event_timeline(events=event_dicts, run_id=run_id)],
                collapsed=False,
            )
        ],
    )


async def build_run_trace_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Trace tab: token/cost totals + per-agent breakdown.

    Uses the three-layer fallback from routes_history: trace_id JOIN,
    traces.run_id reverse lookup, then the task_runs rollup cache.
    """
    from src.models.task_graph import TaskRun
    from src.models.traces import ModelCall, Trace
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("trace", "No linked run.")

    run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one_or_none()
    if not run:
        return _empty_tab("trace", "Run not found.")

    trace_row = None
    if run.trace_id:
        trace_row = (
            await db.execute(select(Trace).where(Trace.trace_id == run.trace_id))
        ).scalar_one_or_none()
    if trace_row is None:
        trace_row = (
            await db.execute(select(Trace).where(Trace.run_id == run.run_id))
        ).scalar_one_or_none()

    input_t = int((trace_row.total_input_tokens if trace_row else 0) or run.input_tokens or 0)
    output_t = int((trace_row.total_output_tokens if trace_row else 0) or run.output_tokens or 0)
    cost = float((trace_row.total_cost_usd if trace_row else 0) or run.cost_usd or 0.0)
    duration_ms = (
        trace_row.duration_ms
        if trace_row and trace_row.duration_ms
        else (
            int((run.completed_at - run.started_at).total_seconds() * 1000)
            if run.started_at and run.completed_at
            else None
        )
    )

    step_breakdown: list[dict[str, Any]] = []
    if trace_row is not None:
        calls = list(
            (await db.execute(select(ModelCall).where(ModelCall.trace_id == trace_row.trace_id)))
            .scalars()
            .all()
        )
        by_agent: dict[str, dict[str, Any]] = {}
        for c in calls:
            key = c.agent_name or "unknown"
            entry = by_agent.setdefault(
                key,
                {
                    "step_id": key,
                    "agent": key,
                    "calls": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "duration_ms": 0,
                },
            )
            entry["calls"] += 1
            entry["tokens"] += int((c.input_tokens or 0) + (c.output_tokens or 0))
            entry["cost_usd"] = round(entry["cost_usd"] + float(c.cost_usd or 0), 6)
            entry["duration_ms"] += int(c.duration_ms or 0)
        step_breakdown = list(by_agent.values())

    return DetailTabResponse(
        tab_id="trace",
        sections=[
            _section(
                "trace",
                "Trace",
                [
                    units.trace_metrics(
                        input_tokens=input_t,
                        output_tokens=output_t,
                        cost_usd=cost,
                        duration_ms=duration_ms,
                        step_breakdown=step_breakdown or None,
                        run_id=run_id,
                    )
                ],
                collapsed=False,
            )
        ],
    )


# ── Registry ────────────────────────────────────────────────────

TAB_BUILDERS: dict[tuple[str, str], Any] = {
    # Unified run surface tabs
    ("run", "steps"): build_run_steps_tab,
    ("run", "plan"): build_run_plan_tab,
    ("run", "events"): build_run_events_tab,
    ("run", "trace"): build_run_trace_tab,
    # Summary surface reuses the same tabs (maps to the archived run).
    ("summary", "steps"): build_run_steps_tab,
    ("summary", "plan"): build_run_plan_tab,
    ("summary", "events"): build_run_events_tab,
    ("summary", "trace"): build_run_trace_tab,
    # Legacy
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
    ("recommendation", "evidence"): build_recommendation_evidence,
    ("recommendation", "context"): build_recommendation_context,
    ("alert", "overview"): build_alert_overview,
    ("alert", "diagnostics"): build_alert_diagnostics,
    ("checklist", "items"): build_checklist_items,
    ("checklist", "context"): build_checklist_context,
    ("comparison", "options"): build_comparison_options,
    ("comparison", "criteria"): build_comparison_criteria,
    ("timeline", "events"): build_timeline_events,
    ("timeline", "context"): build_timeline_context,
    ("table", "data"): build_table_data,
    ("table", "sources"): build_table_sources,
    ("activity", "runs"): build_activity_runs,
    ("activity", "stats"): build_activity_stats,
    ("proactive_insight", "signal"): build_insight_signal,
    ("proactive_insight", "actions"): build_insight_actions,
    ("proactive_insight", "context"): build_insight_context,
}
