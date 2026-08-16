"""Capability enforcement at the adapter boundary.

The platform JWT carries an explicit ``capabilities`` list. The allowlist
(``ensure_action_allowed``) only proves an action is a known Gmail action —
it does NOT prove the *caller* was authorized for it. Without a capability
check, a read-scoped token (``capabilities=["email.search"]``) could invoke
``gmail.send`` because ``gmail.send`` is in the shared action allowlist.

These tests pin the second gate: each action maps to a required Jarvis
capability, and a call is rejected (fail-closed) unless the principal was
granted that capability.
"""

import pytest

from src.adapter.enforcement import (
    ACTION_REQUIRED_CAPABILITY,
    GMAIL_ACTION_ALLOWLIST,
    CapabilityDenied,
    ensure_capability_allowed,
)


def test_send_denied_for_read_only_token():
    """A token scoped to email.search cannot invoke gmail.send."""
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("gmail.send", ("email.search",))


def test_send_allowed_when_send_capability_granted():
    """A token carrying email.send may invoke gmail.send."""
    ensure_capability_allowed("gmail.send", ("email.search", "email.send"))


def test_search_allowed_for_search_capability():
    """A token carrying email.search may invoke gmail.search."""
    ensure_capability_allowed("gmail.search", ("email.search",))


def test_unmapped_action_denied_fail_closed():
    """An action with no capability mapping is denied even with broad scope."""
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("gmail.unknown", ("email.search", "email.send"))


def test_empty_capabilities_denies_everything():
    """A token with no capabilities cannot invoke any action."""
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("gmail.search", ())


def test_every_allowlisted_action_has_a_required_capability():
    """Guard: no allowlisted action may bypass capability enforcement.

    If a future action is added to GMAIL_ACTION_ALLOWLIST without a matching
    ACTION_REQUIRED_CAPABILITY entry, ensure_capability_allowed would have no
    capability to check — this test turns that silent gap into a failure.
    """
    for action in GMAIL_ACTION_ALLOWLIST:
        assert action in ACTION_REQUIRED_CAPABILITY, (
            f"Allowlisted action {action!r} has no required-capability mapping"
        )
