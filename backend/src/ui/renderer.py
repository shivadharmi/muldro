"""A2UI surface generation helpers.

Provides builder functions for creating A2UI component trees.
Used by the Presenter agent to generate dynamic UI payloads.
"""

from src.ui.component_properties import (
    AlertProperties,
    AvatarProperties,
    BadgeProperties,
    ButtonProperties,
    CalendarProperties,
    ChartProperties,
    CodeBlockProperties,
    DataGridProperties,
    EntityCardProperties,
    ExecutionTraceProperties,
    KanbanBoardProperties,
    MemoryCardProperties,
    MetricProperties,
    ModalProperties,
    ProgressProperties,
    SelectProperties,
    StatusIndicatorProperties,
    TableProperties,
    TabsProperties,
    TextFieldProperties,
    TextProperties,
    TimelineProperties,
    ToggleProperties,
)
from src.ui.contracts import (
    A2UIAction,
    A2UIComponent,
    A2UISurface,
    DetailConfig,
    DetailTab,
)

# --- Text components ---


def text(id: str, text: str, variant: str = "body") -> A2UIComponent:
    props = TextProperties(text=text, variant=variant)
    return A2UIComponent(type="Text", id=id, properties=props.model_dump())


def heading(id: str, text: str) -> A2UIComponent:
    props = TextProperties(text=text, variant="heading")
    return A2UIComponent(type="Text", id=id, properties=props.model_dump())


def caption(id: str, text: str) -> A2UIComponent:
    props = TextProperties(text=text, variant="caption")
    return A2UIComponent(type="Text", id=id, properties=props.model_dump())


def code_block(id: str, code: str, language: str = "text") -> A2UIComponent:
    props = CodeBlockProperties(code=code, language=language)
    return A2UIComponent(
        type="CodeBlock",
        id=id,
        properties=props.model_dump(),
    )


def badge(id: str, label: str, variant: str = "default") -> A2UIComponent:
    props = BadgeProperties(label=label, variant=variant)
    return A2UIComponent(
        type="Badge",
        id=id,
        properties=props.model_dump(),
    )


def alert(id: str, message: str, severity: str = "info", title: str | None = None) -> A2UIComponent:
    props = AlertProperties(message=message, severity=severity, title=title)
    return A2UIComponent(type="Alert", id=id, properties=props.model_dump())


# --- Layout components ---


def card(id: str, children: list[A2UIComponent]) -> A2UIComponent:
    return A2UIComponent(type="Card", id=id, children=children)


def row(id: str, children: list[A2UIComponent]) -> A2UIComponent:
    return A2UIComponent(type="Row", id=id, children=children)


def column(id: str, children: list[A2UIComponent]) -> A2UIComponent:
    return A2UIComponent(type="Column", id=id, children=children)


def divider(id: str) -> A2UIComponent:
    return A2UIComponent(type="Divider", id=id)


def tabs(
    id: str,
    tab_labels: list[str],
    tab_contents: list[list[A2UIComponent]],
    active_tab: int = 0,
) -> A2UIComponent:
    children = []
    for i, (label, content) in enumerate(zip(tab_labels, tab_contents)):
        children.append(
            A2UIComponent(
                type="Card",
                id=f"{id}_tab_{i}",
                properties={"tab_label": label, "tab_index": i},
                children=content,
            )
        )
    props = TabsProperties(active_tab=active_tab, labels=tab_labels)
    return A2UIComponent(
        type="Tabs",
        id=id,
        properties=props.model_dump(),
        children=children,
    )


def modal(
    id: str,
    title: str,
    children: list[A2UIComponent],
    open: bool = True,
) -> A2UIComponent:
    props = ModalProperties(title=title, open=open)
    return A2UIComponent(
        type="Modal",
        id=id,
        properties=props.model_dump(),
        children=children,
    )


# --- Input components ---


def button(
    id: str,
    label: str,
    variant: str = "primary",
    action_payload: dict | None = None,
) -> A2UIComponent:
    actions = []
    if action_payload:
        actions = [A2UIAction(type="click", payload=action_payload)]
    props = ButtonProperties(label=label, variant=variant)
    return A2UIComponent(
        type="Button",
        id=id,
        properties=props.model_dump(),
        actions=actions,
    )


def text_field(
    id: str,
    label: str = "",
    placeholder: str = "",
    value: str = "",
) -> A2UIComponent:
    props = TextFieldProperties(label=label, placeholder=placeholder, value=value)
    return A2UIComponent(
        type="TextField",
        id=id,
        properties=props.model_dump(),
    )


def select_field(
    id: str,
    label: str,
    options: list[dict],
    value: str = "",
) -> A2UIComponent:
    props = SelectProperties(label=label, options=options, value=value)
    return A2UIComponent(
        type="Select",
        id=id,
        properties=props.model_dump(),
    )


def toggle(
    id: str,
    label: str,
    checked: bool = False,
) -> A2UIComponent:
    props = ToggleProperties(label=label, checked=checked)
    return A2UIComponent(
        type="Toggle",
        id=id,
        properties=props.model_dump(),
    )


def form(
    id: str,
    fields: list[A2UIComponent],
    submit_label: str = "Submit",
    submit_payload: dict | None = None,
) -> A2UIComponent:
    submit = button(f"{id}_submit", submit_label, "primary", submit_payload)
    return A2UIComponent(
        type="Form",
        id=id,
        children=fields + [submit],
    )


# --- Data components ---


def table(
    id: str,
    columns: list[dict],
    rows: list[dict],
    sortable: bool = False,
) -> A2UIComponent:
    props = TableProperties(columns=columns, rows=rows, sortable=sortable)
    return A2UIComponent(
        type="Table",
        id=id,
        properties=props.model_dump(),
    )


def data_grid(
    id: str,
    columns: list[dict],
    rows: list[dict],
    page_size: int = 20,
) -> A2UIComponent:
    props = DataGridProperties(columns=columns, rows=rows, page_size=page_size)
    return A2UIComponent(
        type="DataGrid",
        id=id,
        properties=props.model_dump(),
    )


def timeline(id: str, events: list[dict]) -> A2UIComponent:
    props = TimelineProperties(events=events)
    return A2UIComponent(
        type="Timeline",
        id=id,
        properties=props.model_dump(),
    )


def metric(
    id: str,
    label: str,
    value: str | int | float,
    change: str | None = None,
    trend: str | None = None,
) -> A2UIComponent:
    props = MetricProperties(label=label, value=value, change=change, trend=trend)
    return A2UIComponent(type="Metric", id=id, properties=props.model_dump())


def progress(
    id: str,
    value: float,
    max_value: float = 100,
    label: str | None = None,
) -> A2UIComponent:
    props = ProgressProperties(value=value, max=max_value, label=label)
    return A2UIComponent(type="Progress", id=id, properties=props.model_dump())


def chart(
    id: str,
    chart_type: str,
    data: dict,
    title: str = "",
) -> A2UIComponent:
    props = ChartProperties(chart_type=chart_type, data=data, title=title)
    return A2UIComponent(
        type="Chart",
        id=id,
        properties=props.model_dump(),
    )


# --- Display components ---


def list_component(id: str, children: list[A2UIComponent]) -> A2UIComponent:
    return A2UIComponent(type="List", id=id, children=children)


def avatar(
    id: str,
    name: str,
    url: str | None = None,
    size: str = "md",
) -> A2UIComponent:
    props = AvatarProperties(name=name, url=url, size=size)
    return A2UIComponent(type="Avatar", id=id, properties=props.model_dump())


def status_indicator(
    id: str,
    status: str,
    label: str = "",
) -> A2UIComponent:
    props = StatusIndicatorProperties(status=status, label=label)
    return A2UIComponent(
        type="StatusIndicator",
        id=id,
        properties=props.model_dump(),
    )


def entity_card(
    id: str,
    name: str,
    entity_type: str,
    entity_id: str = "",
    attributes: dict | None = None,
) -> A2UIComponent:
    props = EntityCardProperties(
        name=name, entity_type=entity_type, entity_id=entity_id, attributes=attributes
    )
    return A2UIComponent(type="EntityCard", id=id, properties=props.model_dump())


def memory_card(
    id: str,
    fact_text: str,
    memory_type: str,
    source: str = "",
    confidence: float = 1.0,
) -> A2UIComponent:
    props = MemoryCardProperties(
        fact_text=fact_text, memory_type=memory_type, source=source, confidence=confidence
    )
    return A2UIComponent(
        type="MemoryCard",
        id=id,
        properties=props.model_dump(),
    )


# --- Specialized components ---


def execution_trace(
    id: str,
    steps: list[dict],
    status: str = "running",
) -> A2UIComponent:
    props = ExecutionTraceProperties(steps=steps, status=status)
    return A2UIComponent(
        type="ExecutionTrace",
        id=id,
        properties=props.model_dump(),
    )


def kanban_board(
    id: str,
    columns_data: list[dict],
) -> A2UIComponent:
    props = KanbanBoardProperties(columns=columns_data)
    return A2UIComponent(
        type="KanbanBoard",
        id=id,
        properties=props.model_dump(),
    )


def calendar_view(
    id: str,
    events: list[dict],
    view: str = "week",
) -> A2UIComponent:
    props = CalendarProperties(events=events, view=view)
    return A2UIComponent(
        type="Calendar",
        id=id,
        properties=props.model_dump(),
    )


# --- Surface builder ---


def surface(
    id: str,
    children: list[A2UIComponent],
    metadata: dict | None = None,
) -> A2UISurface:
    return A2UISurface(id=id, children=children, metadata=metadata or {})


# --- Detail config factory ---


_TABS_BY_KIND: dict[str, list[tuple[str, str]]] = {
    "plan": [("overview", "Overview"), ("context", "Context"), ("execution", "Execution")],
    "summary": [("overview", "Overview"), ("sources", "Sources"), ("context", "Context")],
    "briefing": [("priorities", "Priorities"), ("events", "Events"), ("actions", "Actions")],
    "approval": [("request", "Request"), ("risk", "Risk"), ("history", "History")],
    "recommendation": [("overview", "Overview"), ("context", "Context")],
    "alert": [("overview", "Overview")],
}


def build_detail_config(kind: str, surface_id: str) -> DetailConfig | None:
    """Build detail modal configuration for a surface kind.

    Returns None for kinds with no detail tabs (checklist, timeline, etc.).
    """
    tab_defs = _TABS_BY_KIND.get(kind)
    if not tab_defs:
        return None
    base = f"/v1/surfaces/{surface_id}/detail"
    tabs = [DetailTab(id=tid, label=label, endpoint=f"{base}/{tid}") for tid, label in tab_defs]
    return DetailConfig(tabs=tabs)


# --- Composite surfaces ---


def briefing_surface(
    briefing_id: str,
    headline: str,
    priorities: list[dict],
    approvals: list[dict],
    schedule: list[dict],
) -> A2UISurface:
    """Generate a complete briefing A2UI surface."""
    children = [
        card("headline_card", [heading("headline", headline)]),
    ]

    if priorities:
        priority_cards = []
        for i, p in enumerate(priorities):
            card_children = [
                text(f"p{i}_title", p.get("title", "")),
                caption(f"p{i}_why", p.get("why", "")),
            ]
            if p.get("approval_id"):
                card_children.append(
                    row(
                        f"p{i}_actions",
                        [
                            button(
                                f"approve_{p['approval_id']}",
                                "Approve",
                                "primary",
                                {"action": "approve", "id": p["approval_id"]},
                            ),
                            button(
                                f"reject_{p['approval_id']}",
                                "Reject",
                                "secondary",
                                {"action": "reject", "id": p["approval_id"]},
                            ),
                        ],
                    )
                )
            priority_cards.append(card(f"priority_{i}", card_children))
        children.append(
            card(
                "priorities_section",
                [heading("priorities_title", "Top Priorities")]
                + [list_component("priorities_list", priority_cards)],
            )
        )

    if approvals:
        approval_cards = []
        for i, a in enumerate(approvals):
            approval_cards.append(
                card(
                    f"approval_{i}",
                    [
                        text(f"a{i}_title", a.get("title", "")),
                        caption(
                            f"a{i}_risk",
                            f"Risk: {a.get('risk_level', 'medium')}",
                        ),
                        row(
                            f"a{i}_actions",
                            [
                                button(
                                    f"approve_{a['approval_id']}",
                                    "Approve",
                                    "primary",
                                    {
                                        "action": "approve",
                                        "id": a["approval_id"],
                                    },
                                ),
                                button(
                                    f"reject_{a['approval_id']}",
                                    "Reject",
                                    "secondary",
                                    {
                                        "action": "reject",
                                        "id": a["approval_id"],
                                    },
                                ),
                            ],
                        ),
                    ],
                )
            )
        children.append(
            card(
                "approvals_section",
                [heading("approvals_title", "Pending Approvals")]
                + [list_component("approvals_list", approval_cards)],
            )
        )

    if schedule:
        event_rows = []
        for i, evt in enumerate(schedule):
            event_rows.append(
                row(
                    f"event_{i}",
                    [
                        text(f"e{i}_time", evt.get("time", "")),
                        text(f"e{i}_title", evt.get("title", "")),
                        button(
                            f"e{i}_prep",
                            "Prep Card",
                            "secondary",
                            {
                                "action": "meeting_prep",
                                "event_id": evt.get("event_id", ""),
                            },
                        ),
                    ],
                )
            )
        children.append(
            card(
                "schedule_section",
                [heading("schedule_title", "Today's Schedule")]
                + [list_component("schedule_list", event_rows)],
            )
        )

    return surface(briefing_id, children)
