"""validate_identity_coverage_strict: every IRREVERSIBLE write capability must have a
semantic IdentitySpec OR be explicitly positional-accepted. Mirrors validate_registry
(list[str], never raises). Wired as a startup hard-gate."""

from src.services.idempotency.identity import (
    IDENTITY_SPECS,
    POSITIONAL_KEY_ACCEPTED,
    validate_identity_coverage_strict,
)
from src.services.verification.predicate import is_irreversible_capability, write_capabilities


def test_real_catalog_identity_coverage_is_complete():
    irreversible = {c for c in write_capabilities() if is_irreversible_capability(c)}
    errors = validate_identity_coverage_strict(irreversible)
    assert errors == [], f"irreversible caps with no identity strategy: {errors}"


def test_missing_irreversible_cap_is_flagged():
    errors = validate_identity_coverage_strict({"brand.new_irreversible_write"})
    assert any("brand.new_irreversible_write" in e for e in errors)


def test_spec_covered_cap_passes():
    assert "email.send" in IDENTITY_SPECS
    assert validate_identity_coverage_strict({"email.send"}) == []


def test_positional_accepted_cap_passes():
    # e.g. messaging.send has no semantic spec but is explicitly positional-accepted
    assert "messaging.send" in POSITIONAL_KEY_ACCEPTED
    assert validate_identity_coverage_strict({"messaging.send"}) == []
