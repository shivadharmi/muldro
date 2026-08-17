"""Behavior-preservation characterization: resolve EACH agent through the real
``ModelResolver`` (deployment-default seed + capability map, no mocks) and assert
the resolved ``kwargs`` equal the pre-cutover ``deep_runtime/_thinking.py`` output.

Unlike ``tests/deep_runtime/test_build_chat_model.py`` (which mocks
``ModelResolver.resolve``) and ``test_model_config_seed.py`` (which only checks
model_ids), this test exercises the *real* resolver end-to-end so it pins the
actual per-agent thinking/effort/temperature shape produced after the
Claude-only -> multi-provider cutover collapsed per-agent thinking budgets into
3 tier effort levels.

The anthropic credential resolves via the ``JARVIS_ANTHROPIC_API_KEY`` env
fallback (see ``ModelResolver._resolve_credential``), so no credential row is
needed — and NULL-workspace credential rows are cleared to avoid a stale-master-key
decrypt from an app-lifespan seed.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from src.orchestrator.agents import AGENTS
from src.services.model_config_registry import ModelConfigRegistry
from src.services.model_resolver import ModelResolver, ResolvedModel


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


# Expected resolved model id per agent tier (the deployment-default Claude seed).
_EXPECTED_MODEL_ID = {
    "reasoning": "claude-opus-4-8",
    "balanced": "claude-sonnet-4-6",
    "fast": "claude-haiku-4-5-20251001",
}

# Explicit per-agent expected kwargs table. These reproduce the pre-cutover
# _thinking.py output given each agent's tier binding + the capability map:
#   - reasoning/opus  = anthropic_adaptive: thinking={type:adaptive,display:summarized}
#                       + effort, NO temperature (adaptive models reject it).
#   - balanced/sonnet = anthropic_legacy: effort medium -> budget 4096, clamped to
#                       max_tokens-1 = 4095; temperature=1 (required when legacy
#                       thinking is on).
#   - fast/haiku      = anthropic_legacy: effort low -> budget 2048; temperature=1.
_EXPECTED_KWARGS = {
    "planner": {
        "max_tokens": 8192,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "effort": "high",
    },
    "perceiver": {
        "max_tokens": 4096,
        "thinking": {"type": "enabled", "budget_tokens": 4095},
        "temperature": 1,
    },
    "librarian": {
        "max_tokens": 4096,
        "thinking": {"type": "enabled", "budget_tokens": 4095},
        "temperature": 1,
    },
    "presenter": {
        "max_tokens": 4096,
        "thinking": {"type": "enabled", "budget_tokens": 4095},
        "temperature": 1,
    },
    # ACCEPTED DEVIATION (executor): pre-cutover, the executor ran with an explicit
    # per-agent thinking budget of 2048 (AGENT_THINKING["executor"].budget_tokens).
    # The multi-provider cutover collapsed per-agent budgets into the 3 tier effort
    # levels, and the executor is on the `balanced` tier (effort=medium -> budget
    # 4096, clamped to max_tokens-1 = 4095). The user explicitly accepted this
    # 2048 -> 4095 shift rather than seeding a per-agent executor override, so this
    # is the ONE resolved-kwargs entry that intentionally differs from the
    # pre-cutover value. Every other agent must match pre-cutover exactly.
    "executor": {
        "max_tokens": 4096,
        "thinking": {"type": "enabled", "budget_tokens": 4095},
        "temperature": 1,
    },
    "persona": {
        "max_tokens": 4096,
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "temperature": 1,
    },
}


async def _seed_defaults(db) -> None:
    """Guarantee an exact, known deployment-default seed inside this test's txn.

    Clear any committed NULL-workspace config rows (an app-lifespan test may have
    committed them, possibly with a different master key for credentials), then
    re-seed the tier bindings deterministically via the real registry. All of this
    rolls back with the test transaction, leaving real deployment defaults intact.
    """
    await db.execute(delete(ModelBinding).where(ModelBinding.workspace_id.is_(None)))
    await db.execute(delete(ProviderCredential).where(ProviderCredential.workspace_id.is_(None)))
    await ModelConfigRegistry(db).seed_defaults()
    await db.flush()


async def test_resolved_per_agent_kwargs_match_pre_cutover():
    """Each agent resolves to the exact pre-cutover kwargs (executor = sole deviation)."""
    # Guard: keep the expectation table in lockstep with the real agent registry.
    assert set(AGENTS) == set(_EXPECTED_KWARGS), (
        "agent registry changed; update the expected-kwargs table"
    )

    async with _session() as db:
        await _seed_defaults(db)
        resolver = ModelResolver(db)

        for name, agent in sorted(AGENTS.items()):
            resolved = await resolver.resolve(
                agent=agent.name,
                agent_tier=agent.model_tier,
                workspace_id=None,
                thinking_enabled=agent.thinking.enabled,
            )
            assert isinstance(resolved, ResolvedModel)
            assert resolved.provider == "anthropic", f"{name}: provider"
            assert resolved.model_id == _EXPECTED_MODEL_ID[agent.model_tier], f"{name}: model_id"
            assert resolved.kwargs == _EXPECTED_KWARGS[name], (
                f"{name}: resolved kwargs {resolved.kwargs!r} "
                f"!= expected {_EXPECTED_KWARGS[name]!r}"
            )
