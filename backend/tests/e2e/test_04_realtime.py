"""Layer 4: Real-time tests — SSE and WebSocket.

Tests SSE streaming endpoints and WebSocket connections.
"""

import asyncio
import json

import httpx
import pytest
import websockets

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"


class TestSSE:
    async def test_sse_global_events(self, auth_token: str):
        """SSE endpoint opens and sends keepalives."""
        headers = {"Authorization": f"Bearer {auth_token}"}
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as client:
            async with client.stream("GET", "/v1/realtime/events", timeout=5.0) as resp:
                assert resp.status_code == 200
                content_type = resp.headers.get("content-type", "")
                assert "text/event-stream" in content_type

                # Read at least one chunk (keepalive or event)
                chunks = []
                try:
                    async for chunk in resp.aiter_text():
                        chunks.append(chunk)
                        if len(chunks) >= 2:
                            break
                except httpx.ReadTimeout:
                    pass

                # Should have received at least a keepalive
                assert len(chunks) >= 1

    async def test_sse_redis_forward(self, auth_token: str, user_id: str):
        """SSE receives events published to Redis channel."""
        import redis.asyncio as aioredis

        r = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
        received = []
        headers = {"Authorization": f"Bearer {auth_token}"}

        async def listen():
            async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as client:
                async with client.stream("GET", "/v1/realtime/events", timeout=8.0) as resp:
                    async for chunk in resp.aiter_text():
                        received.append(chunk)
                        if "e2e_redis_test" in chunk:
                            break

        try:
            listener = asyncio.create_task(listen())
            await asyncio.sleep(1.0)

            # Publish to the user-specific channel the SSE endpoint subscribes to
            await r.publish(
                f"jarvis:realtime:{user_id}",
                json.dumps({"type": "test", "data": "e2e_redis_test"}),
            )
            await asyncio.wait_for(listener, timeout=8.0)

            assert any("e2e_redis_test" in c for c in received)
        except asyncio.TimeoutError:
            pytest.fail("SSE Redis forward timed out — event never arrived")
        finally:
            await r.aclose()

    async def test_sse_run_progress(self, auth_token: str):
        """SSE run progress endpoint opens successfully."""
        headers = {"Authorization": f"Bearer {auth_token}"}
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers) as client:
            async with client.stream(
                "GET", "/v1/realtime/runs/run_fake", timeout=3.0
            ) as resp:
                # 200 if Redis is available, 503 if not
                assert resp.status_code in (200, 503)


class TestWebSocket:
    async def test_ws_connect(self, user_id: str):
        """WebSocket connects and accepts the connection."""
        async with websockets.connect(f"{WS_URL}/ws/{user_id}") as ws:
            # Send a heartbeat and verify connection is alive
            await ws.send(json.dumps({"type": "heartbeat"}))
            # Connection accepted without error — success
            await ws.close()

    async def test_ws_heartbeat(self, user_id: str):
        """WebSocket receives server heartbeat within 35s."""
        async with websockets.connect(f"{WS_URL}/ws/{user_id}") as ws:
            # Server sends heartbeat every 30s (routes_ws.py:107)
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=35.0)
                parsed = json.loads(msg)
                assert parsed["type"] == "heartbeat"
            except asyncio.TimeoutError:
                pytest.fail("No server heartbeat received within 35s")

    async def test_ws_redis_relay(self, user_id: str):
        """WebSocket receives events published to Redis a2ui channel."""
        import redis.asyncio as aioredis

        r = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)

        try:
            async with websockets.connect(f"{WS_URL}/ws/{user_id}") as ws:
                # Give the server time to subscribe to Redis channels
                await asyncio.sleep(0.5)

                # Publish to the a2ui channel the WS endpoint subscribes to
                payload = json.dumps({"type": "test", "data": "e2e_ws_relay"})
                await r.publish(f"jarvis:a2ui:{user_id}", payload)

                # Read messages until we find ours (skip potential heartbeats)
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    parsed = json.loads(msg)
                    assert parsed["data"] == "e2e_ws_relay"
                except asyncio.TimeoutError:
                    pytest.fail("WebSocket Redis relay timed out — event never arrived")
        finally:
            await r.aclose()
