"""Tests for the surface registry service."""

from tests.conftest import TEST_USER_ID


class TestSurfaceRegistryLocal:
    """Tests with in-memory fallback (no Redis)."""

    async def test_register_and_get_active(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "telegram")
        await registry.register(TEST_USER_ID, "web")

        surfaces = await registry.get_active_surfaces(TEST_USER_ID)
        assert set(surfaces) == {"telegram", "web"}

    async def test_unregister(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "telegram")
        await registry.register(TEST_USER_ID, "web")

        await registry.unregister(TEST_USER_ID, "web")
        surfaces = await registry.get_active_surfaces(TEST_USER_ID)
        assert surfaces == ["telegram"]

    async def test_is_active(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "telegram")

        assert await registry.is_active(TEST_USER_ID, "telegram") is True
        assert await registry.is_active(TEST_USER_ID, "web") is False

    async def test_preferred_surface_web_over_telegram(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "telegram")
        await registry.register(TEST_USER_ID, "web")

        preferred = await registry.get_preferred_surface(TEST_USER_ID)
        assert preferred == "web"

    async def test_preferred_surface_telegram_only(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "telegram")

        preferred = await registry.get_preferred_surface(TEST_USER_ID)
        assert preferred == "telegram"

    async def test_preferred_surface_none_when_empty(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        preferred = await registry.get_preferred_surface(TEST_USER_ID)
        assert preferred is None

    async def test_get_surface_info(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "telegram", metadata={"chat_id": "12345"})

        info = await registry.get_surface_info(TEST_USER_ID, "telegram")
        assert info is not None
        assert info.surface == "telegram"
        assert info.metadata == {"chat_id": "12345"}

    async def test_get_surface_info_not_found(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        info = await registry.get_surface_info(TEST_USER_ID, "web")
        assert info is None

    async def test_heartbeat_updates_timestamp(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register(TEST_USER_ID, "web")

        info_before = await registry.get_surface_info(TEST_USER_ID, "web")
        old_hb = info_before.last_heartbeat

        await registry.heartbeat(TEST_USER_ID, "web")

        info_after = await registry.get_surface_info(TEST_USER_ID, "web")
        assert info_after.last_heartbeat >= old_hb

    async def test_empty_surfaces_for_unknown_user(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        surfaces = await registry.get_active_surfaces("unknown_user")
        assert surfaces == []

    async def test_multiple_users_independent(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("user_a", "telegram")
        await registry.register("user_b", "web")

        assert await registry.get_active_surfaces("user_a") == ["telegram"]
        assert await registry.get_active_surfaces("user_b") == ["web"]
