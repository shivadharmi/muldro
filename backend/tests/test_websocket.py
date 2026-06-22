"""Tests for the WebSocket route and real-time A2UI delivery."""

from tests.conftest import TEST_USER_ID


class TestWebSocketRoute:
    def _get_app(self):
        """Get a test app with WebSocket route."""
        from src.api.app import create_app

        return create_app()

    def test_ws_route_exists(self):
        from tests.conftest import iter_app_routes

        app = self._get_app()
        # Recurse into included routers/mounts: newer Starlette/FastAPI no longer
        # flatten them into ``app.routes`` (WS routes are not in the OpenAPI map,
        # so introspection is the only way to assert registration).
        ws_routes = [p for p, _ in iter_app_routes(app.routes) if "/ws/" in p]
        assert len(ws_routes) > 0


class TestBroadcast:
    async def test_broadcast_to_user_no_connections(self):
        from src.api.routes_ws import broadcast_to_user

        sent = await broadcast_to_user(TEST_USER_ID, {"type": "test"})
        assert sent == 0

    def test_get_connected_users_empty(self):
        from src.api.routes_ws import get_connected_users

        # Initially no connections
        users = get_connected_users()
        assert isinstance(users, list)
