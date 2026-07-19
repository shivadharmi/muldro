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

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.llm.utility import complete_text
from src.models.approvals import Approval
from src.models.briefings import Briefing
from src.models.entities import Entity
from src.models.events import NormalizedEvent
from src.models.memory import Memory
from src.models.plans import Plan
from src.services.integration_status import get_integration_statuses

logger = logging.getLogger(__name__)

BRIEFING_JSON_SCHEMA = """\
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
- The "Connected Integrations" section lists the user's real data sources and
  their live connection state. When an integration is CONNECTED but produced no
  events, treat it as a genuinely quiet period — do NOT suggest the user verify,
  reconnect, or check whether their integrations are connected. Only recommend
  verifying/reconnecting an integration that is explicitly listed as NOT
  CONNECTED. Never speculate that "nothing may be connected" when the section
  shows connected sources.
"""

BRIEFING_STYLE_PROMPTS: dict[str, str] = {
    "founder": (
        "You are Jarvis's briefing generator. Given structured data about recent events, "
        "pending approvals, and active plans, produce a concise daily briefing for a busy founder. "
        "Prioritize revenue-impacting items, investor relations, and team blockers.\n\n"
        + BRIEFING_JSON_SCHEMA
    ),
    "personal": (
        "You are Jarvis's briefing generator. Given structured data about recent events "
        "and plans, produce a friendly daily briefing for personal life management. "
        "Prioritize health, family, finances, and personal goals. "
        "Keep the tone warm and supportive.\n\n" + BRIEFING_JSON_SCHEMA
    ),
    "academic": (
        "You are Jarvis's briefing generator. Given structured data about recent events "
        "and plans, produce a daily briefing for an academic or researcher. "
        "Prioritize deadlines, publications, collaborations, and research milestones.\n\n"
        + BRIEFING_JSON_SCHEMA
    ),
    "general": (
        "You are Jarvis's briefing generator. Given structured data about recent events, "
        "pending approvals, and active plans, produce a concise daily briefing. "
        "Adapt priority grouping to whatever matters most in the data.\n\n" + BRIEFING_JSON_SCHEMA
    ),
}

# Default used when no style preference is set
BRIEFING_SYSTEM_PROMPT = BRIEFING_STYLE_PROMPTS["general"]


MEETING_PREP_SYSTEM_PROMPT = """\
You are Jarvis's meeting preparation engine. Given structured data about an \
upcoming meeting (attendees, related emails, memories, entity info), produce \
a comprehensive meeting prep document for a busy founder.

You MUST respond with valid JSON matching this schema:
{
  "agenda": ["topic 1", "topic 2", ...],
  "attendee_briefs": [
    {
      "name": "string",
      "email": "string",
      "role": "string or null",
      "recent_context": "what you know about recent interactions"
    }
  ],
  "related_threads": [
    {"title": "email/event title", "summary": "brief summary", "event_id": "string"}
  ],
  "action_items": [
    {"description": "string", "owner": "string or null", "priority": "high|medium|low"}
  ],
  "risks": ["potential risk or concern"],
  "talking_points": ["key point to raise"]
}

Rules:
- Infer agenda from title, description, attendees, and related threads
- Keep attendee_briefs focused on what matters for this meeting
- Related threads should surface emails/events involving the same people
- Action items should be concrete and specific
- Risks: scheduling conflicts, missing context, unresolved issues
- Talking points: 3-5 max, most important first
"""


VIEW_TYPE_MAP: dict[str, str] = {
    "draft_email": "approval_panel",
    "send_email": "approval_panel",
    "research": "research_report",
    "meeting_prep": "meeting_prep",
    "create_event": "approval_panel",
    "inbox_triage": "inbox_triage",
    "post_message": "approval_panel",
    "briefing": "briefing_full",
    "general": "detail_card",
}


class Presenter:
    """Generate user-facing content from internal state."""

    def __init__(self, settings: Settings, db: AsyncSession, notifier=None):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._notifier = notifier

    @staticmethod
    def select_view(
        task_type: str | None = None,
        output: dict | None = None,
    ) -> str:
        """Select the best A2UI view type for a task output.

        Returns a view_type string used by the frontend renderer.
        """
        if task_type and task_type in VIEW_TYPE_MAP:
            return VIEW_TYPE_MAP[task_type]
        if output and output.get("requires_approval"):
            return "approval_panel"
        return "detail_card"

    async def _get_briefing_style(self, user_id: str) -> str:
        """Look up user's preferred briefing style from settings."""
        try:
            from src.services.settings_service import SettingsService

            svc = SettingsService(self._db)
            style = await svc.get(user_id, "presentation", "briefing_style")
            if style and style in BRIEFING_STYLE_PROMPTS:
                return style
        except Exception:
            logger.debug("Failed to load briefing style preference", exc_info=True)
        return "general"

    async def generate_briefing(
        self, user_id: str, briefing_date: date, workspace_id: str = ""
    ) -> Briefing:
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

        context = await self._gather_briefing_data(
            user_id,
            briefing_date,
            workspace_id=workspace_id,
        )
        style = await self._get_briefing_style(user_id)
        briefing_content = await self._call_claude(context, style=style)

        briefing_id = f"brief_{ULID()}"
        briefing = Briefing(
            briefing_id=briefing_id,
            user_id=user_id,
            workspace_id=workspace_id,
            briefing_date=briefing_date,
            headline=briefing_content.get("headline"),
            top_priorities=briefing_content.get("top_priorities"),
            changes_since_last=briefing_content.get("changes_since_last"),
            pending_approvals=None,
            recommended_actions=briefing_content.get("recommended_actions"),
            full_text=briefing_content.get("full_text"),
        )

        pending = await self._get_pending_approvals(user_id, workspace_id=workspace_id)
        if pending:
            briefing.pending_approvals = [
                {"approval_id": a.approval_id, "title": a.title} for a in pending
            ]

        self._db.add(briefing)
        await self._db.commit()
        await self._db.refresh(briefing)

        logger.info("Briefing generated: %s for %s", briefing_id, briefing_date)

        # Delivery (notification + surface push) is owned solely by the
        # orchestrator's generate_briefing path so there is exactly one
        # notification + one surface per (user, date). The Presenter only
        # builds and caches the Briefing row. See orchestrator.generate_briefing.
        return briefing

    async def generate_meeting_prep(
        self, meeting_id: str, user_id: str, next_meeting: bool = False, workspace_id: str = ""
    ) -> dict:
        """Generate meeting preparation content.

        If next_meeting is True, finds the next upcoming calendar event.
        Otherwise, looks up by meeting_id (which is the calendar_event entity_id).
        """
        meeting_event = await self._find_meeting_event(
            user_id,
            meeting_id,
            next_meeting,
            workspace_id=workspace_id,
        )
        if not meeting_event:
            return {
                "meeting_id": meeting_id or "none",
                "title": "Meeting not found",
                "attendees": [],
                "agenda": [],
                "related_threads": [],
                "action_items": [],
                "risks": ["Could not find the specified meeting."],
            }

        context = await self._gather_meeting_context(
            user_id,
            meeting_event,
            workspace_id=workspace_id,
        )
        prep = await self._call_meeting_prep(context)

        return {
            "meeting_id": meeting_event.event_id,
            "title": meeting_event.title or "Untitled Meeting",
            "starts_at": (
                meeting_event.occurred_at.isoformat() if meeting_event.occurred_at else None
            ),
            "attendees": prep.get("attendee_briefs", []),
            "agenda": prep.get("agenda", []),
            "related_threads": prep.get("related_threads", []),
            "action_items": prep.get("action_items", []),
            "risks": prep.get("risks", []),
            "talking_points": prep.get("talking_points", []),
        }

    async def _gather_briefing_data(
        self, user_id: str, briefing_date: date, workspace_id: str = ""
    ) -> str:
        """Compose structured context from events, plans, approvals."""
        lookback = timedelta(hours=self._settings.briefing_lookback_hours)
        start_of_day = datetime.combine(briefing_date, datetime.min.time(), tzinfo=timezone.utc)
        cutoff = start_of_day - lookback

        events = await self._get_recent_events(user_id, cutoff, workspace_id=workspace_id)
        plans = await self._get_active_plans(user_id, workspace_id=workspace_id)
        approvals = await self._get_pending_approvals(user_id, workspace_id=workspace_id)
        upcoming_meetings = await self._get_upcoming_meetings(user_id, workspace_id=workspace_id)

        sections = [f"Date: {briefing_date.isoformat()}"]

        connection_section = await self._build_connection_section(user_id, workspace_id)
        if connection_section:
            sections.append(connection_section)

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

        if upcoming_meetings:
            meeting_lines = []
            for m in upcoming_meetings:
                time_str = m.occurred_at.strftime("%H:%M") if m.occurred_at else "TBD"
                line = f"- {time_str} — {m.title or 'Untitled Meeting'}"
                if m.summary:
                    line += f"\n  {m.summary[:100]}"
                meeting_lines.append(line)
            sections.append(
                f"## Upcoming Meetings ({len(upcoming_meetings)})\n" + "\n".join(meeting_lines)
            )

        if plans:
            plan_lines = [f"- {p.goal} (priority: {p.priority}, status: {p.status})" for p in plans]
            sections.append(f"## Active Plans ({len(plans)})\n" + "\n".join(plan_lines))

        if approvals:
            approval_lines = [f"- {a.title} (risk: {a.risk_level})" for a in approvals]
            sections.append(
                f"## Pending Approvals ({len(approvals)})\n" + "\n".join(approval_lines)
            )

        return "\n\n".join(sections)

    async def _build_connection_section(self, user_id: str, workspace_id: str) -> str:
        """Render real integration connection status so the LLM can distinguish a
        quiet-but-connected day from an actual disconnection (fixes the false
        "verify your integrations" subtitle on zero-event days).
        """
        try:
            statuses = await get_integration_statuses(self._db, user_id, workspace_id)
        except Exception:
            logger.debug("Failed to load integration status for briefing", exc_info=True)
            return ""

        if not statuses:
            return ""

        connected = [s for s in statuses if s.enabled and s.connected]
        disconnected = [s for s in statuses if s.enabled and not s.connected]

        lines: list[str] = []
        if connected:
            names = ", ".join(f"{s.display_name} (connected)" for s in connected)
            lines.append(f"## Connected Integrations\n{names}")
        if disconnected:
            names = ", ".join(f"{s.display_name} (not connected)" for s in disconnected)
            lines.append(f"## Not Connected\n{names}")

        return "\n\n".join(lines)

    async def _get_recent_events(
        self, user_id: str, cutoff: datetime, workspace_id: str = ""
    ) -> list[NormalizedEvent]:
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.workspace_id == workspace_id,
                NormalizedEvent.occurred_at >= cutoff,
            )
            .order_by(NormalizedEvent.importance_score.desc().nullslast())
            .limit(50)
        )
        return list(result.scalars().all())

    async def _get_active_plans(self, user_id: str, workspace_id: str = "") -> list[Plan]:
        # Bound by the plan TTL so a stale plan the heartbeat reaper has not yet
        # collected can never be surfaced in a briefing as if it were actionable
        # today (regression: a stuck 'created' plan appeared as "1 critical
        # security alert requires immediate attention" in every briefing).
        ttl_hours = getattr(self._settings, "plan_ttl_hours", 72)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
        result = await self._db.execute(
            select(Plan)
            .where(
                Plan.user_id == user_id,
                Plan.workspace_id == workspace_id,
                Plan.status.in_(["created", "executing"]),
                Plan.created_at >= cutoff,
            )
            .order_by(Plan.created_at.desc())
            .limit(20)
        )
        return list(result.scalars().all())

    async def _get_pending_approvals(self, user_id: str, workspace_id: str = "") -> list[Approval]:
        result = await self._db.execute(
            select(Approval)
            .where(
                Approval.user_id == user_id,
                Approval.workspace_id == workspace_id,
                Approval.status == "pending",
            )
            .order_by(Approval.created_at.desc())
            .limit(10)
        )
        return list(result.scalars().all())

    async def _get_upcoming_meetings(
        self, user_id: str, limit: int = 10, workspace_id: str = ""
    ) -> list[NormalizedEvent]:
        """Get upcoming calendar events for today and tomorrow."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=36)
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.workspace_id == workspace_id,
                NormalizedEvent.source == "calendar",
                NormalizedEvent.occurred_at >= now,
                NormalizedEvent.occurred_at <= end,
            )
            .order_by(NormalizedEvent.occurred_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _find_meeting_event(
        self,
        user_id: str,
        meeting_id: str | None,
        next_meeting: bool,
        workspace_id: str = "",
    ) -> NormalizedEvent | None:
        """Find a calendar event by ID or get the next upcoming one."""
        if next_meeting:
            result = await self._db.execute(
                select(NormalizedEvent)
                .where(
                    NormalizedEvent.user_id == user_id,
                    NormalizedEvent.workspace_id == workspace_id,
                    NormalizedEvent.source == "calendar",
                    NormalizedEvent.occurred_at >= datetime.now(timezone.utc),
                )
                .order_by(NormalizedEvent.occurred_at.asc())
                .limit(1)
            )
            return result.scalar_one_or_none()

        if meeting_id:
            result = await self._db.execute(
                select(NormalizedEvent).where(
                    NormalizedEvent.user_id == user_id,
                    NormalizedEvent.event_id == meeting_id,
                    NormalizedEvent.source == "calendar",
                )
            )
            evt = result.scalar_one_or_none()
            if evt:
                return evt

            # Also try matching by entity_id (calendar_event_id)
            result = await self._db.execute(
                select(NormalizedEvent).where(
                    NormalizedEvent.user_id == user_id,
                    NormalizedEvent.entity_id == meeting_id,
                    NormalizedEvent.source == "calendar",
                )
            )
            return result.scalar_one_or_none()

        return None

    async def _gather_meeting_context(
        self, user_id: str, meeting: NormalizedEvent, workspace_id: str = ""
    ) -> str:
        """Compose structured context for meeting prep."""
        sections = [
            f"Meeting: {meeting.title or 'Untitled'}",
            f"Time: {meeting.occurred_at.isoformat() if meeting.occurred_at else 'unknown'}",
        ]

        if meeting.summary:
            sections.append(f"Description: {meeting.summary}")

        # Extract attendee emails from actor_entities
        attendee_emails = []
        if meeting.actor_entities:
            for actor in meeting.actor_entities:
                if isinstance(actor, dict) and actor.get("email"):
                    attendee_emails.append(actor["email"])

        # Get attendee info from entities
        attendee_info = await self._get_attendee_entities(
            user_id,
            attendee_emails,
            workspace_id=workspace_id,
        )
        if attendee_info:
            att_lines = []
            for att in attendee_info:
                line = f"- {att['canonical_name']} ({att['entity_type']})"
                if att.get("attributes"):
                    attrs = att["attributes"]
                    if attrs.get("role"):
                        line += f" — {attrs['role']}"
                    if attrs.get("company"):
                        line += f" at {attrs['company']}"
                att_lines.append(line)
            sections.append("## Known Attendees\n" + "\n".join(att_lines))

        # Find related events (same attendees, recent)
        related = await self._get_related_events(
            user_id,
            attendee_emails,
            meeting.event_id,
            workspace_id=workspace_id,
        )
        if related:
            rel_lines = [
                f"- [{e.source}] {e.title or 'Untitled'}: {e.summary or 'no summary'}"
                for e in related
            ]
            sections.append(f"## Related Events ({len(related)})\n" + "\n".join(rel_lines))

        # Find relevant memories about attendees
        memories = await self._get_attendee_memories(
            user_id,
            attendee_emails,
            workspace_id=workspace_id,
        )
        if memories:
            mem_lines = [f"- {m.fact_text}" for m in memories]
            sections.append(f"## Relevant Memories ({len(memories)})\n" + "\n".join(mem_lines))

        return "\n\n".join(sections)

    async def _get_attendee_entities(
        self, user_id: str, emails: list[str], workspace_id: str = ""
    ) -> list[dict]:
        """Look up entity info for attendee emails."""
        if not emails:
            return []

        from src.models.entities import EntityAlias

        result = await self._db.execute(
            select(Entity).where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
                Entity.entity_id.in_(
                    select(EntityAlias.entity_id).where(EntityAlias.alias.in_(emails))
                ),
            )
        )
        entities = result.scalars().all()
        return [
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "canonical_name": e.canonical_name,
                "attributes": e.attributes,
            }
            for e in entities
        ]

    async def _get_related_events(
        self,
        user_id: str,
        attendee_emails: list[str],
        exclude_event_id: str,
        workspace_id: str = "",
    ) -> list[NormalizedEvent]:
        """Find recent events involving the same attendees."""
        if not attendee_emails:
            return []

        lookback = datetime.now(timezone.utc) - timedelta(days=7)
        # Search for events mentioning attendee emails in summary or title
        from sqlalchemy import or_

        conditions = [NormalizedEvent.summary.ilike(f"%{email}%") for email in attendee_emails[:5]]

        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.workspace_id == workspace_id,
                NormalizedEvent.event_id != exclude_event_id,
                NormalizedEvent.occurred_at >= lookback,
                or_(*conditions),
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(10)
        )
        return list(result.scalars().all())

    async def _get_attendee_memories(
        self, user_id: str, emails: list[str], workspace_id: str = ""
    ) -> list:
        """Find memories mentioning attendee emails."""
        if not emails:
            return []

        from sqlalchemy import or_

        conditions = [Memory.fact_text.ilike(f"%{email}%") for email in emails[:5]]

        result = await self._db.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
                or_(*conditions),
            )
            .order_by(Memory.confidence.desc())
            .limit(10)
        )
        return list(result.scalars().all())

    async def _call_meeting_prep(self, context: str) -> dict:
        """Call Claude to generate meeting prep content."""
        try:
            text = await complete_text(
                system=MEETING_PREP_SYSTEM_PROMPT,
                user=context,
                tier="resolved",
                max_tokens=2048,
            )
            from src.llm_utils import parse_llm_json

            return parse_llm_json(text)
        except Exception:
            logger.warning("Meeting prep generation failed", exc_info=True)
            return {
                "agenda": [],
                "attendee_briefs": [],
                "related_threads": [],
                "action_items": [],
                "risks": ["Meeting prep generation failed."],
                "talking_points": [],
            }

    async def _call_claude(self, context: str, style: str = "general") -> dict:
        """Call Claude to generate briefing content."""
        system_prompt = BRIEFING_STYLE_PROMPTS.get(style, BRIEFING_SYSTEM_PROMPT)
        try:
            text = await complete_text(
                system=system_prompt,
                user=context,
                tier="resolved",
                max_tokens=2048,
            )
            from src.llm_utils import parse_llm_json

            return parse_llm_json(text)
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
