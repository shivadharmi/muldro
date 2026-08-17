"""Provider profiles — the adapter's allowlist/capability-map/provider are
selected by a single code-defined profile, not hardcoded to Gmail.

The allowlist stays CODE-defined (never env-injected): a setting only selects
WHICH reviewed profile is active. Gmail remains the default so the existing
adapter behavior is unchanged.
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
    assert "gmail.fetch_emails" in GMAIL_PROFILE.action_allowlist
    assert get_gateway_profile("gmail") is GMAIL_PROFILE


def test_every_allowlisted_action_maps_to_a_required_capability():
    """Fail-closed guard: an allowlisted action with no capability is a policy hole."""
    for action in GMAIL_PROFILE.action_allowlist:
        assert action in GMAIL_PROFILE.action_required_capability


def test_unknown_provider_is_denied_fail_closed():
    with pytest.raises(ValueError):
        get_gateway_profile("dropbox")


def test_enforcement_respects_the_passed_profile_not_the_gmail_default():
    """Enforcement reads the profile it is handed, not a hardcoded Gmail policy."""
    other = GatewayProfile(
        provider_id="other",
        action_allowlist=frozenset({"other.read_thing"}),
        action_required_capability={"other.read_thing": "other.read"},
    )
    # A gmail action is NOT allowed under a different provider's profile.
    with pytest.raises(ActionNotAllowed):
        ensure_action_allowed("gmail.send_email", other)
    # That profile's own action is allowed, and needs that profile's capability.
    ensure_action_allowed("other.read_thing", other)
    ensure_capability_allowed("other.read_thing", ("other.read",), other)
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("other.read_thing", ("email.search",), other)


def test_enforcement_defaults_to_gmail_profile_when_unspecified():
    # Backward compatibility: existing call sites pass no profile -> Gmail.
    ensure_action_allowed("gmail.fetch_emails")
    ensure_capability_allowed("gmail.fetch_emails", ("email.search",))
    with pytest.raises(ActionNotAllowed):
        ensure_action_allowed("dropbox.upload_file")


def test_gateway_profile_is_frozen():
    assert isinstance(GMAIL_PROFILE, GatewayProfile)
    with pytest.raises((AttributeError, TypeError)):
        GMAIL_PROFILE.provider_id = "x"


def test_incomplete_profile_is_rejected_at_construction():
    """An allowlisted action with no capability mapping must fail to construct."""
    with pytest.raises(ValueError):
        GatewayProfile(
            provider_id="broken",
            action_allowlist=frozenset({"broken.read"}),
            action_required_capability={},  # missing the mapping
        )


def test_profile_capability_map_is_immutable():
    """The capability map cannot be mutated after construction (defense-in-depth)."""
    with pytest.raises(TypeError):
        GMAIL_PROFILE.action_required_capability["gmail.send_email"] = "email.read"
