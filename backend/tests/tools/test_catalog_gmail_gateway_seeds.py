from src.integrations.gateway_actions.gmail import GMAIL_ACTIONS
from src.integrations.gateway_naming import action_id_to_tool_name
from src.tools.catalog import EXTERNAL_TOOL_SEEDS

BY = {s.name: s for s in EXTERNAL_TOOL_SEEDS}


def test_gmail_gateway_seeds_are_derived_agent_legal_names():
    for a in GMAIL_ACTIONS:
        name = action_id_to_tool_name(a.action_id)
        assert name in BY, name
        s = BY[name]
        assert (s.capability, s.server) == (a.capability, "google-workspace")
        assert (s.risk_level, s.requires_approval) == (a.risk, a.requires_approval)
        assert "." not in s.name


def test_no_dotted_gmail_seed_names_remain():
    assert not any(s.name.startswith("gmail.") for s in EXTERNAL_TOOL_SEEDS)


def test_native_gmail_seeds_retained():
    assert "search_gmail_messages" in BY and "send_gmail_message" in BY
