"""The semantic identity key must be stable across recomposed args, distinct per
logical write, and never a hash of the full (volatile) payload. Pure — no DB."""

from src.services.idempotency.identity import (
    IDENTITY_SPECS,
    IdentitySpec,
    derive_identity_key,
    validate_identity_coverage,
)

_RUN = "run_1"
_STEP = "step_1"


def test_recomposed_body_yields_the_same_key():
    """The failure the ledger exists to prevent: a regenerated body must NOT
    change the identity (else resume double-fires)."""
    a = {"to": "bob@acme.com", "subject": "Q3 sync", "body": "Hi Bob, first draft."}
    b = {"to": "bob@acme.com", "subject": "Q3 sync", "body": "Hello Bob — REWRITTEN on resume."}
    key_a = derive_identity_key("email.send", a, run_id=_RUN, step_id=_STEP, ordinal=0)
    key_b = derive_identity_key("email.send", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    assert key_a == key_b


def test_recipient_change_yields_a_different_key():
    """Over-normalization guard: a genuinely different write must NOT collapse."""
    a = {"to": "bob@acme.com", "subject": "Q3 sync", "body": "x"}
    b = {"to": "carol@acme.com", "subject": "Q3 sync", "body": "x"}
    key_a = derive_identity_key("email.send", a, run_id=_RUN, step_id=_STEP, ordinal=0)
    key_b = derive_identity_key("email.send", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    assert key_a != key_b


def test_recipient_order_is_normalized():
    """Recipient list order is not identity-bearing."""
    a = {"to": ["a@x.com", "b@x.com"], "subject": "s", "body": "x"}
    b = {"to": ["b@x.com", "a@x.com"], "subject": "s", "body": "x"}
    assert derive_identity_key("email.send", a, run_id=_RUN, step_id=_STEP, ordinal=0) == (
        derive_identity_key("email.send", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    )


def test_key_is_scoped_by_run_and_step():
    """The same logical write in two different runs is two different writes."""
    args = {"to": "bob@acme.com", "subject": "s", "body": "x"}
    k1 = derive_identity_key("email.send", args, run_id="run_A", step_id=_STEP, ordinal=0)
    k2 = derive_identity_key("email.send", args, run_id="run_B", step_id=_STEP, ordinal=0)
    assert k1 != k2
    assert "run_a" in k1.lower() and "run_b" in k2.lower()


def test_native_token_is_used_when_present():
    spec = IdentitySpec(native_token_field="idempotency_key")
    IDENTITY_SPECS["_test.native"] = spec
    try:
        args = {"idempotency_key": "tok-123", "body": "anything"}
        key = derive_identity_key("_test.native", args, run_id=_RUN, step_id=_STEP, ordinal=0)
        assert "tok-123" in key
    finally:
        del IDENTITY_SPECS["_test.native"]


def test_unregistered_write_falls_back_to_positional():
    """No spec -> args-independent positional key (fully robust to recompose)."""
    a = {"anything": "v1"}
    b = {"totally": "different"}
    k_a = derive_identity_key("unknown.write", a, run_id=_RUN, step_id=_STEP, ordinal=3)
    k_b = derive_identity_key("unknown.write", b, run_id=_RUN, step_id=_STEP, ordinal=3)
    assert k_a == k_b  # positional ignores args
    assert k_a.endswith(":pos:3")


def test_email_send_has_an_explicit_semantic_spec():
    """Known irreversible writes must carry a real (non-positional) identity."""
    spec = IDENTITY_SPECS.get("email.send")
    assert spec is not None and spec.identity_fields, "email.send needs semantic identity_fields"


def test_validate_identity_coverage_flags_specless_writes():
    missing = validate_identity_coverage({"email.send", "brand.new.write"})
    assert "brand.new.write" in missing
    assert "email.send" not in missing


def test_calendar_attendees_as_dicts_is_stable_and_order_independent():
    """Regression: dict list elements must not raise, and order must not matter."""
    a = {
        "calendar_id": "c1",
        "start": "T1",
        "end": "T2",
        "summary": "Sync",
        "attendees": [{"email": "a@x.com"}, {"email": "b@x.com"}],
    }
    b = {
        "calendar_id": "c1",
        "start": "T1",
        "end": "T2",
        "summary": "Sync",
        "attendees": [{"email": "b@x.com"}, {"email": "a@x.com"}],
    }
    ka = derive_identity_key("calendar.create", a, run_id=_RUN, step_id=_STEP, ordinal=0)
    kb = derive_identity_key("calendar.create", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    assert ka == kb


def test_recipient_scalar_and_single_element_list_collapse():
    a = {"to": "bob@acme.com", "subject": "s", "body": "x"}
    b = {"to": ["bob@acme.com"], "subject": "s", "body": "y"}
    assert derive_identity_key("email.send", a, run_id=_RUN, step_id=_STEP, ordinal=0) == (
        derive_identity_key("email.send", b, run_id=_RUN, step_id=_STEP, ordinal=0)
    )


def test_normalize_does_not_raise_on_mixed_or_none_list_elements():
    args = {"to": ["b@x.com", None], "subject": "s", "body": "x"}
    # Must not raise (was a TypeError before the total-_normalize fix).
    derive_identity_key("email.send", args, run_id=_RUN, step_id=_STEP, ordinal=0)
