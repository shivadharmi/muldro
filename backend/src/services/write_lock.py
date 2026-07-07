"""Cross-path per-(workspace, capability) write lock (Step 6C).

Both the deep-runtime dispatcher and the autonomous DAG path acquire the SAME key so a
chat write and a scheduler write to the same capability in one workspace mutually exclude.
Reads never lock. Keyed on capability (not tool name) per the spec — two tools sharing a
capability serialize; two different capabilities never block each other.

Correctness (proven by spikes/step6c_write_lock): the base primitive's unconditional
release deletes ANOTHER owner's lock after a TTL-expiry-mid-call, so release is an
owner-token compare-and-delete (Lua CAS). Contention is bounded-wait then fail-closed.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

WRITE_LOCK_TTL_SECONDS = 120  # 2x the 60s agent_loop tool timeout — must exceed max call length
_WAIT_TIMEOUT_DEFAULT = 5.0
_POLL_INTERVAL = 0.05

# Compare-and-delete: only the owner (matching token) may release.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


class WriteLockContended(RuntimeError):  # noqa: N818 - spec-required public name (imported by tests + later 6C tasks)
    """Raised when the write lock could not be acquired within ``wait_timeout``."""


def write_lock_key(workspace_id: str, capability: str) -> str:
    """Deterministic key shared by BOTH execution paths — do not change independently."""
    return f"write:{workspace_id}:{capability}"


@asynccontextmanager
async def acquire_write_lock(
    redis,
    workspace_id: str,
    capability: str,
    *,
    ttl: int = WRITE_LOCK_TTL_SECONDS,
    wait_timeout: float = _WAIT_TIMEOUT_DEFAULT,
):
    """Acquire the per-(workspace, capability) write lock, bounded-wait then fail-closed.

    Raises ``WriteLockContended`` if not acquired within ``wait_timeout``. Releases via an
    owner-token CAS so a lock that expired and was re-acquired by another owner is never
    deleted out from under them.
    """
    redis_key = f"lock:{write_lock_key(workspace_id, capability)}"
    token = uuid.uuid4().hex
    deadline = asyncio.get_event_loop().time() + wait_timeout
    acquired = False
    while True:
        acquired = bool(await redis.set(redis_key, token, nx=True, ex=ttl))
        if acquired:
            break
        if asyncio.get_event_loop().time() >= deadline:
            raise WriteLockContended(
                f"write lock contended: {write_lock_key(workspace_id, capability)}"
            )
        await asyncio.sleep(_POLL_INTERVAL)
    try:
        yield
    finally:
        try:
            await redis.eval(_RELEASE_LUA, 1, redis_key, token)
        except Exception:
            # Best-effort release; TTL guarantees eventual expiry.
            pass
