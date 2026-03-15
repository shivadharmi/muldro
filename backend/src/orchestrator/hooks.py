"""Hooks for the Jarvis orchestrator.

Governor policy hook: Intercepts tool calls to enforce approval policies.
Audit hook: Logs every tool call for observability.
Budget hook: Tracks token usage per agent call.
"""

import logging

from ulid import ULID

from src.models.agent_decision_log import AgentDecisionLog

logger = logging.getLogger(__name__)

# Tools that write to external systems — require Governor approval
WRITE_TOOLS = frozenset(
    {
        # Google Workspace writes
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
        # Slack writes
        "slack_post_message",
        "slack_send_message",
        "slack_react",
        "slack_update_message",
        # GitHub writes
        "github_create_issue",
        "github_comment",
        "github_create_pr",
        "github_merge_pr",
        # Telegram writes (via communication server)
        "send_telegram",
        "send_approval_prompt",
    }
)

# Tools that are always safe (read-only or internal)
READ_ONLY_TOOLS = frozenset(
    {
        "search_memory",
        "get_entities",
        "get_active_plans",
        "get_briefing",
        "get_observation_cursor",
        "report_observation",
        "gmail_list",
        "gmail_read",
        "gmail_search",
        "calendar_list",
        "calendar_get",
        "drive_list",
        "drive_search",
        "slack_list_channels",
        "slack_get_messages",
        "slack_search",
    }
)

# Tools that are always blocked
BLOCKED_TOOLS = frozenset(
    {
        "gmail_delete",
        "drive_delete",
        "calendar_delete_event",
    }
)


async def governor_pre_tool_hook(
    tool_name: str,
    tool_input: dict,
    agent_name: str,
    db_factory=None,
    services: dict | None = None,
) -> dict:
    """Pre-tool-use hook: enforce Governor policy before external writes.

    Returns:
        {"allowed": True} to proceed
        {"allowed": False, "reason": "..."} to block
        {"allowed": False, "approval_required": True, "approval_id": "..."} for approval gate
    """
    # Read-only tools always pass
    if tool_name in READ_ONLY_TOOLS:
        return {"allowed": True}

    # Blocked tools never pass
    if tool_name in BLOCKED_TOOLS:
        logger.warning(
            "governor_blocked_tool",
            extra={"tool": tool_name, "agent": agent_name},
        )
        return {"allowed": False, "reason": f"Tool '{tool_name}' is blocked by policy"}

    # Write tools require approval
    if tool_name in WRITE_TOOLS:
        logger.info(
            "governor_approval_required",
            extra={"tool": tool_name, "agent": agent_name},
        )

        # Create approval record if we have DB access
        if db_factory and services:
            try:
                db = db_factory()
                from src.models.approvals import Approval

                approval = Approval(
                    approval_id=f"apr_{ULID()}",
                    user_id="usr_default",
                    approval_type=f"tool_call:{tool_name}",
                    title=f"Approve: {tool_name}",
                    summary=_summarize_tool_input(tool_name, tool_input),
                    risk_level=_classify_risk(tool_name),
                    status="pending",
                    expires_at=None,
                )
                db.add(approval)
                await db.flush()
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

    # Internal tools (ingest_event, update_execution, etc.) — allow
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
) -> None:
    """Post-tool-use hook: log every tool call to the agent decision log."""
    if db_factory is None:
        return

    try:
        db = db_factory()
        log_entry = AgentDecisionLog(
            log_id=f"adl_{ULID()}",
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
        await db.flush()
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


def _classify_risk(tool_name: str) -> str:
    """Classify the risk level of a tool call."""
    high_risk = {"gmail_send", "gmail_send_email", "github_merge_pr", "slack_post_message"}
    if tool_name in high_risk:
        return "high"
    if tool_name in BLOCKED_TOOLS:
        return "critical"
    return "medium"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
