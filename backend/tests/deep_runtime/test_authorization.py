"""Step 6B: authorization_source phase-1 — direct_user_request is ungated; the others are gated."""

from src.deep_runtime.authorization import AuthorizationSource, is_gated_source


def test_direct_user_request_is_not_gated():
    assert is_gated_source(AuthorizationSource.DIRECT_USER_REQUEST) is False


def test_autonomous_headless_custom_are_gated():
    sources = (
        AuthorizationSource.AUTONOMOUS,
        AuthorizationSource.HEADLESS,
        AuthorizationSource.CUSTOM,
    )
    for src in sources:
        assert is_gated_source(src) is True


def test_unknown_source_is_gated_fail_closed():
    assert is_gated_source("something_new") is True
