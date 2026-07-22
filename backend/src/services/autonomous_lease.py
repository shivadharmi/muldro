"""Single-flight Redis lease for durable autonomous runs (Step 10C P3).

Mirrors ``src.services.write_lock`` (``SET NX`` + owner-token Lua CAS release +
``@asynccontextmanager``) but with two differences:

  * The lease is per-RUN (keyed on ``run_id``), not per-(workspace, capability).
  * It is **try-once / NON-blocking** — one worker acquires + drives the durable
    run, the other BACKS OFF immediately rather than bounded-waiting. Two workers
    both driving the same durable thread would replay ``ainvoke(None, cfg)`` and
    contend on the checkpoint; the lease makes exactly one drive.

NOTE ON CORRECTNESS: the idempotency ledger already makes the tool EFFECT
exactly-once across a double-drive — this lease is a wasted-work / checkpoint-
contention optimization, not the correctness guarantee. The TTL auto-expires so a
crashed holder never deadlocks the run: the next scheduler tick re-acquires.

Migration-free by construction: this is a Redis key (``SET NX PX``), never a
``TaskRun`` column — the 10C zero-migration invariant holds.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

RUN_LEASE_TTL_SECONDS = 660  # exceeds the 600s background execution timeout; a crashed
# holder's lease auto-expires so the next tick re-acquires.

# Compare-and-delete: only the owner (matching token) may release. A lease that
# expired and was re-acquired by another owner is never deleted out from under them.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


def run_lease_key(run_id: str) -> str:
    """Deterministic per-run lease key. One key per durable run."""
    return f"lease:run:{run_id}"


@asynccontextmanager
async def acquire_run_lease(redis, run_id: str, *, ttl_s: int = RUN_LEASE_TTL_SECONDS):
    """Single-flight lease for a durable autonomous run.

    Yields ``True`` if THIS caller acquired the lease (it should drive the run) or
    ``False`` if another worker holds it (the caller must back off — do NOT drive,
    to avoid a wasted concurrent ``ainvoke(None, cfg)`` replay of the same durable
    thread + checkpoint contention). Releases via an owner-token CAS so a lease
    that expired and was re-acquired by another owner is never deleted out from
    under them.

    NOTE: the idempotency ledger already makes the tool EFFECT exactly-once across
    a double-drive; this lease is a wasted-work/contention optimization, not the
    correctness guarantee. TTL auto-expires so a crashed holder never deadlocks
    the run.
    """
    key = run_lease_key(run_id)
    token = uuid.uuid4().hex
    acquired = bool(await redis.set(key, token, nx=True, px=ttl_s * 1000))
    try:
        yield acquired
    finally:
        if acquired:
            try:
                await redis.eval(_RELEASE_LUA, 1, key, token)
            except Exception:
                pass  # best-effort; TTL guarantees eventual expiry
