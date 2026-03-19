"""Hooks for the Jarvis orchestrator.

Governor policy hook: Intercepts tool calls to enforce approval policies.
Audit hook: Logs every tool call for observability.
Budget hook: Tracks token usage per agent call.
"""

import logging

from ulid import ULID

from src.models.agent_decision_log import AgentDecisionLog

logger = logging.getLogger(__name__)

# Fallback sets — used ONLY when ToolRegistry DB is unavailable.
# The ToolRegistry DB is the source of truth; these ensure graceful degradation.
_FALLBACK_WRITE_TOOLS = frozenset(
    {
        "gmail_send",
        "gmail_send_email",
        "gmail_draft",
        "gmail_create_draft",
        "gmail_reply",
        "calendar_create",
        "calendar_create_event",
        "calendar_update",
        "calendar_update_event",
        "calendar_delete",
        "drive_create",
        "docs_create",
        "sheets_update",
        "slack_post_message",
        "slack_send_message",
        "slack_react",
        "slack_update_message",
        "github_create_issue",
        "github_comment",
        "github_create_pr",
        "github_merge_pr",
        "send_telegram",
        "send_approval_prompt",
        "approve_action",
    }
)

_FALLBACK_BLOCKED_TOOLS = frozenset(
    {
        "gmail_delete",
        "drive_delete",
        "calendar_delete_event",
    }
)


async def _classify_via_registry(tool_name: str, db_factory) -> tuple[bool, bool, str]:
    """Classify a tool via ToolRegistry.

    Returns (is_blocked, is_write, risk_level).
    Raises if DB unavailable so caller can fall back.
    """
    from src.services.tool_registry import ToolRegistry

    async with db_factory() as db:
        registry = ToolRegistry(db)
        tool_def = await registry.get_tool(tool_name)
        if not tool_def:
            return False, False, "low"
        return (
            not tool_def.enabled,
            tool_def.requires_approval,
            tool_def.risk_level,
        )


async def governor_pre_tool_hook(
    tool_name: str,
    tool_input: dict,
    agent_name: str,
    *,
    user_id: str,
    db_factory=None,
    services: dict | None = None,
) -> dict:
    """Pre-tool-use hook: enforce Governor policy before external writes.

    Returns:
        {"allowed": True} to proceed
        {"allowed": False, "reason": "..."} to block
        {"allowed": False, "approval_required": True, "approval_id": "..."} for approval gate
    """
    is_blocked = False
    is_write = False
    risk_level = "low"

    # Primary: use ToolRegistry (DB-backed)
    if db_factory:
        try:
            is_blocked, is_write, risk_level = await _classify_via_registry(tool_name, db_factory)
        except Exception:
            # Fallback to hardcoded sets
            is_blocked = tool_name in _FALLBACK_BLOCKED_TOOLS
            is_write = tool_name in _FALLBACK_WRITE_TOOLS
            risk_level = _classify_risk_fallback(tool_name)
    else:
        is_blocked = tool_name in _FALLBACK_BLOCKED_TOOLS
        is_write = tool_name in _FALLBACK_WRITE_TOOLS
        risk_level = _classify_risk_fallback(tool_name)

    # Blocked tools never pass
    if is_blocked:
        logger.warning(
            "governor_blocked_tool",
            extra={"tool": tool_name, "agent": agent_name},
        )
        return {"allowed": False, "reason": f"Tool '{tool_name}' is blocked by policy"}

    # Write tools require approval
    if is_write:
        logger.info(
            "governor_approval_required",
            extra={"tool": tool_name, "agent": agent_name},
        )

        # Create approval record if we have DB access
        if db_factory and services:
            try:
                from src.models.approvals import Approval

                async with db_factory() as db:
                    approval = Approval(
                        approval_id=f"apr_{ULID()}",
                        user_id=user_id,
                        approval_type=f"tool_call:{tool_name}",
                        title=f"Approve: {tool_name}",
                        summary=_summarize_tool_input(tool_name, tool_input),
                        risk_level=risk_level,
                        status="pending",
                        expires_at=None,
                    )
                    db.add(approval)
                    await db.commit()

                    return {
                        "allowed": False,
                        "approval_required": True,
                        "approval_id": approval.approval_id,
                        "reason": f"Approval required for {tool_name}",
                    }
            except Exception as e:
                logger.error("Failed to create approval: %s", e, exc_info=True)

        return {
            "allowed": False,
            "approval_required": True,
            "reason": f"Approval required for {tool_name}",
        }

    # Internal/read-only tools — allow
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
                input_summary=_truncate(str(tool_input), 500),
                output_summary=_truncate(str(tool_result), 500),
                tokens_used=tokens_used,
                latency_ms=latency_ms,
            )
            db.add(log_entry)
            await db.commit()
    except Exception as e:
        logger.error("audit_post_tool_hook failed: %s", e, exc_info=True)


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Create a human-readable summary of tool input for approval display."""
    if "to" in tool_input and "subject" in tool_input:
        return f"Send email to {tool_input['to']}: {tool_input.get('subject', '')}"
    if "channel" in tool_input and "text" in tool_input:
        return f"Post to {tool_input['channel']}: {_truncate(tool_input['text'], 100)}"
    if "title" in tool_input:
        return f"{tool_name}: {tool_input['title']}"
    return f"{tool_name} with {len(tool_input)} parameters"


def _classify_risk_fallback(tool_name: str) -> str:
    """Classify risk level using hardcoded fallback (when DB unavailable)."""
    high_risk = {"gmail_send", "gmail_send_email", "github_merge_pr", "slack_post_message"}
    if tool_name in high_risk:
        return "high"
    if tool_name in _FALLBACK_BLOCKED_TOOLS:
        return "critical"
    if tool_name in _FALLBACK_WRITE_TOOLS:
        return "medium"
    return "low"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
