"""OpenClaw client — two-way bridge to the OpenClaw gateway.

Allows the Jarvis backend to proactively communicate with the OpenClaw agent:
- Wake the agent with system messages (briefings ready, approvals needed)
- Delegate task execution to the agent (which has gog, gh, message, etc.)
"""

import logging

import httpx

from src.config.settings import Settings
from src.services.retry import retry_async

logger = logging.getLogger(__name__)


class OpenClawClient:
    """Two-way bridge to OpenClaw gateway."""

    def __init__(self, settings: Settings):
        self._base_url = settings.openclaw_gateway_url
        self._token = settings.openclaw_hook_token

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @retry_async(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=(httpx.ConnectError, httpx.TimeoutException),
    )
    async def wake_agent(self, message: str, session_key: str = "hook:jarvis") -> dict:
        """POST /hooks/wake — enqueue system event to main session.

        Wakes the agent with a message (e.g. "Daily briefing ready",
        "Approval needed: draft reply to investor").
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/hooks/wake",
                headers=self._headers(),
                json={"message": message, "sessionKey": session_key},
            )
            response.raise_for_status()
            result = response.json()
            logger.info("Agent woken: %s", message[:100])
            return result

    @retry_async(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=(httpx.ConnectError, httpx.TimeoutException),
    )
    async def run_agent_turn(
        self,
        message: str,
        agent_id: str = "jarvis",
        deliver: str | None = None,
    ) -> dict:
        """POST /hooks/agent — run isolated agent turn.

        Used to delegate execution tasks (draft email via gog, send message, etc.)
        The agent runs in an isolated session and returns the result.
        """
        payload: dict = {"message": message, "agentId": agent_id}
        if deliver:
            payload["deliver"] = deliver

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._base_url}/hooks/agent",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            logger.info("Agent turn completed for: %s", message[:100])
            return result

    async def delegate_task(self, task_type: str, instructions: str, context: dict) -> dict:
        """Delegate a task to the OpenClaw agent for execution.

        Agent has access to gog, gh, message, browser, etc.
        Backend tracks the execution state; agent does the actual work.
        """
        message_parts = [
            f"Execute task: {task_type}",
            f"Instructions: {instructions}",
        ]
        if context.get("recipient"):
            message_parts.append(f"Recipient: {context['recipient']}")
        if context.get("tone"):
            message_parts.append(f"Tone: {context['tone']}")
        if context.get("background"):
            message_parts.append(f"Background: {context['background']}")

        message = "\n".join(message_parts)
        return await self.run_agent_turn(message)
