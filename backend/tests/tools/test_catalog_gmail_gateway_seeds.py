from src.tools.catalog import EXTERNAL_TOOL_SEEDS

BY_NAME = {s.name: s for s in EXTERNAL_TOOL_SEEDS}

EXPECTED = {
    "gmail.get_profile": ("email.read", "low", False),
    "gmail.fetch_emails": ("email.search", "low", False),
    "gmail.search_threads": ("email.search", "low", False),
    "gmail.get_message": ("email.read", "low", False),
    "gmail.list_threads": ("email.list", "low", False),
    "gmail.list_labels": ("email.list", "low", False),
    "gmail.send_email": ("email.send", "high", True),
}


def test_gmail_gateway_seeds_present_with_capabilities():
    for name, (cap, risk, approval) in EXPECTED.items():
        s = BY_NAME[name]
        assert (s.capability, s.risk_level, s.requires_approval) == (cap, risk, approval)
        assert s.server == "google-workspace"


def test_native_gmail_seeds_are_retained():
    assert "search_gmail_messages" in BY_NAME
    assert "send_gmail_message" in BY_NAME
