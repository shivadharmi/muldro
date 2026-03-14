"""OpenClaw client — communicates with the OpenClaw gateway via its
OpenAI-compatible HTTP API (/v1/chat/completions).

Allows the Jarvis backend to proactively communicate with the OpenClaw agent:
- Wake the agent with system messages (briefings ready, approvals needed)
- Run agent turns for scheduled tasks (observations, meeting prep, etc.)
- Delegate task execution to the agent (which has gog, gh, message, etc.)
"""

import logging

import httpx

from src.config.settings import Settings
from src.services.retry import retry_async

logger = logging.getLogger(__name__)


class OpenClawClient:
    """Communicates with OpenClaw gateway via /v1/chat/completions."""

    def __init__(self, settings: Settings):
        self._base_url = settings.openclaw_gateway_url
        self._token = settings.openclaw_gateway_token

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
    async def run_agent_turn(
        self,
        message: str,
        agent_id: str = "main",
        timeout: float = 120.0,
    ) -> dict:
        """Run an agent turn via the OpenAI-compatible chat completions API.

        Sends a message to the OpenClaw agent and waits for the full response.
        Used for scheduled tasks, delegated execution, and system notifications.
        """
        payload = {
            "model": f"openclaw:{agent_id}",
            "messages": [{"role": "user", "content": message}],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            content = ""
            if result.get("choices"):
                content = result["choices"][0].get("message", {}).get("content", "")
            logger.info(
                "Agent turn completed (%d chars) for: %s",
                len(content),
                message[:100],
            )
            return result

    async def wake_agent(self, message: str) -> dict:
        """Send a system message to the agent.

        Uses the chat completions API with a system-prefixed message.
        The agent's SOUL.md instructs it to deliver important items to the user.
        """
        return await self.run_agent_turn(message)

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
