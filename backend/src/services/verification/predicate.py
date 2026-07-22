"""The shared IRREVERSIBLE predicate + a deterministic per-capability classifier.

Spec §4.3: `IRREVERSIBLE = (reversible is False) OR (blast_radius in
{external_single, external_multiple, public})` — set-membership, no ordering.
Used by (a) the §4.5 verification trigger [here, Step 3] and later (b) the §4.3
gate override [Step 6] — the SAME predicate, extracted once.

The startup coverage gate needs a STATIC irreversibility classification (no LLM at
startup), so `reversible`/`blast_radius` become a deterministic per-capability
registry property. Rather than annotate ~45 external writes, we default an unlisted
write capability to IRREVERSIBLE (fail-closed) and list only the reversible-internal
exceptions — so a brand-new write capability can never silently skip verification.
"""

from __future__ import annotations

from src.integrations.capabilities import (
    CAPABILITY_CATALOG,
    SYSTEM_ACTION_CAPABILITIES,
    is_read_only_capability,
)

# The external blast-radius tiers (no bare "external"; no ordering — set membership).
_EXTERNAL_BLAST_RADIUS = frozenset({"external_single", "external_multiple", "public"})


def IRREVERSIBLE(*, reversible: bool, blast_radius: str) -> bool:  # noqa: N802 — shared §4.3 predicate name
    """The shared §4.3 irreversibility predicate over a (reversible, blast_radius)
    pair. Keyword-only so call sites read as `IRREVERSIBLE(reversible=..., blast_radius=...)`."""
    return (reversible is False) or (blast_radius in _EXTERNAL_BLAST_RADIUS)


# Write capabilities that are genuinely reversible AND internal (blast_radius
# self/internal) — the ONLY writes that skip read-back verification. Everything else
# not listed here defaults to IRREVERSIBLE (fail-closed). Keep this list explicit and
# audited: adding a capability here is a deliberate "this write needs no read-back."
REVERSIBLE_INTERNAL_CAPABILITIES: frozenset[str] = frozenset(
    {
        # Internal intelligence writes — self/internal blast radius, undoable.
        "internal.report_observation",
        "internal.ingest_event",
        "internal.update_entity",
        "internal.evaluate_policy",
        "internal.report_verdict",
        "internal.approve_action",
        "internal.update_cursor",
        "internal.extract_preferences",
        "internal.verify_run",
        "internal.update_execution",
        "internal.push_ui",
        "internal.store_memory",
        "internal.store_preference",
        # A local draft is not yet sent — internal + reversible.
        "email.draft",
        # Marking a message read is trivially reversible and low blast radius.
        "messaging.mark_read",
    }
    # system.* action writes (P2.5a) are internal, self blast-radius, and undoable (the user's
    # own goals / instructions / reminders / briefing) — reversible-internal like the
    # internal.* writes above, so they skip read-back verification. Sourced from the single
    # SYSTEM_ACTION_CAPABILITIES set so this list can never drift from the middleware exemptions.
    | SYSTEM_ACTION_CAPABILITIES
)


def is_irreversible_capability(capability: str) -> bool:
    """Deterministic, LLM-free irreversibility classification for the startup
    coverage gate. Read-only caps are never irreversible; a write cap is
    NOT irreversible only if it is an explicit reversible-internal exception;
    everything else (incl. unknown/new write caps) is IRREVERSIBLE (fail-closed)."""
    if is_read_only_capability(capability):
        return False
    if capability in REVERSIBLE_INTERNAL_CAPABILITIES:
        return False
    return True


def is_write_verification_required(capability: str, risk) -> bool:
    """Fail-closed UNION of the static classifier and the per-step RiskAssessment.

    Verification is required if EITHER the deterministic registry says the
    capability is irreversible OR the per-step risk assessment does — so a 24h-cache
    LLM mislabel (reversible) cannot skip verification for a statically-irreversible
    capability, and an unclassified capability flagged by the LLM is still verified.
    `risk` may be None (e.g. no assessment) — then only the static classifier applies.
    """
    if is_irreversible_capability(capability):
        return True
    if risk is None:
        return False
    return IRREVERSIBLE(
        reversible=getattr(risk, "reversible", True),
        blast_radius=getattr(risk, "blast_radius", "self"),
    )


def write_capabilities() -> set[str]:
    """The set of write (non-read-only) capabilities in the catalog — the domain the
    post-condition + identity coverage gates validate."""
    return {cap for cap in CAPABILITY_CATALOG if not is_read_only_capability(cap)}
