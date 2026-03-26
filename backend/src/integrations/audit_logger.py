"""Integration audit logger — hash inputs, redact sensitive fields.

Records every cross-boundary MCP tool call for compliance and debugging.
Sensitive field values are replaced with [REDACTED] before storage.
"""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.integration_audit import IntegrationAuditEvent

logger = logging.getLogger(__name__)

# Fields to redact in tool inputs
_SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "api-key",
        "authorization",
        "auth",
        "credential",
        "private_key",
        "ssh_key",
        "access_token",
        "refresh_token",
        "client_secret",
    }
)

_REDACTED = "[REDACTED]"


def _hash_input(data: dict) -> str:
    """SHA-256 hash of canonical JSON for tamper detection."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _redact_dict(data: dict, depth: int = 0) -> dict:
    """Deep-redact sensitive fields from a dict."""
    if depth > 5:
        return {"_truncated": True}

    redacted: dict = {}
    for key, value in data.items():
        if key.lower() in _SENSITIVE_FIELDS:
            redacted[key] = _REDACTED
        elif isinstance(value, dict):
            redacted[key] = _redact_dict(value, depth + 1)
        elif isinstance(value, list):
            redacted[key] = [
                _redact_dict(item, depth + 1) if isinstance(item, dict) else item
                for item in value[:20]  # cap list length
            ]
        elif isinstance(value, str) and len(value) > 500:
            redacted[key] = value[:500] + "...[truncated]"
        else:
            redacted[key] = value
    return redacted


class IntegrationAuditLogger:
    def __init__(self, db: AsyncSession, workspace_id: str, user_id: str) -> None:
        self._db = db
        self._workspace_id = workspace_id
        self._user_id = user_id

    async def log_tool_call(
        self,
        server_name: str,
        tool_name: str,
        trust_tier: str,
        tool_input: dict,
        output_summary: str | None = None,
        status: str = "success",
        error_message: str | None = None,
        latency_ms: int | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> str:
        """Log a tool call with hashed + redacted inputs."""
        input_hash = _hash_input(tool_input)
        input_redacted = _redact_dict(tool_input)

        event = IntegrationAuditEvent(
            workspace_id=self._workspace_id,
            user_id=self._user_id,
            server_name=server_name,
            tool_name=tool_name,
            trust_tier=trust_tier,
            action="tool_call",
            input_hash=input_hash,
            input_redacted=input_redacted,
            output_summary=output_summary[:2048] if output_summary else None,
            status=status,
            error_message=error_message[:1024] if error_message else None,
            latency_ms=latency_ms,
            run_id=run_id,
            step_id=step_id,
        )
        self._db.add(event)
        await self._db.flush()
        return event.audit_id

    async def log_action(
        self,
        server_name: str,
        action: str,
        trust_tier: str,
        details: dict | None = None,
        status: str = "success",
        error_message: str | None = None,
    ) -> str:
        """Log a non-tool action (install, activate, revoke, inspect)."""
        event = IntegrationAuditEvent(
            workspace_id=self._workspace_id,
            user_id=self._user_id,
            server_name=server_name,
            tool_name="",
            trust_tier=trust_tier,
            action=action,
            input_redacted=_redact_dict(details) if details else None,
            status=status,
            error_message=error_message[:1024] if error_message else None,
        )
        self._db.add(event)
        await self._db.flush()
        return event.audit_id
