"""Inbox Triage workflow — fetch, classify, group, draft, approve, send."""

import logging

from src.workflows.workflow_registry import Workflow, WorkflowStep

logger = logging.getLogger(__name__)


async def fetch_unread(context: dict) -> dict:
    """Fetch unread emails from Gmail connector."""
    # In real implementation, this would call the Gmail connector
    return {"emails": [], "count": 0}


async def classify_emails(context: dict) -> dict:
    """Classify emails by urgency and type."""
    emails = context.get("emails", [])
    return {
        "classified": [],
        "urgent_count": 0,
        "total": len(emails),
    }


async def group_emails(context: dict) -> dict:
    """Group emails by thread/topic."""
    return {"groups": [], "group_count": 0}


async def draft_responses(context: dict) -> dict:
    """Draft responses for emails that need replies."""
    return {"drafts": [], "draft_count": 0}


async def send_approved(context: dict) -> dict:
    """Send approved draft responses."""
    return {"sent_count": 0}


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
