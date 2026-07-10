"""Canonical 'this write did not run' shape shared by BOTH execution paths (Step-10A A7).

The deep-runtime write-lock middleware wraps this body in a langchain ``ToolMessage``
(status="error"); the autonomous step runner returns it as a bare dict. Sharing the body here
means the two envelopes can never drift. Lives in ``services`` (not ``deep_runtime``) so the
autonomous path does not import upward — the one-way dependency is ``deep_runtime -> services``.
"""

from __future__ import annotations

# Byte-exact messages preserved from the pre-A7 inline returns (do NOT reword — existing tests
# assert these strings).
CONTENDED_MESSAGE = "resource busy — another write is in progress, retry"
WRITE_LOCK_UNAVAILABLE_MESSAGE = "write refused — redis write-lock required but unavailable"


def blocked_body(error: str) -> dict[str, object]:
    """The canonical blocked-write body: ``{"error": <error>, "blocked": True}``. Both paths
    derive their surface from this so a contended (or fail-closed) write is reported identically."""
    return {"error": error, "blocked": True}
