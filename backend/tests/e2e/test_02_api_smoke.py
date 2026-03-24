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


# ── 2.3 Goals ────────────────────────────────────────────────────


class TestGoals:
    async def test_create_goal(self, client: httpx.AsyncClient, created_ids: dict):
        resp = await client.post("/v1/goals", json={"title": "E2E Ship v1", "priority": "high"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["goal_id"].startswith("goal_")
        created_ids["goals"].append(data["goal_id"])

    async def test_list_goals(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/goals")
        assert resp.status_code == 200
        data = resp.json()
        assert "goals" in data

    async def test_get_goal(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["goals"]:
            pytest.skip("No goal created")
        resp = await client.get(f"/v1/goals/{created_ids['goals'][0]}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "E2E Ship v1"

    async def test_patch_goal(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["goals"]:
            pytest.skip("No goal created")
        resp = await client.patch(f"/v1/goals/{created_ids['goals'][0]}", json={"progress": 0.5})
        assert resp.status_code == 200
        assert resp.json()["progress"] == 0.5

    async def test_delete_goal(self, client: httpx.AsyncClient):
        create_resp = await client.post(
            "/v1/goals", json={"title": "E2E Temp Goal", "priority": "low"}
        )
        goal_id = create_resp.json()["goal_id"]
        resp = await client.delete(f"/v1/goals/{goal_id}")
        assert resp.status_code == 204


# ── 2.4 Tasks ────────────────────────────────────────────────────


class TestTasks:
    async def test_create_task(self, client: httpx.AsyncClient, created_ids: dict):
        resp = await client.post("/v1/tasks", json={"title": "E2E Test task"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"].startswith("task_")
        created_ids["tasks"].append(data["task_id"])

    async def test_list_tasks(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/tasks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_task(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["tasks"]:
            pytest.skip("No task created")
        resp = await client.get(f"/v1/tasks/{created_ids['tasks'][0]}")
        assert resp.status_code == 200

    async def test_start_task(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["tasks"]:
            pytest.skip("No task created")
        resp = await client.post(f"/v1/tasks/{created_ids['tasks'][0]}/start")
        assert resp.status_code == 200

    async def test_cancel_task(self, client: httpx.AsyncClient, created_ids: dict):
        # Create fresh task to cancel
        create_resp = await client.post("/v1/tasks", json={"title": "E2E Cancel me"})
        task_id = create_resp.json()["task_id"]
        resp = await client.post(f"/v1/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    async def test_resume_cancelled_task_fails(self, client: httpx.AsyncClient):
        # Create and cancel a task, then try to resume it
        create_resp = await client.post("/v1/tasks", json={"title": "E2E Resume fail"})
        task_id = create_resp.json()["task_id"]
        await client.post(f"/v1/tasks/{task_id}/cancel")
        resp = await client.post(f"/v1/tasks/{task_id}/resume")
        assert resp.status_code == 400

    async def test_add_dependency(self, client: httpx.AsyncClient):
        t1 = await client.post("/v1/tasks", json={"title": "E2E Dep T1"})
        t2 = await client.post("/v1/tasks", json={"title": "E2E Dep T2"})
        t1_id = t1.json()["task_id"]
        t2_id = t2.json()["task_id"]
        resp = await client.post(
            f"/v1/tasks/{t2_id}/dependencies",
            json={"depends_on_task_id": t1_id},
        )
        assert resp.status_code == 200


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


# ── 2.6 Schedules ───────────────────────────────────────────────


class TestSchedules:
    async def test_list_schedules(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/schedules")
        assert resp.status_code == 200
        schedules = resp.json()
        assert isinstance(schedules, list)

    async def test_create_schedule(self, client: httpx.AsyncClient, created_ids: dict):
        resp = await client.post(
            "/v1/schedules",
            json={
                "name": "e2e_test_schedule",
                "schedule_type": "recurring",
                "cron_expr": "0 9 * * *",
                "action_type": "heartbeat",
                "source": "user",
                "priority": "low",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["schedule_id"].startswith("sched_")
        created_ids["schedules"].append(data["schedule_id"])

    async def test_get_schedule(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["schedules"]:
            pytest.skip("No schedule created")
        resp = await client.get(f"/v1/schedules/{created_ids['schedules'][0]}")
        assert resp.status_code == 200

    async def test_update_schedule(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["schedules"]:
            pytest.skip("No schedule created")
        resp = await client.patch(
            f"/v1/schedules/{created_ids['schedules'][0]}",
            json={"name": "e2e_updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "e2e_updated"

    async def test_pause_schedule(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["schedules"]:
            pytest.skip("No schedule created")
        resp = await client.post(f"/v1/schedules/{created_ids['schedules'][0]}/pause")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    async def test_resume_schedule(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["schedules"]:
            pytest.skip("No schedule created")
        resp = await client.post(f"/v1/schedules/{created_ids['schedules'][0]}/resume")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    async def test_delete_schedule(self, client: httpx.AsyncClient):
        create_resp = await client.post(
            "/v1/schedules",
            json={
                "name": "e2e_delete_me",
                "schedule_type": "recurring",
                "cron_expr": "0 12 * * *",
                "action_type": "heartbeat",
                "source": "user",
                "priority": "low",
            },
        )
        sched_id = create_resp.json()["schedule_id"]
        resp = await client.delete(f"/v1/schedules/{sched_id}")
        assert resp.status_code == 204


# ── 2.7 Briefings ───────────────────────────────────────────────


class TestBriefings:
    async def test_get_briefing_by_date(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/briefings/2026-03-18")
        # 200 with data, 404 if none, 500/503 if LLM unavailable
        assert resp.status_code in (200, 404, 500, 503)

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


# ── 2.10 Triggers ───────────────────────────────────────────────


class TestTriggers:
    async def test_list_triggers(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/triggers")
        assert resp.status_code == 200
        data = resp.json()
        assert "triggers" in data

    async def test_create_trigger(self, client: httpx.AsyncClient, created_ids: dict):
        resp = await client.post(
            "/v1/triggers",
            json={
                "name": "e2e_test_trigger",
                "conditions": {"source": "gmail"},
                "action_type": "notify",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["trigger_id"].startswith("trg_")
        created_ids["triggers"].append(data["trigger_id"])

    async def test_patch_trigger(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["triggers"]:
            pytest.skip("No trigger created")
        resp = await client.patch(
            f"/v1/triggers/{created_ids['triggers'][0]}",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    async def test_delete_trigger(self, client: httpx.AsyncClient):
        create_resp = await client.post(
            "/v1/triggers",
            json={
                "name": "e2e_delete_trigger",
                "conditions": {"source": "test"},
                "action_type": "notify",
            },
        )
        trg_id = create_resp.json()["trigger_id"]
        resp = await client.delete(f"/v1/triggers/{trg_id}")
        assert resp.status_code == 204


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


# ── 2.13 Executions & Runs ──────────────────────────────────────


class TestExecutionsAndRuns:
    async def test_list_executions(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/executions")
        assert resp.status_code == 200

    async def test_get_nonexistent_execution(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/executions/exec_nonexistent")
        assert resp.status_code == 404

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


# ── 2.15 Agents ─────────────────────────────────────────────────


class TestAgents:
    async def test_list_agents(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/agents")
        assert resp.status_code == 200
        agents = resp.json()
        assert len(agents) >= 8

    async def test_get_agent(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/agents")
        agents = resp.json()
        if not agents:
            pytest.skip("No agents")
        agent_id = agents[0]["agent_id"]
        resp = await client.get(f"/v1/agents/{agent_id}")
        assert resp.status_code == 200

    async def test_create_agent(self, client: httpx.AsyncClient, created_ids: dict):
        import time

        name = f"e2e_test_agent_{int(time.time())}"
        resp = await client.post(
            "/v1/agents",
            json={
                "name": name,
                "display_name": "E2E Test",
                "system_prompt": "You are a test agent.",
            },
        )
        assert resp.status_code == 201
        created_ids["agents"].append(resp.json()["agent_id"])

    async def test_update_agent(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["agents"]:
            pytest.skip("No agent created")
        resp = await client.patch(
            f"/v1/agents/{created_ids['agents'][0]}",
            json={"display_name": "E2E Updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "E2E Updated"

    async def test_disable_agent(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["agents"]:
            pytest.skip("No agent created")
        resp = await client.post(f"/v1/agents/{created_ids['agents'][0]}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    async def test_enable_agent(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["agents"]:
            pytest.skip("No agent created")
        resp = await client.post(f"/v1/agents/{created_ids['agents'][0]}/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True


# ── 2.16 Routes ─────────────────────────────────────────────────


class TestRoutes:
    async def test_list_routes(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/routes")
        assert resp.status_code == 200
        routes = resp.json()
        assert len(routes) >= 8

    async def test_get_route(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/routes")
        routes = resp.json()
        if not routes:
            pytest.skip("No routes")
        resp = await client.get(f"/v1/routes/{routes[0]['route_id']}")
        assert resp.status_code == 200

    async def test_create_route(self, client: httpx.AsyncClient, created_ids: dict):
        import time

        name = f"e2e_test_route_{int(time.time())}"
        resp = await client.post(
            "/v1/routes",
            json={
                "name": name,
                "decision_type": "e2e_test",
                "agent_pipeline": [{"agent": "planner"}],
            },
        )
        assert resp.status_code == 201
        created_ids["routes"].append(resp.json()["route_id"])

    async def test_update_route(self, client: httpx.AsyncClient, created_ids: dict):
        if not created_ids["routes"]:
            pytest.skip("No route created")
        resp = await client.patch(f"/v1/routes/{created_ids['routes'][0]}", json={"priority": 50})
        assert resp.status_code == 200
        assert resp.json()["priority"] == 50

    async def test_resolve_route(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/routes/resolve",
            json={"decision": {"decision_type": "research"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline" in data

    async def test_delete_route(self, client: httpx.AsyncClient):
        import time

        create_resp = await client.post(
            "/v1/routes",
            json={
                "name": f"e2e_delete_route_{int(time.time())}",
                "decision_type": "e2e_delete",
                "agent_pipeline": [],
            },
        )
        route_id = create_resp.json()["route_id"]
        resp = await client.delete(f"/v1/routes/{route_id}")
        assert resp.status_code == 204


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


# ── 2.19 Workflows ──────────────────────────────────────────────


class TestWorkflows:
    async def test_list_workflows(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/workflows")
        assert resp.status_code == 200

    async def test_start_workflow(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/workflows/inbox_triage/start", json={})
        # May succeed or fail depending on integration availability
        assert resp.status_code in (200, 400, 500)


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
        resp = await client.get("/v1/ui/surfaces/usr_01JTEST00000000000000000000")
        assert resp.status_code == 200

    async def test_get_nonexistent_surface(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/ui/surfaces/usr_01JTEST00000000000000000000/surf_nonexistent")
        assert resp.status_code == 404

    async def test_canvas_dashboard(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/canvas/dashboard")
        assert resp.status_code == 200


# ── 2.23 Command ────────────────────────────────────────────────


class TestCommand:
    async def test_command(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/jarvis/command",
            json={"command": "status", "context": "e2e test"},
        )
        # May need Anthropic key — accept 200 or 500/503
        assert resp.status_code in (200, 500, 503)


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


# ── 2.26 Workflows Extended ─────────────────────────────────────


class TestWorkflowsExtended:
    async def test_workflow_runs(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/workflows/inbox_triage/runs")
        assert resp.status_code in (200, 404)
