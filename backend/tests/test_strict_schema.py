"""The schema handed to a provider is strict; the models that parse payloads are not.

Provider-side enforcement (OpenAI Structured Outputs and the strict tool-schema dialects
modelled on it) needs `additionalProperties: false` on every object and every declared key
listed in `required`. Pydantic emits neither. Making the *models* strict would be the wrong
fix — `component_properties.py` also validates inbound payloads on the legacy fenced path,
where `extract_surface_data` drops the WHOLE surface on one validation failure. So the
transformation happens on the way out.

The load-bearing test here is `test_the_untransformed_schema_fails_the_same_assertions`:
without it, the strict assertions could be passing because the input was already compliant.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from src.tools.schemas import RenderSurfaceInput
from src.ui.strict_schema import FreeFormMapError, to_strict_json_schema

# ── Shared helpers (also imported by tests/test_render_surface_tool.py) ──────────

_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")


def iter_schema_nodes(node: Any):
    """Yield every schema node reachable from `node`, without descending into non-schema
    maps such as a discriminator's `mapping` (whose values are strings, not schemas)."""
    if isinstance(node, list):
        for item in node:
            yield from iter_schema_nodes(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key in ("$defs", "definitions"):
        if isinstance(node.get(key), dict):
            for sub in node[key].values():
                yield from iter_schema_nodes(sub)
    for key in _SCHEMA_LIST_KEYS:
        if isinstance(node.get(key), list):
            yield from iter_schema_nodes(node[key])
    if "items" in node:
        yield from iter_schema_nodes(node["items"])
    if isinstance(node.get("properties"), dict):
        for sub in node["properties"].values():
            yield from iter_schema_nodes(sub)


def object_nodes(schema: dict) -> list[dict]:
    """Every node that declares properties — i.e. every node strict mode constrains."""
    return [n for n in iter_schema_nodes(schema) if isinstance(n.get("properties"), dict)]


def assert_strict(schema: dict) -> None:
    """The three constraints, asserted recursively."""
    nodes = object_nodes(schema)
    assert nodes, "schema declares no object nodes at all — assertion would be vacuous"
    for node in nodes:
        title = node.get("title", "<anonymous>")
        assert node.get("additionalProperties") is False, f"{title} allows undeclared keys"
        assert set(node["properties"]) == set(node.get("required", [])), (
            f"{title} declares {sorted(node['properties'])} but requires "
            f"{sorted(node.get('required', []))}"
        )
    for node in iter_schema_nodes(schema):
        extra = node.get("additionalProperties")
        assert extra in (None, False), f"{node.get('title')} sets additionalProperties={extra!r}"


# ── Tests ───────────────────────────────────────────────────────────────────────


def test_the_transformed_schema_is_strict():
    assert_strict(to_strict_json_schema(RenderSurfaceInput))


def test_the_untransformed_schema_fails_the_same_assertions():
    """The teeth. If Pydantic ever started emitting a compliant schema on its own, every
    other assertion in this file would pass for the wrong reason."""
    with pytest.raises(AssertionError):
        assert_strict(RenderSurfaceInput.model_json_schema())


def test_optional_fields_become_required_and_stay_nullable():
    schema = to_strict_json_schema(RenderSurfaceInput)
    event = schema["$defs"]["TimelineEvent"]
    assert set(event["required"]) == {"time", "title", "description", "source"}
    assert {"type": "null"} in event["properties"]["description"]["anyOf"]


def test_the_recursive_container_is_transformed_too():
    """`CardComponent.children` is a list of the union that contains CardComponent — a walker
    that stopped at the first `$defs` pass, or recursed forever, would show up here."""
    card = to_strict_json_schema(RenderSurfaceInput)["$defs"]["CardComponent"]
    assert card["additionalProperties"] is False
    assert card["required"] == ["id", "type", "children"]


def test_the_transformer_does_not_mutate_the_models_own_schema():
    before = RenderSurfaceInput.model_json_schema()
    to_strict_json_schema(RenderSurfaceInput)
    assert RenderSurfaceInput.model_json_schema() == before
    assert "additionalProperties" not in before["$defs"]["CardComponent"]


def test_ref_nodes_are_not_corrupted():
    """A `$ref` must stay a bare pointer — a strict dialect rejects a `$ref` with siblings."""
    schema = to_strict_json_schema(RenderSurfaceInput)
    refs = [n for n in iter_schema_nodes(schema) if "$ref" in n]
    assert refs
    for node in refs:
        assert set(node) == {"$ref"}, f"$ref node gained siblings: {sorted(node)}"


def test_a_payload_satisfying_the_strict_schema_still_parses_through_the_real_model():
    """Strict on the way out, forgiving on the way in — a payload that names every key,
    including the ones only strict mode forces, must round-trip through the lenient model."""
    payload = {
        "kind": "summary",
        "title": "Q3 pipeline",
        "subtitle": None,
        "metrics": [{"label": "Deals", "value": "12", "variant": "success"}],
        "sections": [
            {
                "type": "Card",
                "id": "c1",
                "children": [
                    {"type": "Text", "id": "t1", "properties": {"text": "hi", "variant": "body"}},
                    {
                        "type": "Timeline",
                        "id": "tl1",
                        "properties": {
                            "events": [
                                {
                                    "time": "09:00",
                                    "title": "Kickoff",
                                    "description": None,
                                    "source": None,
                                }
                            ]
                        },
                    },
                ],
            }
        ],
    }
    parsed = RenderSurfaceInput.model_validate(payload)
    assert parsed.sections[0].children[1].properties.events[0].title == "Kickoff"


def test_a_free_form_map_is_asserted_not_repaired():
    """Coercing `dict` to `additionalProperties: false` would silently narrow the field to
    "accepts only {}". The preceding commits closed every such shape; this catches a return."""

    class _Open(BaseModel):
        attributes: dict[str, str]

    with pytest.raises(FreeFormMapError):
        to_strict_json_schema(_Open)


def test_an_object_with_no_declared_properties_is_asserted_too():
    """A bare `dict` generates `{"type": "object"}` with no `additionalProperties` key at
    all, which is just as open and would otherwise slip through."""

    class _BareDict(BaseModel):
        blob: dict

    with pytest.raises(FreeFormMapError):
        to_strict_json_schema(_BareDict)


def test_matches_the_openai_reference_transformer():
    """Independent oracle. `openai.lib._pydantic` is private, so this is a cross-check, not a
    dependency — it skips rather than fails if the private path moves. Structural, not byte
    equality: the OpenAI version additionally strips `default: null`, which is informative to
    a model and which the API accepts."""
    pytest.importorskip("openai.lib._pydantic")
    from openai.lib._pydantic import to_strict_json_schema as reference

    def drop_null_defaults(node: Any) -> Any:
        if isinstance(node, list):
            return [drop_null_defaults(n) for n in node]
        if not isinstance(node, dict):
            return node
        return {
            k: drop_null_defaults(v) for k, v in node.items() if not (k == "default" and v is None)
        }

    assert drop_null_defaults(to_strict_json_schema(RenderSurfaceInput)) == drop_null_defaults(
        reference(RenderSurfaceInput)
    )
