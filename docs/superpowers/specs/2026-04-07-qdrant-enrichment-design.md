# Spec 5A: Qdrant Enrichment

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 0 (Foundation Hardening) — memory expiration fix is prerequisite
**Builds toward:** Spec 5B (Neo4j Enrichment), enhances Specs 2A and 4A

## Problem Statement

Qdrant has 4 collections but only 2 are populated (memories, entities). Events and artifacts collections are empty. Conversations, plans, and approvals are not vectorized. Memory payloads are minimal (missing confidence, stability, timestamps), forcing a Postgres round-trip after every vector search. No payload indexing exists.

## Design

### Component 1: Populate Events Collection

When `EventProcessor` ingests events with importance_score >= 0.3, embed and store in Qdrant:

```python
# In event_processor.py, after Postgres insert
if event.importance_score >= 0.3 and embedding_service and vector_store:
    text = f"{event.title}: {event.summary}"
    embedding = await embedding_service.embed_text(text)
    if embedding:
        await vector_store.upsert(
            collection="events",
            id=event.event_id,
            vector=embedding,
            payload={
                "event_type": event.event_type,
                "source": event.source,
                "importance_score": event.importance_score,
                "occurred_at": event.occurred_at.isoformat(),
                "actor": event.actor_name,
            },
            user_id=event.user_id,
        )
```

### Component 2: Add Conversations Collection

New collection constant in `vector_store.py`: `COLLECTION_CONVERSATIONS = "conversations"`

Embed conversation summaries when `_summarize_history` runs in `jarvis.py`:

```python
# After summarization produces summary text
if vector_store and embedding_service:
    embedding = await embedding_service.embed_text(summary)
    if embedding:
        await vector_store.upsert(
            collection="conversations",
            id=conversation_id,
            vector=embedding,
            payload={
                "conversation_id": conversation_id,
                "message_count": len(messages),
                "created_at": conversation.created_at.isoformat(),
            },
            user_id=user_id,
        )
```

### Component 3: Add Approvals Collection

New collection: `COLLECTION_APPROVALS = "approvals"`

Embed approval decisions after user approves/rejects (alongside Spec 2A trust feedback):

```python
# In routes_approvals.py, after approve/reject
text = f"{approval.capability}: {approval.summary} → {outcome}"
embedding = await embedding_service.embed_text(text)
if embedding:
    await vector_store.upsert(
        collection="approvals",
        id=approval.approval_id,
        vector=embedding,
        payload={
            "capability": capability,
            "risk_level": risk_level,
            "outcome": outcome,  # "approved" or "rejected"
            "decided_at": approval.decided_at.isoformat(),
        },
        user_id=user_id,
    )
```

### Component 4: Populate Artifacts Collection

Embed artifact titles on creation:

```python
# In artifact_storage.py, after S3 upload + Postgres insert
if embedding_service and vector_store:
    text = f"{artifact.title}: {artifact.description or ''}"
    embedding = await embedding_service.embed_text(text)
    if embedding:
        await vector_store.upsert(
            collection="artifacts",
            id=artifact.artifact_id,
            vector=embedding,
            payload={
                "mime_type": artifact.mime_type,
                "artifact_type": artifact.artifact_type,
                "created_at": artifact.created_at.isoformat(),
            },
            user_id=user_id,
        )
```

### Component 5: Payload Indexing

Add Qdrant payload indexes for filtered search:

```python
async def ensure_indexes(self) -> None:
    client = await self._get_client()
    if not client:
        return
    from qdrant_client.models import PayloadSchemaType

    indexes = {
        COLLECTION_MEMORIES: [
            ("memory_type", PayloadSchemaType.KEYWORD),
            ("confidence", PayloadSchemaType.FLOAT),
        ],
        COLLECTION_ENTITIES: [
            ("entity_type", PayloadSchemaType.KEYWORD),
        ],
        COLLECTION_EVENTS: [
            ("source", PayloadSchemaType.KEYWORD),
            ("event_type", PayloadSchemaType.KEYWORD),
            ("importance_score", PayloadSchemaType.FLOAT),
        ],
    }
    for collection, fields in indexes.items():
        for field_name, schema_type in fields:
            try:
                await client.create_payload_index(
                    collection_name=collection, field_name=field_name, field_schema=schema_type,
                )
            except Exception:
                pass
```

Call `ensure_indexes()` after `ensure_collections()` in startup.

### Component 6: Richer Memory Payloads

Extend memory Qdrant payloads to include scoring metadata:

```python
# Current payload: {"memory_type": str, "fact_text": str}
# New payload:
{
    "memory_type": str,
    "fact_text": str,
    "confidence": float,
    "stability_score": float,
    "entity_ids": list[str],
    "created_at": str,
    "scope": str,
    "preference_strength": str | None,  # "strong", "moderate", "weak"
}
```

TriSearch can compute composite score directly from Qdrant payload — skip Postgres round-trip.

### Component 7: Memory Expiration Job

Add `_tick_memory_expiration()` to scheduler:

```python
async def _tick_memory_expiration(self):
    """Mark expired memories and cascade delete from Qdrant."""
    async with self._db_factory() as db:
        expired = await db.execute(
            select(Memory).where(
                Memory.status == "active",
                Memory.ttl_days.isnot(None),
                Memory.created_at + func.make_interval(days=Memory.ttl_days) < func.now(),
            ).limit(100)
        )
        for mem in expired.scalars():
            mem.status = "expired"
            if vector_store:
                await vector_store.delete("memories", mem.memory_id)
        await db.commit()
```

## Absorbed Issues from Audit

**Issue #3 — Expired memories still returned:** Component 7 fixes this.

**Issue #25 — Preference strength unused:** Stored in enriched payload (Component 6). TriSearch scoring updated to use it.

## Files Changed

### New Files
- `tests/test_event_embedding.py`
- `tests/test_payload_indexing.py`

### Modified Files
- `src/services/vector_store.py` — Add `COLLECTION_CONVERSATIONS`, `COLLECTION_APPROVALS` constants. Add `ensure_indexes()`. Create new collections in `ensure_collections()`.
- `src/services/event_processor.py` — Add event embedding after Postgres insert
- `src/services/artifact_storage.py` — Add artifact embedding on create
- `src/services/memory_service.py` — Enrich Qdrant payloads with confidence, stability, entity_ids, preference_strength
- `src/services/tri_search.py` — Use enriched payloads for composite scoring (skip Postgres round-trip). Add preference_strength boost.
- `src/orchestrator/jarvis.py` — Embed conversation summaries after `_summarize_history`
- `src/api/routes_approvals.py` — Embed approval decisions (alongside trust feedback)
- `src/services/scheduler.py` — Add `_tick_memory_expiration()`

## Testing Strategy

- Unit tests: event embedding (conditional on importance threshold)
- Unit tests: conversation summary embedding
- Unit tests: approval embedding (correct text format)
- Unit tests: artifact embedding
- Unit tests: payload indexing creation
- Unit tests: richer memory payloads include all fields
- Unit tests: memory expiration marks expired + cascades to Qdrant
- Integration: store event → search by similarity → found
- Integration: TriSearch uses enriched payload for scoring (no Postgres query)

## Success Criteria

1. Events collection populated (importance >= 0.3)
2. Conversations collection populated (on history summarization)
3. Approvals collection populated (on approve/reject)
4. Artifacts collection populated (on create)
5. Payload indexing enables filtered search by memory_type, entity_type, source
6. TriSearch skips Postgres round-trip using enriched memory payloads
7. Expired memories cleaned from both Postgres and Qdrant

## Blast Radius

**Low — mostly additive embedding calls in existing write paths.**

| File | Change | Risk |
|------|--------|------|
| `src/services/vector_store.py` | Add constants + ensure_indexes | **LOW** — additive |
| `src/services/event_processor.py` | Add embedding call | **LOW** — after existing insert |
| `src/services/memory_service.py` | Richer payloads | **LOW** — additive fields |
| `src/services/tri_search.py` | Use enriched payloads | **MEDIUM** — changes scoring path |
| `src/services/scheduler.py` | Add expiration tick | **LOW** — new tick method |

### Total: ~15 files (8 modified, 2 new tests, 5 existing tests updated)
