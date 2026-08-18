"""Layer 8: Chat-driven flows.

Goals, tasks, and schedules no longer have dedicated CRUD endpoints — they are
created via natural-language intent through the chat orchestrator (chat → Planner
→ internal creation). These tests exercise that surviving path end-to-end against
the live multi-agent loop, replacing the deleted REST coverage.

Each test makes a real LLM call, so assertions are kept robust: the contract is
that the chat path *accepts and responds* to the intent and drives the loop
(intent → plan → response) without surfacing an error frame. Deep assertions on
non-deterministic artifacts are intentionally avoided.
"""

import json

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


async def _chat(client: httpx.AsyncClient, message: str) -> dict:
    """Send one chat message over SSE and collect the typed events.

    Returns {"events": [...], "response": str|None, "error": dict|None}.
    """
    events: list[str] = []
    response: str | None = None
    error: dict | None = None
    last_event: str | None = None

    async with client.stream(
        "POST", "/v1/muldro/chat", json={"message": message, "surface": "web"}, timeout=120.0
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                last_event = line.split(":", 1)[1].strip()
                events.append(last_event)
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                if last_event == "response":
                    try:
                        response = json.loads(payload).get("text", payload)
                    except json.JSONDecodeError:
                        response = payload
                elif last_event == "error":
                    try:
                        error = json.loads(payload)
                    except json.JSONDecodeError:
                        error = {"raw": payload}

    return {"events": events, "response": response, "error": error}


class TestChatLoop:
    """The core perceive → plan → respond loop over the live stack."""

    async def test_chat_drives_loop_to_response(self, client: httpx.AsyncClient):
        result = await _chat(client, "Hello Muldro — reply in one short sentence.")
        assert result["error"] is None, f"chat surfaced an error: {result['error']}"
        # The orchestrator classifies intent and emits a final response.
        assert "intent" in result["events"]
        assert "response" in result["events"]
        assert result["response"]
        assert "done" in result["events"]

    async def test_chat_reflects_in_loop_health(self, client: httpx.AsyncClient):
        """A chat turn moves the loop-health budget/call counters."""
        await _chat(client, "What's one thing you can help me with today?")
        health = await client.get("/v1/health/loop")
        assert health.status_code == 200
        data = health.json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        # At least one model call has been billed this session.
        assert data["budget"]["total_calls_today"] >= 1


class TestChatDrivenCreation:
    """Goals / tasks / schedules are created via chat intent (no CRUD API)."""

    async def test_chat_accepts_goal_intent(self, client: httpx.AsyncClient):
        result = await _chat(client, "Set a goal for me: ship the v1 release by next Friday.")
        assert result["error"] is None, f"goal intent errored: {result['error']}"
        assert result["response"]

    async def test_chat_accepts_task_intent(self, client: httpx.AsyncClient):
        result = await _chat(client, "Add a task to review the Q3 analytics report this week.")
        assert result["error"] is None, f"task intent errored: {result['error']}"
        assert result["response"]

    async def test_chat_accepts_schedule_intent(self, client: httpx.AsyncClient):
        result = await _chat(client, "Remind me every weekday morning at 9am to check my inbox.")
        assert result["error"] is None, f"schedule intent errored: {result['error']}"
        assert result["response"]
