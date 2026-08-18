"""Capability enforcement at the adapter boundary.

The platform JWT carries an explicit ``capabilities`` list. The allowlist
(``ensure_action_allowed``) only proves an action is a known Gmail action —
it does NOT prove the *caller* was authorized for it. Without a capability
check, a read-scoped token (``capabilities=["email.search"]``) could invoke
``gmail.send_email`` because ``gmail.send_email`` is in the shared action allowlist.

These tests pin the second gate: each action maps to a required Muldro
capability, and a call is rejected (fail-closed) unless the principal was
granted that capability.
"""

import pytest

from src.adapter.enforcement import (
    CapabilityDenied,
    ensure_capability_allowed,
    get_gateway_profile,
)

GMAIL_PROFILE = get_gateway_profile("gmail")
GMAIL_ACTION_ALLOWLIST = GMAIL_PROFILE.action_allowlist
ACTION_REQUIRED_CAPABILITY = GMAIL_PROFILE.action_required_capability


def test_send_denied_for_read_only_token():
    """A token scoped to email.search cannot invoke gmail.send_email."""
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("gmail.send_email", ("email.search",), GMAIL_PROFILE)


def test_send_allowed_when_send_capability_granted():
    """A token carrying email.send may invoke gmail.send_email."""
    ensure_capability_allowed("gmail.send_email", ("email.search", "email.send"), GMAIL_PROFILE)


def test_search_allowed_for_search_capability():
    """A token carrying email.search may invoke gmail.fetch_emails."""
    ensure_capability_allowed("gmail.fetch_emails", ("email.search",), GMAIL_PROFILE)


def test_unmapped_action_denied_fail_closed():
    """An action with no capability mapping is denied even with broad scope."""
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("gmail.unknown", ("email.search", "email.send"), GMAIL_PROFILE)


def test_empty_capabilities_denies_everything():
    """A token with no capabilities cannot invoke any action."""
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("gmail.fetch_emails", (), GMAIL_PROFILE)


def test_every_allowlisted_action_has_a_required_capability():
    """Guard: no allowlisted action may bypass capability enforcement.

    If a future action is added to the Gmail profile's allowlist without a
    matching required-capability entry, ensure_capability_allowed would have no
    capability to check — this test turns that silent gap into a failure.
    """
    for action in GMAIL_ACTION_ALLOWLIST:
        assert action in ACTION_REQUIRED_CAPABILITY, (
            f"Allowlisted action {action!r} has no required-capability mapping"
        )
