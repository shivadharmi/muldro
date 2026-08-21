"""The component tree is a discriminated union, so an invalid component cannot parse.

Before this, `A2UIComponent.type` was a bare `str` whose validator logged a warning for an
unknown type and then PASSED, and `properties` was an untyped `dict`. Nothing about a
component was enforced, so the taxonomy had to live as prose in the Presenter prompt.
"""

from __future__ import annotations

import json
import typing

import pytest
from pydantic import BaseModel, ValidationError

from src.ui.components import AnyComponent, parse_component
from src.ui.contracts import ComponentType


class _EnvelopeForSchema(BaseModel):
    component: AnyComponent


def test_a_valid_text_component_parses():
    c = parse_component({"type": "Text", "id": "t1", "properties": {"text": "hello"}})
    assert c.type == "Text"
    assert c.properties.text == "hello"


def test_an_unknown_component_type_is_rejected():
    """It used to log a warning and pass."""
    with pytest.raises(ValidationError):
        parse_component({"type": "Bogus", "id": "x", "properties": {}})


def test_a_component_missing_its_required_property_is_rejected():
    with pytest.raises(ValidationError):
        parse_component({"type": "Metric", "id": "m1", "properties": {"label": "Funding"}})


def test_an_empty_id_is_rejected():
    with pytest.raises(ValidationError):
        parse_component({"type": "Text", "id": "  ", "properties": {"text": "hi"}})


def test_layout_components_nest_children():
    card = parse_component(
        {
            "type": "Card",
            "id": "c1",
            "children": [{"type": "Text", "id": "t1", "properties": {"text": "inner"}}],
        }
    )
    assert card.children[0].type == "Text"


def test_a_nested_child_of_the_wrong_shape_is_rejected():
    """Recursion must validate all the way down, not just at the top level."""
    with pytest.raises(ValidationError):
        parse_component(
            {
                "type": "Card",
                "id": "c1",
                "children": [{"type": "Bogus", "id": "t1", "properties": {}}],
            }
        )


def test_the_union_covers_every_component_type_exactly():
    """Drift guard: a new ComponentType with no union member would silently be
    unrepresentable, and a union member with no ComponentType would be unrenderable."""
    # `AnyComponent` is `Annotated[A | B | ..., Field(discriminator=...)]`, so the members
    # are one level in: get_args gives (the union, the FieldInfo).
    members = typing.get_args(typing.get_args(AnyComponent)[0])
    union_types = set()
    for member in members:
        ann = member.model_fields["type"].annotation
        union_types.update(typing.get_args(ann))
    assert union_types == {c.value for c in ComponentType}


def test_the_generated_schema_has_no_free_form_map():
    """The whole point: this schema is what a provider will be asked to enforce, and the live
    OpenAI Structured Outputs API rejects any object that allows undeclared keys."""
    schema = json.dumps(_EnvelopeForSchema.model_json_schema())
    assert '"additionalProperties": true' not in schema
