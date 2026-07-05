"""Deterministic, evidence-derived confidence for world-model beliefs (spec §4.6
item 4). confidence = source-reliability × corroboration, age-decayed. NEVER
LLM-self-reported — reliability is a fixed per-origin lookup, corroboration is a
count, decay is exponential. Pure: no DB, no network, no LLM."""

import math

# Per-origin source reliability. Higher = more trustworthy provenance. A missing
# origin falls back to "unknown". These are deterministic weights, NOT LLM output.
SOURCE_RELIABILITY: dict[str, float] = {
    "user_message": 0.95,  # the user stated it directly
    "tool_output": 0.90,  # a tool/connector returned it
    "connector": 0.90,
    "perception": 0.70,  # observed from a monitored source
    "retrieved_memory": 0.60,  # recalled from prior stored belief
    "llm_inference": 0.50,  # inferred by the model (lowest — not self-reported confidence)
    "unknown": 0.50,
}

# 30-day half-life age decay (mirrors the Step-2 recency half-life).
_CONFIDENCE_HALF_LIFE_DAYS = 30.0
_DECAY_LAMBDA = math.log(2) / _CONFIDENCE_HALF_LIFE_DAYS


def reliability_for(origin: str) -> float:
    """Deterministic per-origin reliability weight (never LLM-reported)."""
    return SOURCE_RELIABILITY.get(origin, SOURCE_RELIABILITY["unknown"])


def age_factor(age_days: float) -> float:
    """Exponential age decay; negative/zero age → 1.0 (fresh)."""
    return math.exp(-_DECAY_LAMBDA * max(0.0, age_days))


def compute_confidence(*, origin: str, corroboration_count: int, age_days: float) -> float:
    """Full confidence = noisy-OR of `corroboration_count` independent observations
    of a source of `reliability_for(origin)`, times the age-decay factor, clamped.

    base = 1 - (1 - r)^n  (n independent corroborating observations)
    confidence = base * exp(-lambda * age_days)
    """
    r = reliability_for(origin)
    n = max(1, corroboration_count)
    base = 1.0 - (1.0 - r) ** n
    return _clamp(base * age_factor(age_days))


def current_confidence(base: float, *, age_days: float) -> float:
    """Apply live age decay to a STORED age-0 base (the value persisted on
    entity_facts.confidence). Used at read/render time so a fact's shown confidence
    decays over time without a DB rewrite."""
    return _clamp(base * age_factor(age_days))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))
