# Entity Provenance Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread a typed `SourceRef` through the entity-extraction write path so every entity and fact created going forward records where it came from.

**Architecture:** A new Pydantic `SourceRef` model + a `merge_source_refs` accumulation helper (`src/services/provenance.py`). The event id (already held by `extract_from_event`) is threaded: `extract_from_event`/`extract_from_text` → `upsert_entity(source_ref=…)` → `_record_attribute_facts(source_ref=…)` → `record_fact(source_ref=…)` (which already accepts it). `entity.source_refs` accumulates dedup+cap-20; each fact's `provenance.source_ref` carries the ref. Forward-only — NO backfill.

**Tech Stack:** Python 3.12, async SQLAlchemy, Pydantic v2, pytest (custom `asyncio.run` hook — NO pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-07-21-entity-provenance-wiring-design.md`

**Test harness reminders:**
- Full gate: `cd backend && uv run pytest tests/ --ignore=tests/e2e`
- NO `asyncio_mode`; async tests use `asyncio.run(...)`.
- Real-DB tests (for `upsert_entity`/facts) use the self-contained `_db_reachable`/NullPool/seed-User→Workspace pattern — mirror existing `world_model`/`entity_facts` tests. Local docker infra is up this session.
- Commit messages: conventional, NO `Co-Authored-By`.
- After the last task, run `uv run python scripts/check_file_size.py` — the pre-commit hook does NOT enforce the 800-line cap (only ruff does).

---

## File Structure

**New:**
- `backend/src/services/provenance.py` — `SourceRef` model + `merge_source_refs` helper. One responsibility: the provenance data shape + accumulation policy.
- `backend/tests/services/test_provenance.py` — unit tests for the model + helper.

**Modified:**
- `backend/src/services/world_model.py` — `_record_attribute_facts`, `upsert_entity`, `extract_from_event`, `extract_from_text` (thread `source_ref`).
- `backend/src/services/interaction_learner.py` — pass `SourceRef(source="interaction")`.
- `backend/src/services/outcome_learner.py` — pass `SourceRef(source="outcome", run_id=run_id)`.
- Tests alongside each.

---

## Task 1: `SourceRef` model + `merge_source_refs` helper

**Files:**
- Create: `backend/src/services/provenance.py`
- Test: `backend/tests/services/test_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_provenance.py
from src.services.provenance import SourceRef, merge_source_refs


def test_sourceref_requires_source_omits_none():
    ref = SourceRef(source="gmail", event_id="evt_1")
    assert ref.to_dict() == {"source": "gmail", "event_id": "evt_1"}  # run_id omitted (None)


def test_sourceref_dedup_key_prefers_event_then_run_then_source():
    assert SourceRef(source="gmail", event_id="evt_1").dedup_key() == "evt_1"
    assert SourceRef(source="outcome", run_id="run_1").dedup_key() == "run_1"
    assert SourceRef(source="interaction").dedup_key() == "interaction"


def test_merge_appends_and_dedups_by_key():
    existing = [{"source": "gmail", "event_id": "evt_1"}]
    out = merge_source_refs(existing, SourceRef(source="gmail", event_id="evt_2"))
    assert out == [
        {"source": "gmail", "event_id": "evt_1"},
        {"source": "gmail", "event_id": "evt_2"},
    ]


def test_merge_replaces_same_key_moving_to_end():
    existing = [
        {"source": "gmail", "event_id": "evt_1"},
        {"source": "gmail", "event_id": "evt_2"},
    ]
    out = merge_source_refs(existing, SourceRef(source="gmail", event_id="evt_1"))
    assert out == [
        {"source": "gmail", "event_id": "evt_2"},
        {"source": "gmail", "event_id": "evt_1"},
    ]  # evt_1 deduped, re-appended most-recent


def test_merge_caps_to_most_recent():
    existing = [{"source": "s", "event_id": f"evt_{i}"} for i in range(20)]
    out = merge_source_refs(existing, SourceRef(source="s", event_id="evt_new"), cap=20)
    assert len(out) == 20
    assert out[-1] == {"source": "s", "event_id": "evt_new"}
    assert {"source": "s", "event_id": "evt_0"} not in out  # oldest dropped


def test_merge_handles_none_existing():
    out = merge_source_refs(None, SourceRef(source="slack", event_id="evt_x"))
    assert out == [{"source": "slack", "event_id": "evt_x"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/test_provenance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.provenance'`

- [ ] **Step 3: Write the module**

```python
# backend/src/services/provenance.py
"""Entity/fact provenance — where an observation came from.

A typed replacement for the previously-unwired ``dict | None`` source_ref slot.
``source`` is always present; ``event_id`` is set for event-sourced extraction,
``run_id`` for outcome-sourced. Serialized to JSONB at the storage boundary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    event_id: str | None = None
    run_id: str | None = None

    def dedup_key(self) -> str:
        """Identity for dedup: event_id, else run_id, else source."""
        return self.event_id or self.run_id or self.source

    def to_dict(self) -> dict:
        """JSONB-ready dict, omitting null event_id/run_id."""
        return self.model_dump(exclude_none=True)


def _key_of(ref: dict) -> str:
    return ref.get("event_id") or ref.get("run_id") or ref.get("source") or ""


def merge_source_refs(existing: list[dict] | None, new: SourceRef, cap: int = 20) -> list[dict]:
    """Append ``new`` to ``existing`` (list of serialized SourceRef dicts),
    deduping by dedup key (the matching prior ref is dropped and the new one
    appended as most-recent), keeping the most-recent ``cap``."""
    refs = [r for r in existing if isinstance(r, dict)] if isinstance(existing, list) else []
    new_key = new.dedup_key()
    refs = [r for r in refs if _key_of(r) != new_key]
    refs.append(new.to_dict())
    return refs[-cap:]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/services/test_provenance.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/provenance.py backend/tests/services/test_provenance.py
git commit -m "feat(provenance): SourceRef model + merge_source_refs accumulation helper"
```

---

## Task 2: Thread `source_ref` through `_record_attribute_facts`

**Files:**
- Modify: `backend/src/services/world_model.py` — `_record_attribute_facts` (~line 496)
- Test: `backend/tests/services/test_world_model_provenance.py` (new)

> `EntityFactStore.record_fact` already accepts `source_ref: dict | None` and writes it into `provenance` (`entity_facts/store.py:43,79`). This task passes it from `_record_attribute_facts`; no fact-store change.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_world_model_provenance.py
import asyncio
from unittest.mock import AsyncMock, patch

from src.services.provenance import SourceRef


def test_record_attribute_facts_passes_source_ref():
    # _record_attribute_facts must forward the serialized SourceRef to record_fact.
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    with patch("src.services.entity_facts.store.EntityFactStore.record_fact",
               new=AsyncMock(return_value=("fact_1", False))) as rec:
        asyncio.run(wm._record_attribute_facts(
            "ent_1", "user_1", "ws_1", {"role": "investor"}, "perception",
            __import__("datetime").datetime(2026, 7, 21, tzinfo=__import__("datetime").timezone.utc),
            source_ref=SourceRef(source="gmail", event_id="evt_9"),
        ))
    assert rec.await_count == 1
    assert rec.call_args.kwargs["source_ref"] == {"source": "gmail", "event_id": "evt_9"}


def test_record_attribute_facts_none_source_ref_passes_none():
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    with patch("src.services.entity_facts.store.EntityFactStore.record_fact",
               new=AsyncMock(return_value=("fact_1", False))) as rec:
        asyncio.run(wm._record_attribute_facts(
            "ent_1", "user_1", "ws_1", {"role": "investor"}, "perception",
            __import__("datetime").datetime(2026, 7, 21, tzinfo=__import__("datetime").timezone.utc),
        ))
    assert rec.call_args.kwargs["source_ref"] is None
```

> Verify `WorldModel(settings=…, db=…)` is the real constructor shape (mirror existing world_model tests). If it needs more args, adjust. `_record_attribute_facts` only uses `self._db` (indirectly via EntityFactStore), so an AsyncMock db is fine.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/test_world_model_provenance.py -v`
Expected: FAIL — `_record_attribute_facts()` got an unexpected keyword argument `source_ref`

- [ ] **Step 3: Add the `source_ref` param and forward it**

In `world_model.py`, change `_record_attribute_facts` signature and the `record_fact` call:

```python
    async def _record_attribute_facts(
        self,
        entity_id: str,
        user_id: str,
        workspace_id: str,
        attributes: dict,
        origin: str,
        now: datetime,
        source_ref: "SourceRef | None" = None,
    ) -> None:
        """Record each attribute as a bi-temporal fact (supersede-on-change). The
        entities.attributes JSONB stays the denormalized current snapshot (D2)."""
        from src.services.entity_facts.store import EntityFactStore

        store = EntityFactStore(self._db)
        ref_dict = source_ref.to_dict() if source_ref else None
        for attr_key, attr_value in attributes.items():
            try:
                await store.record_fact(
                    entity_id=entity_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    attr_key=str(attr_key),
                    attr_value=attr_value,
                    origin=origin,
                    source_ref=ref_dict,
                    now=now,
                )
            except Exception:
                logger.debug(
                    "entity_fact record failed: entity=%s key=%s",
                    entity_id,
                    attr_key,
                    exc_info=True,
                )
```

Add the import at the top of `world_model.py` (with the other imports):

```python
from src.services.provenance import SourceRef, merge_source_refs
```

(`merge_source_refs` is used in Task 3.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/services/test_world_model_provenance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/world_model.py backend/tests/services/test_world_model_provenance.py
git commit -m "feat(provenance): thread source_ref through _record_attribute_facts to record_fact"
```

---

## Task 3: `upsert_entity` — accept `source_ref`, wire entity list + facts

**Files:**
- Modify: `backend/src/services/world_model.py` — `upsert_entity` (~line 349)
- Test: `backend/tests/services/test_world_model_provenance.py`

> Replace the unused `source_refs: list[dict] | None = None` param with `source_ref: SourceRef | None = None`. No external caller passes the old `source_refs=` kwarg (verified: only 2 callers, neither passes it), so this is safe.

- [ ] **Step 1: Write the failing test (real DB — mirror existing world_model DB tests)**

```python
# append to backend/tests/services/test_world_model_provenance.py
# Real-DB test: entity created via upsert_entity carries source_refs; a second
# upsert with a new event accumulates (dedup+cap). Guard with the repo's
# _db_reachable pattern (mirror tests/test_world_model_*.py real-DB setup:
# NullPool engine, seed User->Workspace FK chain, construct WorldModel(settings, db)).
#
# Assertions (the contract):
#   ent_id = await wm.upsert_entity(user_id=U, entity_type="person",
#       canonical_name="Jane VC", attributes={"role": "investor"},
#       workspace_id=WS, origin="perception",
#       source_ref=SourceRef(source="gmail", event_id="evt_1"))
#   row = <load Entity by ent_id>
#   assert row.source_refs == [{"source": "gmail", "event_id": "evt_1"}]
#   # second observation, different event, same entity name -> update branch
#   await wm.upsert_entity(user_id=U, entity_type="person", canonical_name="Jane VC",
#       attributes={"role": "investor"}, workspace_id=WS, origin="perception",
#       source_ref=SourceRef(source="gmail", event_id="evt_2"))
#   row = <reload>
#   assert row.source_refs == [
#       {"source": "gmail", "event_id": "evt_1"},
#       {"source": "gmail", "event_id": "evt_2"},
#   ]
#   # and the fact carries provenance.source_ref for the latest event
#   facts = <load EntityFact rows for ent_id>
#   assert any(f.provenance.get("source_ref") == {"source": "gmail", "event_id": "evt_2"}
#              for f in facts)
```

Fill in the real-DB boilerplate by mirroring the existing world_model real-DB tests (find them: `grep -rln "_db_reachable\|NullPool" backend/tests/ | xargs grep -l "WorldModel"`). If no real DB is reachable, the test must skip via the same guard those tests use.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/test_world_model_provenance.py -v -k "upsert"`
Expected: FAIL — `upsert_entity()` got an unexpected keyword argument `source_ref`

- [ ] **Step 3: Change `upsert_entity`**

Replace the `source_refs: list[dict] | None = None` param in the signature with:

```python
        source_ref: SourceRef | None = None,
```

**Update branch** (`if existing:`) — where it records facts and updates temporal tracking, thread the ref into facts and accumulate on the entity. Change the fact call and add the source_refs merge:

```python
        if existing:
            if attributes:
                await self._record_attribute_facts(
                    existing.entity_id, user_id, workspace_id, attributes, origin, now,
                    source_ref=source_ref,
                )
                existing.attributes = {**(existing.attributes or {}), **attributes}
            if aliases:
                await self._add_aliases(existing.entity_id, aliases, workspace_id=workspace_id)
            if source_ref is not None:
                existing.source_refs = merge_source_refs(existing.source_refs, source_ref)
            existing.last_seen_at = now
            # ... rest of the update branch unchanged ...
```

**Create branch** — set `source_refs` from the ref and thread into the create-path fact call:

```python
        entity = Entity(
            entity_id=entity_id,
            user_id=user_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            attributes=attributes,
            source_refs=[source_ref.to_dict()] if source_ref else None,
            last_seen_at=now,
            interaction_count=1,
            importance_score=importance or 0.5,
            confidence_score=compute_confidence(origin=origin, corroboration_count=1, age_days=0.0),
        )
```

And the create-path fact recording call (~line 454):

```python
        if attributes:
            await self._record_attribute_facts(
                entity_id, user_id, workspace_id, attributes, origin, now,
                source_ref=source_ref,
            )
            await self._db.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/services/test_world_model_provenance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/world_model.py backend/tests/services/test_world_model_provenance.py
git commit -m "feat(provenance): upsert_entity records source_ref on entity + facts (dedup+cap)"
```

---

## Task 4: `extract_from_event` builds and passes the `SourceRef`

**Files:**
- Modify: `backend/src/services/world_model.py` — `extract_from_event` (~line 289), the `upsert_entity` call (~line 323)
- Test: `backend/tests/services/test_world_model_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/services/test_world_model_provenance.py
def test_extract_from_event_passes_event_sourceref():
    # extract_from_event must call upsert_entity with source_ref carrying the event_id + source.
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    ev = MagicMock()
    ev.event_id = "evt_42"
    ev.source = "gmail"
    ev.title = "t"
    ev.summary = "s"
    ev.event_type = "email_received"
    ev.actor_entities = None

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    # event lookup returns ev
    wm._db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
    with patch.object(wm, "_call_extraction",
                      new=AsyncMock(return_value={"entities": [
                          {"entity_type": "person", "canonical_name": "Jane", "attributes": {}}],
                          "relationships": []})), \
         patch.object(wm, "upsert_entity", new=AsyncMock(return_value="ent_1")) as up, \
         patch.object(wm, "_create_relationship_by_name", new=AsyncMock()):
        asyncio.run(wm.extract_from_event("evt_42", "user_1", workspace_id="ws_1"))
    assert up.await_count == 1
    passed = up.call_args.kwargs["source_ref"]
    assert passed.source == "gmail" and passed.event_id == "evt_42"
```

> Verify the real `extract_from_event` internals (how it reads the event, the extraction call name `_call_extraction`) and adjust the mocks to match. The assertion contract is fixed: `upsert_entity` receives a `source_ref` with `source=event.source, event_id=event_id`.

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL (`source_ref` not in the call / is None)

- [ ] **Step 3: Build and pass the ref**

In `extract_from_event`, in the entity loop, build the ref once (before the loop) and pass it:

```python
        ref = SourceRef(source=event.source, event_id=event_id)

        for ent_data in extracted.get("entities", []):
            raw_type = ent_data.get("entity_type", "person")
            entity_type = raw_type if raw_type in ENTITY_TYPES else "person"
            importance = min(max(float(ent_data.get("importance", 0.5)), 0.0), 1.0)

            entity_id = await self.upsert_entity(
                user_id=user_id,
                entity_type=entity_type,
                canonical_name=ent_data.get("canonical_name", "Unknown"),
                attributes=ent_data.get("attributes"),
                aliases=ent_data.get("aliases"),
                importance=importance,
                workspace_id=workspace_id,
                origin="perception",
                source_ref=ref,
            )
```

- [ ] **Step 4: Run test to verify it passes** — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/world_model.py backend/tests/services/test_world_model_provenance.py
git commit -m "feat(provenance): extract_from_event records event SourceRef on entities + facts"
```

---

## Task 5: `extract_from_text` accepts `source_ref`; both callers pass it

**Files:**
- Modify: `backend/src/services/world_model.py` — `extract_from_text` (~line 745), its `upsert_entity` call (~line 755)
- Modify: `backend/src/services/interaction_learner.py` (~line 152)
- Modify: `backend/src/services/outcome_learner.py` (~line 193)
- Test: `backend/tests/services/test_world_model_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/services/test_world_model_provenance.py
def test_extract_from_text_threads_caller_sourceref():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    with patch.object(wm, "_call_extraction",
                      new=AsyncMock(return_value={"entities": [
                          {"entity_type": "person", "canonical_name": "Jane", "attributes": {}}],
                          "relationships": []})), \
         patch.object(wm, "upsert_entity", new=AsyncMock(return_value="ent_1")) as up, \
         patch.object(wm, "_create_relationship_by_name", new=AsyncMock()):
        asyncio.run(wm.extract_from_text(
            "some text", user_id="user_1", workspace_id="ws_1",
            source_ref=SourceRef(source="interaction"),
        ))
    assert up.call_args.kwargs["source_ref"].source == "interaction"
```

> Verify `extract_from_text`'s real internals and mirror the mocks. Contract: a `source_ref` passed to `extract_from_text` reaches `upsert_entity`.

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL (`extract_from_text()` got an unexpected keyword argument `source_ref`)

- [ ] **Step 3: Thread the param**

In `world_model.py`, change `extract_from_text` signature:

```python
    async def extract_from_text(
        self, text: str, user_id: str, workspace_id: str = "",
        source_ref: SourceRef | None = None,
    ) -> list[str]:
```

and pass `source_ref=source_ref` to its `upsert_entity(...)` call (~line 755).

In `interaction_learner.py` (~line 152), pass the interaction ref:

```python
            from src.services.provenance import SourceRef

            entity_ids = await wm.extract_from_text(
                text, user_id=user_id, workspace_id=workspace_id,
                source_ref=SourceRef(source="interaction"),
            )
```

In `outcome_learner.py` (~line 193) — `run_id` is in scope (a param of the enclosing method), so:

```python
            from src.services.provenance import SourceRef

            entity_ids = await world_model.extract_from_text(
                source_text,
                user_id=user_id,
                workspace_id=workspace_id,
                source_ref=SourceRef(source="outcome", run_id=run_id),
            )
```

- [ ] **Step 4: Run test + both learner test files**

Run: `cd backend && uv run pytest tests/services/test_world_model_provenance.py -v && cd backend && uv run pytest tests/ -k "interaction_learner or outcome_learner" -v`
Expected: PASS (existing learner tests still green)

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/world_model.py backend/src/services/interaction_learner.py backend/src/services/outcome_learner.py backend/tests/services/test_world_model_provenance.py
git commit -m "feat(provenance): extract_from_text threads caller SourceRef (interaction/outcome)"
```

---

## Task 6: `knowledge_service` source-systems now resolves

**Files:**
- Test only: `backend/tests/services/test_knowledge_service_provenance.py` (new)

> `_entity_sources` (`knowledge_service.py:~68`) already reads `source` from each `source_refs` dict — no code change, just prove it works now that `source_refs` is populated (was always `[]` before).

- [ ] **Step 1: Write the test**

```python
# backend/tests/services/test_knowledge_service_provenance.py
from types import SimpleNamespace

from src.services.knowledge_service import _entity_sources


def test_entity_sources_resolves_from_populated_source_refs():
    entity = SimpleNamespace(source_refs=[
        {"source": "gmail", "event_id": "evt_1"},
        {"source": "gmail", "event_id": "evt_2"},
        {"source": "slack", "event_id": "evt_3"},
    ])
    assert _entity_sources(entity) == ["gmail", "slack"]  # dedup, order-preserving


def test_entity_sources_empty_when_null():
    assert _entity_sources(SimpleNamespace(source_refs=None)) == []
```

> Confirm `_entity_sources` is importable at module level (it is a module-level function). If it's named differently, use the real name found via `grep -n "def _entity_sources\|def _source_systems" backend/src/services/knowledge_service.py`.

- [ ] **Step 2: Run test** — Expected: PASS immediately (proves the consumer works with populated refs; no code change).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/test_knowledge_service_provenance.py
git commit -m "test(provenance): knowledge_service resolves source-systems from populated source_refs"
```

---

## Final verification

- [ ] Full gate green: `cd backend && uv run pytest tests/ --ignore=tests/e2e`
- [ ] `ruff check src/ tests/ && ruff format --check src/ tests/`
- [ ] `uv run python scripts/check_file_size.py` (pre-commit does NOT enforce the 800-line cap — `world_model.py` may be near it; if this task pushed it over, extract the provenance-threading helper or a cohesive block into a leaf module).
- [ ] Optional live check: enable perception, run a poll, confirm a freshly-created entity has non-null `source_refs` and its facts have `provenance.source_ref` with the event_id.

---

## Spec coverage check

| Spec § | Requirement | Task(s) |
|---|---|---|
| §3 | `SourceRef` Pydantic model + dedup key | 1 |
| §3 | `merge_source_refs` (dedup + cap 20) | 1 |
| §4 | thread source_ref → `_record_attribute_facts` → record_fact | 2 |
| §4 | `upsert_entity` create sets source_refs | 3 |
| §4 | `upsert_entity` update accumulates (dedup+cap) | 3 |
| §4 | `extract_from_event` builds event ref | 4 |
| §4 | `extract_from_text` param + 2 callers | 5 |
| §5 | `knowledge_service` resolves source-systems | 6 |
| §6 | TDD across model, threading, e2e, consumer | 1–6 |
| §2 | forward-only, NO backfill | (nothing touches existing rows) |
