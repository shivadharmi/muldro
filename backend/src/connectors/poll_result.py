"""Typed result for connector poll operations.

Replaces the bare 2-tuple (events, cursor) so callers can distinguish
success-empty from failure, and route failed polls to the circuit breaker's
failure path instead of the success path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.services.event_processor import RawEvent

PollErrorClass = Literal["none", "transient", "permanent", "rate_limited", "auth_failed"]

# Sentinel error strings that map to PerceptionPolicyService's classify_error patterns.
# These strings are chosen so that classify_error() returns the right ErrorClass
# without needing an additional translation layer in jarvis.py.
# "none" is intentionally absent — a successful poll must never be passed to record_failure.
_ERROR_CLASS_MESSAGES: dict[str, str] = {
    "transient": "transient connector error (503 service unavailable)",
    "permanent": "permanent connector error (4xx unrecoverable)",
    "rate_limited": "rate_limited connector error (429 rate limit exceeded)",
    "auth_failed": "auth_failed connector error (401 unauthorized: token invalid or revoked)",
}


def error_class_to_policy_error(error_class: PollErrorClass) -> str:
    """Convert a PollErrorClass into an error string that classify_error() will bucket correctly.

    Used by _poll_connector to populate the poll_error 4-tuple slot, ensuring
    that the error string contains the right keywords for PerceptionPolicyService
    to select the correct circuit-breaker threshold.

    Raises ValueError for ``"none"`` — a successful poll must never be converted to an
    error message and passed to record_failure.
    """
    if error_class == "none":
        raise ValueError(
            "error_class_to_policy_error called with 'none' — "
            "successful polls must not be recorded as failures"
        )
    return _ERROR_CLASS_MESSAGES.get(error_class, error_class)


def _classify_http_status(status_code: int) -> PollErrorClass:
    """Map an HTTP status code to a PollErrorClass.

    Shared by all native connectors (gmail, slack, calendar, github).
    """
    if status_code in (401, 403):
        return "auth_failed"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "transient"
    # Other 4xx errors won't self-heal on retry
    if status_code >= 400:
        return "permanent"
    return "none"


@dataclass(frozen=True)
class PollResult:
    """Immutable result of a connector poll() call.

    Replaces the bare ``(events, cursor)`` 2-tuple so that a failed poll is
    distinguishable from a genuinely empty one.

    Attributes:
        events:      Normalised events collected during this poll (empty on failure).
        cursor:      Opaque cursor for the next incremental poll.
                     MUST equal the incoming cursor on any failure — never advance
                     the cursor when the poll did not complete successfully.
        error_class: Outcome classification:
                     - ``"none"``        — success (events may be empty)
                     - ``"transient"``   — temporary failure (network, 5xx, timeout)
                     - ``"rate_limited"``— rate-limited by provider (429)
                     - ``"auth_failed"`` — credential problem (401/403/revoked token)
                     - ``"permanent"``   — unrecoverable 4xx (won't self-heal)
    """

    events: list[RawEvent] = field(default_factory=list)
    cursor: str | None = None
    error_class: PollErrorClass = "none"

    @property
    def ok(self) -> bool:
        """True when the poll completed without error (events may still be empty)."""
        return self.error_class == "none"

    @property
    def failed(self) -> bool:
        """True when the poll encountered an error."""
        return self.error_class != "none"
