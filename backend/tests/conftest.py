"""Shared test fixtures for Muldro backend tests."""

import os

# The API lifespan registers gateway OAuth configs with OpenConnector and ABORTS
# startup if that fails. Tests must not require a live OpenConnector, so opt out
# here -- before any Settings instance is built. The registrar's own behaviour is
# covered directly in tests/integrations/test_gateway_oauth_registrar.py.
os.environ.setdefault("MULDRO_SKIP_GATEWAY_VALIDATION", "true")

import asyncio
import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.services.event_processor import RawEvent

# Deterministic test user ID in proper usr_{ULID} format.
# ULID: 26 chars Crockford base32 (0-9, A-H, J-K, M-N, P-T, V-Z).
TEST_USER_ID = "usr_01JTEST00000000000000000000"
TEST_WORKSPACE_ID = "ws_test"


def make_raw_event(**overrides) -> RawEvent:
    """Factory for test RawEvent instances."""
    defaults = dict(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id="thr_001",
        occurred_at=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
        title="Investor follow-up on deck",
        summary="Investor requested latest deck and quick call",
        actor={"type": "person", "email": "investor@fund.com", "name": "John Doe"},
        raw_payload={"message_id": "msg_001"},
    )
    defaults.update(overrides)
    return RawEvent(**defaults)


def make_mock_settings(**overrides) -> MagicMock:
    """Factory for mock Settings."""
    settings = MagicMock()
    defaults = dict(
        anthropic_api_key="test-key",
        anthropic_model="claude-sonnet-4-20250514",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        redis_url="redis://localhost:6379/0",
        importance_threshold=0.7,
        briefing_lookback_hours=24,
        debug=False,
        retry_max_attempts=3,
        retry_base_delay=0.01,
        retry_max_delay=0.1,
        plan_ttl_hours=72,
        approval_ttl_hours=24,
        dlq_max_attempts=3,
        rate_limit_rpm=120,
        max_request_body_bytes=1_048_576,
        cors_allowed_origins="",
        elasticsearch_url="",
        daily_token_budget_usd=5.0,
        ses_from_address="",
        ses_region="ap-south-1",
        ses_enabled=False,
        event_processor_concurrency=5,
        max_perception_per_tick=5,
        webhook_lag_threshold=5000,
        db_idle_in_transaction_timeout_ms=60_000,
        db_statement_timeout_ms=120_000,
        scheduler_subtick_timeout_s=90.0,
        resume_reaper_stale_after_s=300.0,
        resume_reaper_max_attempts=5,
        # Mirror the real Settings default (False) so a mock Settings never trips the
        # dormant Step-7B2 deep delegate layer: MagicMock's unset attrs are truthy, which
        # would otherwise flip this flag ON for every runtime="deep" test.
        deep_delegates_enabled=False,
        # Same MagicMock-truthiness hazard: _augment_system_blocks_for_inline does
        # `if not inline_format: return`, so a truthy unset attr would inject PRESENTER_VOICE
        # into every deep test's prompt. Mirror the real Settings default (False).
        deep_inline_format=False,
        # Same MagicMock-truthiness hazard (Step 7C): a truthy unset attr would wire the
        # deep read-back middleware into the chain for every runtime="deep" test. Mirror
        # the real Settings default (False).
        deep_readback_enabled=False,
        # Step 8: an unset MagicMock bool is truthy, which would route every
        # runtime="deep" test through the slim JIT pack. Default OFF to mirror prod.
        deep_context_jit=False,
        # Step 10D A-5: MagicMock unset bool is truthy — would flip the single-lead path ON
        # for every runtime="deep" test. Mirror the real Settings default (False).
        deep_single_lead=False,
        # Step 10D P2.5c: MagicMock unset bool is truthy — would flip the planless reroute ON
        # for every test, firing the early planless gate in _process_core and skipping the
        # Planner. Mirror the real Settings default (False) so the flag-off path stays exercised.
        chat_planless=False,
        # Step-10A A3: MagicMock unset bool is truthy — would flip the fail-closed write lock
        # ON for every test that builds the lock wrapper from a mock Settings. Mirror the real
        # default (False = fail-open).
        write_lock_require_redis=False,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


def pytest_configure(config):
    """Register local markers used by tests.

    The CI/local environment for this kata may not always provide pytest-asyncio.
    We still register the marker to avoid unknown-mark warnings.
    """
    config.addinivalue_line("markers", "asyncio: mark test as async")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Run async test functions without requiring external pytest plugins.

    If pytest-asyncio is installed, it can still handle tests first; this
    fallback only triggers for coroutine test functions that reach this hook.
    """
    test_fn = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_fn):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(test_fn(**kwargs))
    return True


def make_test_db():
    """Real-Postgres session factory (NullPool) bound to ``get_settings().database_url``.

    Thin wrapper over the ``create_async_engine(..., poolclass=NullPool)`` +
    ``async_sessionmaker`` pattern used by the existing real-DB tests (see
    ``tests/test_run_reconcile.py::_run_env``). Returns ``(factory, engine)`` —
    callers are responsible for ``await engine.dispose()`` when done.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from src.config.settings import get_settings

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def seed_user_workspace(factory, user_id: str, workspace_id: str) -> None:
    """Seed the User -> Workspace -> WorkspaceMember FK chain for real-DB tests.

    Idempotent (safe to call with ids that already exist, e.g. the fixed
    ``TEST_USER_ID`` / ``TEST_WORKSPACE_ID`` constants) — checks for existing
    rows before inserting so repeated test runs don't trip unique constraints.
    """
    from sqlalchemy import select

    from src.models.users import User, Workspace, WorkspaceMember

    async with factory() as db:
        user = (await db.execute(select(User).where(User.user_id == user_id))).scalar_one_or_none()
        if user is None:
            db.add(User(user_id=user_id, email=f"{user_id}@example.com", display_name=user_id))

        workspace = (
            await db.execute(select(Workspace).where(Workspace.workspace_id == workspace_id))
        ).scalar_one_or_none()
        if workspace is None:
            db.add(Workspace(workspace_id=workspace_id, name=workspace_id, owner_user_id=user_id))
        await db.flush()

        member = (
            await db.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            db.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role="owner"))

        await db.commit()


def iter_app_routes(routes, _prefix=""):
    """Yield ``(full_path, route)`` for every leaf route, recursing into
    included routers / mounts.

    Newer Starlette/FastAPI no longer flatten ``include_router`` into
    ``app.routes`` — they leave ``_IncludedRouter`` wrapper objects. Two shapes
    exist across versions and BOTH must be traversed:

    * FastAPI >= ~0.136 (Starlette >= 1.3): the wrapper's ``path`` is ``None`` and
      it has no ``.routes``; the real ``APIRouter`` and its prefix live under
      ``.include_context.included_router`` / ``.include_context.prefix``.
    * Older: the wrapper (Mount-style) exposes the real routes under ``.routes``
      with a ``path``/``prefix`` prefix.

    Accumulating the prefix reconstructs the full path in either representation,
    so route-introspection tests survive the framework version change.
    """
    for r in routes:
        # Newer FastAPI: the included APIRouter is reachable via include_context.
        ctx = getattr(r, "include_context", None)
        if ctx is not None:
            inner = getattr(ctx, "included_router", None)
            sub = getattr(inner, "routes", None)
            if sub:
                yield from iter_app_routes(sub, _prefix + (getattr(ctx, "prefix", "") or ""))
                continue
        # Older: Mount-style wrapper exposes the routes directly.
        sub = getattr(r, "routes", None)
        if sub:
            wrapper_prefix = getattr(r, "path", "") or getattr(r, "prefix", "") or ""
            yield from iter_app_routes(sub, _prefix + wrapper_prefix)
            continue
        path = getattr(r, "path", None)
        if path is not None:
            yield _prefix + path, r
