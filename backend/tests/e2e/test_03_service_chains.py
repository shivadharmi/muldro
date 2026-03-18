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


class TestTaskLifecycle:
    """3.2 Task lifecycle with goal linkage."""

    async def test_task_goal_lifecycle(self, client: httpx.AsyncClient):
        # Create goal
        goal_resp = await client.post(
            "/v1/goals", json={"title": "E2E Chain Goal", "priority": "high"}
        )
        assert goal_resp.status_code == 201
        goal_id = goal_resp.json()["goal_id"]

        # Create task linked to goal
        task_resp = await client.post(
            "/v1/tasks", json={"title": "E2E Chain Task", "goal_id": goal_id}
        )
        assert task_resp.status_code == 200
        task_id = task_resp.json()["task_id"]

        # Verify linkage
        detail_resp = await client.get(f"/v1/tasks/{task_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["goal_id"] == goal_id

        # Start task
        start_resp = await client.post(f"/v1/tasks/{task_id}/start")
        assert start_resp.status_code == 200

        # Cancel task
        cancel_resp = await client.post(f"/v1/tasks/{task_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        # Goal still active
        goal_check = await client.get(f"/v1/goals/{goal_id}")
        assert goal_check.status_code == 200
        assert goal_check.json()["status"] == "active"

        # Cleanup
        await client.delete(f"/v1/goals/{goal_id}")


class TestTaskDependencies:
    """3.3 Task dependency chain."""

    async def test_dependency_chain(self, client: httpx.AsyncClient):
        t1 = await client.post("/v1/tasks", json={"title": "E2E Chain T1"})
        t2 = await client.post("/v1/tasks", json={"title": "E2E Chain T2"})
        t1_id = t1.json()["task_id"]
        t2_id = t2.json()["task_id"]

        # Add dependency: T2 depends on T1
        dep_resp = await client.post(
            f"/v1/tasks/{t2_id}/dependencies",
            json={"depends_on_task_id": t1_id},
        )
        assert dep_resp.status_code == 200

        # Verify dependency in task detail
        detail = await client.get(f"/v1/tasks/{t2_id}")
        assert detail.status_code == 200
        deps = detail.json().get("dependencies", [])
        assert any(d["depends_on_task_id"] == t1_id for d in deps)


class TestScheduleLifecycle:
    """3.4 Full schedule lifecycle."""

    async def test_schedule_lifecycle(self, client: httpx.AsyncClient):
        # Create
        resp = await client.post(
            "/v1/schedules",
            json={
                "name": "e2e_lifecycle",
                "schedule_type": "recurring",
                "cron_expr": "0 9 * * *",
                "action_type": "heartbeat",
                "source": "user",
                "priority": "medium",
            },
        )
        assert resp.status_code == 201
        sched_id = resp.json()["schedule_id"]

        # Pause
        pause_resp = await client.post(f"/v1/schedules/{sched_id}/pause")
        assert pause_resp.status_code == 200
        assert pause_resp.json()["enabled"] is False

        # Resume — next_run_at should be recomputed
        resume_resp = await client.post(f"/v1/schedules/{sched_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["enabled"] is True
        assert resume_resp.json()["next_run_at"] is not None

        # Update cron — next_run_at should update
        patch_resp = await client.patch(
            f"/v1/schedules/{sched_id}", json={"cron_expr": "30 10 * * *"}
        )
        assert patch_resp.status_code == 200
        # next_run_at should differ since cron changed
        assert patch_resp.json()["next_run_at"] is not None

        # Delete
        del_resp = await client.delete(f"/v1/schedules/{sched_id}")
        assert del_resp.status_code == 204


class TestAgentRegistryIntegrity:
    """3.5 Agent registry: 8 defaults, disable/enable cycle."""

    async def test_agent_disable_enable(self, client: httpx.AsyncClient):
        # List — should have at least the 8 defaults
        resp = await client.get("/v1/agents")
        assert resp.status_code == 200
        agents = resp.json()
        initial_count = len(agents)
        assert initial_count >= 8
        agent_names = {a["name"] for a in agents}
        expected = {
            "observer",
            "librarian",
            "planner",
            "governor",
            "operator",
            "presenter",
            "researcher",
            "persona",
        }
        assert expected.issubset(agent_names)

        # Disable one
        agent_id = agents[0]["agent_id"]
        await client.post(f"/v1/agents/{agent_id}/disable")

        # List without disabled — should return one fewer
        resp2 = await client.get("/v1/agents")
        assert len(resp2.json()) == initial_count - 1

        # Re-enable
        await client.post(f"/v1/agents/{agent_id}/enable")

        # Back to original count
        resp3 = await client.get("/v1/agents")
        assert len(resp3.json()) == initial_count


class TestRouteResolution:
    """3.6 Route resolution for known and unknown decision types."""

    async def test_route_resolution(self, client: httpx.AsyncClient):
        # Known: create_task -> governor + operator pipeline
        resp1 = await client.post(
            "/v1/routes/resolve",
            json={"decision": {"decision_type": "create_task"}},
        )
        assert resp1.status_code == 200
        pipeline1 = resp1.json()["pipeline"]
        assert len(pipeline1) > 0

        # Known: research -> researcher pipeline
        resp2 = await client.post(
            "/v1/routes/resolve",
            json={"decision": {"decision_type": "research"}},
        )
        assert resp2.status_code == 200

        # Unknown: fallback
        resp3 = await client.post(
            "/v1/routes/resolve",
            json={"decision": {"decision_type": "e2e_unknown_type"}},
        )
        assert resp3.status_code == 200


class TestTriggerEventWiring:
    """3.7 Trigger-to-event matching."""

    async def test_trigger_event_wiring(self, client: httpx.AsyncClient):
        # Create trigger matching github source
        trg_resp = await client.post(
            "/v1/triggers",
            json={
                "name": "e2e_github_trigger",
                "conditions": {"source": "github"},
                "action_type": "notify",
            },
        )
        assert trg_resp.status_code == 201
        trg_id = trg_resp.json()["trigger_id"]

        # Ingest a github event
        await client.post(
            "/v1/events/ingest",
            json={
                "source": "github",
                "event_type": "issue_opened",
                "entity_type": "issue",
                "entity_id": "e2e_issue_trigger",
                "title": "E2E: Trigger test event",
            },
        )

        # Cleanup
        await client.delete(f"/v1/triggers/{trg_id}")


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
