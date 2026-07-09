"""Composable A2UI surface units.

Each unit is a pure function producing a validated ``A2UIComponent`` fragment
with a semantically meaningful purpose (header, step list, approval card, etc).
Surfaces compose from these units; every call site that builds a surface should
use these helpers rather than hand-rolling component trees, to keep rendering
consistent across ``SurfaceService``, ``surface_detail_builders``, and
``GraphExecutor`` emissions.

Design rules:
    * Each unit returns a single ``A2UIComponent`` (typically a ``Card``) so it
      can be dropped into any parent ``children[]`` list.
    * Units accept typed models where available (``StepState``,
      ``ApprovalContext``, ``ResultSummary``, ``InsightSurfaceData``) and plain
      dicts as a fallback for DB payload shapes.
    * Units NEVER read from the database or do I/O — callers pass in the data.
    * ``build_surface_children`` composes multiple units and enforces the
      non-empty invariant for any surface that gets pushed.
"""

from __future__ import annotations

from typing import Any

from src.contracts import ApprovalContext, InsightSurfaceData, ResultSummary, StepState
from src.ui import renderer as r
from src.ui.contracts import (
    AGENT_SURFACE_KINDS,
    SYSTEM_SURFACE_KINDS,
    A2UIComponent,
    SurfaceKind,
)

# ── Status / variant maps ───────────────────────────────────────────


_PHASE_VARIANT: dict[str, str] = {
    "planning": "default",
    "plan_ready": "default",
    "executing": "default",
    "approval_needed": "warning",
    "completed": "success",
    "failed": "danger",
    "partial": "warning",
}

_STEP_STATUS_ICON: dict[str, str] = {
    "pending": "○",
    "executing": "◉",
    "completed": "✓",
    "failed": "✗",
    "approval_needed": "⚠",
    "user_action": "👤",
}

_STEP_STATUS_VARIANT: dict[str, str] = {
    "pending": "default",
    "executing": "default",
    "completed": "success",
    "failed": "danger",
    "approval_needed": "warning",
    "user_action": "warning",
}

_RISK_VARIANT: dict[str, str] = {
    "none": "default",
    "low": "default",
    "medium": "warning",
    "high": "danger",
    "critical": "danger",
}


# ── Individual units ────────────────────────────────────────────────


def run_header(
    title: str,
    phase: str,
    agent_name: str = "",
    progress: str = "",
    run_id: str = "",
) -> A2UIComponent:
    """Header card for a run surface: title, phase badge, agent, progress."""
    phase_variant = _PHASE_VARIANT.get(phase, "default")
    row_children: list[A2UIComponent] = [
        r.badge(f"{run_id or 'run'}_phase", phase.replace("_", " "), variant=phase_variant),
    ]
    if agent_name:
        row_children.append(r.badge(f"{run_id or 'run'}_agent", agent_name, variant="default"))
    if progress:
        row_children.append(r.caption(f"{run_id or 'run'}_progress", progress))

    return r.card(
        f"{run_id or 'run'}_header",
        [
            r.heading(f"{run_id or 'run'}_title", title),
            r.row(f"{run_id or 'run'}_meta", row_children),
        ],
    )


def plan_summary(
    goal: str,
    reasoning: str = "",
    success_criteria: str = "",
    priority: str = "",
    trigger_type: str = "",
    run_id: str = "",
) -> A2UIComponent:
    """Plan block: goal + reasoning + success criteria + priority + trigger."""
    base = run_id or "plan"
    children: list[A2UIComponent] = [
        r.caption(f"{base}_goal_label", "GOAL"),
        r.text(f"{base}_goal", goal or "(no goal recorded)"),
    ]

    if reasoning:
        children.append(r.caption(f"{base}_reasoning_label", "REASONING"))
        children.append(r.text(f"{base}_reasoning", reasoning))

    if success_criteria:
        children.append(r.caption(f"{base}_criteria_label", "SUCCESS CRITERIA"))
        children.append(r.text(f"{base}_criteria", success_criteria))

    meta_row: list[A2UIComponent] = []
    if priority:
        meta_row.append(
            r.badge(
                f"{base}_priority",
                f"priority: {priority}",
                variant=_priority_variant(priority),
            )
        )
    if trigger_type:
        meta_row.append(r.badge(f"{base}_trigger", f"trigger: {trigger_type}"))
    if meta_row:
        children.append(r.row(f"{base}_meta", meta_row))

    return r.card(f"{base}_card", children)


def step_list(
    steps: list[StepState | dict[str, Any]],
    current_step: str | None = None,
    run_id: str = "",
) -> A2UIComponent:
    """Ordered step list. Each step shows status icon + description + duration."""
    base = run_id or "steps"
    if not steps:
        return r.card(
            f"{base}_card",
            [
                r.heading(f"{base}_heading", "Steps"),
                r.caption(f"{base}_empty", "No steps recorded."),
            ],
        )

    step_rows: list[A2UIComponent] = [r.heading(f"{base}_heading", "Steps")]
    for idx, raw in enumerate(steps):
        s = _coerce_step(raw)
        icon = _STEP_STATUS_ICON.get(s.status, "○")
        variant = _STEP_STATUS_VARIANT.get(s.status, "default")
        marker = "▸" if s.step_id == current_step else " "

        row_children: list[A2UIComponent] = [
            r.text(f"{base}_step_{idx}_marker", f"{marker} {icon}"),
            r.text(f"{base}_step_{idx}_desc", s.description or s.step_id or f"step {idx + 1}"),
        ]
        if s.duration_ms is not None:
            row_children.append(
                r.caption(f"{base}_step_{idx}_dur", _format_duration(s.duration_ms))
            )
        row_children.append(r.badge(f"{base}_step_{idx}_status", s.status, variant=variant))

        step_rows.append(r.row(f"{base}_step_{idx}", row_children))

        if s.output_summary:
            step_rows.append(r.caption(f"{base}_step_{idx}_out", _truncate(s.output_summary, 200)))

    return r.card(f"{base}_card", step_rows)


def approval_card(
    approval: ApprovalContext | dict[str, Any],
    include_actions: bool = True,
) -> A2UIComponent:
    """Inline approval block shown when a run is gated on user decision."""
    a = _coerce_approval(approval)

    risk_variant = _RISK_VARIANT.get(a.risk_level, "default")
    severity = "warning" if a.risk_level in ("high", "critical") else "info"

    base = f"approval_{a.approval_id or 'pending'}"

    children: list[A2UIComponent] = [
        r.alert(
            f"{base}_alert",
            a.step_description or "Approval required to proceed.",
            severity=severity,
            title="Approval required",
        ),
    ]

    meta_row: list[A2UIComponent] = [
        r.badge(f"{base}_risk", f"risk: {a.risk_level or 'medium'}", variant=risk_variant),
    ]
    if a.trust_level:
        meta_row.append(r.badge(f"{base}_trust", f"trust: {a.trust_level}"))
    if a.blast_radius:
        meta_row.append(r.badge(f"{base}_blast", f"blast: {a.blast_radius}"))
    if a.reversible is not None:
        meta_row.append(
            r.badge(
                f"{base}_rev",
                "reversible" if a.reversible else "irreversible",
                variant="default" if a.reversible else "warning",
            )
        )
    children.append(r.row(f"{base}_meta", meta_row))

    if a.risk_reasoning:
        children.append(r.caption(f"{base}_why_label", "WHY THIS NEEDS APPROVAL"))
        children.append(r.text(f"{base}_why", a.risk_reasoning))

    if a.trust_context:
        children.append(r.caption(f"{base}_trust_label", "TRUST CONTEXT"))
        children.append(r.text(f"{base}_trust_ctx", a.trust_context))

    if a.graduation_hint:
        children.append(r.caption(f"{base}_grad_label", "GRADUATION"))
        children.append(r.text(f"{base}_grad", a.graduation_hint))

    if include_actions and a.approval_id:
        children.append(
            r.row(
                f"{base}_actions",
                [
                    r.button(
                        f"{base}_approve",
                        "Approve",
                        variant="primary",
                        action_payload={"type": "approval.approve", "approval_id": a.approval_id},
                    ),
                    r.button(
                        f"{base}_reject",
                        "Reject",
                        variant="secondary",
                        action_payload={"type": "approval.reject", "approval_id": a.approval_id},
                    ),
                ],
            )
        )

    return r.card(f"{base}_card", children)


def results_summary(
    results: ResultSummary | dict[str, Any],
    run_id: str = "",
) -> A2UIComponent:
    """Completion block: findings, artifacts, suggested next actions."""
    res = _coerce_results(results)
    base = f"{run_id or 'run'}_results"

    children: list[A2UIComponent] = [r.heading(f"{base}_heading", "Results")]

    if res.key_findings:
        children.append(r.caption(f"{base}_findings_label", "KEY FINDINGS"))
        children.append(
            r.list_component(
                f"{base}_findings",
                [
                    r.text(f"{base}_finding_{i}", f"• {item}")
                    for i, item in enumerate(res.key_findings)
                ],
            )
        )

    if res.artifacts_created:
        children.append(r.caption(f"{base}_artifacts_label", "ARTIFACTS CREATED"))
        children.append(
            r.list_component(
                f"{base}_artifacts",
                [
                    r.text(f"{base}_artifact_{i}", f"• {item}")
                    for i, item in enumerate(res.artifacts_created)
                ],
            )
        )

    if res.suggested_next:
        children.append(r.caption(f"{base}_next_label", "SUGGESTED NEXT"))
        children.append(
            r.list_component(
                f"{base}_next",
                [
                    r.text(f"{base}_next_{i}", f"• {item}")
                    for i, item in enumerate(res.suggested_next)
                ],
            )
        )

    if len(children) == 1:
        children.append(r.caption(f"{base}_empty", "Run completed with no captured outputs."))

    return r.card(f"{base}_card", children)


def trace_metrics(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int | None = None,
    step_breakdown: list[dict[str, Any]] | None = None,
    run_id: str = "",
) -> A2UIComponent:
    """Trace block: totals plus optional per-step breakdown table."""
    base = f"{run_id or 'run'}_trace"

    total_tokens = input_tokens + output_tokens
    metrics_row = r.row(
        f"{base}_metrics",
        [
            r.metric(f"{base}_in", "Input tokens", f"{input_tokens:,}"),
            r.metric(f"{base}_out", "Output tokens", f"{output_tokens:,}"),
            r.metric(f"{base}_total", "Total", f"{total_tokens:,}"),
            r.metric(f"{base}_cost", "Cost", f"${cost_usd:.5f}"),
            r.metric(
                f"{base}_dur",
                "Duration",
                _format_duration(duration_ms) if duration_ms is not None else "—",
            ),
        ],
    )

    children: list[A2UIComponent] = [r.heading(f"{base}_heading", "Trace"), metrics_row]

    if step_breakdown:
        table_rows: list[dict[str, Any]] = [
            {
                "step": row.get("step_id") or row.get("step") or row.get("description") or "",
                "agent": row.get("agent", ""),
                "calls": row.get("calls", 0),
                "tokens": row.get("tokens", 0),
                "cost": f"${float(row.get('cost_usd', 0.0)):.5f}",
                "duration": _format_duration(row.get("duration_ms")),
            }
            for row in step_breakdown
        ]
        children.append(
            r.table(
                f"{base}_breakdown",
                columns=[
                    {"key": "step", "label": "Step"},
                    {"key": "agent", "label": "Agent"},
                    {"key": "calls", "label": "Calls"},
                    {"key": "tokens", "label": "Tokens"},
                    {"key": "cost", "label": "Cost"},
                    {"key": "duration", "label": "Duration"},
                ],
                rows=table_rows,
                sortable=False,
            )
        )

    return r.card(f"{base}_card", children)


def insight_body(data: InsightSurfaceData | dict[str, Any]) -> A2UIComponent:
    """Body card for proactive_insight surfaces."""
    d = _coerce_insight(data)
    base = "insight"

    children: list[A2UIComponent] = [
        r.heading(f"{base}_headline", d.signal_summary or "New insight"),
    ]

    meta_row: list[A2UIComponent] = []
    if d.signal_source:
        meta_row.append(r.badge(f"{base}_source", d.signal_source))
    if d.signal_category:
        meta_row.append(r.badge(f"{base}_category", d.signal_category))
    if d.relevance_score:
        meta_row.append(
            r.badge(
                f"{base}_relevance",
                f"relevance: {d.relevance_score:.2f}",
                variant="default",
            )
        )
    if d.evidence:
        meta_row.append(r.badge(f"{base}_evidence", d.evidence, variant="default"))
    if meta_row:
        children.append(r.row(f"{base}_meta", meta_row))

    if d.relevance_reasoning:
        children.append(r.caption(f"{base}_why_label", "WHY THIS MATTERS"))
        children.append(r.text(f"{base}_why", d.relevance_reasoning))

    if d.related_goals:
        children.append(r.caption(f"{base}_goals_label", "RELATED GOALS"))
        children.append(
            r.list_component(
                f"{base}_goals",
                [r.text(f"{base}_goal_{i}", f"• {g}") for i, g in enumerate(d.related_goals)],
            )
        )

    if d.suggested_actions:
        children.append(r.caption(f"{base}_actions_label", "SUGGESTED ACTIONS"))
        children.append(
            r.row(
                f"{base}_actions",
                [
                    r.button(
                        f"{base}_action_{i}",
                        action.description[:40] or action.capability,
                        variant="secondary",
                        action_payload={
                            "type": "insight.execute",
                            "capability": action.capability,
                            "action_input": action.action_input,
                        },
                    )
                    for i, action in enumerate(d.suggested_actions)
                ],
            )
        )

    return r.card(f"{base}_card", children)


def event_timeline(events: list[dict[str, Any]], run_id: str = "") -> A2UIComponent:
    """Event timeline unit for run events (step_started, approval_requested, etc)."""
    base = f"{run_id or 'run'}_events"
    if not events:
        return r.card(
            f"{base}_card",
            [
                r.heading(f"{base}_heading", "Events"),
                r.caption(f"{base}_empty", "No events recorded."),
            ],
        )

    return r.card(
        f"{base}_card",
        [
            r.heading(f"{base}_heading", "Events"),
            r.timeline(
                f"{base}_timeline",
                [
                    {
                        "timestamp": e.get("timestamp") or e.get("ts") or "",
                        "title": e.get("event_type") or e.get("type") or "event",
                        "description": e.get("description") or e.get("summary") or "",
                    }
                    for e in events
                ],
            ),
        ],
    )


# ── Composite assembly ──────────────────────────────────────────────


def build_run_surface_children(
    *,
    title: str,
    phase: str,
    agent_name: str = "",
    progress: str = "",
    goal: str = "",
    reasoning: str = "",
    success_criteria: str = "",
    priority: str = "",
    trigger_type: str = "",
    steps: list[StepState | dict[str, Any]] | None = None,
    current_step: str | None = None,
    approval: ApprovalContext | dict[str, Any] | None = None,
    results: ResultSummary | dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
    run_id: str = "",
) -> list[A2UIComponent]:
    """Compose a run surface's children from units based on phase and data.

    The ordering is deterministic:
        header → plan → steps → approval (if approval_needed) → results (if completed) → trace.
    """
    children: list[A2UIComponent] = [
        run_header(
            title=title, phase=phase, agent_name=agent_name, progress=progress, run_id=run_id
        ),
    ]

    if goal or reasoning or success_criteria:
        children.append(
            plan_summary(
                goal=goal,
                reasoning=reasoning,
                success_criteria=success_criteria,
                priority=priority,
                trigger_type=trigger_type,
                run_id=run_id,
            )
        )

    if steps:
        children.append(step_list(steps=steps, current_step=current_step, run_id=run_id))

    if phase == "approval_needed" and approval is not None:
        children.append(approval_card(approval))

    if phase == "completed" and results is not None:
        children.append(results_summary(results, run_id=run_id))

    if trace:
        children.append(
            trace_metrics(
                input_tokens=int(trace.get("input_tokens") or 0),
                output_tokens=int(trace.get("output_tokens") or 0),
                cost_usd=float(trace.get("cost_usd") or 0.0),
                duration_ms=trace.get("duration_ms"),
                step_breakdown=trace.get("step_breakdown"),
                run_id=run_id,
            )
        )

    validate_surface_children(children)
    return children


def validate_surface_children(children: list[A2UIComponent]) -> None:
    """Reject empty children lists for pushed surfaces.

    Raises ``ValueError`` when a surface would be emitted with zero renderables.
    This prevents silent failures where a surface is pushed but renders blank.
    """
    if not children:
        raise ValueError("Surface children cannot be empty — at least one unit must be present.")
    for idx, c in enumerate(children):
        if not isinstance(c, A2UIComponent):
            raise ValueError(f"Surface child at index {idx} is not an A2UIComponent: {type(c)!r}")


def require_kind(kind: str) -> SurfaceKind:
    """Validate a kind string against the SurfaceKind Literal at runtime.

    Used by emission pipelines that receive a dynamic kind string and want
    an early failure before building a surface.
    """
    if kind in SYSTEM_SURFACE_KINDS or kind in AGENT_SURFACE_KINDS or kind in _LEGACY_KINDS:
        return kind  # type: ignore[return-value]
    raise ValueError(f"Unknown surface kind: {kind!r}")


_LEGACY_KINDS: frozenset[str] = frozenset({"plan", "approval"})


# ── Coercion helpers ────────────────────────────────────────────────


def _coerce_step(raw: StepState | dict[str, Any]) -> StepState:
    if isinstance(raw, StepState):
        return raw
    return StepState.model_validate(raw)


def _coerce_approval(raw: ApprovalContext | dict[str, Any]) -> ApprovalContext:
    if isinstance(raw, ApprovalContext):
        return raw
    defaults = {
        "approval_id": raw.get("approval_id", "") if isinstance(raw, dict) else "",
        "step_description": raw.get("step_description", "") if isinstance(raw, dict) else "",
        "risk_reasoning": raw.get("risk_reasoning", "") if isinstance(raw, dict) else "",
        "trust_context": raw.get("trust_context", "") if isinstance(raw, dict) else "",
    }
    merged = {**defaults, **(raw if isinstance(raw, dict) else {})}
    return ApprovalContext.model_validate(merged)


def _coerce_results(raw: ResultSummary | dict[str, Any]) -> ResultSummary:
    if isinstance(raw, ResultSummary):
        return raw
    return ResultSummary.model_validate(raw)


def _coerce_insight(raw: InsightSurfaceData | dict[str, Any]) -> InsightSurfaceData:
    if isinstance(raw, InsightSurfaceData):
        return raw
    defaults = {
        "signal_source": raw.get("signal_source", "") if isinstance(raw, dict) else "",
        "signal_summary": raw.get("signal_summary", "") if isinstance(raw, dict) else "",
    }
    merged = {**defaults, **(raw if isinstance(raw, dict) else {})}
    return InsightSurfaceData.model_validate(merged)


def _priority_variant(priority: str) -> str:
    return {
        "low": "default",
        "medium": "default",
        "high": "warning",
        "critical": "danger",
    }.get(priority, "default")


def _truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "—"
    seconds = duration_ms / 1000
    if seconds < 1:
        return f"{duration_ms}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"
