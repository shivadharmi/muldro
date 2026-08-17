"""Google Calendar gateway actions, checked against the OpenConnector ground truth.

Scope: this file owns Calendar's curated action set, its reviewed policy table,
and its schemas. Registry-wide invariants that hold for EVERY provider --
capability-catalog membership, legal agent tool names, action-id uniqueness,
capability disjointness, seeded-installation binding -- are asserted once in
test_gateway_registry.py and deliberately not repeated here.
"""

from src.integrations.gateway_actions import GatewayAction, GatewayProvider
from src.integrations.gateway_actions.googlecalendar import (
    GOOGLECALENDAR,
    GOOGLECALENDAR_ACTIONS,
)
from tests.gateway_ground_truth import input_schema_for

EXPECTED = {
    "googlecalendar.list_calendars": ("calendar.list", "low", False),
    "googlecalendar.list_events": ("calendar.list", "low", False),
    "googlecalendar.get_event": ("calendar.get", "low", False),
    "googlecalendar.free_busy_query": ("calendar.get", "low", False),
    "googlecalendar.create_event": ("calendar.create", "medium", True),
    "googlecalendar.update_event": ("calendar.update", "medium", True),
}

# Derived from the policy table above rather than restated, so the write set can
# never drift from the approval column it is meant to describe.
WRITE_CAPABILITIES = {cap for cap, _risk, approval in EXPECTED.values() if approval}

BY_ID = {a.action_id: a for a in GOOGLECALENDAR_ACTIONS}


def test_googlecalendar_provider_is_bound_to_the_google_workspace_installation():
    assert isinstance(GOOGLECALENDAR, GatewayProvider)
    assert GOOGLECALENDAR.provider_id == "googlecalendar"
    assert GOOGLECALENDAR.server_name == "google-workspace"
    assert GOOGLECALENDAR.actions == GOOGLECALENDAR_ACTIONS


def test_googlecalendar_the_curated_six_are_declared():
    assert set(BY_ID) == set(EXPECTED)


def test_googlecalendar_capability_risk_approval_match_the_reviewed_policy():
    for action_id, (cap, risk, approval) in EXPECTED.items():
        a = BY_ID[action_id]
        assert (a.capability, a.risk, a.requires_approval) == (cap, risk, approval)


def test_googlecalendar_writes_require_approval_and_reads_do_not():
    """Approval is decided per CAPABILITY: actions sharing one must not disagree."""
    for a in GOOGLECALENDAR_ACTIONS:
        assert a.requires_approval is (a.capability in WRITE_CAPABILITIES)


def test_googlecalendar_schemas_are_verbatim_openconnector_ground_truth():
    for a in GOOGLECALENDAR_ACTIONS:
        assert a.input_schema == input_schema_for(a.action_id), (
            f"{a.action_id} schema diverges from the OpenConnector catalog"
        )


def test_googlecalendar_schemas_are_object_typed_and_not_opaque():
    for a in GOOGLECALENDAR_ACTIONS:
        assert isinstance(a, GatewayAction)
        assert a.input_schema.get("type") == "object"
        assert a.input_schema != {"type": "object", "additionalProperties": True}


def test_googlecalendar_event_payload_is_shared_but_not_aliased():
    """create/update share one declared event shape; each must own a deep copy.

    A shallow copy would let an edit to one action's nested event schema silently
    mutate the other's -- the failure mode the _EVENT_PROPERTIES dedup invites.
    """
    create = BY_ID["googlecalendar.create_event"].input_schema["properties"]["event"]
    update = BY_ID["googlecalendar.update_event"].input_schema["properties"]["event"]

    assert create["properties"] == update["properties"]
    assert create["properties"] is not update["properties"]
    assert create["properties"]["start"] is not update["properties"]["start"]
    # They differ only in the wrapper's description and required list.
    assert create["required"] == ["start", "end"]
    assert "required" not in update
