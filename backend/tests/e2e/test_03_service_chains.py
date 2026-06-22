"""Layer 3: Service integration chains.

Multi-step tests verifying services work together through the database.
"""

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


class TestEventIngestionPipeline:
    """3.1 Event ingestion, observation tracking, and dedup."""

    async def test_event_pipeline(self, client: httpx.AsyncClient):
        # Ingest an event
        resp = await client.post(
            "/v1/events/ingest",
            json={
                "source": "github",
                "event_type": "pr_opened",
                "entity_type": "pull_request",
                "entity_id": "e2e_pr_101",
                "title": "E2E: Fix auth bug",
                "summary": "Fixes a critical auth bypass in the login flow",
            },
        )
        assert resp.status_code == 200
        first = resp.json()
        assert first["status"] in ("processed", "duplicate")

        # Verify observation status updated
        obs_resp = await client.get("/v1/observations/status")
        assert obs_resp.status_code == 200

        # Ingest same event again — should be deduplicated or idempotent
        resp2 = await client.post(
            "/v1/events/ingest",
            json={
                "source": "github",
                "event_type": "pr_opened",
                "entity_type": "pull_request",
                "entity_id": "e2e_pr_101",
                "title": "E2E: Fix auth bug",
            },
        )
        assert resp2.status_code == 200


class TestSettingsPersistence:
    """3.8 Settings read-write persistence."""

    async def test_settings_persist(self, client: httpx.AsyncClient):
        # Get original policy
        orig_policy = await client.get("/v1/settings/policy")
        original_mode = orig_policy.json()["mode"]

        # Set policy to full_auto
        await client.put("/v1/settings/policy/mode", json={"mode": "full_auto"})
        verify = await client.get("/v1/settings/policy")
        assert verify.json()["mode"] == "full_auto"

        # Get original budget
        orig_budget = await client.get("/v1/settings/budget")
        original_limit = orig_budget.json()["daily_limit_usd"]

        # Set budget to 25.0
        await client.put("/v1/settings/budget/daily_limit", json={"daily_limit_usd": 25.0})
        budget_verify = await client.get("/v1/settings/budget")
        assert budget_verify.json()["daily_limit_usd"] == 25.0

        # Restore originals
        await client.put("/v1/settings/policy/mode", json={"mode": original_mode})
        await client.put(
            "/v1/settings/budget/daily_limit",
            json={"daily_limit_usd": original_limit},
        )


class TestConversationPersistence:
    """3.9 Conversation create, messages, archive."""

    async def test_conversation_persistence(self, client: httpx.AsyncClient):
        # Create
        create_resp = await client.post("/v1/conversations", json={"surface": "web"})
        conv_id = create_resp.json()["conversation_id"]

        # Messages empty
        msgs_resp = await client.get(f"/v1/conversations/{conv_id}/messages")
        assert msgs_resp.status_code == 200
        assert msgs_resp.json()["messages"] == []

        # Archive (delete)
        del_resp = await client.delete(f"/v1/conversations/{conv_id}")
        assert del_resp.status_code == 204

        # Archived conversation not in active list
        list_resp = await client.get("/v1/conversations")
        conv_ids = [c["conversation_id"] for c in list_resp.json()]
        assert conv_id not in conv_ids
