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
