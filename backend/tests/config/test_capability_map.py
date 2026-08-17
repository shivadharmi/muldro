from src.config.capability_map import build_model_kwargs
from src.config.model_catalog import get_model_spec


def test_adaptive_opus_thinking_on():
    spec = get_model_spec("anthropic", "claude-opus-4-8")
    kw = build_model_kwargs(
        spec, effort="high", max_tokens=8192, temperature=0.3, thinking_enabled=True
    )
    assert kw["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kw["effort"] == "high"
    assert "temperature" not in kw  # adaptive rejects temperature


def test_adaptive_opus_thinking_off():
    spec = get_model_spec("anthropic", "claude-opus-4-8")
    kw = build_model_kwargs(
        spec, effort="high", max_tokens=8192, temperature=0.3, thinking_enabled=False
    )
    assert "thinking" not in kw and "temperature" not in kw and "effort" not in kw


def test_legacy_sonnet_thinking_on_clamps_budget_and_sets_temp1():
    spec = get_model_spec("anthropic", "claude-sonnet-4-6")
    kw = build_model_kwargs(
        spec, effort="medium", max_tokens=4096, temperature=0.3, thinking_enabled=True
    )
    assert kw["thinking"]["type"] == "enabled"
    assert kw["thinking"]["budget_tokens"] < 4096  # clamped below max_tokens
    assert kw["temperature"] == 1


def test_legacy_sonnet_thinking_off_passes_temperature():
    spec = get_model_spec("anthropic", "claude-sonnet-4-6")
    kw = build_model_kwargs(
        spec, effort="none", max_tokens=4096, temperature=0.3, thinking_enabled=False
    )
    assert kw["temperature"] == 0.3 and "thinking" not in kw


def test_openai_effort():
    spec = get_model_spec("openai", "gpt-5")
    kw = build_model_kwargs(
        spec, effort="high", max_tokens=4096, temperature=0.3, thinking_enabled=True
    )
    assert kw["reasoning_effort"] == "high"


def test_temperature_dropped_when_not_accepted():
    spec = get_model_spec("anthropic", "claude-opus-4-8")  # accepts_temperature False
    kw = build_model_kwargs(
        spec, effort="none", max_tokens=4096, temperature=0.3, thinking_enabled=False
    )
    assert "temperature" not in kw
