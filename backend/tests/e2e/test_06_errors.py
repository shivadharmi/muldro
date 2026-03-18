"""Layer 6: Error handling and validation tests.

Verifies the system returns proper error codes for invalid inputs,
nonexistent resources, and invalid state transitions.
"""

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


# ── 6.1 404 Not Found ───────────────────────────────────────────


class TestNotFound:
    async def test_task_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/tasks/task_nonexistent")
        assert resp.status_code == 404

    async def test_goal_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/goals/goal_nonexistent")
        assert resp.status_code == 404

    async def test_agent_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/agents/agent_nonexistent")
        assert resp.status_code == 404

    async def test_route_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/routes/route_nonexistent")
        assert resp.status_code == 404

    async def test_schedule_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/schedules/sched_nonexistent")
        assert resp.status_code == 404

    async def test_artifact_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/artifacts/art_nonexistent")
        assert resp.status_code == 404

    async def test_execution_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/executions/exec_nonexistent")
        assert resp.status_code == 404

    async def test_trace_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/traces/trace_nonexistent")
        assert resp.status_code == 404


# ── 6.2 422 Validation Errors ───────────────────────────────────


class TestValidationErrors:
    async def test_task_empty_body(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/tasks", json={})
        assert resp.status_code == 422

    async def test_goal_empty_body(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/goals", json={})
        assert resp.status_code == 422

    async def test_agent_no_name(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/agents", json={})
        assert resp.status_code == 422

    async def test_route_no_name(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/routes", json={})
        assert resp.status_code == 422

    async def test_trigger_no_conditions(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/triggers", json={})
        assert resp.status_code == 422

    async def test_schedule_invalid_cron(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/schedules",
            json={
                "name": "e2e_invalid_cron",
                "schedule_type": "recurring",
                "cron_expr": "not_a_cron",
                "action_type": "heartbeat",
                "source": "user",
                "priority": "low",
            },
        )
        assert resp.status_code == 400

    async def test_invalid_policy_mode(self, client: httpx.AsyncClient):
        resp = await client.put("/v1/settings/policy/mode", json={"mode": "invalid_mode"})
        assert resp.status_code == 400

    async def test_event_missing_fields(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/events/ingest", json={})
        assert resp.status_code == 422


# ── 6.3 State Transition Errors ─────────────────────────────────


class TestStateTransitionErrors:
    async def test_resume_cancelled_task(self, client: httpx.AsyncClient):
        """Cannot resume a cancelled task."""
        create_resp = await client.post("/v1/tasks", json={"title": "E2E State Error"})
        task_id = create_resp.json()["task_id"]
        await client.post(f"/v1/tasks/{task_id}/cancel")
        resp = await client.post(f"/v1/tasks/{task_id}/resume")
        assert resp.status_code == 400

    async def test_approve_nonexistent_approval(self, client: httpx.AsyncClient):
        """Cannot approve a nonexistent approval."""
        resp = await client.post("/v1/approvals/apr_does_not_exist/approve", json={})
        assert resp.status_code == 404

    async def test_duplicate_agent_name(self, client: httpx.AsyncClient):
        """Creating an agent with a duplicate name returns 409."""
        # First, get existing agent names
        agents_resp = await client.get("/v1/agents")
        if not agents_resp.json():
            pytest.skip("No agents to test against")
        existing_name = agents_resp.json()[0]["name"]

        resp = await client.post(
            "/v1/agents",
            json={
                "name": existing_name,
                "display_name": "Duplicate",
                "system_prompt": "test",
            },
        )
        assert resp.status_code == 409


# ── 6.4 Auth Errors ─────────────────────────────────────────────


class TestAuthErrors:
    async def test_verify_expired_token(self, client: httpx.AsyncClient):
        """Verifying an invalid magic link token returns 400."""
        resp = await client.post("/v1/auth/verify", json={"token": "expired_fake_token"})
        assert resp.status_code == 400

    async def test_refresh_invalid_token(self, client: httpx.AsyncClient):
        """Refreshing with an invalid token returns 401."""
        resp = await client.post("/v1/auth/refresh", json={"refresh_token": "invalid_refresh"})
        assert resp.status_code == 401
