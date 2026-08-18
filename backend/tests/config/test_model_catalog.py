from src.config.model_catalog import ModelSpec, get_model_spec, get_model_spec_by_id


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


def test_default_model_id_for_tier():
    from src.config.model_catalog import default_model_id_for_tier

    assert default_model_id_for_tier("reasoning") == "claude-opus-4-8"
    assert default_model_id_for_tier("balanced") == "claude-sonnet-4-6"
    assert default_model_id_for_tier("fast") == "claude-haiku-4-5-20251001"
    assert default_model_id_for_tier("nope") is None


def test_catalog_costs_are_authoritative():
    """Pin per-1k costs to authoritative provider rates so a stale-pricing
    regression (e.g. reverting Opus to Opus-3 $15/$75) is caught (L5)."""
    expected = {
        ("anthropic", "claude-opus-4-8"): (0.005, 0.025),
        ("anthropic", "claude-sonnet-4-6"): (0.003, 0.015),
        ("anthropic", "claude-haiku-4-5-20251001"): (0.001, 0.005),
        ("openai", "gpt-5"): (0.00125, 0.010),
        ("openai", "gpt-5-mini"): (0.00025, 0.002),
        ("google_genai", "gemini-2.5-pro"): (0.00125, 0.010),
        ("google_genai", "gemini-2.5-flash"): (0.0003, 0.0025),
        ("ollama", "llama3.1"): (0.0, 0.0),
    }
    for (provider, model_id), (in_cost, out_cost) in expected.items():
        spec = get_model_spec(provider, model_id)
        assert spec is not None, f"{provider}/{model_id} missing from catalog"
        assert spec.input_cost_per_1k == in_cost, f"{model_id} input cost"
        assert spec.output_cost_per_1k == out_cost, f"{model_id} output cost"


def test_opus_no_longer_priced_at_stale_opus3_rates():
    spec = get_model_spec_by_id("claude-opus-4-8")
    assert spec is not None
    assert (spec.input_cost_per_1k, spec.output_cost_per_1k) != (0.015, 0.075)
