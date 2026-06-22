"""Layer 7: Extended API coverage.

Tests for endpoints not covered by earlier layers.
"""

import base64

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


class TestPushSubscription:
    """POST/DELETE /v1/notifications/push/subscribe"""

    async def test_subscribe_push(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/notifications/push/subscribe",
            json={
                "endpoint": "https://e2e.example.com/push",
                "keys": {"p256dh": "k", "auth": "a"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "subscribed"

    async def test_unsubscribe_push(self, client: httpx.AsyncClient):
        # Subscribe first to ensure there's something to delete
        await client.post(
            "/v1/notifications/push/subscribe",
            json={
                "endpoint": "https://e2e.example.com/push",
                "keys": {"p256dh": "k", "auth": "a"},
            },
        )
        resp = await client.delete("/v1/notifications/push/subscribe")
        assert resp.status_code == 200


class TestIntegrationInterval:
    """PUT /v1/settings/integrations/{source}/interval"""

    async def test_set_integration_interval(self, client: httpx.AsyncClient):
        resp = await client.put(
            "/v1/settings/integrations/gmail/interval",
            json={"interval_minutes": 30},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["interval_minutes"] == 30

    async def test_get_integration_intervals(self, client: httpx.AsyncClient):
        # Set a known value first
        await client.put(
            "/v1/settings/integrations/gmail/interval",
            json={"interval_minutes": 30},
        )
        resp = await client.get("/v1/settings/integrations")
        assert resp.status_code == 200
        assert "intervals" in resp.json()


class TestArtifactLifecycle:
    """POST /v1/artifacts, GET metadata and content."""

    async def test_create_and_get_artifact(self, client: httpx.AsyncClient):
        content = base64.b64encode(b"Hello E2E artifact").decode()
        create_resp = await client.post(
            "/v1/artifacts",
            json={
                "artifact_type": "document",
                "title": "E2E Test Artifact",
                "mime_type": "text/plain",
                "content_base64": content,
            },
        )
        assert create_resp.status_code == 201
        artifact_id = create_resp.json()["artifact_id"]

        # GET metadata
        meta_resp = await client.get(f"/v1/artifacts/{artifact_id}")
        assert meta_resp.status_code == 200
        assert meta_resp.json()["title"] == "E2E Test Artifact"

        # GET content — 200 if MinIO reachable, 502 otherwise
        content_resp = await client.get(f"/v1/artifacts/{artifact_id}/content")
        assert content_resp.status_code in (200, 502)

    async def test_artifact_content_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/artifacts/art_nonexistent_99999/content")
        assert resp.status_code == 404


class TestMemoryFilters:
    """GET /v1/memories with query params."""

    async def test_filter_by_type(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/memories", params={"memory_type": "preference"})
        assert resp.status_code == 200
        data = resp.json()
        assert "memories" in data
        # All returned memories should match the filter
        for m in data["memories"]:
            assert m["memory_type"] == "preference"

    async def test_limit(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/memories", params={"limit": 5})
        assert resp.status_code == 200
        assert len(resp.json()["memories"]) <= 5


class TestEntitySearch:
    """POST /v1/search with entity scope."""

    async def test_search_entities(self, client: httpx.AsyncClient):
        resp = await client.post(
            "/v1/search",
            json={"query": "test", "scope": "entities"},
        )
        assert resp.status_code == 200
        assert "results" in resp.json()


class TestRunResume:
    """POST /v1/runs/{run_id}/resume — 404 path."""

    async def test_resume_nonexistent_run(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/runs/run_nonexistent_99999/resume")
        assert resp.status_code == 404
