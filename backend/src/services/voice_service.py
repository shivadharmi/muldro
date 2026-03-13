"""Voice Service — TTS-friendly output formatting for Talk Mode.

Responsibilities:
- Convert structured data into voice-optimized prose
- Generate concise spoken-language summaries
- Adapt verbosity based on content priority
- Strip markdown, links, and visual formatting
"""

import json
import logging
import re

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings

logger = logging.getLogger(__name__)

VOICE_SYSTEM_PROMPT = """\
You are Jarvis, a personal AI assistant speaking to a busy founder via voice.

Rules for voice output:
- Use natural spoken language — no markdown, no bullet points, no tables
- Be concise: aim for 30-60 seconds of speech per response
- Lead with the most important information
- Use transitions like "Also", "Next", "Finally" instead of visual structure
- Numbers: say "three" not "3", unless it's a specific figure
- Times: say "two thirty" not "14:30"
- Names: always use first name after introducing someone
- For approvals: clearly state what needs approval and why
- For briefings: prioritize top 2-3 items, mention others as "also on your radar"
- End with a clear question or suggested next action

You MUST respond with valid JSON:
{
  "spoken_text": "The natural spoken response",
  "duration_hint": "short" | "medium" | "long"
}
"""


class VoiceService:
    """Generate voice-optimized responses from structured data."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def to_voice(self, content: str, content_type: str = "general") -> dict:
        """Convert content to voice-friendly format.

        Args:
            content: The structured content to convert (markdown, JSON, etc.)
            content_type: Hint about content type (briefing, approval, task, general)

        Returns:
            Dict with spoken_text and duration_hint.
        """
        prompt = f"Content type: {content_type}\n\n{content}"

        try:
            response = await self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=1024,
                system=VOICE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except Exception:
            logger.warning("Voice conversion failed, using stripped fallback", exc_info=True)
            return {
                "spoken_text": self._strip_to_voice(content),
                "duration_hint": "medium",
            }

    def _strip_to_voice(self, text: str) -> str:
        """Fallback: strip markdown formatting for basic voice readability."""
        # Remove markdown headers
        text = re.sub(r"#{1,6}\s+", "", text)
        # Remove bold/italic markers
        text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
        # Remove bullet points
        text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
        # Remove links, keep text
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        # Remove code blocks
        text = re.sub(r"`{1,3}[^`]*`{1,3}", "", text)
        # Collapse whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
