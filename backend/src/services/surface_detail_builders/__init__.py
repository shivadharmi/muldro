"""Tab builder functions for surface detail modal.

Each builder fetches grounded data from existing services and returns a
DetailTabResponse with collapsible A2UI sections. The detail API dispatches to
builders via the TAB_BUILDERS registry keyed on (kind, tab_id).

Split from a single module by surface kind (SVC-P2-2a). This __init__ is the
public facade: it re-exports every builder and assembles the TAB_BUILDERS
registry, so ``from src.services.surface_detail_builders import X`` is unchanged.
"""

from typing import Any

from .alert import build_alert_diagnostics, build_alert_overview
from .approval import build_approval_history, build_approval_request, build_approval_risk
from .briefing import build_briefing_actions, build_briefing_events, build_briefing_priorities
from .insight import build_insight_actions, build_insight_context, build_insight_signal
from .lists import (
    build_activity_runs,
    build_activity_stats,
    build_checklist_context,
    build_checklist_items,
    build_comparison_criteria,
    build_comparison_options,
    build_table_data,
    build_table_sources,
    build_timeline_context,
    build_timeline_events,
)
from .plan import build_plan_context, build_plan_execution, build_plan_overview
from .recommendation import (
    build_recommendation_context,
    build_recommendation_evidence,
    build_recommendation_overview,
)
from .run import (
    build_run_events_tab,
    build_run_plan_tab,
    build_run_steps_tab,
    build_run_trace_tab,
)
from .summary import build_summary_context, build_summary_overview, build_summary_sources

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


__all__ = [
    "TAB_BUILDERS",
    "build_plan_overview",
    "build_plan_context",
    "build_plan_execution",
    "build_summary_overview",
    "build_summary_sources",
    "build_summary_context",
    "build_briefing_priorities",
    "build_briefing_events",
    "build_briefing_actions",
    "build_approval_request",
    "build_approval_risk",
    "build_approval_history",
    "build_recommendation_overview",
    "build_recommendation_context",
    "build_recommendation_evidence",
    "build_alert_overview",
    "build_alert_diagnostics",
    "build_checklist_items",
    "build_checklist_context",
    "build_comparison_options",
    "build_comparison_criteria",
    "build_timeline_events",
    "build_timeline_context",
    "build_table_data",
    "build_table_sources",
    "build_activity_runs",
    "build_activity_stats",
    "build_insight_signal",
    "build_insight_actions",
    "build_insight_context",
    "build_run_steps_tab",
    "build_run_plan_tab",
    "build_run_events_tab",
    "build_run_trace_tab",
]
