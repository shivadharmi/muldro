"""Presenter — turns system state into user-facing communication.

The only service that produces user-visible output. All other services
produce internal state; the Presenter transforms that into briefs,
approval prompts, summaries, and Canvas payloads.

Responsibilities:
- Generate daily briefings
- Generate meeting prep cards
- Format approval prompts
- Format execution results
- Adapt output format (chat text vs Canvas JSON)
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.models.approvals import Approval
from src.models.briefings import Briefing
from src.models.events import NormalizedEvent
from src.models.plans import Plan

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM_PROMPT = """\
You are Jarvis's briefing generator. Given structured data about recent events, \
pending approvals, and active plans, produce a concise daily briefing for a busy founder.

You MUST respond with valid JSON matching this schema:
{
  "headline": "One-line summary (e.g. '3 priorities, 2 follow-ups, 1 meeting risk')",
  "top_priorities": [
    {"title": "string", "reason": "why this matters"}
  ],
  "changes_since_last": [
    {"source": "gmail|calendar|slack", "summary": "string", "count": number}
  ],
  "recommended_actions": ["action 1", "action 2", ...],
  "full_text": "3-5 paragraph narrative briefing in markdown"
}

Rules:
- Lead with what matters most
- Keep top_priorities to 3-5 items maximum
- Group changes by source
- Recommended actions should be concrete and actionable
- full_text should be scannable — use bold, bullets, short paragraphs
- If there are no events, say so briefly
"""


class Presenter:
    """Generate user-facing content from internal state."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def generate_briefing(self, user_id: str, briefing_date: date) -> Briefing:
        """Generate or retrieve the daily briefing. Returns Briefing model."""
        existing = await self._db.execute(
            select(Briefing).where(
                Briefing.user_id == user_id,
                Briefing.briefing_date == briefing_date,
            )
        )
        cached = existing.scalar_one_or_none()
        if cached:
            return cached

        context = await self._gather_briefing_data(user_id, briefing_date)
        briefing_content = await self._call_claude(context)

        briefing_id = f"brief_{ULID()}"
        briefing = Briefing(
            briefing_id=briefing_id,
            user_id=user_id,
            briefing_date=briefing_date,
            headline=briefing_content.get("headline"),
            top_priorities=briefing_content.get("top_priorities"),
            changes_since_last=briefing_content.get("changes_since_last"),
            pending_approvals=None,
            recommended_actions=briefing_content.get("recommended_actions"),
            full_text=briefing_content.get("full_text"),
        )

        pending = await self._get_pending_approvals(user_id)
        if pending:
            briefing.pending_approvals = [
                {"approval_id": a.approval_id, "title": a.title} for a in pending
            ]

        self._db.add(briefing)
        await self._db.commit()
        await self._db.refresh(briefing)

        logger.info("Briefing generated: %s for %s", briefing_id, briefing_date)
        return briefing

    async def generate_meeting_prep(self, meeting_id: str, user_id: str) -> dict:
        """Generate meeting preparation content."""
        # TODO: Implement in Sprint 4 (Calendar + Meeting Prep)
        return {}

    async def _gather_briefing_data(self, user_id: str, briefing_date: date) -> str:
        """Compose structured context from events, plans, approvals."""
        lookback = timedelta(hours=self._settings.briefing_lookback_hours)
        start_of_day = datetime.combine(briefing_date, datetime.min.time(), tzinfo=timezone.utc)
        cutoff = start_of_day - lookback

        events = await self._get_recent_events(user_id, cutoff)
        plans = await self._get_active_plans(user_id)
        approvals = await self._get_pending_approvals(user_id)

        sections = [f"Date: {briefing_date.isoformat()}"]

        if events:
            event_lines = []
            for e in events:
                line = (
                    f"- [{e.source}] {e.title or 'Untitled'} "
                    f"(importance: {e.importance_score or 0:.1f}, "
                    f"urgency: {e.urgency_score or 0:.1f})"
                )
                if e.summary:
                    line += f"\n  {e.summary}"
                event_lines.append(line)
            sections.append(f"## Recent Events ({len(events)})\n" + "\n".join(event_lines))
        else:
            sections.append("## Recent Events\nNo events in the lookback window.")

        if plans:
            plan_lines = [f"- {p.goal} (priority: {p.priority}, status: {p.status})" for p in plans]
            sections.append(f"## Active Plans ({len(plans)})\n" + "\n".join(plan_lines))

        if approvals:
            approval_lines = [f"- {a.title} (risk: {a.risk_level})" for a in approvals]
            sections.append(
                f"## Pending Approvals ({len(approvals)})\n" + "\n".join(approval_lines)
            )

        return "\n\n".join(sections)

    async def _get_recent_events(self, user_id: str, cutoff: datetime) -> list[NormalizedEvent]:
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at >= cutoff,
            )
            .order_by(NormalizedEvent.importance_score.desc().nullslast())
            .limit(50)
        )
        return list(result.scalars().all())

    async def _get_active_plans(self, user_id: str) -> list[Plan]:
        result = await self._db.execute(
            select(Plan)
            .where(
                Plan.user_id == user_id,
                Plan.status.in_(["created", "executing"]),
            )
            .order_by(Plan.created_at.desc())
            .limit(20)
        )
        return list(result.scalars().all())

    async def _get_pending_approvals(self, user_id: str) -> list[Approval]:
        result = await self._db.execute(
            select(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.status == "pending",
            )
            .order_by(Approval.created_at.desc())
            .limit(10)
        )
        return list(result.scalars().all())

    async def _call_claude(self, context: str) -> dict:
        """Call Claude to generate briefing content."""
        try:
            response = await self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=2048,
                system=BRIEFING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": context}],
            )
            text = response.content[0].text
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except Exception:
            logger.warning("Briefing generation failed", exc_info=True)
            return {
                "headline": "Unable to generate briefing",
                "top_priorities": [],
                "changes_since_last": [],
                "recommended_actions": ["Check system logs — briefing generation failed."],
                "full_text": "Jarvis was unable to generate today's briefing. "
                "Please check the system.",
            }
