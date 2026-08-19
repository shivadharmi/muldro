"""`render_surface` carries the A2UI taxonomy in its input schema.

The channel it replaces was fenced markdown in the reply text: an unparseable tree returned
`None` and pushed an empty surface behind a `logger.debug`, so the author never learned it
had failed. A tool is the only channel that reports back — `muldro_tool_dispatcher` sets
`ToolMessage(status="error")` when a result carries an error, and the agent loop can retry.

A tool rather than `response_format`, because the lead's reply is prose PLUS optional UI;
`response_format` would force the whole reply to be JSON and destroy `<always_reply>`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from src.integrations.capabilities import CAPABILITY_CATALOG
from src.tools.catalog import INTERNAL_TOOLS
from src.tools.schemas import TOOL_INPUT_MODELS, RenderSurfaceInput
from src.ui.contracts import ComponentType
from src.ui.strict_schema import to_strict_json_schema
from tests.test_strict_schema import assert_strict


def _tool():
    return next(t for t in INTERNAL_TOOLS if t.name == "render_surface")


def _surface(**overrides) -> dict:
    payload = {
        "kind": "summary",
        "title": "Pipeline review",
        "sections": [
            {
                "type": "Metric",
                "id": "m1",
                "properties": {"label": "Open deals", "value": "12"},
            }
        ],
    }
    payload.update(overrides)
    return payload


# ── Registration: all three sites plus the capability taxonomy ──────────────────


def test_the_tool_is_in_the_catalog_with_the_right_metadata():
    tool = _tool()
    assert tool.capability == "internal.render_surface"
    assert tool.read_only is False
    assert tool.requires_approval is False
    assert tool.server == "communication"


def test_the_capability_exists_in_the_catalog():
    """`validate_registry` check 1 fails otherwise — a tool cannot reference a capability
    no agent scope could ever name."""
    assert "internal.render_surface" in CAPABILITY_CATALOG


def test_the_input_model_is_registered():
    """`validate_registry` check 5: a catalog tool with no input model has no schema, so
    nothing validates the component tree before it reaches the server."""
    assert TOOL_INPUT_MODELS["render_surface"] is RenderSurfaceInput


def test_the_tool_is_actually_served_by_the_composed_mcp_server():
    """Catalogued is not served. Dispatch goes through `Client(muldro_tools)` with the
    name `{server}_{tool}`, so this is the name that has to exist."""
    from fastmcp import Client

    from src.tools.server import muldro_tools

    async def _names() -> set[str]:
        async with Client(muldro_tools) as client:
            return {t.name for t in await client.list_tools()}

    assert "communication_render_surface" in asyncio.run(_names())


# ── The taxonomy is enforced, not described ────────────────────────────────────


def test_a_valid_surface_parses_into_typed_components():
    parsed = RenderSurfaceInput.model_validate(_surface())
    section = parsed.sections[0]
    assert section.type == "Metric"
    # Not a dict — the whole point of the discriminated union.
    assert section.properties.label == "Open deals"
    assert section.properties.value == "12"


def test_an_unknown_component_type_is_rejected():
    bad = _surface(sections=[{"type": "Chart", "id": "c1", "properties": {"data": []}}])
    with pytest.raises(ValidationError):
        RenderSurfaceInput.model_validate(bad)


def test_a_component_missing_a_required_property_is_rejected():
    bad = _surface(sections=[{"type": "Metric", "id": "m1", "properties": {"label": "x"}}])
    with pytest.raises(ValidationError):
        RenderSurfaceInput.model_validate(bad)


def test_a_blank_title_is_rejected():
    with pytest.raises(ValidationError):
        RenderSurfaceInput.model_validate(_surface(title="   "))


# ── `kind` makes the prompt rule enforceable ───────────────────────────────────


def test_a_system_only_kind_is_rejected():
    """Settled decision D2: the `prepared_work` queue is the ONLY place a staged action can
    be acted on. An agent that could author a `prepared_work` surface could stand up a
    second, ungated review path — so the prompt rule saying "don't" becomes a schema rule."""
    with pytest.raises(ValidationError):
        RenderSurfaceInput.model_validate(_surface(kind="prepared_work"))


def test_only_the_agent_authorable_kinds_are_offered():
    kinds = to_strict_json_schema(RenderSurfaceInput)["properties"]["kind"]["enum"]
    assert set(kinds) == {"message", "summary", "briefing", "alert", "recommendation"}
    for system_only in ("run", "approval", "proactive_insight", "prepared_work", "plan"):
        assert system_only not in kinds


# ── Metrics are typed and bounded ──────────────────────────────────────────────


def test_metrics_are_typed():
    parsed = RenderSurfaceInput.model_validate(
        _surface(metrics=[{"label": "Deals", "value": "12", "variant": "success"}])
    )
    assert parsed.metrics[0].variant == "success"
    # `SurfaceMetric` is the existing contract, coercing validator and all — not a copy.
    assert (
        RenderSurfaceInput.model_validate(
            _surface(metrics=[{"label": "x", "value": "1", "variant": "neutral"}])
        )
        .metrics[0]
        .variant
        == "default"
    )


def test_more_than_four_metrics_is_rejected():
    """Four is what the preview card renders; a fifth would be silently dropped."""
    five = [{"label": f"m{i}", "value": str(i)} for i in range(5)]
    with pytest.raises(ValidationError):
        RenderSurfaceInput.model_validate(_surface(metrics=five))


# ── The schema is what carries the taxonomy ────────────────────────────────────


def test_the_strict_schema_names_every_component_type():
    """This is the ~900 tokens of prose the tool replaces: if a type is absent from the
    schema the model has no way to learn it exists."""
    schema = json.dumps(to_strict_json_schema(RenderSurfaceInput))
    missing = [c.value for c in ComponentType if f'"const": "{c.value}"' not in schema]
    assert not missing, f"component types absent from the tool schema: {missing}"


def test_the_strict_schema_satisfies_the_strict_constraints():
    assert_strict(to_strict_json_schema(RenderSurfaceInput))
