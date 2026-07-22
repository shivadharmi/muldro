"""Shared FastMCP instance, runtime configuration, and DB session helper.

`configure()` is called once at orchestrator startup to inject settings and the
ServiceContainer. Tool submodules read `_settings` and `_services` via attribute
access on this module (``_shared._services``) so they observe the configured values
at call time rather than an import-time copy.

DB sessions are resolved PER LOOP: ``_get_db()`` uses the thread-local
``get_session_factory()`` (each thread/loop owns its own asyncpg engine) rather than
a shared global factory. This is the fix for the worker dual-loop bug (Step 11 Phase
3): ``run.py --worker`` runs the API and worker on separate threads/loops, and a
single shared ``_db_factory`` set last-writer-wins by ``configure_tool_servers`` on
both threads meant a worker tool call could use the API loop's engine →
``got Future attached to a different loop``. ``_db_factory`` remains only as an
explicit TEST override (tests inject a mock via ``configure``); production passes
``None`` so the per-thread engine is used.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

intelligence = FastMCP("jarvis-intelligence")

# Runtime dependencies, set by configure() at orchestrator startup.
# _db_factory is a TEST-ONLY override; production passes None (see module docstring
# and _get_db) so each thread/loop resolves its own thread-local session factory.
_db_factory = None
_settings = None
_services = None


def configure(db_factory, settings, services):
    """Configure the intelligence server with runtime dependencies.

    Called during orchestrator startup. ``services`` should be a ServiceContainer.
    ``db_factory`` is a TEST-ONLY override for ``_get_db``: production passes ``None``
    so sessions resolve the thread-local (per-loop) factory. See module docstring.
    """
    global _db_factory, _settings, _services
    _db_factory = db_factory
    _settings = settings
    _services = services


@asynccontextmanager
async def _get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session bound to the CURRENT loop's engine.

    Uses the thread-local ``get_session_factory()`` unless a test has injected an
    explicit ``_db_factory`` override. Resolving per-loop (not via a shared global)
    is what keeps the worker and API threads from cross-binding asyncpg pools.
    """
    factory = _db_factory
    if factory is None:
        from src.models.database import get_session_factory

        factory = get_session_factory()
    async with factory() as session:
        yield session


def request_services(db):
    """Return DB-bound services for the per-request session ``db``.

    Reuse the configured container when it already carries DB-bound services
    (tests / single-flow ``build``); otherwise build them per request from the
    shared session-free singletons. This keeps MCP tool calls from operating a
    long-lived, cross-request ``AsyncSession`` (P2 #4).
    """
    from src.runtime import request_services as _request_services

    return _request_services(_services, _settings, db)
