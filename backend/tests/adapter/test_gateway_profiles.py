"""Provider profiles — the adapter's allowlist/capability-map/provider are
selected by a single code-defined profile, not hardcoded to Gmail.

The allowlist stays CODE-defined (never env-injected). Every action resolves
its OWN profile from its OWN action_id (fail-closed on an unknown action), so
one adapter can serve several providers at once.
"""

from types import MappingProxyType

import pytest

from src.adapter.enforcement import (
    ActionNotAllowed,
    CapabilityDenied,
    GatewayProfile,
    ensure_action_allowed,
    ensure_capability_allowed,
    get_gateway_profile,
)
from src.integrations.gateway_actions import GatewayAction

GMAIL_PROFILE = get_gateway_profile("gmail")


def test_gmail_profile_is_registered():
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


def test_enforcement_reads_the_profile_it_is_handed():
    """Enforcement reads the profile it is handed, not a hardcoded Gmail policy."""
    other = GatewayProfile(
        provider_id="other",
        display_name="Other",
        actions=(
            GatewayAction(
                "other.read_thing",
                "other.read",
                "low",
                False,
                {"type": "object"},
            ),
        ),
    )
    # A gmail action is NOT allowed under a different provider's profile.
    with pytest.raises(ActionNotAllowed):
        ensure_action_allowed("gmail.send_email", other)
    # That profile's own action is allowed, and needs that profile's capability.
    ensure_action_allowed("other.read_thing", other)
    ensure_capability_allowed("other.read_thing", ("other.read",), other)
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("other.read_thing", ("email.search",), other)


def test_gateway_profile_is_frozen():
    assert isinstance(GMAIL_PROFILE, GatewayProfile)
    with pytest.raises((AttributeError, TypeError)):
        GMAIL_PROFILE.provider_id = "x"


def test_profile_capability_map_is_immutable():
    """What actually makes the profile's policy state unmutatable after construction.

    ``action_required_capability`` is now a ``cached_property``, so a caller holds
    the SAME object the profile does — mutating the returned proxy would be a real
    mutation, not a throwaway one. Immutability therefore rests on two things,
    both asserted here: the mapping is handed out as a read-only
    ``MappingProxyType``, and the state it is derived from (``profile.actions``)
    is a tuple of frozen ``GatewayAction`` dataclasses.
    """
    capability_map = GMAIL_PROFILE.action_required_capability
    assert isinstance(capability_map, MappingProxyType)
    with pytest.raises(TypeError):
        capability_map["gmail.send_email"] = "email.read"

    assert isinstance(GMAIL_PROFILE.actions, tuple)
    for action in GMAIL_PROFILE.actions:
        assert isinstance(action, GatewayAction)
        with pytest.raises((AttributeError, TypeError)):
            action.capability = "email.read"


def test_derived_views_are_cached_not_rebuilt_per_access():
    """They sit on the per-request enforcement path; a plain @property reallocated."""
    assert GMAIL_PROFILE.action_allowlist is GMAIL_PROFILE.action_allowlist
    assert GMAIL_PROFILE.action_required_capability is GMAIL_PROFILE.action_required_capability
