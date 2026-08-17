"""Model capability map — the code-side source of truth for provider/model traits.

Replaces the hardcoded MODEL_TIERS / MODEL_TIER_IDS dicts and the Anthropic-only
branching in deep_runtime/_thinking.py. Choices (which model backs a tier, whose
key) are DB data; capability FACTS live here so they are versioned and testable.
"""

from __future__ import annotations

from dataclasses import dataclass

# thinking_style values:
#   "anthropic_adaptive" — Opus 4.7/4.8, Fable/Mythos 5: thinking={type:adaptive}+effort,
#                          no temperature
#   "anthropic_legacy"   — Sonnet/Haiku: thinking={type:enabled,budget_tokens}+temperature=1
#   "openai_effort"      — reasoning_effort=<level>
#   "gemini"             — provider thinking config
#   "none"               — plain completion


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model_id: str
    display_name: str
    thinking_style: str
    accepts_temperature: bool
    supports_prompt_cache: bool
    context_window: int
    input_cost_per_1k: float
    output_cost_per_1k: float
    suggested_tier: str  # "reasoning" | "balanced" | "fast"


MODEL_CATALOG: dict[str, list[ModelSpec]] = {
    "anthropic": [
        ModelSpec(
            "anthropic",
            "claude-opus-4-8",
            "Claude Opus 4.8",
            "anthropic_adaptive",
            False,
            True,
            200_000,
            0.015,
            0.075,
            "reasoning",
        ),
        ModelSpec(
            "anthropic",
            "claude-sonnet-4-6",
            "Claude Sonnet 4.6",
            "anthropic_legacy",
            True,
            True,
            200_000,
            0.003,
            0.015,
            "balanced",
        ),
        ModelSpec(
            "anthropic",
            "claude-haiku-4-5-20251001",
            "Claude Haiku 4.5",
            "anthropic_legacy",
            True,
            True,
            200_000,
            0.001,
            0.005,
            "fast",
        ),
    ],
    "openai": [
        ModelSpec(
            "openai",
            "gpt-5",
            "GPT-5",
            "openai_effort",
            True,
            False,
            400_000,
            0.01,
            0.03,
            "reasoning",
        ),
        ModelSpec(
            "openai",
            "gpt-5-mini",
            "GPT-5 mini",
            "openai_effort",
            True,
            False,
            400_000,
            0.001,
            0.004,
            "fast",
        ),
    ],
    "google_genai": [
        ModelSpec(
            "google_genai",
            "gemini-2.5-pro",
            "Gemini 2.5 Pro",
            "gemini",
            True,
            False,
            1_000_000,
            0.00125,
            0.005,
            "reasoning",
        ),
        ModelSpec(
            "google_genai",
            "gemini-2.5-flash",
            "Gemini 2.5 Flash",
            "gemini",
            True,
            False,
            1_000_000,
            0.0003,
            0.0012,
            "fast",
        ),
    ],
    "ollama": [
        ModelSpec(
            "ollama",
            "llama3.1",
            "Llama 3.1 (local)",
            "none",
            True,
            False,
            128_000,
            0.0,
            0.0,
            "balanced",
        ),
    ],
}


def get_model_spec(provider: str, model_id: str) -> ModelSpec | None:
    """Return the ModelSpec for (provider, model_id), or None if unknown."""
    for spec in MODEL_CATALOG.get(provider, []):
        if spec.model_id == model_id:
            return spec
    return None


def get_model_spec_by_id(model_id: str) -> ModelSpec | None:
    """Return the first ModelSpec across all providers whose model_id matches, or None.
    Provider-agnostic lookup for cost/pricing sites that only have a model id."""
    for specs in MODEL_CATALOG.values():
        for spec in specs:
            if spec.model_id == model_id:
                return spec
    return None


def default_model_id_for_tier(tier: str) -> str | None:
    """Return the default (Anthropic) model id whose suggested_tier == *tier*.

    A sync, catalog-sourced tier->id lookup for cost/harness label sites that need
    a model id without a DB round-trip. Behavior-preserving replacement for the
    deleted MODEL_TIERS / MODEL_TIER_IDS maps (reasoning->opus, balanced->sonnet,
    fast->haiku).
    """
    for spec in MODEL_CATALOG.get("anthropic", []):
        if spec.suggested_tier == tier:
            return spec.model_id
    return None
