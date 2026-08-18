"""Unit tests for Step-10A A5: GP-disable process-global scope audit + restore-not-pop
teardown (``src/deep_runtime/delegates.py``).

Offline, no API calls. Proves two things:

1. ``disable_general_purpose_subagent`` is key-scoped (disabling one model id never
   touches another) and idempotent (a second call is a no-op, still disabled).
2. ``general_purpose_disabled`` (the new bounded-scope context-manager) RESTORES the
   prior harness profile on exit — restore-not-pop. This is the 7B2 lesson: a naive
   ``_HARNESS_PROFILES.pop(key)`` teardown would delete a pre-existing profile
   (including a deepagents built-in), poisoning every future lead on that model.

CRITICAL test-isolation note: ``_HARNESS_PROFILES`` is a SHARED process-global dict
used by every other suite test that builds a deep agent. Every test here uses FAKE
model names (``claude-a5-scopetest-*``) that no other test uses, AND cleans up its
own keys in a ``try/finally`` so nothing leaks into the wider suite.
"""

from __future__ import annotations

from deepagents import GeneralPurposeSubagentProfile, HarnessProfile
from deepagents.profiles.harness.harness_profiles import _HARNESS_PROFILES

from src.deep_runtime.delegates import disable_general_purpose_subagent

# NOTE: `general_purpose_disabled` is imported lazily inside the two tests that need
# it (rather than at module level) so that tests 1 & 2 — which only exercise the
# pre-existing `disable_general_purpose_subagent` — can run and pass independently
# even before the new context-manager exists.

_FAKE_X = "claude-a5-scopetest-x"
_FAKE_Y = "claude-a5-scopetest-y"
_KEY_X = f"anthropic:{_FAKE_X}"
_KEY_Y = f"anthropic:{_FAKE_Y}"


def test_disable_is_key_scoped():
    try:
        disable_general_purpose_subagent(_FAKE_X)

        profile_x = _HARNESS_PROFILES[_KEY_X]
        assert profile_x.general_purpose_subagent.enabled is False

        # disabling X must not have touched Y at all.
        assert _KEY_Y not in _HARNESS_PROFILES
    finally:
        _HARNESS_PROFILES.pop(_KEY_X, None)
        _HARNESS_PROFILES.pop(_KEY_Y, None)


def test_disable_is_idempotent():
    try:
        disable_general_purpose_subagent(_FAKE_X)
        disable_general_purpose_subagent(_FAKE_X)  # second call must not raise

        profile_x = _HARNESS_PROFILES[_KEY_X]
        assert profile_x.general_purpose_subagent.enabled is False
    finally:
        _HARNESS_PROFILES.pop(_KEY_X, None)
        _HARNESS_PROFILES.pop(_KEY_Y, None)


def test_context_manager_restores_a_preexisting_profile():
    """The restore-not-pop teeth: a pre-existing profile (e.g. a deepagents built-in)
    must survive the ``general_purpose_disabled`` block intact."""
    from src.deep_runtime.delegates import general_purpose_disabled

    sentinel = HarnessProfile(
        system_prompt_suffix="A5 sentinel suffix",
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=True),
    )
    try:
        _HARNESS_PROFILES[_KEY_X] = sentinel

        with general_purpose_disabled(_FAKE_X):
            # inside the block: GP is disabled.
            inside = _HARNESS_PROFILES[_KEY_X]
            assert inside.general_purpose_subagent.enabled is False

        # after the block: the ORIGINAL sentinel is back — GP enabled=True again,
        # and it is the SAME object (restore, not a re-derived merge).
        restored = _HARNESS_PROFILES[_KEY_X]
        assert restored is sentinel
        assert restored.general_purpose_subagent.enabled is True
        assert restored.system_prompt_suffix == "A5 sentinel suffix"
    finally:
        _HARNESS_PROFILES.pop(_KEY_X, None)


def test_context_manager_removes_a_key_it_added():
    """No prior profile for the key -> after the block, the key is gone entirely
    (we remove only what we added, not a wider swath of the registry)."""
    from src.deep_runtime.delegates import general_purpose_disabled

    assert _KEY_X not in _HARNESS_PROFILES
    try:
        with general_purpose_disabled(_FAKE_X):
            inside = _HARNESS_PROFILES[_KEY_X]
            assert inside.general_purpose_subagent.enabled is False

        assert _KEY_X not in _HARNESS_PROFILES
    finally:
        _HARNESS_PROFILES.pop(_KEY_X, None)  # defensive, in case of assertion failure above


def test_disable_key_is_provider_scoped():
    """The harness key is ``f"{provider}:{model}"`` — deepagents derives it from the
    BUILT model's provider + identifier. A hardcoded ``anthropic:`` prefix registers a
    key that a workspace-overridden openai/google_genai/ollama lead never looks up, so
    that lead silently keeps its general-purpose child."""
    openai_key = f"openai:{_FAKE_X}"
    try:
        disable_general_purpose_subagent(_FAKE_X, provider="openai")

        assert _HARNESS_PROFILES[openai_key].general_purpose_subagent.enabled is False
        # Same model id under a different provider is a different lead — untouched.
        assert _KEY_X not in _HARNESS_PROFILES
    finally:
        _HARNESS_PROFILES.pop(openai_key, None)
        _HARNESS_PROFILES.pop(_KEY_X, None)


def test_bounded_scope_restores_under_the_same_provider_key():
    """``general_purpose_disabled`` must build and restore the SAME provider-scoped key
    it disabled — a teardown that pops ``anthropic:<model>`` after disabling
    ``openai:<model>`` would leak the openai key into the wider suite."""
    from src.deep_runtime.delegates import general_purpose_disabled

    openai_key = f"openai:{_FAKE_Y}"
    try:
        with general_purpose_disabled(_FAKE_Y, provider="openai"):
            assert _HARNESS_PROFILES[openai_key].general_purpose_subagent.enabled is False
        assert openai_key not in _HARNESS_PROFILES  # nothing before -> removed on exit
    finally:
        _HARNESS_PROFILES.pop(openai_key, None)
