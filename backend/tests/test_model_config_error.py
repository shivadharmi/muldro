"""B7: a config failure must name the binding, not surface a bare RuntimeError."""

import pytest

from src.services.model_resolver import ModelConfigError


def test_error_carries_binding_identity():
    err = ModelConfigError(
        "provider openai is not configured",
        scope_type="tier",
        scope_key="reasoning",
        provider="openai",
        remediation="Connect openai in Settings › Providers.",
    )
    assert err.scope_type == "tier"
    assert err.scope_key == "reasoning"
    assert err.provider == "openai"
    assert "Settings" in err.remediation


def test_error_fields_default_to_none():
    err = ModelConfigError("no binding")
    assert err.scope_type is None
    assert err.scope_key is None
    assert err.provider is None
    assert err.remediation is None


def test_error_is_still_a_runtime_error():
    """Existing `except RuntimeError` handlers must keep working."""
    with pytest.raises(RuntimeError):
        raise ModelConfigError("boom")
