"""Evidence-derived confidence (spec §4.6 item 4): source-reliability × corroboration,
age-decayed. Deterministic — NEVER LLM-self-reported. Pure, no DB."""

from src.services.entity_facts.confidence import (
    SOURCE_RELIABILITY,
    age_factor,
    compute_confidence,
    current_confidence,
    reliability_for,
)


def test_reliability_lookup_known_and_unknown():
    assert reliability_for("user_message") == 0.95
    assert reliability_for("perception") == 0.7
    # unknown origin falls back to the explicit "unknown" reliability, not a crash
    assert reliability_for("banana") == SOURCE_RELIABILITY["unknown"]


def test_single_observation_equals_reliability_at_age_zero():
    # base = 1 - (1 - r)^1 = r ; age_factor(0) = 1
    assert (
        abs(compute_confidence(origin="user_message", corroboration_count=1, age_days=0.0) - 0.95)
        < 1e-9
    )
    assert (
        abs(compute_confidence(origin="perception", corroboration_count=1, age_days=0.0) - 0.7)
        < 1e-9
    )


def test_corroboration_raises_via_noisy_or():
    # two independent perception observations: 1 - (1 - 0.7)^2 = 0.91
    c = compute_confidence(origin="perception", corroboration_count=2, age_days=0.0)
    assert abs(c - 0.91) < 1e-9
    # more corroboration only ever increases confidence
    c3 = compute_confidence(origin="perception", corroboration_count=3, age_days=0.0)
    assert c3 > c


def test_age_decays_confidence_with_30_day_half_life():
    # after one half-life the age factor is 0.5
    assert abs(age_factor(30.0) - 0.5) < 1e-9
    fresh = compute_confidence(origin="user_message", corroboration_count=1, age_days=0.0)
    aged = compute_confidence(origin="user_message", corroboration_count=1, age_days=30.0)
    assert abs(aged - fresh * 0.5) < 1e-9


def test_confidence_is_clamped_to_unit_interval():
    assert (
        0.0
        <= compute_confidence(origin="llm_inference", corroboration_count=1, age_days=9999)
        <= 1.0
    )
    assert compute_confidence(origin="user_message", corroboration_count=100, age_days=0.0) <= 1.0


def test_current_confidence_applies_age_to_stored_base():
    # current_confidence takes a stored age-0 base + an age and decays it live
    base = 0.8
    assert abs(current_confidence(base, age_days=0.0) - 0.8) < 1e-9
    assert abs(current_confidence(base, age_days=30.0) - 0.4) < 1e-9


def test_negative_age_is_treated_as_fresh():
    assert age_factor(-5.0) == 1.0
