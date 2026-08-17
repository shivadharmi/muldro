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


def test_table_has_the_curated_seven():
    assert set(BY_ID) == set(EXPECTED)


def test_capability_risk_approval_match():
    for aid, (cap, risk, appr) in EXPECTED.items():
        a = BY_ID[aid]
        assert (a.capability, a.risk, a.requires_approval) == (cap, risk, appr)


def test_every_action_has_an_object_schema():
    for a in GMAIL_ACTIONS:
        assert isinstance(a, GatewayAction)
        assert isinstance(a.input_schema, dict) and a.input_schema.get("type") == "object"


def test_send_email_cc_is_string_or_array():
    cc = BY_ID["gmail.send_email"].input_schema["properties"]["cc"]
    assert cc["anyOf"] == [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]


def test_search_threads_requires_query():
    assert BY_ID["gmail.search_threads"].input_schema.get("required") == ["query"]


def test_schemas_are_verbatim_openconnector_ground_truth():
    for a in GMAIL_ACTIONS:
        assert a.input_schema == input_schema_for(a.action_id), (
            f"{a.action_id} schema diverges from the OpenConnector catalog"
        )
