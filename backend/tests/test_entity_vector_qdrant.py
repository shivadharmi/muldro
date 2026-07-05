"""Real-Qdrant proof that an entity upserted with the fixed payload is returned by
a workspace-scoped vector search. Skips when Qdrant is unreachable (bring it up
with `docker compose up -d qdrant`). Uses a deterministic fake vector (no Voyage
call)."""

import asyncio

import pytest

from src.config.settings import get_settings
from src.services.vector_store import VectorStore


def _qdrant_reachable() -> bool:
    settings = get_settings()
    if not settings.qdrant_url:
        return False

    async def _probe() -> bool:
        vs = VectorStore(settings)
        client = await vs._get_client()
        if client is None:
            return False
        await client.get_collections()
        return True

    try:
        return asyncio.run(_probe())
    except Exception:  # pragma: no cover - environment-dependent
        return False


pytestmark = pytest.mark.skipif(not _qdrant_reachable(), reason="Qdrant not reachable")


async def test_workspace_scoped_entity_vector_search_returns_the_point():
    from ulid import ULID

    vs = VectorStore(get_settings())
    await vs.ensure_collections()
    user_id = f"usr_{ULID()}"
    workspace_id = f"ws_{ULID()}"
    entity_id = f"ent_{ULID()}"
    vector = [0.0] * 1024
    vector[0] = 1.0  # deterministic, no embedding provider needed

    from src.services.world_model import _entity_vector_payload

    await vs.upsert(
        "entities",
        entity_id,
        vector,
        _entity_vector_payload("person", "Bob Smith", user_id, workspace_id),
        user_id,
    )
    hits = await vs.search(
        "entities", vector, user_id, filters={"workspace_id": workspace_id}, limit=5
    )
    assert any(h["id"] == entity_id for h in hits), f"scoped vector search missed it: {hits}"

    # Cleanup.
    await vs.delete("entities", entity_id)
