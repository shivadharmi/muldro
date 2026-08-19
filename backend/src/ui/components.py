"""A2UI components as a discriminated union.

`PROPERTY_MODELS` already held the per-type property shapes, but they were applied in a
runtime `model_validator` on a `properties: dict` field — so the generated JSON Schema said
only "object" and every constraint had to be restated as prose in the Presenter prompt. Here
the type IS the schema: `type` is a Literal discriminator and `properties` is that type's
model, which means a provider can enforce the taxonomy and the prompt does not have to
describe it.

Layout containers (`Card`, `Row`, `List`, `Divider`) have no properties and carry `children`
instead; they are intentionally absent from `PROPERTY_MODELS`.

Deliberately absent: `actions`. `A2UIComponent.actions` carries an untyped `dict` payload, the
prompt it replaces says actions are "usually omitted", and every interactive surface is
system-built — so leaving it out keeps the last untyped `dict` off the model-facing path. If an
agent-authored action is ever needed it gets a typed union of its own, not a free-form map.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str

    @field_validator("id")
    @classmethod
    def _id_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("component id must not be empty")
        return v


class TextComponent(_Base):
    type: Literal["Text"]
    properties: TextProperties


class MarkdownComponent(_Base):
    type: Literal["Markdown"]
    properties: MarkdownProperties


class CodeBlockComponent(_Base):
    type: Literal["CodeBlock"]
    properties: CodeBlockProperties


class BadgeComponent(_Base):
    type: Literal["Badge"]
    properties: BadgeProperties


class AlertComponent(_Base):
    type: Literal["Alert"]
    properties: AlertProperties


class ButtonComponent(_Base):
    type: Literal["Button"]
    properties: ButtonProperties


class TableComponent(_Base):
    type: Literal["Table"]
    properties: TableProperties


class TimelineComponent(_Base):
    type: Literal["Timeline"]
    properties: TimelineProperties


class MetricComponent(_Base):
    type: Literal["Metric"]
    properties: MetricProperties


class ProgressComponent(_Base):
    type: Literal["Progress"]
    properties: ProgressProperties


class EntityCardComponent(_Base):
    type: Literal["EntityCard"]
    properties: EntityCardProperties


class MemoryCardComponent(_Base):
    type: Literal["MemoryCard"]
    properties: MemoryCardProperties


class ExecutionTraceComponent(_Base):
    type: Literal["ExecutionTrace"]
    properties: ExecutionTraceProperties


class CardComponent(_Base):
    type: Literal["Card"]
    children: list[AnyComponent] = Field(default_factory=list)


class RowComponent(_Base):
    type: Literal["Row"]
    children: list[AnyComponent] = Field(default_factory=list)


class ListComponent(_Base):
    type: Literal["List"]
    children: list[AnyComponent] = Field(default_factory=list)


class DividerComponent(_Base):
    type: Literal["Divider"]


AnyComponent = Annotated[
    TextComponent
    | MarkdownComponent
    | CodeBlockComponent
    | BadgeComponent
    | AlertComponent
    | ButtonComponent
    | TableComponent
    | TimelineComponent
    | MetricComponent
    | ProgressComponent
    | EntityCardComponent
    | MemoryCardComponent
    | ExecutionTraceComponent
    | CardComponent
    | RowComponent
    | ListComponent
    | DividerComponent,
    Field(discriminator="type"),
]

CardComponent.model_rebuild()
RowComponent.model_rebuild()
ListComponent.model_rebuild()


class _ComponentEnvelope(BaseModel):
    """Parsing helper — Pydantic needs a model to resolve an Annotated union against."""

    model_config = ConfigDict(extra="ignore")

    component: AnyComponent


def parse_component(data: dict) -> AnyComponent:
    """Parse one component, raising `ValidationError` on anything invalid."""
    return _ComponentEnvelope(component=data).component
