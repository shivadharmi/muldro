"""A2UI surface/component Pydantic models.

Declarative JSON protocol for agent-driven interfaces. The Presenter agent
generates these surfaces, which the frontend renders using native React components.

Component Types (25+):
  Layout: Row, Column, Card, Tabs, Modal, Divider
  Text: Text, CodeBlock, Badge, Alert
  Data: Table, DataGrid, Timeline, Metric, Progress, Chart
  Input: Button, TextField, Select, Toggle, Form
  Display: Avatar, StatusIndicator, EntityCard, MemoryCard
  Specialized: ExecutionTrace, KanbanBoard, Calendar, CommandPalette
"""

import logging
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ── Surface kind taxonomy ───────────────────────────────────────

SurfaceKind = Literal[
    "summary",
    "briefing",
    "plan",
    "checklist",
    "approval",
    "comparison",
    "alert",
    "timeline",
    "table",
    "recommendation",
    "activity",
    "proactive_insight",
]


class ComponentType(str, Enum):
    # Layout
    ROW = "Row"
    COLUMN = "Column"
    CARD = "Card"
    TABS = "Tabs"
    MODAL = "Modal"
    DIVIDER = "Divider"
    # Text
    TEXT = "Text"
    CODE_BLOCK = "CodeBlock"
    BADGE = "Badge"
    ALERT = "Alert"
    # Data
    TABLE = "Table"
    DATA_GRID = "DataGrid"
    TIMELINE = "Timeline"
    METRIC = "Metric"
    PROGRESS = "Progress"
    CHART = "Chart"
    # Input
    BUTTON = "Button"
    TEXT_FIELD = "TextField"
    SELECT = "Select"
    TOGGLE = "Toggle"
    FORM = "Form"
    # Display
    LIST = "List"
    AVATAR = "Avatar"
    STATUS_INDICATOR = "StatusIndicator"
    ENTITY_CARD = "EntityCard"
    MEMORY_CARD = "MemoryCard"
    IMAGE = "Image"
    # Specialized
    EXECUTION_TRACE = "ExecutionTrace"
    KANBAN_BOARD = "KanbanBoard"
    CALENDAR = "Calendar"
    COMMAND_PALETTE = "CommandPalette"


class A2UIAction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = "click"  # click, submit, change
    payload: dict = Field(default_factory=dict)


class A2UIComponent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    id: str
    properties: dict = Field(default_factory=dict)
    children: list["A2UIComponent"] = Field(default_factory=list)
    actions: list[A2UIAction] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def validate_component_type(cls, v: str) -> str:
        valid_types = {ct.value for ct in ComponentType}
        if v not in valid_types:
            logger.warning("Unknown A2UI component type: %s", v)
        return v

    @field_validator("id")
    @classmethod
    def validate_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Component id must not be empty")
        return v


class A2UISurface(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = "surface"
    id: str
    children: list[A2UIComponent] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


# ── Rich preview + detail modal contracts ───────────────────────


class SurfaceMetric(BaseModel):
    """Single metric displayed on a preview card (e.g. '3 tasks', 'high risk')."""

    model_config = ConfigDict(extra="ignore")

    label: str
    value: str
    variant: Literal["default", "success", "warning", "danger"] = "default"


class SurfacePreview(BaseModel):
    """Rich preview data for workspace grid cards.

    Contains everything the frontend needs to render a visually
    differentiated card — no A2UI component tree needed.
    """

    model_config = ConfigDict(extra="ignore")

    title: str
    subtitle: str | None = None
    status: (
        Literal[
            "pending",
            "running",
            "completed",
            "failed",
            "awaiting_approval",
            "cancelled",
            "proposal",
        ]
        | None
    ) = None
    priority: Literal["low", "medium", "high", "critical"] | None = None
    metrics: list[SurfaceMetric] = []
    entities: list[str] = []
    progress: float | None = None
    timestamp: str | None = None
    tags: list[str] = []


class DetailTab(BaseModel):
    """Single tab in the detail modal — points to a server endpoint."""

    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    endpoint: str
    icon: str | None = None
    badge_count: int | None = None


class DetailConfig(BaseModel):
    """Configuration for the detail modal — which tabs to show."""

    model_config = ConfigDict(extra="ignore")

    tabs: list[DetailTab]
    default_tab: str | None = None

    @model_validator(mode="after")
    def _check_default_tab(self) -> "DetailConfig":
        if self.default_tab is not None and self.tabs:
            tab_ids = [t.id for t in self.tabs]
            if self.default_tab not in tab_ids:
                msg = f"default_tab '{self.default_tab}' not in tabs: {tab_ids}"
                raise ValueError(msg)
        return self


class DetailSection(BaseModel):
    """Collapsible section within a detail tab response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    collapsed: bool = True
    children: list[A2UIComponent]


class DetailTabResponse(BaseModel):
    """Response from a detail tab endpoint — sections of A2UI content."""

    model_config = ConfigDict(extra="ignore")

    tab_id: str
    sections: list[DetailSection]


# Rebuild for recursive model
A2UIComponent.model_rebuild()
