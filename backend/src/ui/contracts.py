"""A2UI surface/component Pydantic models.

Declarative JSON protocol for agent-driven interfaces. The Presenter agent
generates these surfaces, which the frontend renders using native React components.

Component Types (16 — the set actually produced by renderer.py builders):
  Layout: Row, Card, Divider
  Text: Text, CodeBlock, Badge, Alert
  Data: Table, Timeline, Metric, Progress
  Input: Button
  Display: List, EntityCard, MemoryCard
  Specialized: ExecutionTrace
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Current A2UI schema version. Bump on contract changes readers must distinguish.
# Readers MUST tolerate unknown future values and missing values (defaults applied).
A2UI_SCHEMA_VERSION = 1

# ── Surface kind taxonomy ───────────────────────────────────────

SurfaceKind = Literal[
    # System-managed (detail API exposed)
    "run",  # unified execution run surface (replaces execution/plan/approval trio)
    "summary",  # lightweight completion card emitted when a run finishes
    "briefing",
    "alert",
    "recommendation",
    "proactive_insight",
    # Agent-managed (inline children, no detail API)
    "message",  # Presenter-authored rich response promoted to workspace feed
    # Legacy kinds retained for backward compatibility with existing persisted surfaces
    # and REST-polled fallbacks; new code SHOULD NOT create these.
    "plan",
    "checklist",
    "approval",
    "comparison",
    "timeline",
    "table",
    "activity",
]


SYSTEM_SURFACE_KINDS = frozenset(
    {
        "run",
        "summary",
        "briefing",
        "alert",
        "recommendation",
        "proactive_insight",
    }
)
"""Surface kinds owned by backend services. These expose detail APIs."""


AGENT_SURFACE_KINDS = frozenset({"message"})
"""Surface kinds authored inline by the Presenter. No detail API — children carry all content."""


def is_system_surface(kind: str) -> bool:
    """Return True if the kind is a system-managed surface (has detail API)."""
    return kind in SYSTEM_SURFACE_KINDS


def is_agent_surface(kind: str) -> bool:
    """Return True if the kind is an agent-authored surface (inline children only)."""
    return kind in AGENT_SURFACE_KINDS


class ComponentType(str, Enum):
    # Layout
    ROW = "Row"
    CARD = "Card"
    DIVIDER = "Divider"
    # Text
    TEXT = "Text"
    CODE_BLOCK = "CodeBlock"
    BADGE = "Badge"
    ALERT = "Alert"
    # Data
    TABLE = "Table"
    TIMELINE = "Timeline"
    METRIC = "Metric"
    PROGRESS = "Progress"
    # Input
    BUTTON = "Button"
    # Display
    LIST = "List"
    ENTITY_CARD = "EntityCard"
    MEMORY_CARD = "MemoryCard"
    # Specialized
    EXECUTION_TRACE = "ExecutionTrace"


class A2UIAction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = "click"  # click, submit, change
    payload: dict = Field(default_factory=dict)


class A2UIComponent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: int = A2UI_SCHEMA_VERSION
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

    @model_validator(mode="after")
    def _validate_properties(self) -> "A2UIComponent":
        from src.ui.component_properties import PROPERTY_MODELS

        model = PROPERTY_MODELS.get(self.type)
        if model is not None:
            model(**self.properties)
        return self


class A2UISurface(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: int = A2UI_SCHEMA_VERSION
    type: str = "surface"
    id: str
    children: list[A2UIComponent] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


# ── Rich preview + detail modal contracts ───────────────────────


_VALID_METRIC_VARIANTS = {"default", "success", "warning", "danger"}


class SurfaceMetric(BaseModel):
    """Single metric displayed on a preview card (e.g. '3 tasks', 'high risk')."""

    model_config = ConfigDict(extra="ignore")

    label: str
    value: str
    variant: Literal["default", "success", "warning", "danger"] = "default"

    @field_validator("variant", mode="before")
    @classmethod
    def _coerce_variant(cls, v: str) -> str:
        """Map unknown LLM-generated variants (e.g. 'neutral', 'info') to 'default'."""
        return v if v in _VALID_METRIC_VARIANTS else "default"


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

    # ── Per-kind first-class fields (all optional; preserve back-compat) ──
    # Populated by the surface builders so the frontend design's per-kind
    # cards have a typed home on the wire instead of overloading metrics[].
    tokens: int | None = None  # run/execution: input+output tokens summed
    cost_usd: float | None = None  # run/execution: USD cost rollup
    risk: Literal["low", "medium", "high", "critical"] | None = None  # approval context
    flags: list[str] = []  # e.g. ["Irreversible", "LEARNING"]
    items: list[str] = []  # briefing priority strings (capped)
    evidence: str | None = None  # e.g. "42 days observed"
    updated_at: datetime | None = None  # run last-updated / completed timestamp


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
