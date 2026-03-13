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


class Presenter:
    """Generate user-facing content from internal state."""

    async def generate_briefing(self, user_id: str, date_str: str) -> dict:
        """Generate the daily briefing content."""
        # TODO: Implement
        # 1. Fetch events since last briefing
        # 2. Group by project, people, deadlines
        # 3. Fetch pending approvals
        # 4. Retrieve user preferences (concise, detailed, etc.)
        # 5. Call Claude to generate narrative
        # 6. Return structured BriefingResponse
        return {}

    async def generate_meeting_prep(self, meeting_id: str, user_id: str) -> dict:
        """Generate meeting preparation content."""
        # TODO: Implement
        return {}
