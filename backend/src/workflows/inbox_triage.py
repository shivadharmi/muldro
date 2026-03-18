"""Inbox Triage workflow — fetch, classify, group, draft, approve, send.

Uses Google Workspace MCP server (preferred) or Gmail connector fallback
for real email operations, and Claude for classification and draft generation.
"""

import json
import logging

from src.workflows.workflow_registry import Workflow, WorkflowStep

logger = logging.getLogger(__name__)

CLASSIFY_PROMPT = """\
Classify each email into one of these categories:
- action_required: needs a reply or follow-up action
- info_only: FYI, no action needed
- meeting_related: calendar invites, meeting notes
- automated: newsletters, notifications, marketing

Also assign urgency (high, medium, low) and a one-line summary.

Respond with JSON array:
[{"message_id": "...", "category": "...", "urgency": "...", "summary": "..."}]
"""

DRAFT_PROMPT = """\
You are Jarvis drafting email replies for a busy founder. For each email
that needs a reply, generate a concise, professional response.

Respond with JSON array:
[{
  "message_id": "...",
  "thread_id": "...",
  "to": "sender email",
  "subject": "Re: original subject",
  "body": "reply text",
  "tone": "professional|casual|urgent"
}]

Rules:
- Keep replies concise (2-4 sentences)
- Match the tone of the original
- Include a clear call to action when appropriate
"""


async def _list_unread_via_mcp(max_results: int = 20) -> dict | None:
    """Try listing unread emails via Google Workspace MCP server."""
    from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

    # Google Workspace MCP namespaces tools as google_workspace_<tool>
    for tool_name in (
        "google_workspace_gmail_list_unread",
        "google-workspace_gmail_list_unread",
        "gmail_list_unread",
    ):
        if is_mcp_tool(tool_name):
            return await call_mcp_tool(tool_name, {"max_results": max_results})
    return None


async def _send_via_mcp(to: str, subject: str, body: str, thread_id: str | None) -> dict | None:
    """Try sending email via Google Workspace MCP server."""
    from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

    for tool_name in (
        "google_workspace_gmail_send_email",
        "google-workspace_gmail_send_email",
        "gmail_send_email",
    ):
        if is_mcp_tool(tool_name):
            params = {"to": to, "subject": subject, "body": body}
            if thread_id:
                params["thread_id"] = thread_id
            return await call_mcp_tool(tool_name, params)
    return None


async def fetch_unread(context: dict) -> dict:
    """Fetch unread emails — MCP first, connector fallback."""
    max_results = context.get("max_results", 20)

    # Try MCP bridge
    mcp_result = await _list_unread_via_mcp(max_results)
    if mcp_result and mcp_result.get("status") == "ok":
        logger.info("Fetched unread emails via MCP bridge")
        return {
            "emails": mcp_result.get("emails", []),
            "count": len(mcp_result.get("emails", [])),
            "source": "mcp",
        }

    # Fallback to direct connector
    from src.connectors.base import CONNECTOR_REGISTRY

    credentials = context.get("credentials", {})
    settings = context.get("settings")

    gmail_cls = CONNECTOR_REGISTRY.get("gmail")
    if not gmail_cls:
        return {"emails": [], "count": 0, "error": "No Gmail source available"}

    connector = gmail_cls(settings) if settings else gmail_cls.__new__(gmail_cls)
    result = await connector.execute_action(
        "list_unread", {"max_results": max_results}, credentials
    )

    if result.get("status") != "ok":
        return {"emails": [], "count": 0, "error": result.get("error")}

    return {
        "emails": result.get("emails", []),
        "count": result.get("count", 0),
        "source": "connector",
    }


async def classify_emails(context: dict) -> dict:
    """Classify emails by urgency and type using Claude."""
    emails = context.get("emails", [])
    if not emails:
        return {"classified": [], "urgent_count": 0, "total": 0}

    settings = context.get("settings")
    if not settings:
        return {"classified": [], "urgent_count": 0, "total": len(emails)}

    from src.config.settings import get_anthropic_client

    client = get_anthropic_client(settings)

    # Build email summaries for classification
    email_summaries = []
    for e in emails[:30]:  # cap at 30 for context window
        email_summaries.append(
            f"ID: {e['message_id']}\n"
            f"From: {e.get('from', '')}\n"
            f"Subject: {e.get('subject', '')}\n"
            f"Snippet: {e.get('snippet', '')[:200]}"
        )

    try:
        response = await client.messages.create(
            model=settings.resolved_model,
            max_tokens=2048,
            system=CLASSIFY_PROMPT,
            messages=[{"role": "user", "content": "\n---\n".join(email_summaries)}],
        )
        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        classified = json.loads(text)
    except Exception:
        logger.warning("Email classification failed", exc_info=True)
        classified = [
            {
                "message_id": e["message_id"],
                "category": "info_only",
                "urgency": "low",
                "summary": e.get("subject", ""),
            }
            for e in emails
        ]

    urgent_count = sum(1 for c in classified if c.get("urgency") == "high")

    return {
        "classified": classified,
        "urgent_count": urgent_count,
        "total": len(classified),
        "action_required": [c for c in classified if c.get("category") == "action_required"],
    }


async def group_emails(context: dict) -> dict:
    """Group emails by thread/topic."""
    classified = context.get("classified", [])
    emails = context.get("emails", [])

    email_by_id = {e["message_id"]: e for e in emails}
    threads: dict[str, list[dict]] = {}

    for item in classified:
        msg_id = item.get("message_id", "")
        email = email_by_id.get(msg_id, {})
        thread_id = email.get("thread_id", msg_id)
        threads.setdefault(thread_id, []).append({**item, **email})

    groups = [
        {
            "thread_id": tid,
            "subject": msgs[0].get("subject", ""),
            "count": len(msgs),
            "messages": msgs,
            "has_action_required": any(m.get("category") == "action_required" for m in msgs),
        }
        for tid, msgs in threads.items()
    ]

    groups.sort(key=lambda g: (not g["has_action_required"], -g["count"]))

    return {"groups": groups, "group_count": len(groups)}


async def draft_responses(context: dict) -> dict:
    """Draft responses for emails that need replies using Claude."""
    action_required = context.get("action_required", [])
    emails = context.get("emails", [])

    if not action_required:
        return {"drafts": [], "draft_count": 0}

    settings = context.get("settings")
    if not settings:
        return {"drafts": [], "draft_count": 0}

    from src.config.settings import get_anthropic_client

    client = get_anthropic_client(settings)

    email_by_id = {e["message_id"]: e for e in emails}

    to_draft = []
    for item in action_required[:10]:
        msg_id = item.get("message_id", "")
        email = email_by_id.get(msg_id, {})
        to_draft.append(
            f"ID: {msg_id}\n"
            f"Thread: {email.get('thread_id', '')}\n"
            f"From: {email.get('from', '')}\n"
            f"Subject: {email.get('subject', '')}\n"
            f"Snippet: {email.get('snippet', '')}\n"
            f"Classification: {item.get('summary', '')}"
        )

    try:
        response = await client.messages.create(
            model=settings.resolved_model,
            max_tokens=4096,
            system=DRAFT_PROMPT,
            messages=[{"role": "user", "content": "\n---\n".join(to_draft)}],
        )
        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        drafts = json.loads(text)
    except Exception:
        logger.warning("Draft generation failed", exc_info=True)
        drafts = []

    return {"drafts": drafts, "draft_count": len(drafts)}


async def send_approved(context: dict) -> dict:
    """Send approved draft responses — MCP first, connector fallback."""
    drafts = context.get("drafts", [])
    approved = context.get("approved_draft_ids", [])
    credentials = context.get("credentials", {})
    settings = context.get("settings")

    if not drafts or not approved:
        return {"sent_count": 0, "skipped": len(drafts)}

    sent = 0
    errors = []
    for draft in drafts:
        msg_id = draft.get("message_id", "")
        if msg_id not in approved:
            continue

        to = draft.get("to", "")
        subject = draft.get("subject", "")
        body = draft.get("body", "")
        thread_id = draft.get("thread_id")

        # Try MCP bridge first
        mcp_result = await _send_via_mcp(to, subject, body, thread_id)
        if mcp_result and mcp_result.get("status") == "ok":
            sent += 1
            continue

        # Fallback to connector
        from src.connectors.base import CONNECTOR_REGISTRY

        gmail_cls = CONNECTOR_REGISTRY.get("gmail")
        if not gmail_cls:
            errors.append({"message_id": msg_id, "error": "No Gmail source available"})
            continue

        connector = gmail_cls(settings) if settings else gmail_cls.__new__(gmail_cls)
        result = await connector.execute_action(
            "send_email",
            {"to": to, "subject": subject, "body": body, "thread_id": thread_id},
            credentials,
        )

        if result.get("status") == "ok":
            sent += 1
        else:
            errors.append({"message_id": msg_id, "error": result.get("error")})

    return {"sent_count": sent, "errors": errors}


inbox_triage_workflow = Workflow(
    name="inbox_triage",
    description="Fetch unread emails, classify, group, draft responses, and send after approval",
    steps=[
        WorkflowStep(name="fetch_unread", handler=fetch_unread),
        WorkflowStep(name="classify_emails", handler=classify_emails),
        WorkflowStep(name="group_emails", handler=group_emails),
        WorkflowStep(name="draft_responses", handler=draft_responses),
        WorkflowStep(name="send_approved", handler=send_approved, requires_approval=True),
    ],
    tags=["email", "triage"],
)
