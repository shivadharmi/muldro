"""A2UI view generators — typed surface builders for common views.

Each function returns a complete A2UISurface with a specific structure.
Used by the Presenter agent and API routes to generate dynamic UI.
"""

from enum import Enum

from src.ui import renderer as r
from src.ui.contracts import A2UISurface


class ViewType(str, Enum):
    DASHBOARD = "dashboard"
    TASK_BOARD = "task_board"
    APPROVAL_PANEL = "approval_panel"
    EXECUTION_TRACE = "execution_trace"
    ENTITY_EXPLORER = "entity_explorer"
    MEMORY_BROWSER = "memory_browser"
    CONNECTOR_STATUS = "connector_status"
    BRIEFING_VIEW = "briefing_view"
    TIMELINE = "timeline"
    SETTINGS_PANEL = "settings_panel"


def dashboard_view(
    user_id: str,
    active_tasks: list[dict],
    pending_approvals: list[dict],
    recent_events: list[dict],
    budget: dict,
    connector_health: list[dict],
) -> A2UISurface:
    """Main user dashboard with overview widgets."""
    children = [r.heading("dash_title", "Dashboard")]

    # Metrics row
    metrics = [
        r.metric(
            "m_tasks",
            "Active Tasks",
            len(active_tasks),
        ),
        r.metric(
            "m_approvals",
            "Pending Approvals",
            len(pending_approvals),
        ),
        r.metric(
            "m_events",
            "Events Today",
            len(recent_events),
        ),
        r.metric(
            "m_budget",
            "Budget Used",
            f"${budget.get('used', 0):.2f}",
            change=f"of ${budget.get('limit', 5):.2f}",
        ),
    ]
    children.append(r.row("metrics_row", metrics))

    # Connector health
    if connector_health:
        health_indicators = [
            r.status_indicator(
                f"conn_{c['provider']}",
                c.get("status", "unknown"),
                label=c["provider"],
            )
            for c in connector_health
        ]
        children.append(
            r.card(
                "connectors_card",
                [
                    r.text("conn_title", "Connectors", variant="heading"),
                    r.row("conn_health", health_indicators),
                ],
            )
        )

    # Pending approvals
    if pending_approvals:
        approval_items = []
        for i, a in enumerate(pending_approvals[:5]):
            approval_items.append(
                r.card(
                    f"apr_{i}",
                    [
                        r.text(f"apr_{i}_title", a.get("title", "")),
                        r.badge(
                            f"apr_{i}_risk",
                            a.get("risk_level", "medium"),
                            variant=a.get("risk_level", "medium"),
                        ),
                        r.row(
                            f"apr_{i}_actions",
                            [
                                r.button(
                                    f"apr_{i}_approve",
                                    "Approve",
                                    "primary",
                                    {"action": "approve", "id": a.get("approval_id")},
                                ),
                                r.button(
                                    f"apr_{i}_reject",
                                    "Reject",
                                    "secondary",
                                    {"action": "reject", "id": a.get("approval_id")},
                                ),
                            ],
                        ),
                    ],
                )
            )
        children.append(
            r.card(
                "approvals_section",
                [
                    r.text("apr_title", "Pending Approvals", variant="heading"),
                    r.list_component("apr_list", approval_items),
                ],
            )
        )

    # Recent events timeline
    if recent_events:
        events_data = [
            {
                "time": e.get("occurred_at", ""),
                "title": e.get("title", ""),
                "source": e.get("source", ""),
                "type": e.get("event_type", ""),
            }
            for e in recent_events[:10]
        ]
        children.append(
            r.card(
                "events_card",
                [
                    r.text("events_title", "Recent Events", variant="heading"),
                    r.timeline("events_timeline", events_data),
                ],
            )
        )

    return r.surface(f"dashboard_{user_id}", children)


def task_board_view(
    user_id: str,
    tasks_by_status: dict[str, list[dict]],
) -> A2UISurface:
    """Kanban-style task board."""
    columns = []
    for status, tasks in tasks_by_status.items():
        columns.append(
            {
                "title": status.replace("_", " ").title(),
                "items": [
                    {
                        "id": t.get("task_id", ""),
                        "title": t.get("title", t.get("goal", "")),
                        "subtitle": t.get("decision", ""),
                    }
                    for t in tasks
                ],
            }
        )

    return r.surface(
        f"tasks_{user_id}",
        [
            r.heading("tasks_title", "Task Board"),
            r.kanban_board("task_kanban", columns),
        ],
    )


def execution_trace_view(
    run_id: str,
    steps: list[dict],
    status: str,
) -> A2UISurface:
    """Step-by-step execution visualization."""
    children = [
        r.heading("exec_title", f"Execution: {run_id}"),
        r.badge("exec_status", status, variant=status),
        r.divider("exec_div"),
        r.execution_trace("exec_trace", steps, status),
    ]

    # Progress bar
    completed = sum(1 for s in steps if s.get("status") == "completed")
    total = len(steps)
    if total > 0:
        children.insert(
            2,
            r.progress(
                "exec_progress",
                completed,
                total,
                label=f"{completed}/{total} steps",
            ),
        )

    return r.surface(f"exec_{run_id}", children)


def entity_explorer_view(
    user_id: str,
    entities: list[dict],
    query: str = "",
) -> A2UISurface:
    """Entity browser with search results."""
    children = [r.heading("ent_title", "Entity Explorer")]

    entity_cards = []
    for i, ent in enumerate(entities):
        entity_cards.append(
            r.entity_card(
                f"ent_{i}",
                name=ent.get("canonical_name", ""),
                entity_type=ent.get("entity_type", ""),
                entity_id=ent.get("entity_id", ""),
                attributes=ent.get("attributes"),
            )
        )

    if entity_cards:
        children.append(r.list_component("ent_list", entity_cards))
    else:
        children.append(r.text("ent_empty", "No entities found", variant="caption"))

    return r.surface(f"entities_{user_id}", children)


def memory_browser_view(
    user_id: str,
    memories: list[dict],
) -> A2UISurface:
    """Memory browser with type filtering."""
    children = [r.heading("mem_title", "Memory Browser")]

    memory_cards = []
    for i, mem in enumerate(memories):
        memory_cards.append(
            r.memory_card(
                f"mem_{i}",
                fact_text=mem.get("fact_text", ""),
                memory_type=mem.get("memory_type", ""),
                source=mem.get("source_event_ids", [""])[0] if mem.get("source_event_ids") else "",
                confidence=mem.get("stability_score", 1.0),
            )
        )

    if memory_cards:
        children.append(r.list_component("mem_list", memory_cards))
    else:
        children.append(r.text("mem_empty", "No memories found", variant="caption"))

    return r.surface(f"memories_{user_id}", children)


def connector_status_view(
    user_id: str,
    connectors: list[dict],
) -> A2UISurface:
    """Connector health and configuration view."""
    columns = [
        {"key": "provider", "label": "Provider"},
        {"key": "status", "label": "Status"},
        {"key": "last_poll", "label": "Last Poll"},
        {"key": "events_count", "label": "Events"},
    ]
    rows = [
        {
            "provider": c.get("provider", ""),
            "status": c.get("status", "unknown"),
            "last_poll": c.get("last_poll_at", "-"),
            "events_count": c.get("events_count", 0),
        }
        for c in connectors
    ]

    return r.surface(
        f"connectors_{user_id}",
        [
            r.heading("conn_title", "Connector Status"),
            r.table("conn_table", columns, rows, sortable=True),
        ],
    )


VIEWS = {
    ViewType.DASHBOARD: dashboard_view,
    ViewType.TASK_BOARD: task_board_view,
    ViewType.EXECUTION_TRACE: execution_trace_view,
    ViewType.ENTITY_EXPLORER: entity_explorer_view,
    ViewType.MEMORY_BROWSER: memory_browser_view,
    ViewType.CONNECTOR_STATUS: connector_status_view,
}
