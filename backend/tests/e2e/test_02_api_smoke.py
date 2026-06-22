"""Layer 2: API smoke tests.

Hit every endpoint once with minimal valid input and verify expected HTTP status.
"""

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


# ── 2.1 Health & System ─────────────────────────────────────────


class TestHealthAndSystem:
    async def test_health(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    async def test_system_dashboard(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/system/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "budget" in data
        assert "queues" in data
        assert "agents" in data

    async def test_system_heartbeat(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/system/heartbeat")
        assert resp.status_code == 200

    async def test_system_metrics(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/system/metrics")
        assert resp.status_code == 200

    async def test_system_dlq(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/system/dlq")
        assert resp.status_code == 200

    async def test_prometheus_metrics(self, client: httpx.AsyncClient):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")


# ── 2.2 Conversations ───────────────────────────────────────────


class TestConversations:
    async def test_create_conversation(self, client: httpx.AsyncClient, created_ids: dict):
        resp = await client.post("/v1/conversations", json={"surface": "web"})
        assert resp.status_code == 200
        data = resp.json()
        assert "conversation_id" in data
        assert data["conversation_id"].startswith("conv_")
        created_ids["conversations"].append(data["conversation_id"])

    async def test_list_conversations(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/conversations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_messages(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["conversations"]:
            pytest.skip("No conversation created")
        conv_id = created_ids["conversations"][0]
        resp = await client.get(f"/v1/conversations/{conv_id}/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert data["messages"] == []

    async def test_update_conversation(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["conversations"]:
            pytest.skip("No conversation created")
        conv_id = created_ids["conversations"][0]
        resp = await client.patch(f"/v1/conversations/{conv_id}", json={"status": "archived"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    async def test_delete_conversation(self, client: httpx.AsyncClient):
        # Create a fresh one to delete
        create_resp = await client.post("/v1/conversations", json={"surface": "web"})
        conv_id = create_resp.json()["conversation_id"]
        resp = await client.delete(f"/v1/conversations/{conv_id}")
        assert resp.status_code == 204


# ── 2.5 Approvals ───────────────────────────────────────────────


class TestApprovals:
    async def test_list_approvals(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/approvals")
        assert resp.status_code == 200

    async def test_get_nonexistent_approval(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/approvals/apr_nonexistent")
        assert resp.status_code == 404

    async def test_approve_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/approvals/apr_nonexistent/approve", json={})
        assert resp.status_code == 404

    async def test_reject_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/approvals/apr_nonexistent/reject", json={})
        assert resp.status_code == 404


# ── 2.7 Briefings ───────────────────────────────────────────────


class TestBriefings:
    async def test_get_briefing_by_date(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/briefings/2026-03-18")
        # 200 with data, 202 if generation queued, 404 if none, 500/503 if LLM unavailable
        assert resp.status_code in (200, 202, 404, 500, 503)

    async def test_briefing_feedback_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/briefings/brief_fake/feedback",
            json={"feedback_type": "rating", "rating": 5},
        )
        assert resp.status_code in (200, 404)

    async def test_briefing_feedback_get_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/briefings/brief_fake/feedback")
        assert resp.status_code in (200, 404)


# ── 2.8 Search & Memories ───────────────────────────────────────


class TestSearchAndMemories:
    async def test_search(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/search", json={"query": "test", "scope": "all"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    async def test_list_memories(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/memories")
        assert resp.status_code == 200


# ── 2.9 Notifications ───────────────────────────────────────────


class TestNotifications:
    async def test_list_notifications(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/notifications")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_read_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/notifications/notif_fake/read")
        assert resp.status_code == 404

    async def test_dismiss_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/notifications/notif_fake/dismiss")
        assert resp.status_code == 404


# ── 2.11 Traces ─────────────────────────────────────────────────


class TestTraces:
    async def test_list_traces(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert "traces" in data

    async def test_traces_performance(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/traces/performance")
        assert resp.status_code == 200

    async def test_traces_metrics(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/traces/metrics")
        assert resp.status_code == 200

    async def test_get_nonexistent_trace(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/traces/trace_nonexistent")
        assert resp.status_code == 404


# ── 2.12 Artifacts ──────────────────────────────────────────────


class TestArtifacts:
    async def test_list_artifacts(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/artifacts")
        assert resp.status_code == 200

    async def test_get_nonexistent_artifact(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/artifacts/art_nonexistent")
        assert resp.status_code == 404


# ── 2.13 Runs ───────────────────────────────────────────────────


class TestRuns:
    async def test_get_nonexistent_run(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/runs/run_nonexistent")
        assert resp.status_code == 404

    async def test_get_nonexistent_run_steps(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/runs/run_nonexistent/steps")
        assert resp.status_code == 404

    async def test_get_nonexistent_run_trace(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/runs/run_nonexistent/trace")
        assert resp.status_code == 404

    async def test_get_nonexistent_run_artifacts(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/runs/run_nonexistent/artifacts")
        # May return 200 with empty list or 404
        assert resp.status_code in (200, 404)


# ── 2.14 Events & Webhooks ──────────────────────────────────────


class TestEventsAndWebhooks:
    async def test_ingest_event(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/events/ingest",
            json={
                "source": "test",
                "event_type": "test_event",
                "entity_type": "test",
                "entity_id": "e2e_t1",
                "title": "E2E Test Event",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("processed", "duplicate")

    async def test_webhook_generic(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/webhooks/generic",
            json={
                "source": "webhook",
                "event_type": "test",
                "entity_type": "test",
                "entity_id": "e2e_wh1",
                "title": "E2E Webhook Test",
            },
        )
        assert resp.status_code == 200


# ── 2.17 Settings ───────────────────────────────────────────────


class TestSettings:
    async def test_get_settings(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/settings")
        assert resp.status_code == 200
        assert "settings" in resp.json()

    async def test_update_setting(self, client: httpx.AsyncClient):
        resp = await client.put("/v1/settings/test_cat/test_key", json={"value": "e2e_test"})
        assert resp.status_code == 200

    async def test_get_policy(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/settings/policy")
        assert resp.status_code == 200
        assert "mode" in resp.json()

    async def test_set_policy_mode(self, client: httpx.AsyncClient):
        resp = await client.put("/v1/settings/policy/mode", json={"mode": "approval_required"})
        assert resp.status_code == 200

    async def test_get_budget(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/settings/budget")
        assert resp.status_code == 200
        assert "daily_limit_usd" in resp.json()

    async def test_set_budget_limit(self, client: httpx.AsyncClient):
        resp = await client.put("/v1/settings/budget/daily_limit", json={"daily_limit_usd": 10.0})
        assert resp.status_code == 200

    async def test_get_integration_settings(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/settings/integrations")
        assert resp.status_code == 200


# ── 2.18 Integrations ──────────────────────────────────────────


class TestIntegrations:
    async def test_list_integrations(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/integrations")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_create_integration(self, client: httpx.AsyncClient):
        payload = {
            "server_name": "gmail",
            "display_name": "gmail",
            "transport": "stdio",
        }
        resp = await client.post("/v1/integrations", json=payload)
        # 201 if created, 409 if already exists
        assert resp.status_code in (201, 409)

    async def test_get_integration_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/integrations/inst_nonexistent")
        assert resp.status_code == 404

    async def test_delete_integration_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.delete("/v1/integrations/inst_nonexistent")
        assert resp.status_code == 404

    async def test_check_integration_health_nonexistent(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/integrations/inst_nonexistent/health")
        assert resp.status_code == 404


# ── 2.20 Observations ───────────────────────────────────────────


class TestObservations:
    async def test_report_observation(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/observations/report",
            json={
                "source": "gmail",
                "items_found": 5,
                "items_ingested": 3,
                "status": "ok",
            },
        )
        assert resp.status_code == 200

    async def test_observation_status(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/observations/status")
        assert resp.status_code == 200


# ── 2.21 Auth ────────────────────────────────────────────────────


class TestAuth:
    async def test_get_me(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data

    async def test_magic_link(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/auth/magic-link", json={"email": "test@test.com"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

    async def test_verify_invalid_token(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/auth/verify", json={"token": "invalid"})
        assert resp.status_code == 400

    async def test_logout(self, base_url: str):
        """Test logout with a separate session (don't invalidate shared client)."""
        async with httpx.AsyncClient(base_url=base_url, timeout=10) as c:
            r = await c.post("/v1/auth/magic-link", json={"email": "logout-test@jarvis.local"})
            token = r.json()["token"]
            r2 = await c.post("/v1/auth/verify", json={"token": token})
            temp_token = r2.json()["access_token"]
            resp = await c.post(
                "/v1/auth/logout",
                headers={"Authorization": f"Bearer {temp_token}"},
            )
            assert resp.status_code == 200

    async def test_refresh_invalid_token(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/auth/refresh", json={"refresh_token": "invalid"})
        assert resp.status_code == 401

    async def test_google_authorize(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/auth/google/authorize")
        # Returns 200 with URL when configured, 302 redirect, or 400 when not configured
        assert resp.status_code in (200, 302, 400)


# ── 2.22 UI Surfaces & Canvas ───────────────────────────────────


class TestUISurfacesAndCanvas:
    async def test_get_surfaces(self, client: httpx.AsyncClient):
        # Surfaces are returned for the authenticated user (no user_id in path).
        resp = await client.get("/v1/ui/surfaces")
        assert resp.status_code == 200
        assert "surfaces" in resp.json()

    async def test_get_nonexistent_surface(self, client: httpx.AsyncClient):
        # The single path segment is a surface_id, not a user_id.
        resp = await client.get("/v1/ui/surfaces/surf_nonexistent")
        assert resp.status_code == 404


# ── 2.24 Meetings ───────────────────────────────────────────────


class TestMeetings:
    async def test_meeting_prep(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/meetings/prep", json={"next": True})
        # May return 200 with data or 404/500 if no meetings
        assert resp.status_code in (200, 404, 500)


# ── 2.25 Feedback ───────────────────────────────────────────────


class TestFeedback:
    async def test_briefing_feedback_submit(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/briefings/brief_e2e/feedback",
            json={"feedback_type": "rating", "rating": 5},
        )
        assert resp.status_code in (200, 404)

    async def test_briefing_feedback_get(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/briefings/brief_e2e/feedback")
        assert resp.status_code in (200, 404)
