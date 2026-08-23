"""Internal MCP tool servers."""


def configure_tool_servers(db_factory, settings, services) -> None:
    """Configure the internal MCP servers from one ServiceContainer.

    Each internal server has its own module-level ``configure()``. Routing every
    one of them through this single entry point is what keeps them in lockstep: a
    server whose ``configure()`` is never called comes up with its runtime
    dependencies unset and fails silently rather than loudly.

    PRODUCTION MUST PASS ``db_factory=None``. A real session factory is a TEST-ONLY
    override for the intelligence server's ``_get_db``. In production, run.py runs the
    API and worker on separate threads/loops; a real factory stored here would be used
    last-writer-wins across BOTH loops, so a worker tool call could hit the API loop's
    asyncpg engine → ``got Future attached to a different loop``. With ``None``, each
    tool call resolves the thread-local (per-loop) engine instead (Step 11 Phase 3).
    """
    from src.tools import intelligence_server

    intelligence_server.configure(db_factory, settings, services)
