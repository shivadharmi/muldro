from src.config.model_catalog import ModelSpec, get_model_spec


def test_current_claude_models_present():
    for provider, model_id in [
        ("anthropic", "claude-opus-4-8"),
        ("anthropic", "claude-sonnet-4-6"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ]:
        spec = get_model_spec(provider, model_id)
        assert isinstance(spec, ModelSpec)
        assert spec.provider == provider


def test_opus_is_adaptive_thinking():
    spec = get_model_spec("anthropic", "claude-opus-4-8")
    assert spec.thinking_style == "anthropic_adaptive"
    assert spec.accepts_temperature is False


def test_sonnet_is_legacy_thinking():
    spec = get_model_spec("anthropic", "claude-sonnet-4-6")
    assert spec.thinking_style == "anthropic_legacy"


def test_unknown_model_returns_none():
    assert get_model_spec("anthropic", "no-such-model") is None
