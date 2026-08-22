"""The schema handed to a provider is strict; the models that parse payloads are not.

Provider-side enforcement (OpenAI Structured Outputs and the strict tool-schema dialects
modelled on it) needs `additionalProperties: false` on every object and every declared key
listed in `required`. Pydantic emits neither. Making the *models* strict would be the wrong
fix — `component_properties.py` also validates inbound payloads on the legacy fenced path,
which drops the WHOLE surface on one validation failure. So the transformation happens on
the way out.

The load-bearing test here is `test_the_untransformed_schema_fails_the_same_assertions`:
without it, the strict assertions could be passing because the input was already compliant.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from src.ui.components import AnyComponent
from src.ui.contracts import SurfaceMetric
from src.ui.strict_schema import FreeFormMapError, to_strict_json_schema

# ── The subject ─────────────────────────────────────────────────────────────────
# The transformer's whole difficulty is a recursive DISCRIMINATED UNION nested inside
# optional and length-bounded fields, so the fixture is a model of exactly that shape.
# It is defined here rather than borrowed from a shipped tool because no shipped tool
# needs one — the transformer is what is under test, not any particular caller.


class _ComponentTreeInput(BaseModel):
    """A payload carrying a recursive component tree, optional and bounded fields."""

    kind: Literal["message", "summary", "briefing", "alert", "recommendation"]
    title: str = Field(max_length=80)
    subtitle: str | None = Field(default=None, max_length=120)
    metrics: list[SurfaceMetric] = Field(default_factory=list, max_length=4)
    sections: list[AnyComponent]


# ── Shared helpers ──────────────────────────────────────────────────────────────

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
    assert_strict(to_strict_json_schema(_ComponentTreeInput))


def test_the_untransformed_schema_fails_the_same_assertions():
    """The teeth. If Pydantic ever started emitting a compliant schema on its own, every
    other assertion in this file would pass for the wrong reason."""
    with pytest.raises(AssertionError):
        assert_strict(_ComponentTreeInput.model_json_schema())


def test_optional_fields_become_required_and_stay_nullable():
    schema = to_strict_json_schema(_ComponentTreeInput)
    event = schema["$defs"]["TimelineEvent"]
    assert set(event["required"]) == {"time", "title", "description", "source"}
    assert {"type": "null"} in event["properties"]["description"]["anyOf"]


def test_the_recursive_container_is_transformed_too():
    """`CardComponent.children` is a list of the union that contains CardComponent — a walker
    that stopped at the first `$defs` pass, or recursed forever, would show up here."""
    card = to_strict_json_schema(_ComponentTreeInput)["$defs"]["CardComponent"]
    assert card["additionalProperties"] is False
    assert card["required"] == ["id", "type", "children"]


def test_the_transformer_does_not_mutate_the_models_own_schema():
    before = _ComponentTreeInput.model_json_schema()
    to_strict_json_schema(_ComponentTreeInput)
    assert _ComponentTreeInput.model_json_schema() == before
    assert "additionalProperties" not in before["$defs"]["CardComponent"]


def test_ref_nodes_are_not_corrupted():
    """A `$ref` must stay a bare pointer — a strict dialect rejects a `$ref` with siblings."""
    schema = to_strict_json_schema(_ComponentTreeInput)
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
    parsed = _ComponentTreeInput.model_validate(payload)
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


def test_the_openai_reference_transformer_does_not_handle_discriminated_unions():
    """Independent oracle, recording a KNOWN divergence rather than asserting equality.

    `openai.lib._pydantic.to_strict_json_schema` was a faithful oracle until this module
    started renaming `oneOf` and dropping `discriminator`. It does neither — so on a
    discriminated union its output is a schema the live OpenAI API REJECTS with
    "'oneOf' is not permitted" (measured 2026-08-20, gpt-5-mini). Ours is verified accepted.

    The assertion is therefore inverted: it pins the divergence so that if the reference
    ever learns to handle discriminated unions, this test tells us and we can reconsider
    carrying our own. Private path, so it skips rather than fails if it moves.
    """
    pytest.importorskip("openai.lib._pydantic")
    from openai.lib._pydantic import to_strict_json_schema as reference

    def nodes(node: Any):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from nodes(value)
        elif isinstance(node, list):
            for value in node:
                yield from nodes(value)

    reference_schema = reference(_ComponentTreeInput)
    assert any("oneOf" in n for n in nodes(reference_schema)), (
        "the OpenAI reference transformer now strips `oneOf` — re-evaluate whether this "
        "module still needs to carry its own union handling"
    )
    assert not any("oneOf" in n for n in nodes(to_strict_json_schema(_ComponentTreeInput)))


class TestUnionKeywordsTheProviderRejectsOrIgnores:
    """A discriminated union's `oneOf` + `discriminator` must not survive the transform.

    Measured against the live OpenAI API on 2026-08-20 with `gpt-5-mini`, and the two fail
    in different ways — which is why both are asserted separately:

      `oneOf`         hard 400: "Invalid schema ... 'oneOf' is not permitted".
      `discriminator` ACCEPTED, then the model returned output VIOLATING the schema
                      (a TableColumn missing its required `key`) on 3 trials out of 3,
                      against 3/3 valid once it was dropped.

    The second is why this is a test and not a comment: a schema the provider accepts and
    then silently fails to enforce looks exactly like one that works.
    """

    def _nodes(self, node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from self._nodes(value)
        elif isinstance(node, list):
            for value in node:
                yield from self._nodes(value)

    def test_the_lenient_schema_really_does_contain_both(self):
        """Teeth: without this the two assertions below could pass on an input that never
        had a discriminated union in it."""
        lenient = _ComponentTreeInput.model_json_schema()
        assert any("oneOf" in n for n in self._nodes(lenient))
        assert any("discriminator" in n for n in self._nodes(lenient))

    def test_no_oneof_survives(self):
        strict = to_strict_json_schema(_ComponentTreeInput)
        assert [n for n in self._nodes(strict) if "oneOf" in n] == []

    def test_no_discriminator_survives(self):
        strict = to_strict_json_schema(_ComponentTreeInput)
        assert [n for n in self._nodes(strict) if "discriminator" in n] == []

    def test_the_union_members_are_preserved_as_anyof(self):
        """Renaming must not lose branches — `anyOf` over the same `$ref` members still
        selects exactly one, since each member is fixed by its `type` const."""
        strict = to_strict_json_schema(_ComponentTreeInput)
        branch_counts = [len(n["anyOf"]) for n in self._nodes(strict) if "anyOf" in n]
        assert 17 in branch_counts, (
            f"expected an anyOf with all 17 component branches, saw {sorted(set(branch_counts))}"
        )
