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

    PRODUCTION MUST PASS ``db_factory=None``. A real session factory is a TEST-ONLY
    override for the intelligence server's ``_get_db``. In production, run.py runs the
    API and worker on separate threads/loops; a real factory stored here would be used
    last-writer-wins across BOTH loops, so a worker tool call could hit the API loop's
    asyncpg engine → ``got Future attached to a different loop``. With ``None``, each
    tool call resolves the thread-local (per-loop) engine instead (Step 11 Phase 3).
    """
    from src.tools import communication_server, intelligence_server

    intelligence_server.configure(db_factory, settings, services)
    extras = getattr(services, "extras", None) or {}
    communication_server.configure(settings, redis=extras.get("redis"))
