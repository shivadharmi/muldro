"""Tests for the surface registry service."""


class TestSurfaceRegistryLocal:
    """Tests with in-memory fallback (no Redis)."""

    async def test_register_and_get_active(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "telegram")
        await registry.register("usr_default", "web")

        surfaces = await registry.get_active_surfaces("usr_default")
        assert set(surfaces) == {"telegram", "web"}

    async def test_unregister(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "telegram")
        await registry.register("usr_default", "web")

        await registry.unregister("usr_default", "web")
        surfaces = await registry.get_active_surfaces("usr_default")
        assert surfaces == ["telegram"]

    async def test_is_active(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "telegram")

        assert await registry.is_active("usr_default", "telegram") is True
        assert await registry.is_active("usr_default", "web") is False

    async def test_preferred_surface_web_over_telegram(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "telegram")
        await registry.register("usr_default", "web")

        preferred = await registry.get_preferred_surface("usr_default")
        assert preferred == "web"

    async def test_preferred_surface_telegram_only(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "telegram")

        preferred = await registry.get_preferred_surface("usr_default")
        assert preferred == "telegram"

    async def test_preferred_surface_none_when_empty(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        preferred = await registry.get_preferred_surface("usr_default")
        assert preferred is None

    async def test_get_surface_info(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "telegram", metadata={"chat_id": "12345"})

        info = await registry.get_surface_info("usr_default", "telegram")
        assert info is not None
        assert info.surface == "telegram"
        assert info.metadata == {"chat_id": "12345"}

    async def test_get_surface_info_not_found(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        info = await registry.get_surface_info("usr_default", "web")
        assert info is None

    async def test_heartbeat_updates_timestamp(self):
        from src.services.surface_registry import SurfaceRegistry

        registry = SurfaceRegistry(redis=None)
        await registry.register("usr_default", "web")

        info_before = await registry.get_surface_info("usr_default", "web")
        old_hb = info_before.last_heartbeat

        await registry.heartbeat("usr_default", "web")

        info_after = await registry.get_surface_info("usr_default", "web")
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
