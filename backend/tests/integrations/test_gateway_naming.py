import pytest

from src.integrations.gateway_naming import action_id_to_tool_name


def test_maps_dot_to_underscore():
    assert action_id_to_tool_name("gmail.get_profile") == "gmail_get_profile"
    assert action_id_to_tool_name("gmail.fetch_emails") == "gmail_fetch_emails"


def test_result_is_provider_legal():
    import re

    for a in ("gmail.get_profile", "googlecalendar.list_events", "gmail.send_email"):
        assert re.match(r"^[A-Za-z0-9_-]{1,64}$", action_id_to_tool_name(a))


def test_rejects_illegal_actionid():
    with pytest.raises(ValueError):
        action_id_to_tool_name("gmail.get profile")  # space -> illegal
    with pytest.raises(ValueError):
        action_id_to_tool_name("x" * 70)  # exceeds 64
