"""Hooks for the Jarvis orchestrator.

Governor policy hook: Audit-only — approval gating moved to TrustEngine (Spec 2B-i).
Audit hook: Logs every tool call for observability.
"""

import logging
import re

from ulid import ULID

from src.models.agent_decision_log import AgentDecisionLog
from src.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Create a human-readable summary of tool input for approval display."""
    if "to" in tool_input and "subject" in tool_input:
        return f"Send email to {tool_input['to']}: {tool_input.get('subject', '')}"
    if "channel" in tool_input and "text" in tool_input:
        text = str(tool_input["text"])
        truncated = text[:100] + ("..." if len(text) > 100 else "")
        return f"Post to {tool_input['channel']}: {truncated}"
    if "title" in tool_input:
        return f"{tool_name}: {tool_input['title']}"
    return f"{tool_name} with {len(tool_input)} parameters"


async def governor_pre_tool_hook(
    tool_name: str,
    tool_input: dict,
    agent_name: str,
    *,
    user_id: str,
    workspace_id: str = "",
    db_factory=None,
    services: dict | None = None,
    trust_tier: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Pre-tool-use hook: audit logging only.

    Approval gating moved to TrustEngine in GraphExecutor (Spec 2B-i).
    This hook now only:
    1. Checks if the tool is blocked (disabled in registry)
    2. Logs the tool call for audit
    3. Returns allowed: True for all non-blocked tools

    Args:
        trust_tier: Trust tier of the MCP server (T0-T3).
        run_id: Current TaskRun ID (if available).

    Returns:
        {"allowed": True} for non-blocked tools
        {"allowed": False, "reason": "..."} for blocked tools
    """
    # Classify tool via registry for audit + blocked check
    is_blocked = False
    risk_level = "low"

    if db_factory:
        try:
            async with db_factory() as db:
                registry = ToolRegistry(db)
                tool_def = await registry.get_tool(tool_name)
                if tool_def:
                    is_blocked = not tool_def.enabled
                    risk_level = tool_def.risk_level
        except Exception:
            pass

    # Blocked tools never pass (safety invariant)
    if is_blocked:
        logger.warning(
            "governor_blocked_tool",
            extra={"tool": tool_name, "agent": agent_name},
        )
        return {
            "allowed": False,
            "reason": f"Tool '{tool_name}' is blocked by policy",
        }

    # Audit log — all non-blocked tools
    logger.info(
        "tool_audit",
        extra={
            "tool": tool_name,
            "agent": agent_name,
            "risk_level": risk_level,
            "workspace_id": workspace_id,
        },
    )

    return {"allowed": True}


async def audit_post_tool_hook(
    tool_name: str,
    tool_input: dict,
    tool_result: dict | str,
    agent_name: str,
    trace_id: str | None = None,
    span_id: str | None = None,
    tokens_used: int = 0,
    latency_ms: int = 0,
    db_factory=None,
    workspace_id: str = "",
) -> None:
    """Post-tool-use hook: log every tool call to the agent decision log."""
    if db_factory is None:
        return

    try:
        async with db_factory() as db:
            log_entry = AgentDecisionLog(
                log_id=f"adl_{ULID()}",
                workspace_id=workspace_id,
                trace_id=trace_id or "unknown",
                span_id=span_id,
                agent_name=agent_name,
                tool_name=tool_name,
                input_summary=_sanitize_secrets(str(tool_input)),
                output_summary=_sanitize_secrets(str(tool_result)),
                tokens_used=tokens_used,
                latency_ms=latency_ms,
            )
            db.add(log_entry)
            await db.commit()
    except Exception as e:
        logger.error("audit_post_tool_hook failed: %s", e, exc_info=True)


_SECRET_PATTERN = re.compile(
    r'(["\']?(?:api[_-]?key|token|password|secret|authorization|access_token'
    r"|refresh_token|client_secret)[\"']?"
    r'[\s]*[:=][\s]*["\']?)([^\s"\',:}{]{8,})',
    re.IGNORECASE,
)


def _sanitize_secrets(text: str) -> str:
    """Redact common secret patterns from text before audit persistence."""
    return _SECRET_PATTERN.sub(r"\1***REDACTED***", text)
