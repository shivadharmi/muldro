"""Shared FastMCP instance, runtime configuration, and DB session helper.

`configure()` is called once at orchestrator startup to inject the DB session
factory, settings, and ServiceContainer. Tool submodules read `_settings` and
`_services` via attribute access on this module (``_shared._services``) so they
observe the configured values at call time rather than an import-time copy.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

intelligence = FastMCP("jarvis-intelligence")

# Runtime dependencies, set by configure() at orchestrator startup.
_db_factory = None
_settings = None
_services = None


def configure(db_factory, settings, services):
    """Configure the intelligence server with runtime dependencies.

    Called once during orchestrator startup. Services should be a ServiceContainer.
    """
    global _db_factory, _settings, _services
    _db_factory = db_factory
    _settings = settings
    _services = services


@asynccontextmanager
async def _get_db() -> AsyncGenerator[AsyncSession, None]:
    if _db_factory is None:
        raise RuntimeError("Intelligence server not configured. Call configure() first.")
    async with _db_factory() as session:
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
