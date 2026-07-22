"""Post-condition registry + coverage gate. Every IRREVERSIBLE write capability must
be registered — either with a PostCondition (real read-back) or explicitly marked
UNVERIFIABLE. The coverage validator mirrors validate_registry (returns list[str],
never raises)."""

from src.services.verification.post_conditions import (
    POST_CONDITIONS,
    UNVERIFIABLE_CAPABILITIES,
    validate_post_condition_coverage,
)
from src.services.verification.predicate import is_irreversible_capability, write_capabilities


def test_real_catalog_has_full_coverage():
    # The live catalog must pass the coverage gate (startup would abort otherwise).
    errors = validate_post_condition_coverage(write_capabilities())
    assert errors == [], f"irreversible capabilities missing a post-condition: {errors}"


def test_missing_irreversible_capability_is_flagged():
    errors = validate_post_condition_coverage({"brand.new_irreversible_write"})
    assert any("brand.new_irreversible_write" in e for e in errors)


def test_reversible_internal_capability_needs_no_post_condition():
    # internal.store_memory is reversible-internal -> not irreversible -> not required
    assert is_irreversible_capability("internal.store_memory") is False
    errors = validate_post_condition_coverage({"internal.store_memory"})
    assert errors == []


def test_every_registered_capability_is_actually_a_write():
    # No read-only cap should carry a post-condition (would be dead config).
    for cap in list(POST_CONDITIONS) + list(UNVERIFIABLE_CAPABILITIES):
        assert is_irreversible_capability(cap), f"{cap} is not irreversible but is registered"


def test_registries_are_disjoint():
    # A capability is EITHER verifiable (POST_CONDITIONS) or explicitly UNVERIFIABLE.
    assert not (set(POST_CONDITIONS) & UNVERIFIABLE_CAPABILITIES)


def test_post_condition_has_read_capability_and_assertion():
    for cap, pc in POST_CONDITIONS.items():
        assert pc.read_capability, f"{cap} post-condition missing read_capability"
        assert callable(pc.assertion), f"{cap} post-condition assertion not callable"


def test_coverage_gate_is_exhaustive_over_real_catalog():
    # Belt-and-suspenders: no irreversible write capability in the real catalog is
    # left unregistered (the exact set the startup gate checks).
    registered = set(POST_CONDITIONS) | UNVERIFIABLE_CAPABILITIES
    missing = [
        c for c in write_capabilities() if is_irreversible_capability(c) and c not in registered
    ]
    assert missing == [], f"unregistered irreversible capabilities: {missing}"
