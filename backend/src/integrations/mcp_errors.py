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
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    VALIDATION = "validation_error"
    CIRCUIT_OPEN = "circuit_open"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown_error"


def classify_error(error: Exception) -> str:
    """Classify an exception into a standard error code.

    Returns one of MCPErrorCode constants.
    """
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

    # Server errors (5xx)
    if any(k in error_str for k in ("500", "502", "503", "504", "server error")):
        return MCPErrorCode.SERVER_ERROR

    return MCPErrorCode.UNKNOWN


def is_transient(error_code: str) -> bool:
    """Check if an error is transient and should be retried."""
    return error_code in (
        MCPErrorCode.TIMEOUT,
        MCPErrorCode.RATE_LIMIT,
        MCPErrorCode.SERVER_ERROR,
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
