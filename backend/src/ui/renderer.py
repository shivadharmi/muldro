"""A2UI surface generation helpers.

Provides builder functions for creating A2UI component trees.
Used by the Presenter agent to generate dynamic UI payloads.
"""

from src.ui.contracts import (
    A2UIAction,
    A2UIComponent,
    DetailConfig,
    DetailTab,
)

# --- Text components ---


def text(id: str, text: str, variant: str = "body") -> A2UIComponent:
    return A2UIComponent(type="Text", id=id, properties={"text": text, "variant": variant})


def heading(id: str, text: str) -> A2UIComponent:
    return A2UIComponent(type="Text", id=id, properties={"text": text, "variant": "heading"})


def caption(id: str, text: str) -> A2UIComponent:
    return A2UIComponent(type="Text", id=id, properties={"text": text, "variant": "caption"})


def code_block(id: str, code: str, language: str = "text") -> A2UIComponent:
    return A2UIComponent(
        type="CodeBlock",
        id=id,
        properties={"code": code, "language": language},
    )


def badge(id: str, label: str, variant: str = "default") -> A2UIComponent:
    return A2UIComponent(
        type="Badge",
        id=id,
        properties={"label": label, "variant": variant},
    )


def alert(id: str, message: str, severity: str = "info", title: str | None = None) -> A2UIComponent:
    props = {"message": message, "severity": severity}
    if title:
        props["title"] = title
    return A2UIComponent(type="Alert", id=id, properties=props)


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
    return A2UIComponent(
        type="Tabs",
        id=id,
        properties={"active_tab": active_tab, "labels": tab_labels},
        children=children,
    )


def modal(
    id: str,
    title: str,
    children: list[A2UIComponent],
    open: bool = True,
) -> A2UIComponent:
    return A2UIComponent(
        type="Modal",
        id=id,
        properties={"title": title, "open": open},
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
    return A2UIComponent(
        type="Button",
        id=id,
        properties={"label": label, "variant": variant},
        actions=actions,
    )


def text_field(
    id: str,
    label: str = "",
    placeholder: str = "",
    value: str = "",
) -> A2UIComponent:
    return A2UIComponent(
        type="TextField",
        id=id,
        properties={
            "label": label,
            "placeholder": placeholder,
            "value": value,
        },
    )


def select_field(
    id: str,
    label: str,
    options: list[dict],
    value: str = "",
) -> A2UIComponent:
    return A2UIComponent(
        type="Select",
        id=id,
        properties={"label": label, "options": options, "value": value},
    )


def toggle(
    id: str,
    label: str,
    checked: bool = False,
) -> A2UIComponent:
    return A2UIComponent(
        type="Toggle",
        id=id,
        properties={"label": label, "checked": checked},
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
    return A2UIComponent(
        type="Table",
        id=id,
        properties={
            "columns": columns,
            "rows": rows,
            "sortable": sortable,
        },
    )


def data_grid(
    id: str,
    columns: list[dict],
    rows: list[dict],
    page_size: int = 20,
) -> A2UIComponent:
    return A2UIComponent(
        type="DataGrid",
        id=id,
        properties={
            "columns": columns,
            "rows": rows,
            "page_size": page_size,
        },
    )


def timeline(id: str, events: list[dict]) -> A2UIComponent:
    return A2UIComponent(
        type="Timeline",
        id=id,
        properties={"events": events},
    )


def metric(
    id: str,
    label: str,
    value: str | int | float,
    change: str | None = None,
    trend: str | None = None,
) -> A2UIComponent:
    props: dict = {"label": label, "value": value}
    if change:
        props["change"] = change
    if trend:
        props["trend"] = trend
    return A2UIComponent(type="Metric", id=id, properties=props)


def progress(
    id: str,
    value: float,
    max_value: float = 100,
    label: str | None = None,
) -> A2UIComponent:
    props: dict = {"value": value, "max": max_value}
    if label:
        props["label"] = label
    return A2UIComponent(type="Progress", id=id, properties=props)


def chart(
    id: str,
    chart_type: str,
    data: dict,
    title: str = "",
) -> A2UIComponent:
    return A2UIComponent(
        type="Chart",
        id=id,
        properties={
            "chart_type": chart_type,
            "data": data,
            "title": title,
        },
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
    props: dict = {"name": name, "size": size}
    if url:
        props["url"] = url
    return A2UIComponent(type="Avatar", id=id, properties=props)


def status_indicator(
    id: str,
    status: str,
    label: str = "",
) -> A2UIComponent:
    return A2UIComponent(
        type="StatusIndicator",
        id=id,
        properties={"status": status, "label": label},
    )


def entity_card(
    id: str,
    name: str,
    entity_type: str,
    entity_id: str = "",
    attributes: dict | None = None,
) -> A2UIComponent:
    props: dict = {
        "name": name,
        "entity_type": entity_type,
        "entity_id": entity_id,
    }
    if attributes:
        props["attributes"] = attributes
    return A2UIComponent(type="EntityCard", id=id, properties=props)


def memory_card(
    id: str,
    fact_text: str,
    memory_type: str,
    source: str = "",
    confidence: float = 1.0,
) -> A2UIComponent:
    return A2UIComponent(
        type="MemoryCard",
        id=id,
        properties={
            "fact_text": fact_text,
            "memory_type": memory_type,
            "source": source,
            "confidence": confidence,
        },
    )


# --- Specialized components ---


def execution_trace(
    id: str,
    steps: list[dict],
    status: str = "running",
) -> A2UIComponent:
    return A2UIComponent(
        type="ExecutionTrace",
        id=id,
        properties={"steps": steps, "status": status},
    )


def kanban_board(
    id: str,
    columns_data: list[dict],
) -> A2UIComponent:
    return A2UIComponent(
        type="KanbanBoard",
        id=id,
        properties={"columns": columns_data},
    )


def calendar_view(
    id: str,
    events: list[dict],
    view: str = "week",
) -> A2UIComponent:
    return A2UIComponent(
        type="Calendar",
        id=id,
        properties={"events": events, "view": view},
    )


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
