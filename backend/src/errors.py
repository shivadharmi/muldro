"""Domain error types and client-safe error envelope.

This module is the single source of truth for how errors are represented to
clients. It is intentionally framework-neutral (no FastAPI import) so that any
layer — services, orchestrator, tools — can raise these exceptions without an
upward dependency on the API layer.

The core invariant: an error carries TWO messages.

- ``internal_message`` — full detail for server-side logs (may contain DSNs,
  identifiers, upstream error text). NEVER returned to a client.
- ``safe_message`` — a generic, client-safe message. ALWAYS safe to return.

The HTTP exception handlers (``src/api/error_handlers.py``) and the streaming
helpers (``safe_error_event``) only ever expose ``safe_message`` + ``code`` +
``correlation_id``. Raw exceptions are logged, never serialized to a client.
"""

from __future__ import annotations

from pydantic import BaseModel
from ulid import ULID

# ── Client-safe envelope ──────────────────────────────────────────────


class ErrorBody(BaseModel):
    """The client-safe error payload. Never contains internal detail."""

    code: str
    message: str
    correlation_id: str


class ErrorResponse(BaseModel):
    """Standard error envelope returned by every error path (REST/SSE/WS).

    Shape: ``{"error": {"code": ..., "message": ..., "correlation_id": ...}}``
    """

    error: ErrorBody


def new_correlation_id() -> str:
    """Generate a fallback correlation id when none is available on the request
    (e.g. WebSocket scopes, which the tracing middleware skips)."""
    return f"err_{ULID()}"


# ── Domain exception hierarchy ────────────────────────────────────────


class JarvisError(Exception):
    """Base for all domain errors.

    Subclasses set class-level ``code``/``http_status``/``safe_message``.
    Raise with the internal detail as the first arg; override ``safe_message``
    per-instance only when the message is itself controlled/safe (e.g. a
    validation message you authored, never an upstream exception string).
    """

    code: str = "internal_error"
    http_status: int = 500
    safe_message: str = "Something went wrong. Please try again."

    def __init__(
        self,
        internal_message: str | None = None,
        *,
        safe_message: str | None = None,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        if safe_message is not None:
            self.safe_message = safe_message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        # internal_message defaults to the safe message so we never crash on None,
        # but it is for logs only.
        self.internal_message = internal_message or self.safe_message
        super().__init__(self.internal_message)


class ValidationError(JarvisError):
    """Caller-supplied input was invalid. safe_message is typically the
    controlled validation text (safe to surface)."""

    code = "validation_error"
    http_status = 400
    safe_message = "The request was invalid."


class NotFoundError(JarvisError):
    code = "not_found"
    http_status = 404
    safe_message = "The requested resource was not found."


class AuthError(JarvisError):
    code = "unauthorized"
    http_status = 401
    safe_message = "Authentication failed."


class ForbiddenError(JarvisError):
    code = "forbidden"
    http_status = 403
    safe_message = "You don't have permission to perform this action."


class ConflictError(JarvisError):
    code = "conflict"
    http_status = 409
    safe_message = "The request conflicts with the current state."


class RateLimitedError(JarvisError):
    code = "rate_limited"
    http_status = 429
    safe_message = "Too many requests. Please slow down and try again."


class ExternalServiceError(JarvisError):
    """An upstream dependency (LLM API, MCP server, DB, vector store) failed."""

    code = "upstream_unavailable"
    http_status = 502
    safe_message = "An upstream service is temporarily unavailable. Please try again."


# ── Classification + serialization helpers ────────────────────────────

_GENERIC_CODE = "internal_error"
_GENERIC_MESSAGE = "Something went wrong. Please try again."


def classify(exc: BaseException) -> tuple[str, str, int]:
    """Map any exception to (code, client-safe message, http_status).

    Known ``JarvisError`` types expose their declared safe message. Everything
    else collapses to the generic internal-error tuple — a raw exception string
    is NEVER used as the client message.
    """
    if isinstance(exc, JarvisError):
        return exc.code, exc.safe_message, exc.http_status
    return _GENERIC_CODE, _GENERIC_MESSAGE, 500


def error_body(exc: BaseException, correlation_id: str | None = None) -> ErrorBody:
    """Build the client-safe ErrorBody for an exception."""
    code, message, _ = classify(exc)
    return ErrorBody(
        code=code, message=message, correlation_id=correlation_id or new_correlation_id()
    )


def safe_error_event(
    exc: BaseException,
    correlation_id: str | None = None,
    *,
    channel: str = "sse",
) -> dict:
    """Build a client-safe error frame for a streaming channel.

    ``channel="sse"`` → ``{"event": "error", ...}`` (matches the SSE event shape).
    ``channel="ws"``  → ``{"status": "error", ...}`` (matches the WS reply shape).

    Use this everywhere a stream currently does ``{"message": str(e)}``.
    """
    code, message, _ = classify(exc)
    cid = correlation_id or new_correlation_id()
    if channel == "ws":
        return {"status": "error", "code": code, "message": message, "correlation_id": cid}
    return {"event": "error", "code": code, "message": message, "correlation_id": cid}
