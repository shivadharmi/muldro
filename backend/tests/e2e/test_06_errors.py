"""Layer 6: Error handling and validation tests.

Verifies the system returns proper error codes for invalid inputs,
nonexistent resources, and invalid state transitions.
"""

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


# ── 6.1 404 Not Found ───────────────────────────────────────────


class TestNotFound:
    async def test_artifact_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/artifacts/art_nonexistent")
        assert resp.status_code == 404

    async def test_run_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/runs/run_nonexistent")
        assert resp.status_code == 404

    async def test_trace_not_found(self, client: httpx.AsyncClient):
        resp = await client.get("/v1/traces/trace_nonexistent")
        assert resp.status_code == 404


# ── 6.2 422 Validation Errors ───────────────────────────────────


class TestValidationErrors:
    async def test_invalid_policy_mode(self, client: httpx.AsyncClient):
        resp = await client.put("/v1/settings/policy/mode", json={"mode": "invalid_mode"})
        assert resp.status_code == 400

    async def test_event_missing_fields(self, client: httpx.AsyncClient):
        resp = await client.post("/v1/events/ingest", json={})
        assert resp.status_code == 422


# ── 6.3 State Transition Errors ─────────────────────────────────


class TestStateTransitionErrors:
    async def test_resume_nonexistent_run(self, client: httpx.AsyncClient):
        """Resuming a nonexistent run returns 404."""
        resp = await client.post("/v1/runs/run_does_not_exist/resume")
        assert resp.status_code == 404

    async def test_approve_nonexistent_approval(self, client: httpx.AsyncClient):
        """Cannot approve a nonexistent approval."""
        resp = await client.post("/v1/approvals/apr_does_not_exist/approve", json={})
        assert resp.status_code == 404


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
