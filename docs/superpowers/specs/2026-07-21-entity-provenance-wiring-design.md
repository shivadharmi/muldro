# Entity Provenance Wiring — Design Spec

**Date:** 2026-07-21
**Branch:** `rebuild/first-principles`
**Status:** Approved design → ready for implementation plan
**Scope:** Subsystem C (entity provenance write-path). Forward-only — NO backfill of existing data. Separate from Subsystem A (perception cost) and its deferred junk cleanup.

---

## 1. Problem (code + data proven)

The entity-provenance layer is wired structurally at every level but the originating event id is never threaded through it, so it is empty by construction.

- **`entities.source_refs`** is JSON `null` for **all 201** entities. There are exactly two callers of `WorldModel.upsert_entity` (`extract_from_event` at `world_model.py:323`, `extract_from_text` at `:755`) and **neither passes `source_refs`** — the param defaults to `None`.
- **`entity_facts.provenance.source_ref`** is `null` for **all 706** facts. Every provenance dict is `{"origin":"perception","source_ref":null,"observed_at":…,"reliability":0.7}`. `EntityFactStore.record_fact` **already accepts** `source_ref: dict | None` (`entity_facts/store.py:43`) and writes it into provenance (`:79`) — but `WorldModel._record_attribute_facts` (`:496`) calls it without a `source_ref`.
- **The event id is available and dropped:** `extract_from_event(event_id, …)` holds the id, extracts entities, and calls `upsert_entity(…)` omitting it. `upsert_entity` then calls `_record_attribute_facts(…, origin, now)` omitting it.

This is an **unfinished feature**, not a null-handling bug: the bi-temporal fact system (`entity_facts` with `provenance`/`valid_from`/`valid_to`/`superseded_by`/`corroboration_count`) is fully built; per-observation event provenance was designed (the `source_ref` slot exists everywhere) but never filled.

**Dormant consumer:** `knowledge_service.py` (`_source_systems_for_entity`, `:70`; used at `:398`) derives an entity's source-system slugs from `entity.source_refs` — always empty today.

## 2. Goal & scope

**Immediate goal:** correctly wire provenance **going forward** so every entity and fact created after this change records where it came from, and the existing `knowledge_service` consumer works.

**In scope:**
- Thread a typed `SourceRef` through the write path: `extract_from_event` / `extract_from_text` → `upsert_entity` → `_record_attribute_facts` → `record_fact`.
- Accumulate `entity.source_refs` with dedup + cap.
- Make `knowledge_service` source-system resolution work (verified by test).

**Out of scope (explicit):**
- **No backfill** of the existing 201 entities / 706 facts (they were created without provenance; leave them).
- Reverse-lookup query ("which entities did event X source?").
- The Subsystem-A junk cleanup (blocked on this, handled separately later).
- **Relationship provenance** — `WorldModel.add_relationship` has the same gap; documented as a follow-up, not fixed here.
- UI explainability surfacing.

## 3. Data shape — `SourceRef` (Pydantic)

A typed model replaces the untyped `dict | None` slot (the root cause was an untyped null-able field). New leaf module `src/services/provenance.py`:

```python
from pydantic import BaseModel, ConfigDict


class SourceRef(BaseModel):
    """Where an entity/fact observation came from. `source` is always present;
    `event_id` set when event-sourced, `run_id` when outcome-sourced."""

    model_config = ConfigDict(frozen=True)

    source: str                      # source-system slug: gmail, slack, interaction, outcome, …
    event_id: str | None = None      # normalized_event id when event-sourced
    run_id: str | None = None        # task_run id when outcome-sourced
```

- Serialized to JSONB via `model_dump(exclude_none=True)` at the storage boundary (entity.source_refs entries and fact provenance.source_ref).
- **Dedup key:** `event_id` when present, else `run_id`, else `source`.

Also in `provenance.py`, a pure helper for the accumulation policy:

```python
def merge_source_refs(existing: list[dict] | None, new: SourceRef, cap: int = 20) -> list[dict]:
    """Append `new` to `existing` (list of serialized SourceRef dicts), dedup by
    the ref's dedup key, keep the most-recent `cap`."""
```

## 4. Write-path threading (all in `world_model.py` + 2 caller sites)

- **`extract_from_event`**: build `ref = SourceRef(source=event.source, event_id=event_id)`; pass to `upsert_entity(source_ref=ref, …)`.
- **`extract_from_text`**: add optional `source_ref: SourceRef | None = None` param; thread it to `upsert_entity`. Callers pass it:
  - `interaction_learner.py:152` → `SourceRef(source="interaction")`
  - `outcome_learner.py:193` → `SourceRef(source="outcome", run_id=<run_id if available>)`
- **`upsert_entity`**: replace the unused `source_refs: list[dict] | None` param with `source_ref: SourceRef | None`.
  - **Create branch:** `source_refs = [source_ref.model_dump(exclude_none=True)]` when present, else `None`.
  - **Update branch:** `existing.source_refs = merge_source_refs(existing.source_refs, source_ref)` when a ref is present (this branch currently ignores provenance entirely).
  - Pass `source_ref` into both `_record_attribute_facts` calls (create + update branches, `:375` and `:454`).
- **`_record_attribute_facts`**: add `source_ref: SourceRef | None` param; pass `source_ref=source_ref.model_dump(exclude_none=True) if source_ref else None` to `store.record_fact(…)`.

No change to `EntityFactStore.record_fact` (already accepts `source_ref`).

## 5. Consumer

`knowledge_service._source_systems_for_entity` reads the `source` key from each `source_refs` dict — works unchanged once `source_refs` is populated. Covered by a test that builds an entity via `upsert_entity` with a `SourceRef` and asserts the source-system resolves (was always empty before).

## 6. Testing (TDD)

- `SourceRef` requires `source`; `model_dump(exclude_none=True)` omits null event_id/run_id.
- `merge_source_refs`: appends, dedups by event_id/run_id/source, caps at 20 keeping most-recent.
- `upsert_entity` **create** with a `SourceRef` → `entity.source_refs == [ref.model_dump(...)]`.
- `upsert_entity` **update** of an existing entity with a new event's `SourceRef` → source_refs accumulates (dedup + cap).
- `_record_attribute_facts` → each written fact's `provenance["source_ref"]` equals the serialized ref (not null).
- `extract_from_event` end-to-end → created entities have `source_refs`; their facts have `provenance.source_ref` with the event_id.
- `extract_from_text` with a caller `SourceRef` → provenance carries `{"source":"interaction"}` (no event_id).
- `knowledge_service` resolves source-systems for an entity with populated `source_refs`.

## 7. Success criteria

- Every entity/fact created after this change records a non-null `SourceRef` (verified by the end-to-end tests).
- `entity.source_refs` stays bounded (≤ cap) for hot entities.
- `knowledge_service` source-systems is non-empty for newly-sourced entities.
- No change to existing rows; full gate green; no regression in `world_model`/`entity_facts`/`knowledge_service` tests.

## 8. Follow-ups (not this spec)

- Relationship provenance (`add_relationship`).
- Backfill / re-extraction to give the existing 201 entities provenance.
- Reverse-lookup query + the Subsystem-A junk cleanup it unblocks.
