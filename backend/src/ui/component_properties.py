"""Typed property models for A2UI components.

Each model corresponds to a component type in ComponentType and validates
the properties dict before it is passed to A2UIComponent. Builder functions
in renderer.py construct these models first, then call .model_dump() to
produce the properties dict — giving build-time validation on top of the
runtime model_validator on A2UIComponent.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TextProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    variant: Literal["body", "heading", "caption", "label", "subheading"] = "body"


class CodeBlockProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    language: str = "text"


class BadgeProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    variant: Literal["default", "success", "warning", "danger", "info"] = "default"


class AlertProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str
    severity: Literal["info", "warning", "error", "success"] = "info"
    title: str | None = None


class TabsProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    active_tab: int = 0
    labels: list[str] = Field(default_factory=list)


class ModalProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    open: bool = True


class ButtonProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    variant: Literal["primary", "secondary", "danger", "ghost"] = "primary"


class TextFieldProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str = ""
    placeholder: str = ""
    value: str = ""


class SelectProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    value: str = ""


class ToggleProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    checked: bool = False


class TableProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    sortable: bool = False


class DataGridProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    page_size: int = 20


class TimelineProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[dict[str, Any]] = Field(default_factory=list)


class MetricProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    value: str | int | float
    change: str | None = None
    trend: str | None = None


class ProgressProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: float
    max: float = 100
    label: str | None = None


class ChartProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chart_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    title: str = ""


class AvatarProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    url: str | None = None
    size: Literal["sm", "md", "lg"] = "md"


class StatusIndicatorProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    label: str = ""


class EntityCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    entity_type: str
    entity_id: str = ""
    attributes: dict[str, Any] | None = None


class MemoryCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_text: str
    memory_type: str
    source: str = ""
    confidence: float = 1.0


class ExecutionTraceProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    steps: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "running"


class KanbanBoardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict[str, Any]] = Field(default_factory=list)


class CalendarProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[dict[str, Any]] = Field(default_factory=list)
    view: Literal["day", "week", "month"] = "week"
