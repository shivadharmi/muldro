"""Structured MCP error handling — classify, sanitize, and format errors.

Provides consistent error responses across all MCP tool calls:
- Classification: auth_error, timeout, rate_limit, server_error, validation, unknown
- Sanitization: strip stack traces and internal paths in production
- Structured envelope: {status, error_code, message, retry_after}
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


class MCPErrorCode:
    """Standard error codes for MCP tool failures."""

    AUTH_ERROR = "auth_error"
    # Distinct from AUTH_ERROR: AUTH_ERROR is a *runtime* 401/403 from an MCP
    # call (stale token, mid-session expiry — often transiently recoverable by
    # refreshing the bearer). AUTH_REQUIRED is a *permanent* "user must
    # reconnect" state surfaced before/at session creation when no usable
    # credential exists at all (no_token / no_refresh_token / revoked).
    # NOTE: "permanent" here is this subsystem's vocabulary (a credential
    # problem the user must resolve). The perception poller deliberately
    # classifies AUTH_REQUIRED as "transient" in
    # poll_result.MCP_CODE_TO_POLL_CLASS, because in *its* vocabulary
    # "permanent" means threshold 1 — open the circuit after one attempt.
    AUTH_REQUIRED = "auth_required"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    VALIDATION = "validation_error"
    CIRCUIT_OPEN = "circuit_open"
    NOT_FOUND = "not_found"
    # The MCP session's transport died (background task exited / resource closed
    # — e.g. the on-demand subprocess crashed, or a concurrent refresh tore the
    # shared session down). Recoverable: rebuild the session and retry.
    SESSION_LOST = "session_lost"
    UNKNOWN = "unknown_error"


class McpAuthRequiredError(ConnectionError):
    """A server cannot run because the user must (re-)authorize the provider.

    Raised before/at MCP session creation when the OAuth token is permanently
    unusable (``reason in {"no_token", "no_refresh_token", "revoked"}``). Carries
    the provider/server/reason so callers (the re-auth service) can pause the
    right sources and prompt the user to reconnect.

    Subclasses ``ConnectionError`` so existing ``except ConnectionError``
    boundaries (which already treat "cannot connect this server" as a recorded,
    non-crashing failure) continue to catch it.
    """

    def __init__(self, *, provider: str, server: str, reason: str):
        self.provider = provider
        self.server = server
        self.reason = reason
        super().__init__(f"{provider} needs re-authorization (reason={reason})")


# Phrases that mark a *permanent grant-scope* failure (the token simply was
# never consented for this capability) rather than a transient/stale-token 401.
# Re-fetching the bearer cannot widen a grant — only a user re-consent can — so
# these must route to the re-auth pipeline, never the generic retry/refresh path.
_INSUFFICIENT_SCOPE_MARKERS: tuple[str, ...] = (
    "insufficient authentication scope",
    "insufficient permission",
    "insufficientpermissions",
    "insufficient_scope",
    "request had insufficient",
    "accessnotconfigured",
)


def is_insufficient_scope(error: Exception | str) -> bool:
    """True when *error* is a permanent OAuth grant-scope failure.

    Distinct from a generic 401/403 (which :func:`classify_error` keeps as the
    transiently-refreshable ``AUTH_ERROR``): an insufficient-scope error means
    the user must re-consent with a broader scope set, so callers should surface
    it as ``AUTH_REQUIRED`` and trigger re-authorization.
    """
    error_str = (error if isinstance(error, str) else str(error)).lower()
    return any(marker in error_str for marker in _INSUFFICIENT_SCOPE_MARKERS)


def classify_error(error: Exception) -> str:
    """Classify an exception into a standard error code.

    Returns one of MCPErrorCode constants.
    """
    # Permanent "needs reconnect" — checked before the generic "auth" substring
    # branch so it is not collapsed into AUTH_ERROR.
    if isinstance(error, McpAuthRequiredError):
        return MCPErrorCode.AUTH_REQUIRED

    error_str = str(error).lower()
    error_type = type(error).__name__

    # Auth errors
    if any(k in error_str for k in ("401", "403", "unauthorized", "forbidden", "auth")):
        return MCPErrorCode.AUTH_ERROR

    # Timeout
    if error_type in ("TimeoutError", "asyncio.TimeoutError") or "timeout" in error_str:
        return MCPErrorCode.TIMEOUT

    # Rate limit
    if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
        return MCPErrorCode.RATE_LIMIT

    # Validation
    if error_type in ("ValueError", "TypeError", "ValidationError"):
        return MCPErrorCode.VALIDATION

    # Circuit breaker
    if "circuit open" in error_str or "circuit_open" in error_str:
        return MCPErrorCode.CIRCUIT_OPEN

    # Not found
    if "404" in error_str or "not found" in error_str:
        return MCPErrorCode.NOT_FOUND

    # Session-transport death: the session's background task exited or the
    # underlying resource was closed. Recoverable by rebuilding the session and
    # retrying (handled as transient by the session_pool retry loop).
    if any(
        k in error_str
        for k in (
            "session task completed",
            "closed resource",
            "broken resource",
            "connection closed",
            "session closed",
        )
    ):
        return MCPErrorCode.SESSION_LOST

    # Server errors (5xx)
    if any(k in error_str for k in ("500", "502", "503", "504", "server error")):
        return MCPErrorCode.SERVER_ERROR

    # Generic in-band wrappers used by hosted MCP proxies when the upstream
    # service refuses a call (Atlassian Remote MCP, for example, returns
    # {"error":true,"message":"We are having trouble completing this action.
    # Please try again shortly."} for stale/unauthorized sessions instead of
    # a clean 401/5xx). Without this branch the classifier falls through to
    # UNKNOWN → non-transient → the retry loop breaks on the first attempt
    # AND no session-refresh recovery fires.
    if any(
        k in error_str
        for k in (
            "having trouble",
            "try again shortly",
            "try again later",
            "temporarily unavailable",
            "service unavailable",
        )
    ):
        return MCPErrorCode.SERVER_ERROR

    return MCPErrorCode.UNKNOWN


def is_transient(error_code: str) -> bool:
    """Check if an error is transient and should be retried."""
    return error_code in (
        MCPErrorCode.TIMEOUT,
        MCPErrorCode.RATE_LIMIT,
        MCPErrorCode.SERVER_ERROR,
        MCPErrorCode.SESSION_LOST,
    )


def sanitize_error(error: Exception, *, mask_details: bool | None = None) -> str:
    """Sanitize an error message for external consumption.

    In production (FASTMCP_MASK_ERROR_DETAILS=true), strips:
    - Stack traces
    - Internal file paths
    - Database connection strings
    - Sensitive parameter values

    Args:
        error: The exception to sanitize.
        mask_details: Override production detection. If None, reads env var.

    Returns:
        Sanitized error message string.
    """
    if mask_details is None:
        mask_details = os.environ.get("FASTMCP_MASK_ERROR_DETAILS", "").lower() == "true"

    msg = str(error)

    if not mask_details:
        return msg

    # Strip file paths
    msg = re.sub(r"/[\w/.]+\.py:\d+", "[redacted]", msg)

    # Strip connection strings
    msg = re.sub(
        r"(postgresql|redis|http|https)://[^\s]+",
        r"\1://[redacted]",
        msg,
    )

    # Strip stack trace lines
    msg = re.sub(r"Traceback \(most recent.*?\n(?:.*?\n)*?.*?Error:", "", msg)

    # Truncate long messages
    if len(msg) > 200:
        msg = msg[:200] + "..."

    return msg.strip() or "An internal error occurred"


def make_error_response(
    error: Exception,
    *,
    tool_name: str = "",
    mask_details: bool | None = None,
) -> dict:
    """Build a structured error response dict.

    Returns:
        {
            "status": "error",
            "error_code": "...",
            "message": "...",
            "retry_after": N | None,
            "tool": "..." | None,
        }
    """
    error_code = classify_error(error)
    message = sanitize_error(error, mask_details=mask_details)

    # Suggest retry delay for transient errors
    retry_after: int | None = None
    if error_code == MCPErrorCode.RATE_LIMIT:
        retry_after = 60
    elif error_code == MCPErrorCode.TIMEOUT:
        retry_after = 5
    elif error_code == MCPErrorCode.SERVER_ERROR:
        retry_after = 10

    response: dict = {
        "status": "error",
        "error_code": error_code,
        "message": message,
    }
    if retry_after is not None:
        response["retry_after"] = retry_after
    if tool_name:
        response["tool"] = tool_name

    return response
