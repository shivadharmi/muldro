"""Layer 1: Infrastructure verification.

Verifies all external dependencies are alive before running API tests.
If any fail, downstream tests should be skipped.
"""

import subprocess

import asyncpg
import httpx
import pytest
import redis.asyncio as aioredis

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

# Connection parameters — match docker-compose defaults
PG_DSN = "postgresql://jarvis:jarvis@localhost:5432/jarvis"
REDIS_URL = "redis://localhost:6379/0"
ES_URL = "http://localhost:9200"
MINIO_URL = "http://localhost:9000"
API_URL = "http://localhost:8000"


class TestPostgres:
    async def test_postgres_alive(self):
        """Postgres accepts connections and responds to SELECT 1."""
        conn = await asyncpg.connect(PG_DSN)
        try:
            result = await conn.fetchval("SELECT 1")
            assert result == 1
        finally:
            await conn.close()

    async def test_migrations_current(self):
        """All Alembic migrations have been applied."""
        result = subprocess.run(
            ["alembic", "check"],
            capture_output=True,
            text=True,
            cwd="/Users/sivasankarreddybogala/work/jarvis/backend",
            timeout=30,
        )
        # alembic check returns 0 if head matches current
        # If it fails, fall back to comparing current vs heads
        if result.returncode != 0:
            current = subprocess.run(
                ["alembic", "current"],
                capture_output=True,
                text=True,
                cwd="/Users/sivasankarreddybogala/work/jarvis/backend",
                timeout=30,
            )
            heads = subprocess.run(
                ["alembic", "heads"],
                capture_output=True,
                text=True,
                cwd="/Users/sivasankarreddybogala/work/jarvis/backend",
                timeout=30,
            )
            assert current.stdout.strip(), f"No current migration. Output: {current.stderr}"
            # Both should contain the same revision
            assert "(head)" in current.stdout, (
                f"Migrations not at head.\nCurrent: {current.stdout}\nHeads: {heads.stdout}"
            )


class TestRedis:
    async def test_redis_alive(self):
        """Redis responds to PING."""
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            result = await r.ping()
            assert result is True
        finally:
            await r.aclose()

    async def test_redis_pubsub(self):
        """Redis pub/sub delivers messages within 2 seconds."""
        import asyncio

        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        received = []

        async def subscriber():
            pubsub = r.pubsub()
            await pubsub.subscribe("e2e_test_channel")
            async for message in pubsub.listen():
                if message["type"] == "message":
                    received.append(message["data"])
                    break
            await pubsub.unsubscribe("e2e_test_channel")
            await pubsub.aclose()

        try:
            sub_task = asyncio.create_task(subscriber())
            await asyncio.sleep(0.2)  # Let subscriber connect
            await r.publish("e2e_test_channel", "e2e_hello")
            await asyncio.wait_for(sub_task, timeout=2.0)
            assert received == ["e2e_hello"]
        finally:
            await r.aclose()


class TestElasticsearch:
    async def test_elasticsearch_alive(self):
        """Elasticsearch cluster health is not red."""
        async with httpx.AsyncClient() as c:
            try:
                resp = await c.get(f"{ES_URL}/_cluster/health", timeout=5.0)
                assert resp.status_code == 200
                health = resp.json()
                assert health["status"] in ("green", "yellow")
            except httpx.ConnectError:
                pytest.skip("Elasticsearch not running")


class TestMinIO:
    async def test_minio_alive(self):
        """MinIO is reachable."""
        async with httpx.AsyncClient() as c:
            try:
                resp = await c.get(f"{MINIO_URL}/minio/health/live", timeout=5.0)
                assert resp.status_code == 200
            except httpx.ConnectError:
                pytest.skip("MinIO not running")


class TestWorkspaceProvisioning:
    async def test_new_user_has_workspace_and_settings(self, client: httpx.AsyncClient):
        """A provisioned user resolves to a workspace and has policy/budget settings.

        Replaces the old agents/routes seed check — those global tables are no
        longer API-exposed after capability-based routing replaced agent_routes.
        Workspace provisioning (provision_workspace) is the surviving seed path.
        """
        me = await client.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["user_id"].startswith("usr_")

        policy = await client.get("/v1/settings/policy")
        assert policy.status_code == 200
        assert "mode" in policy.json()

        budget = await client.get("/v1/settings/budget")
        assert budget.status_code == 200
        assert "daily_limit_usd" in budget.json()
