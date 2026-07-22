"""Per-capability SEMANTIC identity keys for the idempotency ledger.

The identity key must be:
  * STABLE across resume even when the LLM recomposes the raw args (so a
    regenerated email body does NOT change the key -> no double-fire), and
  * DISTINCT per logical write (so two genuinely different sends do NOT
    collapse into one).

It is therefore derived from the identity-DEFINING fields (recipients, subject),
NOT the full payload, and scoped to the execution position (run:step:capability).
Where a provider exposes a native idempotency token, that token IS the identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdentitySpec:
    """How to derive a capability's semantic identity.

    Exactly one strategy applies, checked in order:
      1. ``native_token_field`` present in args -> its value is the identity.
      2. ``identity_fields`` -> a normalized digest over just those args.
      3. otherwise -> positional (run:step:ordinal), args-independent.
    """

    native_token_field: str | None = None
    identity_fields: tuple[str, ...] = ()


# Seeded for the write capabilities that exist today (CAPABILITY_CATALOG). The
# VOLATILE fields (body/description) are DELIBERATELY excluded. Consequence: two
# sends with the same to+subject but different body within one step intentionally
# collapse to one key (the "same logical write" default — favouring no-double-fire
# over field-level distinctness). A genuinely distinct send needs a distinct subject.

IDENTITY_SPECS: dict[str, IdentitySpec] = {
    "email.send": IdentitySpec(identity_fields=("to", "cc", "bcc", "subject")),
    "email.delete": IdentitySpec(identity_fields=("message_id",)),
    "calendar.create": IdentitySpec(
        identity_fields=("calendar_id", "start", "end", "summary", "attendees")
    ),
}


# Irreversible write capabilities for which the positional/native-token idempotency
# key is DELIBERATELY accepted (no semantic IdentitySpec authored — typically external
# MCP tools whose arg schema we don't control). Explicit + audited: the startup gate
# forbids an irreversible write cap that is neither spec'd NOR listed here, so a new
# write cap can't silently fall back to positional keying unnoticed. Mirrors the
# verification UNVERIFIABLE escape valve.
POSITIONAL_KEY_ACCEPTED: frozenset[str] = frozenset(
    {
        "email.reply",
        "calendar.update",
        "calendar.delete",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        "repo.review_pr",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.delete",
        "issue.transition",
        "issue.sub_issue",
        "doc.create",
        "doc.update",
        "doc.delete",
        "doc.comment",
        "doc.append",
        "doc.move",
        "doc.update_block",
        "doc.delete_block",
        "doc.create_datasource",
        "doc.update_datasource",
        "doc.drive_create",
        "doc.drive_delete",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "workflow.delete",
        "workflow.create_issues",
        "workflow.bulk_update",
        "workflow.update_comment",
        "workflow.delete_comment",
        "workflow.resolve_comment",
        "workflow.unresolve_comment",
        "workflow.create_project",
        "workflow.create_milestone",
        "workflow.update_milestone",
        "workflow.delete_milestone",
        "workflow.create_customer_need",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        "browser.open",
        "browser.click",
        "browser.type",
        "browser.submit",
        "browser.execute",
        "browser.install",
    }
)


def validate_identity_coverage_strict(irreversible_write_capabilities: set[str]) -> list[str]:
    """Startup HARD-GATE (spec §6 Step-3 carry-forward): every IRREVERSIBLE write
    capability must have a semantic IdentitySpec OR be explicitly positional-accepted.
    The caller supplies the irreversible set (keeps this module free of a verification
    import). Returns list[str] (empty = valid), never raises."""
    allowed = set(IDENTITY_SPECS) | POSITIONAL_KEY_ACCEPTED
    return [
        f"IRREVERSIBLE write capability '{cap}' has no identity strategy "
        "(add an IdentitySpec or list it in POSITIONAL_KEY_ACCEPTED)"
        for cap in sorted(irreversible_write_capabilities)
        if cap not in allowed
    ]


def _normalize(value: object) -> object:
    """Order-independent, whitespace/case-insensitive, TOTAL normalization of an
    identity field. Never raises: heterogeneous / dict / None list elements are
    sorted by a stable JSON projection rather than by raw value."""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        items = [_normalize(v) for v in value]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return value


def derive_identity_key(
    capability: str,
    args: dict,
    *,
    run_id: str,
    step_id: str,
    ordinal: int,
) -> str:
    """Derive the stable semantic identity key for a write, captured at first
    attempt and reproduced verbatim on resume."""
    scope = f"{run_id}:{step_id}:{capability}"
    spec = IDENTITY_SPECS.get(capability)

    if spec and spec.native_token_field:
        token = args.get(spec.native_token_field)
        if token:
            return f"{scope}:tok:{token}"

    if spec and spec.identity_fields:
        payload = {}
        for field in spec.identity_fields:
            v = args.get(field)
            if v is None:
                v = []  # missing field == empty list (stable)
            elif not isinstance(v, (list, tuple)):
                v = [v]  # scalar and single-element list collapse
            payload[field] = _normalize(v)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]
        return f"{scope}:sem:{digest}"

    # Positional fallback: args-independent -> fully robust to recompose, at the
    # cost of assuming the step re-issues its writes in the same order on resume.
    return f"{scope}:pos:{ordinal}"


def validate_identity_coverage(write_capabilities: set[str]) -> list[str]:
    """Return write capabilities that lack a registered IdentitySpec (they will
    fall back to the positional key). Surfaced as a startup WARNING; Step 3 may
    promote this to a hard startup error alongside post-condition coverage."""
    return sorted(c for c in write_capabilities if c not in IDENTITY_SPECS)
