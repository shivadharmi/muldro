"""Typed property models for A2UI component types.

Each component type that carries semantic properties has a corresponding Pydantic model.
Layout containers (Card, Row, Column, List, Divider, Form) have no required properties
and are intentionally absent from PROPERTY_MODELS.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

# ── Text family ─────────────────────────────────────────────────────────────


class TextProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    variant: Literal["heading", "body", "caption"] = "body"


class CodeBlockProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    language: str = "text"


class BadgeProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    variant: Literal["default", "success", "warning", "danger"] = "default"


class AlertProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str
    severity: Literal["info", "warning", "error", "success"] = "info"
    title: str | None = None


# ── Input family ─────────────────────────────────────────────────────────────


class ButtonProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    variant: Literal["primary", "secondary", "danger", "ghost"] = "primary"


class TextFieldProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    placeholder: str = ""
    value: str = ""


class SelectProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    options: list[dict] = []
    value: str = ""


class ToggleProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    checked: bool = False


# ── Data family ───────────────────────────────────────────────────────────────


class TableProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict]
    rows: list[dict]
    sortable: bool = False


class DataGridProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict]
    rows: list[dict]
    page_size: int = 10


class TimelineProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[dict]


class MetricProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    value: str | int | float
    change: str | None = None
    trend: str | None = None


class ProgressProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: float
    max: float = 100.0
    label: str | None = None


class ChartProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chart_type: str
    data: dict
    title: str = ""


# ── Display family ────────────────────────────────────────────────────────────


class AvatarProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    url: str | None = None
    size: Literal["sm", "md", "lg"] = "md"


class StatusIndicatorProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    label: str


class EntityCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    entity_type: str
    entity_id: str
    attributes: dict | None = None


class MemoryCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_text: str
    memory_type: str
    source: str
    confidence: float = 1.0


# ── Specialized family ────────────────────────────────────────────────────────


class ExecutionTraceProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    steps: list[dict]
    status: str


class KanbanBoardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict]


class CalendarProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[dict]
    view: Literal["day", "week", "month"] = "week"


# ── Layout family (with required properties) ──────────────────────────────────


class TabsProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    active_tab: int = 0
    labels: list[str]


class ModalProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    open: bool = False


# ── Registry ──────────────────────────────────────────────────────────────────

PROPERTY_MODELS: dict[str, type[BaseModel]] = {
    # Text
    "Text": TextProperties,
    "CodeBlock": CodeBlockProperties,
    "Badge": BadgeProperties,
    "Alert": AlertProperties,
    # Input
    "Button": ButtonProperties,
    "TextField": TextFieldProperties,
    "Select": SelectProperties,
    "Toggle": ToggleProperties,
    # Data
    "Table": TableProperties,
    "DataGrid": DataGridProperties,
    "Timeline": TimelineProperties,
    "Metric": MetricProperties,
    "Progress": ProgressProperties,
    "Chart": ChartProperties,
    # Display
    "Avatar": AvatarProperties,
    "StatusIndicator": StatusIndicatorProperties,
    "EntityCard": EntityCardProperties,
    "MemoryCard": MemoryCardProperties,
    # Specialized
    "ExecutionTrace": ExecutionTraceProperties,
    "KanbanBoard": KanbanBoardProperties,
    "Calendar": CalendarProperties,
    # Layout (with required properties)
    "Tabs": TabsProperties,
    "Modal": ModalProperties,
}
