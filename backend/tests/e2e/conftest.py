"""Shared E2E test fixtures.

These tests run against a live Jarvis backend at http://localhost:8000.
Prerequisites: docker compose up -d && python run.py
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
try:
    import pytest_asyncio
except ImportError:  # pragma: no cover - only used in minimal local envs
    class _PytestAsyncioShim:
        @staticmethod
        def fixture(*args, **kwargs):
            kwargs.pop("loop_scope", None)
            return pytest.fixture(*args, **kwargs)

    pytest_asyncio = _PytestAsyncioShim()

BASE_URL = "http://localhost:8000"


async def _get_session_token() -> str:
    """Create a test user session via the auth API and return a Bearer token."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as c:
        # Step 1: Request magic link (dev mode returns the token directly)
        resp = await c.post(
            "/v1/auth/magic-link",
            json={"email": "e2e-test@jarvis.local"},
        )
        resp.raise_for_status()
        magic_token = resp.json()["token"]
        assert magic_token, "Magic link token not returned — is backend in dev mode?"

        # Step 2: Verify the magic link to get a session token
        resp = await c.post(
            "/v1/auth/verify",
            json={"token": magic_token},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def auth_token() -> str:
    """Create a valid session token for E2E tests."""
    return await _get_session_token()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(auth_token: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Shared httpx client for the entire E2E session."""
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
        timeout=30.0,
    ) as c:
        yield c


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def created_ids() -> dict[str, list[str]]:
    """Track created resource IDs for reference across tests.

    Keys: conversations, goals, tasks, schedules, triggers, agents, routes
    """
    return {
        "conversations": [],
        "goals": [],
        "tasks": [],
        "schedules": [],
        "triggers": [],
        "agents": [],
        "routes": [],
    }


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def user_id(client: httpx.AsyncClient) -> str:
    """Resolve the authenticated user's ID for WebSocket/SSE tests."""
    resp = await client.get("/v1/auth/me")
    resp.raise_for_status()
    return resp.json()["user_id"]


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL
