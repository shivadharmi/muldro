"""Google Calendar gateway actions, checked against the OpenConnector ground truth."""

import json
from pathlib import Path

import pytest

from src.integrations.capabilities import CAPABILITY_CATALOG
from src.integrations.gateway_actions import GatewayAction, GatewayProvider
from src.integrations.gateway_actions.googlecalendar import (
    GOOGLECALENDAR,
    GOOGLECALENDAR_ACTIONS,
)
from src.integrations.gateway_naming import action_id_to_tool_name

_GROUND_TRUTH = Path(
    "/private/tmp/claude-501/-Users-sivasankarreddybogala-work-jarvis/"
    "bc22864c-2a5d-49fe-866f-14b01750659e/scratchpad/spike/curated-schemas.json"
)

EXPECTED = {
    "googlecalendar.list_calendars": ("calendar.list", "low", False),
    "googlecalendar.list_events": ("calendar.list", "low", False),
    "googlecalendar.get_event": ("calendar.get", "low", False),
    "googlecalendar.free_busy_query": ("calendar.get", "low", False),
    "googlecalendar.create_event": ("calendar.create", "medium", True),
    "googlecalendar.update_event": ("calendar.update", "medium", True),
}

BY_ID = {a.action_id: a for a in GOOGLECALENDAR_ACTIONS}


def test_provider_is_bound_to_the_google_workspace_installation():
    assert isinstance(GOOGLECALENDAR, GatewayProvider)
    assert GOOGLECALENDAR.provider_id == "googlecalendar"
    assert GOOGLECALENDAR.server_name == "google-workspace"
    assert GOOGLECALENDAR.actions == GOOGLECALENDAR_ACTIONS


def test_the_curated_six_are_declared():
    assert set(BY_ID) == set(EXPECTED)


def test_capability_risk_approval_match_the_reviewed_policy():
    for action_id, (cap, risk, approval) in EXPECTED.items():
        a = BY_ID[action_id]
        assert (a.capability, a.risk, a.requires_approval) == (cap, risk, approval)


def test_every_capability_exists_in_the_catalog():
    for a in GOOGLECALENDAR_ACTIONS:
        assert a.capability in CAPABILITY_CATALOG


def test_every_action_maps_to_a_legal_agent_tool_name():
    for a in GOOGLECALENDAR_ACTIONS:
        action_id_to_tool_name(a.action_id)  # raises ValueError if illegal


def test_writes_require_approval_and_reads_do_not():
    for a in GOOGLECALENDAR_ACTIONS:
        is_write = a.capability in {"calendar.create", "calendar.update"}
        assert a.requires_approval is is_write


@pytest.mark.skipif(not _GROUND_TRUTH.exists(), reason="spike capture not present")
def test_schemas_are_verbatim_openconnector_ground_truth():
    truth = json.loads(_GROUND_TRUTH.read_text())
    for a in GOOGLECALENDAR_ACTIONS:
        assert a.input_schema == truth[a.action_id]["inputSchema"], (
            f"{a.action_id} schema diverges from the OpenConnector catalog"
        )


def test_schemas_are_object_typed_and_not_opaque():
    for a in GOOGLECALENDAR_ACTIONS:
        assert isinstance(a, GatewayAction)
        assert a.input_schema.get("type") == "object"
        assert a.input_schema != {"type": "object", "additionalProperties": True}
