"""Gmail gateway actions, checked against the OpenConnector ground truth.

Scope: this file owns Gmail's curated action set, its reviewed policy table,
and its schemas. Registry-wide invariants that hold for EVERY provider --
capability-catalog membership, legal agent tool names, action-id uniqueness,
capability disjointness, seeded-installation binding -- are asserted once in
test_gateway_registry.py and deliberately not repeated here.
"""

from src.integrations.gateway_actions import GatewayAction
from src.integrations.gateway_actions.gmail import GMAIL_ACTIONS
from tests.gateway_ground_truth import input_schema_for

BY_ID = {a.action_id: a for a in GMAIL_ACTIONS}

EXPECTED = {
    "gmail.get_profile": ("email.read", "low", False),
    "gmail.fetch_emails": ("email.search", "low", False),
    "gmail.search_threads": ("email.search", "low", False),
    "gmail.get_message": ("email.read", "low", False),
    "gmail.list_threads": ("email.list", "low", False),
    "gmail.list_labels": ("email.list", "low", False),
    "gmail.send_email": ("email.send", "high", True),
}

# Derived from the policy table above rather than restated, so the write set can
# never drift from the approval column it is meant to describe.
WRITE_CAPABILITIES = {cap for cap, _risk, approval in EXPECTED.values() if approval}


def test_gmail_table_has_the_curated_seven():
    assert set(BY_ID) == set(EXPECTED)


def test_gmail_capability_risk_approval_match():
    for aid, (cap, risk, appr) in EXPECTED.items():
        a = BY_ID[aid]
        assert (a.capability, a.risk, a.requires_approval) == (cap, risk, appr)


def test_gmail_writes_require_approval_and_reads_do_not():
    """Approval is decided per CAPABILITY: actions sharing one must not disagree."""
    for a in GMAIL_ACTIONS:
        assert a.requires_approval is (a.capability in WRITE_CAPABILITIES)


def test_gmail_every_action_has_an_object_schema():
    for a in GMAIL_ACTIONS:
        assert isinstance(a, GatewayAction)
        assert isinstance(a.input_schema, dict) and a.input_schema.get("type") == "object"


def test_gmail_send_email_cc_is_string_or_array():
    cc = BY_ID["gmail.send_email"].input_schema["properties"]["cc"]
    assert cc["anyOf"] == [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]


def test_gmail_search_threads_requires_query():
    assert BY_ID["gmail.search_threads"].input_schema.get("required") == ["query"]


def test_gmail_schemas_are_verbatim_openconnector_ground_truth():
    for a in GMAIL_ACTIONS:
        assert a.input_schema == input_schema_for(a.action_id), (
            f"{a.action_id} schema diverges from the OpenConnector catalog"
        )
