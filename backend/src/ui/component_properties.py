"""Typed property models for A2UI component types.

Each component type that carries semantic properties has a corresponding Pydantic model.
Layout containers (Card, Row, List, Divider) have no required properties
and are intentionally absent from PROPERTY_MODELS.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# ── Text family ─────────────────────────────────────────────────────────────


class TextProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    variant: Literal["heading", "body", "caption"] = "body"


class MarkdownProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str


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


# ── Data family ───────────────────────────────────────────────────────────────


class TableColumn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    label: str


class TableRow(BaseModel):
    """Positional cells, aligned to `TableProperties.columns`.

    Row keys are chosen at runtime FROM the columns, so a keyed map cannot be declared in a
    schema — and a free-form map makes the whole component schema unusable for provider-side
    structured output. Positions carry the same information in a closed shape.
    """

    model_config = ConfigDict(extra="ignore")

    cells: list[str]


class TableProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[TableColumn]
    rows: list[TableRow]
    sortable: bool = False

    @model_validator(mode="after")
    def _rows_match_columns(self) -> "TableProperties":
        width = len(self.columns)
        for i, row in enumerate(self.rows):
            if len(row.cells) != width:
                raise ValueError(
                    f"row {i} has {len(row.cells)} cells but there are {width} columns; "
                    "a mismatched row renders as blank cells with no error"
                )
        return self


class TimelineEvent(BaseModel):
    """One event on a timeline.

    Field names are the CONSUMER's — `timeline.tsx` renders `time`, `title`, then the
    supporting lines. While `events` was `list[dict]`, the producer emitted `timestamp` and
    `description` against a renderer reading `time` and `source`, so every event rendered a
    blank time line and dropped its description with nothing failing anywhere.
    """

    model_config = ConfigDict(extra="ignore")

    time: str
    title: str
    description: str | None = None
    source: str | None = None


class TimelineProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[TimelineEvent]


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


# ── Display family ────────────────────────────────────────────────────────────


class EntityAttribute(BaseModel):
    """One `key: value` line on an entity card.

    Attribute names are entity-dependent and chosen at runtime, so they cannot be declared as
    schema keys — a free-form map makes the whole component schema unusable for provider-side
    structured output. A list of explicit pairs carries the same information in a closed shape.
    """

    model_config = ConfigDict(extra="ignore")

    key: str
    value: str


class EntityCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    entity_type: str
    entity_id: str
    attributes: list[EntityAttribute] | None = None


class MemoryCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_text: str
    memory_type: str
    source: str
    confidence: float = 1.0


# ── Specialized family ────────────────────────────────────────────────────────


class ExecutionTraceStep(BaseModel):
    """One step on an execution trace.

    Field names are the CONSUMER's — `execution-trace.tsx` reads `title`, falling back to
    `task_type` and then to a positional "Step N". While `steps` was `list[dict]` the only
    producer wrote `label` and `result`, neither of which is read anywhere, so every step of
    every trace rendered as "Step 1", "Step 2" and the step's own name never reached a user.

    `error` and `duration_ms` are the reverse case — read by the renderer, written by no
    producer on this path — so they are declared optional rather than dropped.
    """

    model_config = ConfigDict(extra="ignore")

    title: str
    status: str
    task_type: str | None = None
    description: str | None = None
    error: str | None = None
    duration_ms: int | None = None


class ExecutionTraceProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    steps: list[ExecutionTraceStep]
    status: str


# ── Registry ──────────────────────────────────────────────────────────────────

PROPERTY_MODELS: dict[str, type[BaseModel]] = {
    # Text
    "Text": TextProperties,
    "Markdown": MarkdownProperties,
    "CodeBlock": CodeBlockProperties,
    "Badge": BadgeProperties,
    "Alert": AlertProperties,
    # Input
    "Button": ButtonProperties,
    # Data
    "Table": TableProperties,
    "Timeline": TimelineProperties,
    "Metric": MetricProperties,
    "Progress": ProgressProperties,
    # Display
    "EntityCard": EntityCardProperties,
    "MemoryCard": MemoryCardProperties,
    # Specialized
    "ExecutionTrace": ExecutionTraceProperties,
}
