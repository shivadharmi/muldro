"""Layer 5: Full workflow scenarios.

End-to-end lifecycle tests that exercise multiple services in sequence.
"""

import os

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


class TestChatFlow:
    """5.1 Chat flow — requires Anthropic API key."""

    @pytest.mark.skipif(
        not (os.environ.get("JARVIS_ANTHROPIC_API_KEY") or os.environ.get("JARVIS_USE_BEDROCK")),
        reason="Requires JARVIS_ANTHROPIC_API_KEY or JARVIS_USE_BEDROCK",
    )
    async def test_chat_lifecycle(self, client: httpx.AsyncClient, auth_token: str):
        # Create conversation
        conv_resp = await client.post("/v1/conversations", json={"surface": "web"})
        conv_id = conv_resp.json()["conversation_id"]

        # Send chat — SSE stream
        async with httpx.AsyncClient(
            base_url="http://localhost:8000",
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=60.0,
        ) as chat_client:
            async with chat_client.stream(
                "POST",
                "/v1/jarvis/chat",
                json={
                    "message": "What is 2+2?",
                    "surface": "web",
                    "conversation_id": conv_id,
                },
            ) as resp:
                assert resp.status_code == 200
                chunks = []
                async for chunk in resp.aiter_text():
                    chunks.append(chunk)
                    if len(chunks) > 20:
                        break

        # Verify messages persisted
        msgs_resp = await client.get(f"/v1/conversations/{conv_id}/messages")
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()["messages"]
        assert len(messages) >= 1

        # Verify trace created
        traces_resp = await client.get("/v1/traces")
        assert traces_resp.status_code == 200

        # Cleanup
        await client.delete(f"/v1/conversations/{conv_id}")


class TestFullTaskGoalLifecycle:
    """5.2 Full task-goal lifecycle with multiple tasks and dependencies."""

    async def test_full_lifecycle(self, client: httpx.AsyncClient):
        # Create goal
        goal_resp = await client.post(
            "/v1/goals", json={"title": "E2E Launch MVP", "priority": "high"}
        )
        assert goal_resp.status_code == 201
        goal_id = goal_resp.json()["goal_id"]

        # Create 3 tasks linked to goal
        task_ids = []
        for i in range(3):
            t = await client.post(
                "/v1/tasks",
                json={
                    "title": f"E2E MVP Task {i + 1}",
                    "goal_id": goal_id,
                    "priority": "high" if i == 0 else "medium",
                },
            )
            assert t.status_code == 200
            task_ids.append(t.json()["task_id"])

        # Add dependency: task3 depends on task2
        dep_resp = await client.post(
            f"/v1/tasks/{task_ids[2]}/dependencies",
            json={"depends_on_task_id": task_ids[1]},
        )
        assert dep_resp.status_code == 200

        # Start task1, then cancel it
        start_resp = await client.post(f"/v1/tasks/{task_ids[0]}/start")
        assert start_resp.status_code == 200

        cancel_resp = await client.post(f"/v1/tasks/{task_ids[0]}/cancel")
        assert cancel_resp.status_code == 200

        # Verify goal progress unchanged
        goal_check = await client.get(f"/v1/goals/{goal_id}")
        assert goal_check.status_code == 200
        assert goal_check.json()["progress"] == 0.0

        # Cleanup
        await client.delete(f"/v1/goals/{goal_id}")


class TestAgentConfigurationImpact:
    """5.3 Agent configuration and its impact."""

    async def test_agent_config(self, client: httpx.AsyncClient):
        # Get all agents, find planner
        resp = await client.get("/v1/agents")
        agents = resp.json()
        planner = next((a for a in agents if a["name"] == "planner"), None)
        assert planner is not None
        planner_id = planner["agent_id"]
        original_temp = planner["temperature"]

        # Update temperature
        patch_resp = await client.patch(f"/v1/agents/{planner_id}", json={"temperature": 0.5})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["temperature"] == 0.5

        # Verify
        get_resp = await client.get(f"/v1/agents/{planner_id}")
        assert get_resp.json()["temperature"] == 0.5

        # Reset
        await client.patch(f"/v1/agents/{planner_id}", json={"temperature": original_temp})

        # Disable/enable cycle
        await client.post(f"/v1/agents/{planner_id}/disable")
        disabled = await client.get(f"/v1/agents/{planner_id}")
        assert disabled.json()["enabled"] is False

        await client.post(f"/v1/agents/{planner_id}/enable")
        enabled = await client.get(f"/v1/agents/{planner_id}")
        assert enabled.json()["enabled"] is True


class TestSettingsFlow:
    """5.4 Settings end-to-end flow with dashboard verification."""

    async def test_settings_flow(self, client: httpx.AsyncClient):
        # Record originals
        orig_policy = await client.get("/v1/settings/policy")
        orig_mode = orig_policy.json()["mode"]
        orig_budget = await client.get("/v1/settings/budget")
        orig_limit = orig_budget.json()["daily_limit_usd"]

        # Set policy to lockdown
        await client.put("/v1/settings/policy/mode", json={"mode": "lockdown"})
        policy_check = await client.get("/v1/settings/policy")
        assert policy_check.json()["mode"] == "lockdown"

        # Set budget to 25.0
        await client.put("/v1/settings/budget/daily_limit", json={"daily_limit_usd": 25.0})
        budget_check = await client.get("/v1/settings/budget")
        assert budget_check.json()["daily_limit_usd"] == 25.0

        # Dashboard should still work
        dashboard = await client.get("/v1/system/dashboard")
        assert dashboard.status_code == 200
        assert "budget" in dashboard.json()

        # Restore originals
        await client.put("/v1/settings/policy/mode", json={"mode": orig_mode})
        await client.put(
            "/v1/settings/budget/daily_limit",
            json={"daily_limit_usd": orig_limit},
        )
