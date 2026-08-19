"""No property model may contain a free-form map.

Measured against the live OpenAI Structured Outputs API on 2026-08-20: an object with
`additionalProperties` (which is what `dict` and `list[dict]` generate) is rejected outright,
so a single open shape anywhere makes the whole component schema unusable for provider-side
enforcement.
"""

from __future__ import annotations

import pytest

from src.ui.component_properties import PROPERTY_MODELS

# Shapes still open, closed in the commits that follow this one. Named explicitly so the
# remaining gap is visible in CI rather than silent — and so the allowlist cannot outlive its
# reason (see the test below it).
_STILL_OPEN = {"Timeline", "EntityCard", "ExecutionTrace"}


def _open_maps(node, path=""):
    """Yield paths of every object that allows undeclared keys."""
    found = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            ap = node.get("additionalProperties")
            if ap is True or isinstance(ap, dict):
                found.append(path or "<root>")
        for key, value in node.items():
            found += _open_maps(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _open_maps(value, f"{path}[{i}]")
    return found


@pytest.mark.parametrize("name", sorted(set(PROPERTY_MODELS) - _STILL_OPEN))
def test_property_model_has_no_free_form_map(name):
    schema = PROPERTY_MODELS[name].model_json_schema()
    found = _open_maps(schema)
    assert found == [], (
        f"{name} contains a free-form map at {found}; provider-side structured output "
        "rejects the whole schema when any object allows undeclared keys"
    )


@pytest.mark.parametrize("name", sorted(_STILL_OPEN))
def test_an_allowlisted_model_really_is_still_open(name):
    """Teeth on the allowlist: once a model is closed, its exemption must be deleted or it
    silently permits a future regression."""
    schema = PROPERTY_MODELS[name].model_json_schema()
    assert _open_maps(schema) != [], (
        f"{name} is allowlisted as still-open but is now closed — remove it from _STILL_OPEN"
    )


def test_table_rows_are_positional_cells():
    """Row keys are chosen at runtime from `columns`, so they cannot be declared in a schema.
    Positional cells say the same thing in a closed shape."""
    from src.ui.component_properties import TableProperties

    table = TableProperties(
        columns=[{"key": "name", "label": "Company"}, {"key": "raised", "label": "Funding"}],
        rows=[{"cells": ["Acme", "$10M"]}],
    )
    assert table.rows[0].cells == ["Acme", "$10M"]
    assert table.columns[0].key == "name"


def test_table_rejects_a_row_whose_width_does_not_match_the_columns():
    """A row with the wrong number of cells renders as blank columns silently."""
    from pydantic import ValidationError

    from src.ui.component_properties import TableProperties

    with pytest.raises(ValidationError):
        TableProperties(
            columns=[{"key": "name", "label": "Company"}],
            rows=[{"cells": ["Acme", "$10M"]}],
        )
