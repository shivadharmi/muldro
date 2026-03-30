"""Hooks for the Jarvis orchestrator.

Governor policy hook: Intercepts tool calls to enforce approval policies.
Audit hook: Logs every tool call for observability.
"""

import logging
import re

from ulid import ULID

from src.models.agent_decision_log import AgentDecisionLog

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
    """Pre-tool-use hook: enforce Governor policy before external writes.

    Args:
        trust_tier: Trust tier of the MCP server (T0-T3). Passed from
            the capability resolver for trust-aware classification.
        run_id: Current TaskRun ID (if available) — links tool-level
            approvals to execution context for resume after approval.

    Returns:
        {"allowed": True} to proceed
        {"allowed": False, "reason": "..."} to block
        {"allowed": False, "approval_required": True, "approval_id": "..."} for approval gate
    """
    # Classify tool via registry (DB-first, catalog fallback)
    is_blocked = False
    is_write = False
    risk_level = "low"

    if db_factory:
        try:
            from src.services.tool_registry import ToolRegistry

            async with db_factory() as db:
                registry = ToolRegistry(db)
                tool_def = await registry.get_tool(tool_name)
                if tool_def:
                    is_blocked = not tool_def.enabled
                    is_write = tool_def.requires_approval
                    risk_level = tool_def.risk_level
        except Exception:
            pass

    # Catalog fallback when DB unavailable or tool not found in DB
    if not is_blocked and not is_write:
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS

        for t in INTERNAL_TOOLS:
            if t.name == tool_name:
                is_write = t.requires_approval
                risk_level = t.risk_level
                break
        else:
            for s in EXTERNAL_TOOL_SEEDS:
                if s.name == tool_name:
                    is_write = s.requires_approval
                    risk_level = s.risk_level
                    break

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
        if db_factory:
            try:
                from src.services.approval_service import create_approval

                async with db_factory() as db:
                    approval = await create_approval(
                        db,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        approval_type=f"tool_call:{tool_name}",
                        title=f"Approve: {tool_name}",
                        summary=_summarize_tool_input(tool_name, tool_input),
                        risk_level=risk_level,
                        requested_by=user_id,
                        run_id=run_id,
                        artifact_refs={
                            "tool_name": tool_name,
                            "tool_params": tool_input,
                        },
                    )
                    await db.commit()

                    # Notify user about pending approval
                    notifier = getattr(services, "notifier", None) if services else None
                    if notifier:
                        try:
                            await notifier.notify(
                                user_id=user_id,
                                notification_type="approval_request",
                                title=f"Approve: {tool_name}",
                                body=_summarize_tool_input(tool_name, tool_input),
                                data={
                                    "approval_id": approval.approval_id,
                                    "risk_level": risk_level,
                                },
                                workspace_id=workspace_id,
                            )
                        except Exception:
                            logger.warning(
                                "Failed to notify for approval %s",
                                approval.approval_id,
                                exc_info=True,
                            )

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
                input_summary=_truncate(_sanitize_secrets(str(tool_input)), 500),
                output_summary=_truncate(_sanitize_secrets(str(tool_result)), 500),
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


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
