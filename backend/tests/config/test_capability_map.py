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


def test_gemini_effort_maps_to_thinking_budget():
    """Gemini exposes an effort selector, so effort must map to a real thinking_budget
    (N4). Each level yields a distinct, ascending budget instead of being discarded."""
    spec = get_model_spec("google_genai", "gemini-2.5-pro")
    budgets = {}
    for effort in ("low", "medium", "high"):
        kw = build_model_kwargs(
            spec, effort=effort, max_tokens=4096, temperature=None, thinking_enabled=True
        )
        assert "thinking_budget" in kw, f"{effort} produced no thinking_budget"
        budgets[effort] = kw["thinking_budget"]
    # Distinct and ascending — proves effort is honored, not discarded.
    assert budgets["low"] < budgets["medium"] < budgets["high"]


def test_gemini_thinking_off_omits_budget():
    spec = get_model_spec("google_genai", "gemini-2.5-pro")
    kw = build_model_kwargs(
        spec, effort="none", max_tokens=4096, temperature=0.4, thinking_enabled=False
    )
    assert "thinking_budget" not in kw
    assert kw["temperature"] == 0.4  # gemini accepts temperature


def test_legacy_thinking_never_emits_a_budget_below_the_api_minimum():
    """Anthropic legacy thinking requires ``budget_tokens >= 1024`` AND
    ``budget_tokens < max_tokens``. Both cannot hold when ``max_tokens <= 1024``, so
    for those sizes thinking must be dropped entirely rather than clamped to an
    unusable value — the old ``budget = max_tokens - 1`` clamp produced a fatal 400
    for every caller sizing a small completion (e.g. a 256-token classification).
    """
    spec = get_model_spec("anthropic", "claude-sonnet-4-6")
    for max_tokens in (1, 2, 256, 512, 1023, 1024):
        kw = build_model_kwargs(
            spec, effort="high", max_tokens=max_tokens, temperature=0.3, thinking_enabled=True
        )
        assert "thinking" not in kw, f"max_tokens={max_tokens} emitted an invalid thinking config"
        # Falling back to the no-thinking branch means temperature is the caller's
        # again — the forced temperature=1 only belongs with thinking on.
        assert kw["temperature"] == 0.3, f"max_tokens={max_tokens} kept the thinking-on temperature"


def test_legacy_thinking_budget_stays_at_or_above_the_minimum_when_it_fits():
    """Just above the boundary the budget must still be >= 1024, not max_tokens - 1."""
    spec = get_model_spec("anthropic", "claude-sonnet-4-6")
    kw = build_model_kwargs(
        spec, effort="high", max_tokens=1025, temperature=None, thinking_enabled=True
    )
    assert kw["thinking"]["budget_tokens"] >= 1024
    assert kw["thinking"]["budget_tokens"] < 1025
