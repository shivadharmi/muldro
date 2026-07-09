"""A2UI surface generation helpers.

Provides builder functions for creating A2UI component trees.
Used by the Presenter agent to generate dynamic UI payloads.
"""

from src.ui.component_properties import (
    AlertProperties,
    BadgeProperties,
    ButtonProperties,
    CodeBlockProperties,
    EntityCardProperties,
    ExecutionTraceProperties,
    MarkdownProperties,
    MemoryCardProperties,
    MetricProperties,
    ProgressProperties,
    TableProperties,
    TextProperties,
    TimelineProperties,
)
from src.ui.contracts import (
    A2UIAction,
    A2UIComponent,
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


def markdown(id: str, content: str) -> A2UIComponent:
    """Render GitHub-flavored markdown as a single block. Preserves paragraph/
    list/emphasis structure the frontend renders via react-markdown."""
    props = MarkdownProperties(content=content)
    return A2UIComponent(type="Markdown", id=id, properties=props.model_dump())


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


def divider(id: str) -> A2UIComponent:
    return A2UIComponent(type="Divider", id=id)


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


# --- Display components ---


def list_component(id: str, children: list[A2UIComponent]) -> A2UIComponent:
    return A2UIComponent(type="List", id=id, children=children)


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


# --- Detail config factory ---


_TABS_BY_KIND: dict[str, list[tuple[str, str]]] = {
    # Unified run surface: the detail modal shows steps / plan / events / trace
    # which is the shape the user's screenshots revealed.
    "run": [
        ("steps", "Steps"),
        ("plan", "Plan"),
        ("events", "Events"),
        ("trace", "Trace"),
    ],
    # Summary (completion card) reuses run tabs so the user can drill back
    # into the archived run's detail.
    "summary": [
        ("steps", "Steps"),
        ("plan", "Plan"),
        ("events", "Events"),
        ("trace", "Trace"),
    ],
    "plan": [("overview", "Overview"), ("context", "Context"), ("execution", "Execution")],
    "briefing": [("priorities", "Priorities"), ("events", "Events"), ("actions", "Actions")],
    "approval": [("request", "Request"), ("risk", "Risk"), ("history", "History")],
    "recommendation": [("overview", "Overview"), ("evidence", "Evidence"), ("context", "Context")],
    "alert": [("overview", "Overview"), ("diagnostics", "Diagnostics")],
    "proactive_insight": [("signal", "Signal"), ("actions", "Actions"), ("context", "Context")],
}


def build_detail_config(
    kind: str,
    surface_id: str,
    extra_tabs: list[tuple[str, str]] | None = None,
    default_tab: str | None = None,
) -> DetailConfig | None:
    """Build detail modal configuration for a surface kind.

    Returns None for kinds with no detail tabs.

    ``extra_tabs`` appends conditional ``(tab_id, label)`` pairs to the resolved
    tabs (e.g. an Approval tab when a run is awaiting approval). ``default_tab``
    sets the tab the modal opens on; it is validated against the final tab ids.
    """
    tab_defs = list(_TABS_BY_KIND.get(kind) or [])
    if extra_tabs:
        existing = {tid for tid, _ in tab_defs}
        tab_defs += [(tid, label) for tid, label in extra_tabs if tid not in existing]
    if not tab_defs:
        return None
    base = f"/v1/surfaces/{surface_id}/detail"
    tabs = [DetailTab(id=tid, label=label, endpoint=f"{base}/{tid}") for tid, label in tab_defs]
    return DetailConfig(tabs=tabs, default_tab=default_tab)
