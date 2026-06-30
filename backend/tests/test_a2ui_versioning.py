"""Back-compat + forward-compat for the A2UI schema `version` field.
RED before the contracts.py diff (A2UI_SCHEMA_VERSION import fails)."""

from src.ui.contracts import A2UI_SCHEMA_VERSION, A2UIComponent, A2UISurface


def test_component_without_version_defaults_to_current() -> None:
    comp = A2UIComponent.model_validate({"type": "Text", "id": "c1", "properties": {"text": "hi"}})
    assert comp.version == A2UI_SCHEMA_VERSION


def test_surface_without_version_defaults_to_current() -> None:
    surf = A2UISurface.model_validate(
        {
            "type": "surface",
            "id": "surf_1",
            "children": [{"type": "Text", "id": "c1", "properties": {"text": "hi"}}],
            "metadata": {},
        }
    )
    assert surf.version == A2UI_SCHEMA_VERSION
    assert surf.children[0].version == A2UI_SCHEMA_VERSION


def test_unknown_future_version_does_not_crash() -> None:
    surf = A2UISurface.model_validate(
        {
            "version": 999,
            "type": "surface",
            "id": "s1",
            "children": [{"version": 999, "type": "FutureWidget", "id": "c1", "properties": {}}],
            "metadata": {"x": 1},
        }
    )
    assert surf.version == 999
    assert surf.children[0].type == "FutureWidget"


def test_round_trip_preserves_version() -> None:
    surf = A2UISurface(
        id="s1",
        children=[A2UIComponent(type="Text", id="c1", properties={"text": "x"})],
    )
    reparsed = A2UISurface.model_validate(surf.model_dump())
    assert reparsed.version == A2UI_SCHEMA_VERSION
    assert reparsed.children[0].version == A2UI_SCHEMA_VERSION
