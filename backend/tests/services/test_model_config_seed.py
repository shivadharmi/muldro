import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.services.model_config_registry import ModelConfigRegistry


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_seed_creates_default_tier_bindings():
    async with _session() as db:
        await ModelConfigRegistry(db).seed_defaults()
        await db.flush()
        rows = (
            (
                await db.execute(
                    select(ModelBinding).where(
                        ModelBinding.workspace_id.is_(None),
                        ModelBinding.scope_type == "tier",
                    )
                )
            )
            .scalars()
            .all()
        )
        by_key = {r.scope_key: r for r in rows}
        assert by_key["reasoning"].model_id == "claude-opus-4-8"
        assert by_key["balanced"].model_id == "claude-sonnet-4-6"
        assert by_key["fast"].model_id == "claude-haiku-4-5-20251001"
        assert all(
            r.provider == "anthropic"
            for r in rows
            if r.scope_key in {"reasoning", "balanced", "fast"}
        )


async def test_seed_is_idempotent():
    async with _session() as db:
        await ModelConfigRegistry(db).seed_defaults()
        await db.flush()
        n2 = await ModelConfigRegistry(db).seed_defaults()  # second run: no new rows
        await db.flush()
        assert n2 == 0
