"""Provider profiles — the adapter's allowlist/capability-map/provider are
selected by a single code-defined profile, not hardcoded to Gmail.

The allowlist stays CODE-defined (never env-injected): a setting only selects
WHICH reviewed profile is active. Gmail remains the default so the existing
adapter behavior is unchanged; a no-auth `hackernews` profile exists so the
automated integration harness can drive a real provider through the adapter.
"""

import pytest

from src.adapter.enforcement import (
    GMAIL_PROFILE,
    ActionNotAllowed,
    CapabilityDenied,
    GatewayProfile,
    ensure_action_allowed,
    ensure_capability_allowed,
    get_gateway_profile,
)


def test_gmail_profile_is_the_default_provider():
    assert GMAIL_PROFILE.provider_id == "gmail"
    assert "gmail.search" in GMAIL_PROFILE.action_allowlist
    assert get_gateway_profile("gmail") is GMAIL_PROFILE


def test_hackernews_profile_exists_and_is_no_auth_read():
    hn = get_gateway_profile("hackernews")
    assert hn.provider_id == "hackernews"
    assert "hackernews.get_ask_stories" in hn.action_allowlist
    # Every allowlisted action maps to a required capability (fail-closed guard).
    for action in hn.action_allowlist:
        assert action in hn.action_required_capability


def test_unknown_provider_is_denied_fail_closed():
    with pytest.raises(ValueError):
        get_gateway_profile("dropbox")


def test_enforcement_respects_the_selected_profile():
    hn = get_gateway_profile("hackernews")
    # A gmail action is NOT allowed under the hackernews profile.
    with pytest.raises(ActionNotAllowed):
        ensure_action_allowed("gmail.send", hn)
    # The hackernews action is allowed, and needs the hackernews capability.
    ensure_action_allowed("hackernews.get_ask_stories", hn)
    ensure_capability_allowed("hackernews.get_ask_stories", ("hackernews.read",), hn)
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("hackernews.get_ask_stories", ("email.search",), hn)


def test_enforcement_defaults_to_gmail_profile_when_unspecified():
    # Backward compatibility: existing call sites pass no profile -> Gmail.
    ensure_action_allowed("gmail.search")
    ensure_capability_allowed("gmail.search", ("email.search",))
    with pytest.raises(ActionNotAllowed):
        ensure_action_allowed("hackernews.get_ask_stories")


def test_gateway_profile_is_frozen():
    assert isinstance(GMAIL_PROFILE, GatewayProfile)
    with pytest.raises((AttributeError, TypeError)):
        GMAIL_PROFILE.provider_id = "x"
