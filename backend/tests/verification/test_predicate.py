"""The shared IRREVERSIBLE predicate + deterministic per-capability classifier
(spec §4.3). Pure — no DB, no network. The classifier defaults fail-closed:
an unlisted write capability is IRREVERSIBLE."""

from types import SimpleNamespace

from src.services.verification.predicate import (
    IRREVERSIBLE,
    REVERSIBLE_INTERNAL_CAPABILITIES,
    is_irreversible_capability,
    is_write_verification_required,
    write_capabilities,
)


def test_predicate_reversible_false_is_irreversible():
    assert IRREVERSIBLE(reversible=False, blast_radius="self") is True


def test_predicate_external_blast_radius_is_irreversible():
    for br in ("external_single", "external_multiple", "public"):
        assert IRREVERSIBLE(reversible=True, blast_radius=br) is True


def test_predicate_reversible_internal_is_not_irreversible():
    assert IRREVERSIBLE(reversible=True, blast_radius="self") is False
    assert IRREVERSIBLE(reversible=True, blast_radius="internal") is False


def test_read_only_capability_is_never_irreversible():
    # reads don't get read-back verification
    assert is_irreversible_capability("email.read") is False
    assert is_irreversible_capability("calendar.list") is False


def test_external_write_defaults_to_irreversible():
    assert is_irreversible_capability("email.send") is True
    assert is_irreversible_capability("calendar.create") is True
    assert is_irreversible_capability("repo.create_pr") is True


def test_unknown_write_capability_defaults_fail_closed_irreversible():
    # a brand-new write capability not in the catalog is treated as irreversible
    assert is_irreversible_capability("brand.new_write") is True


def test_reversible_internal_exceptions_are_not_irreversible():
    for cap in ("internal.store_memory", "email.draft", "messaging.mark_read"):
        assert cap in REVERSIBLE_INTERNAL_CAPABILITIES
        assert is_irreversible_capability(cap) is False


def test_verification_required_is_fail_closed_union():
    # registry says irreversible even though the per-step risk says reversible
    risk_says_safe = SimpleNamespace(reversible=True, blast_radius="self")
    assert is_write_verification_required("email.send", risk_says_safe) is True
    # registry says reversible-internal, risk also safe -> not required
    assert is_write_verification_required("internal.store_memory", risk_says_safe) is False
    # registry reversible-internal but per-step risk flags external -> required (union)
    risk_says_danger = SimpleNamespace(reversible=True, blast_radius="external_single")
    assert is_write_verification_required("internal.store_memory", risk_says_danger) is True


def test_write_capabilities_excludes_reads():
    caps = write_capabilities()
    assert "email.send" in caps
    assert "email.read" not in caps
    assert "system.discovery" not in caps  # read-only
