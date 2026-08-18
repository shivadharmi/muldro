"""Workspace-bound checkpointer thread-id identity for the deep runtime (A6, Step-10A).

The LangGraph AsyncPostgresSaver keys checkpoints by ``thread_id`` alone and asserts NO
tenant ownership. Embedding the workspace in the thread_id (and asserting it on resume) is
the only surface to bind checkpoint identity to a tenant. Format ``c:{workspace_id}:{ulid}``
(1-char tag + workspace + bare 26-char ULID, ~58 chars) fits ``Approval.thread_id`` =
``String(64)`` with headroom — a longer scheme would overflow and force the migration 10A forbids.
The autonomous durable checkpointer (10C/B9) MUST reuse this identity.
"""

from __future__ import annotations

from ulid import ULID

_TAG = "c"


def make_thread_id(workspace_id: str) -> str:
    """Mint a checkpointer thread_id embedding *workspace_id*.

    Assumes a colonless ``workspace_id`` (Muldro ws ids are ``ws_{ULID}``); a colon in the
    workspace would not round-trip through ``workspace_of_thread_id`` — which fails closed
    (refuses), never grants — so it is safe but non-recoverable."""
    return f"{_TAG}:{workspace_id}:{ULID()}"


def workspace_of_thread_id(thread_id: str | None) -> str | None:
    """Parse the workspace out of a thread_id; None if missing/malformed/colonless. NEVER
    raises — None, empty, or a legacy colonless id all parse to None (defensive, load-bearing:
    resume refuses any thread whose workspace cannot be recovered). The None-safety is a
    contract, not incidental: the 10C/B9 reuse target reads ``Approval.thread_id`` — a
    NULLABLE column — directly."""
    if not thread_id or not isinstance(thread_id, str):
        return None
    parts = thread_id.split(":", 2)
    if len(parts) == 3 and parts[0] == _TAG:
        return parts[1]
    return None
