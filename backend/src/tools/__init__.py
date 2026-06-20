"""Internal MCP tool servers (intelligence + communication)."""


def configure_tool_servers(db_factory, settings, services) -> None:
    """Configure BOTH internal MCP servers from one ServiceContainer.

    The intelligence and communication servers are always brought up together,
    but each has its own module-level ``configure()``. Calling only the
    intelligence one (the historical pattern) left the communication server's
    Redis client unset, so ``push_ui_update`` silently returned
    ``{"status": "skipped", "reason": "redis_not_available"}`` for every surface
    push even when Redis was healthy. Configuring both here — from the shared
    Redis the container already holds — keeps the two in lockstep.
    """
    from src.tools import communication_server, intelligence_server

    intelligence_server.configure(db_factory, settings, services)
    extras = getattr(services, "extras", None) or {}
    communication_server.configure(settings, redis=extras.get("redis"))
