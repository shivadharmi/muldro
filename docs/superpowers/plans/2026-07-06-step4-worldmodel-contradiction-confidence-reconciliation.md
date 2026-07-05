# Step 4 — World-Model Contradiction + Confidence + Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the world model into a *trustworthy control surface*: (1) replace the silent `{**old, **new}` entity-attribute overwrite with a **bi-temporal supersede** (`valid_to`) so contradicting beliefs are versioned, not clobbered; (2) replace the constant `confidence_score = 1.0` with **evidence-derived confidence** (source-reliability × corroboration, age-decayed — never LLM-self-reported), rendered in `to_prompt()` with provenance, and reconciled by the Step-3 verification loop (a CONFIRMED read-back **raises** a belief, a CONTRADICTED one **lowers** it — fed to abstention/ask-the-user only, never the gate); and (3) **only then** expose four workspace-filtered, fail-closed read tools — `get_entity` / `query_facts(as_of)` / `traverse` / `get_provenance`.

**Architecture:** A new bi-temporal `entity_facts` table hangs off `entities.entity_id` (one row per `(entity, attr_key)` assertion with `valid_from`/`valid_to`/`superseded_by`/`confidence`/`corroboration_count`/`provenance`). The existing `entities.attributes` JSONB stays as the denormalized *current* snapshot so the Step-2 resolver and `to_prompt` keep working. A new `backend/src/services/entity_facts/` package (mirroring the Step-1 `idempotency/` and Step-3 `verification/` precedents) holds three small modules: `confidence.py` (a pure, deterministic confidence formula + a `SOURCE_RELIABILITY` table), `store.py` (`EntityFactStore` — supersede-on-change / corroborate-on-same / insert-on-new, plus `current_facts` / `facts_as_of` / `provenance_for` / `corroborate` / `weaken`), and `reconciliation.py` (raise/lower a belief from a Step-3 `VerifyVerdict`, no-op-safe, never touching the gate). `WorldModel.upsert_entity` and the `update_entity` MCP tool — the *two* attribute-overwrite sites — route through the store and set the first-ever evidence-derived `confidence_score`. The four read tools land via the 3-place tool rule (`catalog.py` + `schemas.py` + `intelligence_server/`) + `CAPABILITY_CATALOG` + agent scopes, each workspace-filtered fail-closed like the Step-2 hydration gate.

**Tech Stack:** Python 3.12/3.13, pytest (async via the repo's home-grown `pytest_pyfunc_call` `asyncio.run` hook — **no pytest-asyncio, no `asyncio_mode`**), SQLAlchemy 2 / asyncpg, Postgres 17 (pgvector image) with JSONB, Pydantic v2, FastMCP (`@intelligence.tool`), ruff, **alembic (one real migration — new `entity_facts` table)**.

**Source spec:** [`docs/superpowers/specs/2026-06-28-first-principles-rebuild-design.md`](../specs/2026-06-28-first-principles-rebuild-design.md) §6 Step 4 (migration-order entry), §4.6 items 3→5 (world model as control surface — the detailed build order), §4.5 last paragraph (reconciliation owned by the verification loop), §4.3 (`confidence` as a gate dimension STAYS DEFERRED — today `confidence_score` is a constant 1.0).

**Depends on:** Step 3 (`e2ae451`, alembic head `b3e8c1f5a9d2`) — the green baseline this plan builds on. Baseline suite: **~3039 passed / 18 skipped** for `uv run pytest tests/ --ignore=tests/e2e`.

---

## Infra note (verified 2026-07-06 in this environment)

- **Postgres + Redis + Qdrant are live** (`docker compose ps`: all `Up`). DB is at the single alembic head **`b3e8c1f5a9d2`** (`uv run alembic current`), and `alembic check` reports **`No new upgrade operations detected.`** (drift-free). The Step-4 migration can be applied for real and real-DB integration tests can run.
- **`langgraph` / `langchain` / `deepagents` are installed** — the full suite including `tests/deep_runtime/` collects and runs.
- **This is a `uv`-managed venv with NO `pip`.** Run everything via `uv run …` (`pytest`/`alembic`/`ruff`/`python`) and `uv add …`. Plain `uv sync` drops the dev extras — use `uv sync --all-extras` if you must sync.
- **`spaCy` / any NER library is NOT installed** (irrelevant to Step 4 — Step 4 does no NER; noted for continuity).

**Run all backend commands from `backend/`.**

**Pre-flight (run once before starting):**
```bash
cd backend && uv run pytest tests/ --ignore=tests/e2e -q -p no:cacheprovider 2>&1 | tail -5
uv run alembic current 2>&1 | tail -1   # expect: b3e8c1f5a9d2 (head)
uv run alembic check  2>&1 | tail -1    # expect: No new upgrade operations detected.
```

---

## Current-state corrections (verify-don't-trust — confirmed against code 2026-07-06)

Four extraction passes established the grounding facts this plan relies on. The spec/CLAUDE.md carry point-in-time claims; the deltas that matter:

1. **There are TWO attribute-overwrite sites, not one.** Besides `WorldModel.upsert_entity` (`world_model.py:356` — `merged = {**(existing.attributes or {}), **attributes}; existing.attributes = merged`), the `update_entity` MCP tool does the same silent merge at `src/tools/intelligence_server/memory.py:100` (`existing = entity.attributes or {}; existing.update(new_attrs); entity.attributes = existing`). **Both** must route through the fact store or the bug persists on the Librarian tool path (Tasks 4 + 5).
2. **`confidence_score` is a phantom constant — never assigned by any code.** `entities.py:26` declares `confidence_score: Mapped[float] = mapped_column(Float, default=1.0)`; `grep confidence src/services/world_model.py` returns **zero hits**. Every entity gets the DB default `1.0` and it never changes. Step 4 introduces the **first-ever** assignment (Task 4). The only quality signal computed today is `importance` (clamped 0–1, assigned at `world_model.py:364`/`:391`).
3. **No bi-temporal machinery exists anywhere in `backend/src/`.** `valid_from`, `as_of`, `bitemporal` → zero hits; every `valid_to` hit is the OAuth `get_valid_token` method name. `Entity`/`EntityRelationship` have **no** `valid_from`/`valid_to`/`superseded_*` columns (`EntityRelationship` has a coarse `active`/`start_date`/`end_date` *domain*-validity trio, not system-time bi-temporal). Entity-attribute bi-temporal is **net-new → one real migration** (contrast Step 3's zero-migration posture).
4. **The memory contradiction pattern is REUSE-THE-SHAPE, not reuse-the-detector.** `memory_service/contradictions.py` detects via vector-similarity + an **LLM pairwise judge** because memories are *unstructured free text*. Its supersede is a soft single-row UPDATE (`superseded_by = new; confidence = confidence * 0.5`) + Qdrant delete + a `"memory.updated"` event — **not** a `valid_to` interval. Entity attributes are *structured key→value*, so a contradiction is a **deterministic** `same attr_key, changed attr_value` — **no LLM in the write path** (aligns with "never LLM-self-reported"). Step 4 reuses the *supersede/decay/event* shape and adds a genuine `valid_to` interval; it does **not** reuse the vector+LLM detector.
5. **The Step-3 reconciliation hookpoints are precise and already carry everything Step 4 needs.** `dag_runner._finalize_with_verification` (`dag_runner.py:491-533`) returns a `VerifyVerdict` and *deliberately does not touch trust*; the **auto-exec caller** (`dag_runner.py:401-427`) branches on `verdict == VerifyVerdict.CONFIRMED` and owns trust bookkeeping — **this is where the Step-4 belief hook goes**. The **deferred tick** `_apply_recheck` (`scheduler/deferred_verification_tick.py:58-113`) branches CONFIRMED (upgrade + direct trust write via `record_approval_decision`) / CONTRADICTED (partially_completed + notifier) — the **second** hookpoint. `VerifyVerdict` = `{CONFIRMED, CONTRADICTED, UNVERIFIED}` (`verification/readback.py:33-37`). `build_verification_meta` (`dag_runner.py:53-70`) persists `capability`/`risk_level`/`artifact_ref` to `step.output_data["verification"]`.
6. **The 4 read tools are `internal.*`, not `knowledge.*`.** `classify_capability_agent` (`capability_resolver.py:123-140`) routes `knowledge.*` → Librarian, but there are **zero** `knowledge.*` capabilities registered and no `CapabilityFamily.KNOWLEDGE` member. The consistent home is `internal.*` read caps (like `internal.search`/`internal.build_context`, `_cap(CapabilityFamily.INTERNAL, True)`), which route to Perceiver on the read path and, being `read_only=True`, are auto-exempt from read-back verification via `is_read_only_capability` (`capabilities.py:231-234`, consumed by `verification/predicate.py`).
7. **`validate_registry()` is a startup hard-gate with three relevant checks** (`tools/validation.py:53-71`): every internal tool's `capability` must be in `CAPABILITY_CATALOG`; every agent-scope capability must be in `CAPABILITY_CATALOG`; every internal tool must have a non-null capability. So the 4 new caps must be added to `CAPABILITY_CATALOG` **and** the tools registered with those caps **and** the caps added to at least one agent scope — atomically (Task 8), or startup fails.
8. **`intelligence_server` is a PACKAGE, not a file.** `src/tools/intelligence_server/{_shared,memory,observation,planning,persona}.py`; `_shared.py` holds the `intelligence = FastMCP(...)` instance, `configure()`, `_get_db()`, `request_services(db)`. Tools register by `@intelligence.tool` decorators that run on package `__init__` import — **new tool functions must be imported in `intelligence_server/__init__.py`** (and listed in `__all__`) to register.
9. **`_rank_entities` preserves added dict keys.** `context_builder.py:59` `.get()`s only its scoring keys and returns `sorted(entities, …)` without rebuilding dicts — so `confidence`/`provenance` added at the `world_model` source survive ranking through to the `to_prompt` render (Task 6).
10. **`WorldModel` receives an injected `AsyncSession` (`self._db`)** and commits internally (`world_model.py:365/407/489`); callers wrap it in `async with factory() as db:` and commit again. The fact-store writes execute on `self._db` within the existing commit boundary — no new session management.
11. **Real-DB tests use a self-contained `_entity_env()` context manager, NOT a `db_session` fixture** (there is no such fixture). The authoritative pattern is `tests/test_entity_fts_db.py` (mirroring `tests/idempotency/test_ledger_db.py`): a module-level `_db_reachable()` asyncpg probe → `pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")`; an `@asynccontextmanager async def _entity_env()` that builds its own `create_async_engine(get_settings().database_url, poolclass=NullPool)` + `async_sessionmaker(engine, expire_on_commit=False)`, **seeds the FK parents `User` + `Workspace`** (imported from `src.models.users`, where `Workspace(workspace_id=..., name=..., owner_user_id=user_id)` and `User(user_id=..., email=..., display_name=...)`), `yield`s `(factory, workspace_id, user_id)`, and on exit `delete(Workspace)` (CASCADE removes entities + entity_facts) + `delete(User)` then `await engine.dispose()`. IDs are `usr_{ULID}` / `ws_{ULID}` / `ent_{ULID}`. **Every real-DB test in this plan (Tasks 3/4/5/7/8) must adopt this harness verbatim** — the plan's earlier `db_session`/`INSERT INTO workspaces` sketches are superseded by this note. Reference scaffold to copy:

```python
import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.users import User, Workspace


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
    except Exception:  # pragma: no cover
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _entity_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"s4-{suffix}@example.com", display_name="s4"))
            db.add(Workspace(workspace_id=workspace_id, name="s4-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()
```

> **FK-ordering gotcha (learned in Task 3):** if a test seeds an `Entity` in the *same* `db` block as `User`+`Workspace`, SQLAlchemy's unit-of-work may emit the `entities` INSERT before the `workspaces` INSERT → `ForeignKeyViolationError`. Either add `await db.flush()` after the `User`+`Workspace` adds and before the `Entity` add, or seed the `Entity` in a *separate* committed `async with factory() as db:` block (the parents are already committed by the time `_entity_env` yields).

---

## Design decisions (rationale + rejected alternatives)

- **D1 — A new bi-temporal `entity_facts` table, NOT per-entity SCD-2 columns on `entities`.** The contradiction unit is an *attribute* (a key in the `attributes` JSONB), and `query_facts(as_of)` + `get_provenance` need the *history* of attribute values. A fact row per `(entity, attr_key)` with `valid_from`/`valid_to` gives exactly that. **Rejected:** adding `valid_from`/`valid_to`/`superseded_*` to the `entities` row (a literal reading of "on the existing Entity schema") — that is per-*entity* SCD-2, which would fork the stable `entity_id` PK that `entity_aliases`, `entity_relationships`, Qdrant points, and `source_refs` all reference. "On the existing schema" means *hand-roll it on our Postgres entity tables* (vs buy Graphiti), not *literally version the entity row*. The fact table hangs off `entities.entity_id` (FK CASCADE) — squarely "on the existing schema."
- **D2 — `entities.attributes` JSONB stays as the denormalized *current* snapshot.** The Step-2 resolver, `find_entity`, and `to_prompt` read `entity.attributes`; keeping it in lockstep with the current facts means zero changes to those readers and no test churn on the ~10 `upsert_entity` callers. The fact table *augments*; it is not the read source of truth for existing consumers. **Rejected:** making the fact table the only store (would require rewriting every entity-attribute reader in one step — large, risky, out of scope).
- **D3 — Contradiction detection is deterministic structural equality, no LLM.** Entity attributes are `key→value`; a contradiction is `current fact's value != new value` for the same `attr_key`. This is more correct than the memory LLM judge *and* keeps the LLM out of the write path, satisfying "never LLM-self-reported." We reuse `contradictions.py`'s *supersede/decay/event* shape (successor pointer, confidence adjustment, event emission), not its vector+LLM detector. **Rejected:** an LLM contradiction judge on attributes (unnecessary — structured values compare exactly; adds latency + an LLM dependency the spec forbids for confidence).
- **D4 — Confidence = noisy-OR of corroborating evidence, age-decayed; stored base is age-0.** `base = 1 − (1 − reliability)^corroboration_count` (n independent observations of a source of reliability `r`), and the *presented* value = `base × exp(−λ·age_days)` (30-day half-life, mirroring Step-2's continuous recency). `reliability` is a deterministic per-origin lookup (`SOURCE_RELIABILITY`), never LLM-reported. The `entity_facts.confidence` **column stores the age-0 base** (durable evidence strength); the age decay is applied live at read/render, so a fact's shown confidence decays over time without a rewrite. Reconciliation adjusts the stored base. **Rejected:** storing the fully-decayed value (would need periodic rewrites to stay fresh) or trusting an LLM confidence number (forbidden, §4.6 item 4).
- **D5 — `entities.confidence_score` becomes evidence-derived from the entity's own signals, independent of whether it has facts.** `confidence_score = compute_confidence(origin=latest_origin, corroboration_count=interaction_count, age_days=days_since(last_seen_at))`, set on every `upsert_entity`. This is the first-ever assignment (correction #2), reuses existing entity fields, and yields a meaningful entity-level number even for attribute-less entities. Per-attribute confidence lives on facts. **Rejected:** rolling entity confidence up from facts (an entity with no attributes would keep the constant `1.0`, re-introducing the very constant we're removing).
- **D6 — Reconciliation is narrow, no-op-safe, and NEVER read by the gate.** `reconcile_verdict` resolves an entity from the write's input/output (an explicit `entity_id`, else a resolvable canonical name/email in the workspace); if found, a `CONFIRMED` verdict **corroborates** that entity's current facts (raise) and a `CONTRADICTED` one **weakens** them (lower); no resolvable entity → **no-op** (logged). Confidence is surfaced to the agent only via `to_prompt` (with provenance) so the agent's own abstention/ask-the-user judgment consumes it — `TrustEngine.evaluate`/`PolicyDecision` never reads entity confidence (§4.3: confidence stays a **deferred gate dimension**). A test asserts the gate path is untouched. **Rejected:** a rich write→belief lineage mapper (speculative, unbounded — deferred); wiring confidence into the gate (explicitly forbidden until calibrated, §4.3).
- **D7 — Relationships are OUT OF SCOPE for supersede.** §4.6 item 3 is *entity-attribute* contradiction. `add_relationship` (`world_model.py:458`) is dedup-and-skip (no merge → no overwrite bug), and `EntityRelationship` already carries `active`/`start_date`/`end_date`. Adding bi-temporal columns there would be speculative (YAGNI). The `traverse` tool reads relationships as-is. Noted as an explicit deferral, not an omission.
- **D8 — The 4 read tools are `internal.*` read caps added to Librarian + Perceiver + Planner scopes.** Mirrors `internal.search`/`internal.build_context` exactly (correction #6); `read_only=True` → verification-exempt; routes to the read path. **Rejected:** `knowledge.*` (needs a new `CapabilityFamily`, has a routing short-circuit that bypasses tool-matching, and no precedent).
- **D9 — The "current fact" index is a plain composite `(entity_id, attr_key, valid_to)`, not a partial index.** A partial index (`WHERE valid_to IS NULL`) risks a false `alembic check` drift (Postgres normalizes the predicate textually, like the generated-column risk Step-2's D3 avoided). A plain composite index serves the `WHERE entity_id=? AND attr_key=? AND valid_to IS NULL` lookup and compares clean. Declared on the model `__table_args__` **and** created in the migration (Step-2's alembic-clean pattern). **Rejected:** partial index (drift risk for no material gain at current scale).

---

## In-flight-run / migration posture (spec §6 requires this per schema-touching step)

The only schema change is Task 1's **net-new, empty `entity_facts` table** (plus the first-ever *assignment* of the already-existing `entities.confidence_score` column — no column change there). Therefore:

- **Migration:** one new revision `c4f9e2a71b83` (down_revision `b3e8c1f5a9d2`) — `op.create_table("entity_facts", …)` + two `op.create_index`. Reversible: `downgrade()` drops the table (indexes go with it). Up→down→up round-trip + `alembic check` drift-free are proven against live Postgres (Task 1, Steps 6-8).
- **In-flight posture:** additive, **no drain / dual-read / reconcile needed**. A run in flight when the new code deploys keeps its already-merged `entities.attributes` (historical entities simply have **no** fact rows → empty history; `current_facts` returns `[]`, and `get_entity` shows attributes without per-fact confidence — graceful). Only `upsert_entity`/`update_entity` calls *after* the deploy populate facts. No status, checkpoint, or readiness flag reads `entity_facts`, so an in-flight run is never invalidated.
- **Resume-across-deploy:** a run paused (`awaiting_approval`) before deploy and resumed after: its next entity write populates facts normally; the new table is empty for its historical entities but that is a correct "no evidence trail yet" state, not a corruption. A resume-across-deploy assertion is included (Task 4's real-DB test writes, "deploys" by re-opening a session, and reads back the fact — proving the additive column/table survives a session boundary).
- **Shared write semantics before the dual-runtime window (spec §6 Step 4 requirement):** both attribute-write paths (`upsert_entity` **and** the `update_entity` MCP tool) route through the *one* `EntityFactStore.record_fact`, so whichever runtime (legacy `agent_loop` or the future Deep Agents lead) performs the write shares supersede semantics. There is no third attribute-write path (correction #1 — verified via the blast-radius grep).
- **Optional backfill (deferred, documented):** a one-shot to seed `entity_facts` from existing `entities.attributes` is *not* required for correctness (confidence is evidence-derived going forward; historical entities have no evidence trail). Flagged for a later data-migration if historical provenance becomes needed.

---

## File Structure

**Create (backend):**
- `backend/src/services/entity_facts/__init__.py` — package facade; re-exports `EntityFactStore`, `compute_confidence`, `reliability_for`, `current_confidence`, `SOURCE_RELIABILITY`, `reconcile_verdict`.
- `backend/src/services/entity_facts/confidence.py` — `SOURCE_RELIABILITY`, `reliability_for`, `compute_confidence`, `current_confidence`, `age_factor` (pure, no DB).
- `backend/src/services/entity_facts/store.py` — `EntityFactStore` (`record_fact`, `current_fact`, `current_facts`, `facts_as_of`, `provenance_for`, `corroborate`, `weaken`).
- `backend/src/services/entity_facts/reconciliation.py` — `reconcile_verdict` (raise/lower a belief from a `VerifyVerdict`, no-op-safe).
- `backend/src/tools/intelligence_server/world_model_tools.py` — the 4 `@intelligence.tool` read tools (`get_entity`, `query_facts`, `traverse`, `get_provenance`).
- Tests: `backend/tests/entity_facts/__init__.py`, `backend/tests/entity_facts/test_confidence.py`, `backend/tests/entity_facts/test_store_db.py` (real-DB), `backend/tests/entity_facts/test_reconciliation.py`, `backend/tests/test_entity_facts_schema.py`, `backend/tests/test_entity_fact_supersede_db.py` (real-DB), `backend/tests/test_update_entity_supersede.py`, `backend/tests/test_entity_confidence_render.py`, `backend/tests/test_world_model_query_tools.py` (real-DB), `backend/tests/test_worldmodel_recon_wiring.py`.

**Modify (backend):**
- `backend/src/models/entities.py` — add the `EntityFact` model + its indexes (declared for alembic-clean).
- `backend/alembic/versions/c4f9e2a71b83_entity_facts_bitemporal.py` — the new migration (create table + indexes).
- `backend/src/services/world_model.py` — route `upsert_entity`'s attribute merge through `EntityFactStore.record_fact`; set the evidence-derived `confidence_score`; thread an `origin` arg; add `confidence`/`provenance` to the `find_entity`/`resolve_entities` output dict; thread `origin` from `extract_from_text`/`extract_from_event`.
- `backend/src/tools/intelligence_server/memory.py` — route the `update_entity` tool's merge through `EntityFactStore.record_fact`.
- `backend/src/tools/intelligence_server/__init__.py` — import + `__all__` the 4 new tools.
- `backend/src/services/context_builder.py` — render `confidence` + provenance in `to_prompt`'s entity section.
- `backend/src/services/dag_runner.py` — call `reconcile_verdict` at the auto-exec hookpoint (after the verdict).
- `backend/src/services/scheduler/deferred_verification_tick.py` — call `reconcile_verdict` in `_apply_recheck` on CONFIRMED/CONTRADICTED.
- `backend/src/tools/catalog.py` — 4 `InternalToolDef` entries.
- `backend/src/tools/schemas.py` — 4 Pydantic input models + 4 `TOOL_INPUT_MODELS` entries.
- `backend/src/integrations/capabilities.py` — 4 `CAPABILITY_CATALOG` entries.
- `backend/src/orchestrator/agents.py` — add the 4 caps to librarian/perceiver/planner scopes.
- `backend/src/services/event_families.py` — add `ENTITY_FACT_SUPERSEDED = "entity_fact.superseded"` (adopting the dormant-constant pattern).

**Untouched (by design):** `EntityRelationship` (D7 — no supersede); `TrustEngine`/`PolicyDecision`/the gate (D6 — confidence never feeds the gate, §4.3); `memory_service/contradictions.py` (reused by shape only, not edited); the chat path (`process_message`/`_stream` — [[project_inline_trust_gap]]); the deferred tick's `read_fn=None` seam (a Step-3 carry-forward, out of scope).

---

## Task 1: `EntityFact` bi-temporal model + migration (schema-touching, real-DB proof)

The net-new store for versioned attribute beliefs. Table + two plain composite indexes; indexes declared on the model so `alembic check` stays clean (D9).

**Files:**
- Modify: `backend/src/models/entities.py`
- Create: `backend/alembic/versions/c4f9e2a71b83_entity_facts_bitemporal.py`
- Test: `backend/tests/test_entity_facts_schema.py`

- [ ] **Step 1: Write the failing schema test**

Create `backend/tests/test_entity_facts_schema.py`:

```python
"""The entity_facts model must declare its columns + indexes so the migration and
ORM agree (alembic-check clean). Inspected off table metadata — no DB needed."""

from src.models.entities import EntityFact


def test_entity_facts_table_name():
    assert EntityFact.__tablename__ == "entity_facts"


def test_entity_facts_columns_exist():
    cols = set(EntityFact.__table__.c.keys())
    expected = {
        "fact_id",
        "entity_id",
        "workspace_id",
        "user_id",
        "attr_key",
        "attr_value",
        "confidence",
        "corroboration_count",
        "provenance",
        "valid_from",
        "valid_to",
        "superseded_by",
        "created_at",
        "updated_at",
    }
    assert expected <= cols, f"missing: {expected - cols}"


def test_entity_facts_lookup_index_declared():
    idx = {i.name: [c.name for c in i.columns] for i in EntityFact.__table__.indexes}
    assert idx.get("ix_entity_facts_lookup") == ["entity_id", "attr_key", "valid_to"]
    assert "ix_entity_facts_ws" in idx


def test_valid_to_is_nullable_and_valid_from_not():
    c = EntityFact.__table__.c
    assert c.valid_to.nullable is True
    assert c.valid_from.nullable is False
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_entity_facts_schema.py -q`
Expected: FAIL at import — `cannot import name 'EntityFact'`.

- [ ] **Step 3: Add the `EntityFact` model**

In `backend/src/models/entities.py`, confirm the import line already has what we need. The current header is:

```python
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin
```

Append the `EntityFact` model at the end of the file (after `EntityRelationship`):

```python
class EntityFact(Base, TimestampMixin):
    """Bi-temporal attribute belief — one row per (entity, attr_key) assertion.

    Superseding a contradicting value closes the old row (``valid_to = now``,
    ``superseded_by = new_fact_id``) and inserts a new current row, so the full
    history is queryable as-of (spec §4.6 items 3-5). ``entities.attributes`` JSONB
    remains the denormalized *current* snapshot; these rows are the versioned truth.

    ``confidence`` stores the AGE-0 evidence base (``1 - (1 - reliability)^n``); the
    presented, age-decayed value is computed live at read time (see
    ``entity_facts.confidence.current_confidence``). ``attr_value`` holds the raw
    JSON-serialisable value (scalar or nested)."""

    __tablename__ = "entity_facts"

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attr_key: Mapped[str] = mapped_column(String(128), nullable=False)
    attr_value = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    corroboration_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_entity_facts_lookup", "entity_id", "attr_key", "valid_to"),
        Index("ix_entity_facts_ws", "workspace_id"),
    )
```

- [ ] **Step 4: Run the schema test to verify it PASSES**

Run: `uv run pytest tests/test_entity_facts_schema.py -q`
Expected: all PASS.

- [ ] **Step 5: Write the migration (hand-authored)**

Create `backend/alembic/versions/c4f9e2a71b83_entity_facts_bitemporal.py`:

```python
"""entity_facts bi-temporal attribute belief store

Revision ID: c4f9e2a71b83
Revises: b3e8c1f5a9d2
Create Date: 2026-07-06 00:00:00.000000

Net-new, empty table hanging off entities.entity_id (FK CASCADE). Versioned
attribute beliefs with valid_from/valid_to supersede (spec §6 Step 4 / §4.6 items
3-5). Additive: no existing data migrated; entities.attributes stays the current
snapshot. Indexes are also declared on the ORM model so `alembic check` stays clean.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4f9e2a71b83"
down_revision: Union[str, None] = "b3e8c1f5a9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_facts",
        sa.Column("fact_id", sa.String(length=64), primary_key=True),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("attr_key", sa.String(length=128), nullable=False),
        sa.Column("attr_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("corroboration_count", sa.Integer(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.entity_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_entity_facts_lookup",
        "entity_facts",
        ["entity_id", "attr_key", "valid_to"],
    )
    op.create_index("ix_entity_facts_ws", "entity_facts", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_entity_facts_ws", table_name="entity_facts")
    op.drop_index("ix_entity_facts_lookup", table_name="entity_facts")
    op.drop_table("entity_facts")
```

- [ ] **Step 6: Confirm the new revision is the sole head**

Run:
```bash
uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print('HEADS:', s.get_heads())"
```
Expected: `HEADS: ['c4f9e2a71b83']`.

- [ ] **Step 7: Apply + prove up/down round-trip (live Postgres)**

```bash
uv run alembic upgrade head    2>&1 | tail -3
uv run alembic downgrade -1    2>&1 | tail -3
uv run alembic upgrade head    2>&1 | tail -3
uv run alembic current         2>&1 | tail -1
```
Expected: upgrade → downgrade → upgrade all succeed; final `current` prints `c4f9e2a71b83 (head)`.

- [ ] **Step 8: Prove no schema drift**

```bash
uv run alembic check 2>&1 | tail -3
```
Expected: `No new upgrade operations detected.` If it reports the `entity_facts` indexes, confirm the model `__table_args__` from Step 3 was saved (names + column order must match the migration exactly) and re-run.

- [ ] **Step 9: Commit**

```bash
git add backend/src/models/entities.py backend/alembic/versions/c4f9e2a71b83_entity_facts_bitemporal.py backend/tests/test_entity_facts_schema.py
git commit -m "feat(rebuild): entity_facts bi-temporal attribute belief table + migration (Step 4)"
```

---

## Task 2: Pure confidence formula (`entity_facts/confidence.py`)

Deterministic, evidence-derived confidence — the heart of §4.6 item 4. Pure: no DB, no network, no LLM. Fully unit-testable.

**Files:**
- Create: `backend/src/services/entity_facts/__init__.py`, `backend/src/services/entity_facts/confidence.py`
- Test: `backend/tests/entity_facts/__init__.py`, `backend/tests/entity_facts/test_confidence.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/entity_facts/__init__.py` (empty), then `backend/tests/entity_facts/test_confidence.py`:

```python
"""Evidence-derived confidence (spec §4.6 item 4): source-reliability × corroboration,
age-decayed. Deterministic — NEVER LLM-self-reported. Pure, no DB."""

import math

from src.services.entity_facts.confidence import (
    SOURCE_RELIABILITY,
    age_factor,
    compute_confidence,
    current_confidence,
    reliability_for,
)


def test_reliability_lookup_known_and_unknown():
    assert reliability_for("user_message") == 0.95
    assert reliability_for("perception") == 0.7
    # unknown origin falls back to the explicit "unknown" reliability, not a crash
    assert reliability_for("banana") == SOURCE_RELIABILITY["unknown"]


def test_single_observation_equals_reliability_at_age_zero():
    # base = 1 - (1 - r)^1 = r ; age_factor(0) = 1
    assert abs(compute_confidence(origin="user_message", corroboration_count=1, age_days=0.0) - 0.95) < 1e-9
    assert abs(compute_confidence(origin="perception", corroboration_count=1, age_days=0.0) - 0.7) < 1e-9


def test_corroboration_raises_via_noisy_or():
    # two independent perception observations: 1 - (1 - 0.7)^2 = 0.91
    c = compute_confidence(origin="perception", corroboration_count=2, age_days=0.0)
    assert abs(c - 0.91) < 1e-9
    # more corroboration only ever increases confidence
    c3 = compute_confidence(origin="perception", corroboration_count=3, age_days=0.0)
    assert c3 > c


def test_age_decays_confidence_with_30_day_half_life():
    # after one half-life the age factor is 0.5
    assert abs(age_factor(30.0) - 0.5) < 1e-9
    fresh = compute_confidence(origin="user_message", corroboration_count=1, age_days=0.0)
    aged = compute_confidence(origin="user_message", corroboration_count=1, age_days=30.0)
    assert abs(aged - fresh * 0.5) < 1e-9


def test_confidence_is_clamped_to_unit_interval():
    assert 0.0 <= compute_confidence(origin="llm_inference", corroboration_count=1, age_days=9999) <= 1.0
    assert compute_confidence(origin="user_message", corroboration_count=100, age_days=0.0) <= 1.0


def test_current_confidence_applies_age_to_stored_base():
    # current_confidence takes a stored age-0 base + an age and decays it live
    base = 0.8
    assert abs(current_confidence(base, age_days=0.0) - 0.8) < 1e-9
    assert abs(current_confidence(base, age_days=30.0) - 0.4) < 1e-9


def test_negative_age_is_treated_as_fresh():
    assert age_factor(-5.0) == 1.0
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/entity_facts/test_confidence.py -q`
Expected: FAIL at import — `No module named 'src.services.entity_facts'`.

- [ ] **Step 3: Write the package `__init__` + `confidence.py`**

Create `backend/src/services/entity_facts/__init__.py`. **Build the facade INCREMENTALLY** — a package `__init__` runs on *any* submodule import (`from ...confidence import …` executes this file first), so it must NOT reference `store`/`reconciliation` until those modules exist (Tasks 3 and 7 extend this file). For Task 2, re-export only the confidence surface:

```python
"""World-model beliefs as a control surface (spec §4.6 items 3-5).

Bi-temporal entity-attribute facts with evidence-derived confidence: contradicting
values are superseded (valid_to) rather than clobbered; confidence is
source-reliability × corroboration, age-decayed (never LLM-self-reported); the
Step-3 verification loop reconciles beliefs (confirmed raises, divergent lowers) —
fed to abstention only, never the gate.

The public facade is built up incrementally across Step-4 tasks: Task 2 exports the
confidence surface; Task 3 adds ``EntityFactStore``; Task 7 adds ``reconcile_verdict``.
"""

from src.services.entity_facts.confidence import (
    SOURCE_RELIABILITY,
    compute_confidence,
    current_confidence,
    reliability_for,
)

__all__ = [
    "SOURCE_RELIABILITY",
    "compute_confidence",
    "current_confidence",
    "reliability_for",
]
```

> Because a submodule import triggers the package `__init__`, keeping it confidence-only here is what lets Task 2's `test_confidence.py`, Task 3's `test_store_db.py`, and Task 4's `world_model` import all resolve in order. Tasks 3 and 7 append their re-exports to this file as their modules land.

Create `backend/src/services/entity_facts/confidence.py`:

```python
"""Deterministic, evidence-derived confidence for world-model beliefs (spec §4.6
item 4). confidence = source-reliability × corroboration, age-decayed. NEVER
LLM-self-reported — reliability is a fixed per-origin lookup, corroboration is a
count, decay is exponential. Pure: no DB, no network, no LLM."""

import math

# Per-origin source reliability. Higher = more trustworthy provenance. A missing
# origin falls back to "unknown". These are deterministic weights, NOT LLM output.
SOURCE_RELIABILITY: dict[str, float] = {
    "user_message": 0.95,  # the user stated it directly
    "tool_output": 0.90,  # a tool/connector returned it
    "connector": 0.90,
    "perception": 0.70,  # observed from a monitored source
    "retrieved_memory": 0.60,  # recalled from prior stored belief
    "llm_inference": 0.50,  # inferred by the model (lowest — not self-reported confidence)
    "unknown": 0.50,
}

# 30-day half-life age decay (mirrors the Step-2 recency half-life).
_CONFIDENCE_HALF_LIFE_DAYS = 30.0
_DECAY_LAMBDA = math.log(2) / _CONFIDENCE_HALF_LIFE_DAYS


def reliability_for(origin: str) -> float:
    """Deterministic per-origin reliability weight (never LLM-reported)."""
    return SOURCE_RELIABILITY.get(origin, SOURCE_RELIABILITY["unknown"])


def age_factor(age_days: float) -> float:
    """Exponential age decay; negative/zero age → 1.0 (fresh)."""
    return math.exp(-_DECAY_LAMBDA * max(0.0, age_days))


def compute_confidence(*, origin: str, corroboration_count: int, age_days: float) -> float:
    """Full confidence = noisy-OR of `corroboration_count` independent observations
    of a source of `reliability_for(origin)`, times the age-decay factor, clamped.

    base = 1 - (1 - r)^n  (n independent corroborating observations)
    confidence = base * exp(-lambda * age_days)
    """
    r = reliability_for(origin)
    n = max(1, corroboration_count)
    base = 1.0 - (1.0 - r) ** n
    return _clamp(base * age_factor(age_days))


def current_confidence(base: float, *, age_days: float) -> float:
    """Apply live age decay to a STORED age-0 base (the value persisted on
    entity_facts.confidence). Used at read/render time so a fact's shown confidence
    decays over time without a DB rewrite."""
    return _clamp(base * age_factor(age_days))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))
```

- [ ] **Step 4: Run the test to verify it PASSES**

Run: `uv run pytest tests/entity_facts/test_confidence.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/entity_facts/__init__.py backend/src/services/entity_facts/confidence.py backend/tests/entity_facts/
git commit -m "feat(rebuild): pure evidence-derived confidence formula (Step 4)"
```

---

## Task 3: The bi-temporal fact store (`entity_facts/store.py`)

The supersede-on-change / corroborate-on-same / insert-on-new write path + the as-of read helpers. Executes on an injected `AsyncSession` (mirrors `WorldModel`, correction #10); `flush()`es, never `commit()`s (the caller owns the transaction, mirroring `contradictions.py`).

**Files:**
- Create: `backend/src/services/entity_facts/store.py`
- Modify: `backend/src/services/event_families.py`
- Test: `backend/tests/entity_facts/test_store_db.py` (real-DB, skip-if-no-Postgres)

- [ ] **Step 1: Write the failing real-DB test**

Create `backend/tests/entity_facts/test_store_db.py`:

```python
"""Bi-temporal fact store (spec §4.6 item 3): supersede-on-change, corroborate-on-same,
insert-on-new, plus current/as-of/provenance reads. Real Postgres (migration applied)."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.services.entity_facts.store import EntityFactStore

pytestmark = pytest.mark.skipif(
    not os.getenv("JARVIS_DATABASE_URL") and not os.path.exists("alembic.ini"),
    reason="requires live Postgres at head",
)

WS = "ws_test_facts"
UID = "user_test_facts"


async def _seed_entity(db, entity_id: str):
    # entity_facts.entity_id and workspace_id are FKs; create the parents first.
    from src.models.entities import Entity
    from src.models.workspace import Workspace  # noqa: F401 — imported for table existence

    await db.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO workspaces (workspace_id, name, created_at, updated_at) "
            "VALUES (:w, 'test', now(), now()) ON CONFLICT DO NOTHING"
        ),
        {"w": WS},
    )
    db.add(
        Entity(
            entity_id=entity_id,
            user_id=UID,
            workspace_id=WS,
            entity_type="person",
            canonical_name="Bob",
        )
    )
    await db.flush()


async def test_insert_then_corroborate_then_supersede(db_session):
    db = db_session
    await _seed_entity(db, "ent_facts_1")
    store = EntityFactStore(db)

    # insert-on-new
    fid1, superseded = await store.record_fact(
        entity_id="ent_facts_1",
        workspace_id=WS,
        user_id=UID,
        attr_key="role",
        attr_value="CTO",
        origin="user_message",
    )
    assert superseded is False
    current = await store.current_fact("ent_facts_1", "role", WS)
    assert current.attr_value == "CTO"
    assert current.corroboration_count == 1
    assert current.valid_to is None
    assert abs(current.confidence - 0.95) < 1e-9  # user_message, n=1

    # corroborate-on-same: same value observed again -> count rises, confidence rises, no new row
    fid1b, superseded = await store.record_fact(
        entity_id="ent_facts_1",
        workspace_id=WS,
        user_id=UID,
        attr_key="role",
        attr_value="CTO",
        origin="user_message",
    )
    assert superseded is False
    assert fid1b == fid1
    current = await store.current_fact("ent_facts_1", "role", WS)
    assert current.corroboration_count == 2
    assert current.confidence > 0.95

    # supersede-on-change: different value -> old row closed, new current row
    fid2, superseded = await store.record_fact(
        entity_id="ent_facts_1",
        workspace_id=WS,
        user_id=UID,
        attr_key="role",
        attr_value="CEO",
        origin="perception",
    )
    assert superseded is True
    assert fid2 != fid1
    current = await store.current_fact("ent_facts_1", "role", WS)
    assert current.attr_value == "CEO"
    assert current.fact_id == fid2

    # the old fact is closed (valid_to set) and points to its successor
    old = await store.get_fact(fid1)
    assert old.valid_to is not None
    assert old.superseded_by == fid2


async def test_facts_as_of_returns_the_belief_valid_at_a_past_time(db_session):
    db = db_session
    await _seed_entity(db, "ent_facts_2")
    store = EntityFactStore(db)

    t0 = datetime.now(timezone.utc) - timedelta(days=2)
    await store.record_fact(
        entity_id="ent_facts_2", workspace_id=WS, user_id=UID,
        attr_key="city", attr_value="NYC", origin="user_message", now=t0,
    )
    t1 = datetime.now(timezone.utc)
    await store.record_fact(
        entity_id="ent_facts_2", workspace_id=WS, user_id=UID,
        attr_key="city", attr_value="SF", origin="user_message", now=t1,
    )

    # as-of a time between t0 and t1 → the old belief (NYC)
    as_of = t0 + timedelta(days=1)
    facts = await store.facts_as_of("ent_facts_2", WS, as_of)
    by_key = {f.attr_key: f.attr_value for f in facts}
    assert by_key["city"] == "NYC"

    # as-of now → the current belief (SF)
    facts_now = await store.facts_as_of("ent_facts_2", WS, datetime.now(timezone.utc))
    assert {f.attr_key: f.attr_value for f in facts_now}["city"] == "SF"


async def test_workspace_isolation_is_fail_closed(db_session):
    db = db_session
    await _seed_entity(db, "ent_facts_3")
    store = EntityFactStore(db)
    await store.record_fact(
        entity_id="ent_facts_3", workspace_id=WS, user_id=UID,
        attr_key="role", attr_value="CTO", origin="user_message",
    )
    # a different workspace sees nothing
    assert await store.current_fact("ent_facts_3", "role", "ws_other") is None
    assert await store.current_facts("ent_facts_3", "ws_other") == []


async def test_corroborate_and_weaken_adjust_the_stored_base(db_session):
    db = db_session
    await _seed_entity(db, "ent_facts_4")
    store = EntityFactStore(db)
    fid, _ = await store.record_fact(
        entity_id="ent_facts_4", workspace_id=WS, user_id=UID,
        attr_key="role", attr_value="CTO", origin="perception",
    )
    before = (await store.get_fact(fid)).confidence
    await store.corroborate(fid)
    raised = (await store.get_fact(fid)).confidence
    assert raised > before
    await store.weaken(fid)
    lowered = (await store.get_fact(fid)).confidence
    assert lowered < raised
```

> `db_session` is the repo's real-DB async fixture (see `tests/conftest.py`). If the local fixture name differs, match the one Step-2's `tests/test_entity_fts_db.py` uses.

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/entity_facts/test_store_db.py -q`
Expected: FAIL at import — `cannot import name 'EntityFactStore'`.

- [ ] **Step 3: Add the superseded-event constant**

In `backend/src/services/event_families.py`, next to the existing `MEMORY_SUPERSEDED` constant (correction: `MEMORY_SUPERSEDED = "memory.superseded"` is defined but unused), add:

```python
    ENTITY_FACT_SUPERSEDED = "entity_fact.superseded"
```

(Place it beside the other entity/memory event constants; match the surrounding class/enum style — if the file uses a plain module-level assignment rather than a class attribute, mirror that. Grep the file for `MEMORY_SUPERSEDED` and add the new line immediately after it, same indentation.)

- [ ] **Step 4: Write `store.py`**

Create `backend/src/services/entity_facts/store.py`:

```python
"""Bi-temporal entity-attribute fact store (spec §4.6 item 3). Supersede-on-change
(close the old row's valid_to + insert a new current row), corroborate-on-same (raise
confidence), insert-on-new. Reuses the memory-contradiction SHAPE (successor pointer +
confidence adjustment + event emit) with DETERMINISTIC structural detection (same
attr_key, changed value) — no LLM in the write path.

Executes on an injected AsyncSession and flush()es only; the caller owns commit
(mirrors WorldModel / MemoryContradictions)."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from ulid import ULID

from src.models.entities import EntityFact
from src.services.entity_facts.confidence import compute_confidence, reliability_for

logger = logging.getLogger(__name__)


def _values_equal(a, b) -> bool:
    """Deterministic structural equality for attribute values (JSON-comparable)."""
    return a == b


class EntityFactStore:
    """Bi-temporal store over the entity_facts table."""

    def __init__(self, db):
        self._db = db

    async def record_fact(
        self,
        *,
        entity_id: str,
        workspace_id: str,
        user_id: str,
        attr_key: str,
        attr_value,
        origin: str,
        source_ref: dict | None = None,
        now: datetime | None = None,
    ) -> tuple[str, bool]:
        """Record an attribute observation. Returns (current_fact_id, superseded).

        - No current fact for (entity, attr_key)      -> insert (superseded=False)
        - Current fact with the SAME value            -> corroborate in place (False)
        - Current fact with a DIFFERENT value         -> close old + insert new (True)
        """
        now = now or datetime.now(timezone.utc)
        current = await self.current_fact(entity_id, attr_key, workspace_id)

        if current is not None and _values_equal(current.attr_value, attr_value):
            current.corroboration_count += 1
            current.confidence = compute_confidence(
                origin=origin, corroboration_count=current.corroboration_count, age_days=0.0
            )
            await self._db.flush()
            return current.fact_id, False

        fact_id = f"fact_{ULID()}"
        if current is not None:
            current.valid_to = now
            current.superseded_by = fact_id

        new_fact = EntityFact(
            fact_id=fact_id,
            entity_id=entity_id,
            workspace_id=workspace_id,
            user_id=user_id,
            attr_key=attr_key,
            attr_value=attr_value,
            corroboration_count=1,
            confidence=compute_confidence(origin=origin, corroboration_count=1, age_days=0.0),
            provenance={
                "origin": origin,
                "source_ref": source_ref,
                "observed_at": now.isoformat(),
                "reliability": reliability_for(origin),
            },
            valid_from=now,
        )
        self._db.add(new_fact)
        await self._db.flush()
        if current is not None:
            logger.info(
                "entity_fact superseded: entity=%s key=%s %s -> %s",
                entity_id, attr_key, current.fact_id, fact_id,
            )
        return fact_id, current is not None

    async def current_fact(self, entity_id: str, attr_key: str, workspace_id: str):
        """The single currently-valid fact for (entity, attr_key), or None. Workspace-scoped."""
        result = await self._db.execute(
            select(EntityFact).where(
                EntityFact.entity_id == entity_id,
                EntityFact.attr_key == attr_key,
                EntityFact.workspace_id == workspace_id,
                EntityFact.valid_to.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def current_facts(self, entity_id: str, workspace_id: str) -> list[EntityFact]:
        """All currently-valid facts for an entity. Workspace-scoped (fail-closed)."""
        result = await self._db.execute(
            select(EntityFact).where(
                EntityFact.entity_id == entity_id,
                EntityFact.workspace_id == workspace_id,
                EntityFact.valid_to.is_(None),
            )
        )
        return list(result.scalars().all())

    async def facts_as_of(
        self, entity_id: str, workspace_id: str, as_of: datetime
    ) -> list[EntityFact]:
        """The facts that were valid at `as_of` (bi-temporal query). Workspace-scoped."""
        result = await self._db.execute(
            select(EntityFact).where(
                EntityFact.entity_id == entity_id,
                EntityFact.workspace_id == workspace_id,
                EntityFact.valid_from <= as_of,
                (EntityFact.valid_to.is_(None)) | (EntityFact.valid_to > as_of),
            )
        )
        return list(result.scalars().all())

    async def provenance_for(
        self, entity_id: str, workspace_id: str, attr_key: str | None = None
    ) -> list[dict]:
        """Provenance records for the entity's current facts (optionally one key)."""
        facts = await self.current_facts(entity_id, workspace_id)
        return [
            {
                "attr_key": f.attr_key,
                "attr_value": f.attr_value,
                "confidence": f.confidence,
                "corroboration_count": f.corroboration_count,
                "valid_from": f.valid_from.isoformat() if f.valid_from else None,
                "provenance": f.provenance,
            }
            for f in facts
            if attr_key is None or f.attr_key == attr_key
        ]

    async def get_fact(self, fact_id: str):
        result = await self._db.execute(
            select(EntityFact).where(EntityFact.fact_id == fact_id)
        )
        return result.scalar_one_or_none()

    async def corroborate(self, fact_id: str) -> None:
        """Raise a belief: +1 corroboration, recompute the stored base from its origin.
        Used by post-action reconciliation on a CONFIRMED read-back (spec §4.5)."""
        fact = await self.get_fact(fact_id)
        if fact is None:
            return
        origin = (fact.provenance or {}).get("origin", "unknown")
        fact.corroboration_count += 1
        fact.confidence = compute_confidence(
            origin=origin, corroboration_count=fact.corroboration_count, age_days=0.0
        )
        await self._db.flush()

    async def weaken(self, fact_id: str) -> None:
        """Lower a belief's confidence (halve the stored base — the memory-contradiction
        decay constant). Used on a CONTRADICTED read-back (spec §4.5). Fed to
        abstention only, never the gate."""
        fact = await self.get_fact(fact_id)
        if fact is None:
            return
        fact.confidence = max(0.0, fact.confidence * 0.5)
        await self._db.flush()
```

> `from ulid import ULID` — confirm the import path matches the repo's ULID usage (`world_model.py` uses `f"ent_{ULID()}"`; grep `import ULID` and match it exactly, e.g. `from ulid import ULID` vs `from src...`).

- [ ] **Step 5: Extend the package facade with the store export**

In `backend/src/services/entity_facts/__init__.py`, add the store import + `__all__` entry (the incremental facade, now that `store.py` exists):

```python
from src.services.entity_facts.store import EntityFactStore
```

and add `"EntityFactStore"` to `__all__`.

- [ ] **Step 6: Apply migration (if not already at head) + run the test**

```bash
uv run alembic upgrade head 2>&1 | tail -1   # ensure c4f9e2a71b83
uv run pytest tests/entity_facts/test_store_db.py -q
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/entity_facts/store.py backend/src/services/entity_facts/__init__.py backend/src/services/event_families.py backend/tests/entity_facts/test_store_db.py
git commit -m "feat(rebuild): EntityFactStore bi-temporal supersede/corroborate/as-of (Step 4)"
```

---

## Task 4: Route `upsert_entity` through the fact store + first-ever evidence-derived `confidence_score`

Replace the `{**old, **new}` overwrite (correction #1, site 1) with per-attribute `record_fact`; keep `entities.attributes` as the current snapshot (D2); set the evidence-derived `confidence_score` (D5, correction #2); thread `origin` from the two extractors; add `confidence`/`provenance` to the output dict (for Task 6's render).

**Files:**
- Modify: `backend/src/services/world_model.py`
- Test: `backend/tests/test_entity_fact_supersede_db.py` (real-DB)

- [ ] **Step 1: Write the failing real-DB test**

Create `backend/tests/test_entity_fact_supersede_db.py`:

```python
"""upsert_entity supersedes contradicting attributes via entity_facts instead of the
silent {**old, **new} clobber, and sets the first-ever evidence-derived
confidence_score. Real Postgres (migration applied)."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.path.exists("alembic.ini"), reason="requires live Postgres at head"
)

WS = "ws_supersede"
UID = "user_supersede"


def _make_world_model(db):
    from src.config.settings import Settings
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    return WorldModel(settings=make_mock_settings(), db=db)


async def _seed_ws(db):
    import sqlalchemy as sa

    await db.execute(
        sa.text(
            "INSERT INTO workspaces (workspace_id, name, created_at, updated_at) "
            "VALUES (:w, 'test', now(), now()) ON CONFLICT DO NOTHING"
        ),
        {"w": WS},
    )
    await db.flush()


async def test_contradicting_attribute_is_superseded_not_clobbered(db_session):
    db = db_session
    await _seed_ws(db)
    wm = _make_world_model(db)

    eid = await wm.upsert_entity(
        user_id=UID, entity_type="person", canonical_name="Bob",
        attributes={"role": "CTO"}, workspace_id=WS, origin="user_message",
    )
    await db.commit()

    # a contradicting observation
    await wm.upsert_entity(
        user_id=UID, entity_type="person", canonical_name="Bob",
        attributes={"role": "CEO"}, workspace_id=WS, origin="perception",
    )
    await db.commit()

    from src.services.entity_facts.store import EntityFactStore

    store = EntityFactStore(db)
    current = await store.current_fact(eid, "role", WS)
    assert current.attr_value == "CEO"  # current belief updated
    facts_history = await store.facts_as_of(
        eid, WS, current.valid_from  # at the moment the new fact became valid
    )
    # both the closed CTO fact (still valid at that instant) and the new CEO fact exist in history
    all_roles = await db.execute(
        __import__("sqlalchemy").text(
            "SELECT attr_value FROM entity_facts WHERE entity_id=:e AND attr_key='role'"
        ),
        {"e": eid},
    )
    values = {row[0] for row in all_roles}
    assert values == {"CTO", "CEO"}  # history retained, not clobbered

    # entities.attributes stays the denormalized current snapshot
    from sqlalchemy import select
    from src.models.entities import Entity

    ent = (await db.execute(select(Entity).where(Entity.entity_id == eid))).scalar_one()
    assert ent.attributes["role"] == "CEO"


async def test_confidence_score_is_evidence_derived_not_constant_one(db_session):
    db = db_session
    await _seed_ws(db)
    wm = _make_world_model(db)
    eid = await wm.upsert_entity(
        user_id=UID, entity_type="person", canonical_name="Carol",
        attributes={"role": "eng"}, workspace_id=WS, origin="perception",
    )
    await db.commit()

    from sqlalchemy import select
    from src.models.entities import Entity

    ent = (await db.execute(select(Entity).where(Entity.entity_id == eid))).scalar_one()
    # perception origin, 1 interaction, fresh -> 0.7, NOT the old constant 1.0
    assert ent.confidence_score != 1.0
    assert 0.0 < ent.confidence_score <= 0.75


async def test_resolve_dict_carries_confidence_and_provenance(db_session):
    db = db_session
    await _seed_ws(db)
    wm = _make_world_model(db)
    await wm.upsert_entity(
        user_id=UID, entity_type="person", canonical_name="Dave",
        attributes={"role": "CTO"}, workspace_id=WS, origin="user_message",
    )
    await db.commit()
    results = await wm.find_entity(UID, "Dave", workspace_id=WS)
    assert results
    assert "confidence" in results[0]
    assert "provenance" in results[0]
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_entity_fact_supersede_db.py -q`
Expected: FAIL — `upsert_entity() got an unexpected keyword argument 'origin'`.

- [ ] **Step 3: Add the `origin` param + route the merge through the fact store**

In `backend/src/services/world_model.py`, change `upsert_entity`'s signature to add `origin` and compute confidence. The current signature + existing-branch (lines 336-372) is:

```python
    async def upsert_entity(
        self,
        user_id: str,
        entity_type: str,
        canonical_name: str,
        attributes: dict | None = None,
        aliases: list[str] | None = None,
        source_refs: list[dict] | None = None,
        importance: float | None = None,
        workspace_id: str = "",
    ) -> str:
        """Create or update an entity. Returns entity_id."""
        now = datetime.now(timezone.utc)
        # Privacy guard: never persist a bare email address as the canonical name.
        canonical_name, aliases = sanitize_canonical_name(canonical_name, aliases)
        existing = await self._find_by_name_or_alias(
            user_id, canonical_name, aliases, workspace_id=workspace_id
        )
        if existing:
            if attributes:
                merged = {**(existing.attributes or {}), **attributes}
                existing.attributes = merged
            if aliases:
                await self._add_aliases(existing.entity_id, aliases, workspace_id=workspace_id)
            # Update temporal tracking
            existing.last_seen_at = now
            existing.interaction_count = (existing.interaction_count or 0) + 1
            if importance is not None:
                existing.importance_score = max(existing.importance_score or 0.0, importance)
            await self._db.commit()
```

Change it to (add `origin: str = "unknown"` to the signature, and replace the `if attributes:` merge block; add the `confidence_score` assignment before commit):

```python
    async def upsert_entity(
        self,
        user_id: str,
        entity_type: str,
        canonical_name: str,
        attributes: dict | None = None,
        aliases: list[str] | None = None,
        source_refs: list[dict] | None = None,
        importance: float | None = None,
        workspace_id: str = "",
        origin: str = "unknown",
    ) -> str:
        """Create or update an entity. Returns entity_id.

        ``origin`` names the provenance of this observation (user_message / perception /
        tool_output / …) and drives evidence-derived confidence (spec §4.6 item 4).
        Contradicting attribute values are SUPERSEDED via entity_facts (not clobbered);
        ``attributes`` remains the denormalized current snapshot."""
        now = datetime.now(timezone.utc)
        # Privacy guard: never persist a bare email address as the canonical name.
        canonical_name, aliases = sanitize_canonical_name(canonical_name, aliases)
        existing = await self._find_by_name_or_alias(
            user_id, canonical_name, aliases, workspace_id=workspace_id
        )
        if existing:
            if attributes:
                await self._record_attribute_facts(
                    existing.entity_id, user_id, workspace_id, attributes, origin, now
                )
                # Keep entities.attributes as the denormalized current snapshot (D2).
                existing.attributes = {**(existing.attributes or {}), **attributes}
            if aliases:
                await self._add_aliases(existing.entity_id, aliases, workspace_id=workspace_id)
            # Update temporal tracking
            existing.last_seen_at = now
            existing.interaction_count = (existing.interaction_count or 0) + 1
            if importance is not None:
                existing.importance_score = max(existing.importance_score or 0.0, importance)
            # First-ever evidence-derived confidence_score (spec §4.6 item 4; was a
            # constant 1.0). Corroboration = interaction_count; age = 0 (just seen).
            existing.confidence_score = compute_confidence(
                origin=origin,
                corroboration_count=existing.interaction_count or 1,
                age_days=0.0,
            )
            await self._db.commit()
```

Then in the **new-entity branch**, after the entity is added and before/after the commit, record facts and set confidence. Find the new-entity construction (lines 381-407) — the `Entity(...)` build + `self._db.add(entity)`. Set `confidence_score` on construction and record facts after the successful commit. Change the `Entity(...)` constructor call to include confidence, and add fact recording. The current new-branch is:

```python
        entity = Entity(
            entity_id=entity_id,
            user_id=user_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            attributes=attributes,
            source_refs=source_refs,
            last_seen_at=now,
            interaction_count=1,
            importance_score=importance or 0.5,
        )
        self._db.add(entity)
```

Change it to add `confidence_score`:

```python
        entity = Entity(
            entity_id=entity_id,
            user_id=user_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            attributes=attributes,
            source_refs=source_refs,
            last_seen_at=now,
            interaction_count=1,
            importance_score=importance or 0.5,
            confidence_score=compute_confidence(
                origin=origin, corroboration_count=1, age_days=0.0
            ),
        )
        self._db.add(entity)
```

Then, immediately after the new-entity `await self._db.commit()` succeeds (line 407, inside the `try` after the IntegrityError guard resolves — i.e. right after the `try/except IntegrityError` block, before the Qdrant upsert), record the initial facts:

```python
        # Record initial attribute facts for the new entity (bi-temporal store).
        if attributes:
            await self._record_attribute_facts(
                entity_id, user_id, workspace_id, attributes, origin, now
            )
            await self._db.commit()
```

- [ ] **Step 4: Add the `_record_attribute_facts` helper**

Add this private method to `WorldModel` (near `upsert_entity`):

```python
    async def _record_attribute_facts(
        self,
        entity_id: str,
        user_id: str,
        workspace_id: str,
        attributes: dict,
        origin: str,
        now: datetime,
    ) -> None:
        """Record each attribute as a bi-temporal fact (supersede-on-change). The
        entities.attributes JSONB stays the denormalized current snapshot (D2)."""
        from src.services.entity_facts.store import EntityFactStore

        store = EntityFactStore(self._db)
        for attr_key, attr_value in attributes.items():
            try:
                await store.record_fact(
                    entity_id=entity_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    attr_key=str(attr_key),
                    attr_value=attr_value,
                    origin=origin,
                    now=now,
                )
            except Exception:
                # A belief-store write must never fail the entity upsert.
                logger.debug(
                    "entity_fact record failed: entity=%s key=%s", entity_id, attr_key,
                    exc_info=True,
                )
```

- [ ] **Step 5: Add the top-of-file import**

Ensure `world_model.py` imports the confidence helper. Near the other `src.services` imports at the top of the file add:

```python
from src.services.entity_facts.confidence import compute_confidence, current_confidence
from src.services.entity_facts.store import EntityFactStore  # noqa: F401 (used in helper via local import)
```

(The `EntityFactStore` local import inside `_record_attribute_facts` avoids a circular import at module load; the top-level `compute_confidence` import is safe — `confidence.py` imports nothing from `world_model`.)

- [ ] **Step 6: Add `confidence` + `provenance` to the `find_entity` output dict**

The `find_entity` dict comprehension (`world_model.py:504-513`) currently yields `entity_id`/`entity_type`/`canonical_name`/`attributes`/`importance_score`/`interaction_count`/`last_seen_at`. Add `confidence` (age-decayed live) + `provenance` (current facts). Change the comprehension so each entity dict includes:

```python
                "confidence": current_confidence(
                    e.confidence_score,
                    age_days=_days_since(e.last_seen_at),
                ),
                "provenance": {"origin_hint": e.entity_type},
```

Add the tiny `_days_since` helper at module scope (if not already present from Step 2's recency work — grep first; `context_builder.py` has `_recency_score` but `world_model.py` may not have a days-since helper):

```python
def _days_since(ts) -> float:
    if ts is None:
        return 0.0
    from datetime import datetime, timezone

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)
```

> `provenance` on the *entity* dict is a lightweight hint (the entity has no single origin); rich per-attribute provenance is exposed by the `get_provenance` tool (Task 8). Task 6 renders `confidence` in the prompt; `provenance` is available for the tool path.

- [ ] **Step 7: Thread `origin` from the two extractors**

In `extract_from_text` (`world_model.py:646`), the `upsert_entity(...)` call passes no origin. Add `origin="user_message"` (free text is user-sourced):

```python
            entity_id = await self.upsert_entity(
                user_id=user_id,
                entity_type=entity_type,
                canonical_name=ent_data.get("canonical_name", "Unknown"),
                attributes=ent_data.get("attributes"),
                aliases=ent_data.get("aliases"),
                importance=importance,
                workspace_id=workspace_id,
                origin="user_message",
            )
```

In `extract_from_event` (`world_model.py:311`), the observation came from a monitored source — add `origin="perception"`:

```python
            entity_id = await self.upsert_entity(
                user_id=user_id,
                entity_type=entity_type,
                canonical_name=ent_data.get("canonical_name", "Unknown"),
                attributes=ent_data.get("attributes"),
                aliases=ent_data.get("aliases"),
                importance=importance,
                workspace_id=workspace_id,
                origin="perception",
            )
```

- [ ] **Step 8: Run the supersede test + the existing world-model suite**

```bash
uv run pytest tests/test_entity_fact_supersede_db.py -q
uv run pytest tests/test_world_model.py tests/test_entity_dedup.py tests/test_knowledge_graph.py -q
```
Expected: the new test PASSES; the existing world-model/dedup/knowledge-graph tests still PASS (attributes snapshot behaviour unchanged; the added facts are additive).

- [ ] **Step 9: Commit**

```bash
git add backend/src/services/world_model.py backend/tests/test_entity_fact_supersede_db.py
git commit -m "feat(rebuild): upsert_entity supersedes attrs via entity_facts + evidence-derived confidence (Step 4)"
```

---

## Task 5: Route the `update_entity` MCP tool through the fact store (second overwrite site)

Correction #1, site 2 — the Librarian tool path. Same supersede semantics or the bug persists there.

**Files:**
- Modify: `backend/src/tools/intelligence_server/memory.py`
- Test: `backend/tests/test_update_entity_supersede.py` (real-DB)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_update_entity_supersede.py`:

```python
"""The update_entity MCP tool must supersede contradicting attributes via entity_facts,
not silently dict.update() the JSONB (the second overwrite site). Real Postgres."""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.path.exists("alembic.ini"), reason="requires live Postgres at head"
)

WS = "ws_update_tool"
UID = "user_update_tool"


async def test_update_entity_tool_supersedes_via_facts(db_session, configured_intelligence):
    """configured_intelligence: fixture that calls intelligence_server._shared.configure(...)
    with the test db factory + services (see how existing intelligence-tool tests set up)."""
    import sqlalchemy as sa

    from src.models.entities import Entity
    from src.services.entity_facts.store import EntityFactStore
    from src.tools.intelligence_server.memory import update_entity

    db = db_session
    await db.execute(
        sa.text(
            "INSERT INTO workspaces (workspace_id, name, created_at, updated_at) "
            "VALUES (:w,'t',now(),now()) ON CONFLICT DO NOTHING"
        ),
        {"w": WS},
    )
    db.add(
        Entity(entity_id="ent_upd", user_id=UID, workspace_id=WS,
               entity_type="person", canonical_name="Eve", attributes={"role": "CTO"})
    )
    await db.commit()

    # tool call with a contradicting value
    await update_entity(
        user_id=UID, entity_id="ent_upd",
        attributes=json.dumps({"role": "CEO"}), ctx=None, workspace_id=WS,
    )

    store = EntityFactStore(db)
    current = await store.current_fact("ent_upd", "role", WS)
    assert current is not None and current.attr_value == "CEO"
    hist = await db.execute(
        sa.text("SELECT attr_value FROM entity_facts WHERE entity_id='ent_upd' AND attr_key='role'")
    )
    assert {r[0] for r in hist} == {"CTO", "CEO"}  # history retained
```

> Match the `configured_intelligence`/`ctx` fixture pattern the existing `update_entity` tests use (grep `tests/` for `update_entity(` and `_shared.configure`). If the tool's real signature differs (e.g. `ctx` is required and non-None), adapt the call to the real signature quoted in Step 3.

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_update_entity_supersede.py -q`
Expected: FAIL — the assertion on `entity_facts` history is empty (the tool still `dict.update()`s).

- [ ] **Step 3: Route the tool's merge through the store**

In `backend/src/tools/intelligence_server/memory.py`, the current merge (lines 94-103) is:

```python
            if attributes:
                import json

                try:
                    new_attrs = json.loads(attributes)
                    existing = entity.attributes or {}
                    existing.update(new_attrs)
                    entity.attributes = existing
                except json.JSONDecodeError:
                    return {"status": "error", "error": "Invalid JSON for attributes"}
```

Change it to record facts (supersede-on-change) and keep `attributes` as the current snapshot:

```python
            if attributes:
                import json

                from src.services.entity_facts.store import EntityFactStore

                try:
                    new_attrs = json.loads(attributes)
                except json.JSONDecodeError:
                    return {"status": "error", "error": "Invalid JSON for attributes"}

                store = EntityFactStore(db)
                for attr_key, attr_value in new_attrs.items():
                    await store.record_fact(
                        entity_id=entity.entity_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        attr_key=str(attr_key),
                        attr_value=attr_value,
                        origin="tool_output",
                    )
                # entities.attributes stays the denormalized current snapshot (D2).
                entity.attributes = {**(entity.attributes or {}), **new_attrs}
```

> `db`, `entity`, `user_id`, `workspace_id` are already in scope in this tool body (the tool opens `async with _get_db() as db:` and loads `entity`). Confirm the variable names against the real function (correction #1 shows the block is inside the `update_entity` tool which has `user_id`, `workspace_id`, and a loaded `entity`). The Librarian tool origin is `"tool_output"`.

- [ ] **Step 4: Run the test to verify it PASSES**

Run: `uv run pytest tests/test_update_entity_supersede.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/tools/intelligence_server/memory.py backend/tests/test_update_entity_supersede.py
git commit -m "feat(rebuild): update_entity MCP tool supersedes via entity_facts (Step 4, 2nd overwrite site)"
```

---

## Task 6: Render `confidence` + provenance in `to_prompt()`

Spec §4.6 item 4: confidence "rendered in `to_prompt()` with provenance." This is also the **abstention feed** (D6): the agent sees confidence and can choose to ask/abstain — the value is NEVER read by the gate.

**Files:**
- Modify: `backend/src/services/context_builder.py`
- Test: `backend/tests/test_entity_confidence_render.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_entity_confidence_render.py`:

```python
"""to_prompt renders per-entity confidence (+ a low-confidence abstention hint) so the
agent can ask/abstain (spec §4.6 item 4 / §4.5). Pure string assembly — no DB."""

from src.services.context_builder import ContextBuilder, ContextPack


def _prompt_for(entities: list[dict]) -> str:
    pack = ContextPack(entities=entities)
    return ContextBuilder.to_prompt(pack)


def test_high_confidence_entity_shows_confidence():
    out = _prompt_for(
        [{"canonical_name": "Bob", "entity_type": "person", "confidence": 0.92}]
    )
    assert "Bob (person)" in out
    assert "confidence=0.92" in out


def test_low_confidence_entity_shows_an_abstention_hint():
    out = _prompt_for(
        [{"canonical_name": "Acme", "entity_type": "organization", "confidence": 0.30}]
    )
    assert "confidence=0.30" in out
    # a machine-visible hint the agent can act on (ask/verify) — NOT a gate signal
    assert "unverified" in out.lower() or "low confidence" in out.lower()


def test_missing_confidence_renders_without_crashing():
    out = _prompt_for([{"canonical_name": "Dana", "entity_type": "person"}])
    assert "Dana (person)" in out
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_entity_confidence_render.py -q`
Expected: FAIL — `confidence=` not in the rendered output.

- [ ] **Step 3: Render confidence in the entity section**

In `backend/src/services/context_builder.py`, the entity render (lines 374-390) is:

```python
        if pack.entities:
            ent_lines = []
            for e in pack.entities:
                name = e.get("canonical_name") or e.get("name", "unknown")
                etype = e.get("entity_type", "?")
                parts = [f"- {name} ({etype})"]
                importance = e.get("importance_score")
                if importance and importance > 0.7:
                    parts.append(f"importance={importance:.1f}")
                last_seen = e.get("last_seen_at")
                if last_seen:
                    parts.append(f"last_seen={last_seen[:10]}")
                interactions = e.get("interaction_count")
                if interactions and interactions > 1:
                    parts.append(f"interactions={interactions}")
                ent_lines.append(" ".join(parts))
            sections.append("## Relevant Entities\n" + "\n".join(ent_lines))
```

Insert confidence rendering + a low-confidence abstention hint. Change the loop body to add, right before `ent_lines.append(...)`:

```python
                confidence = e.get("confidence")
                if confidence is not None:
                    parts.append(f"confidence={confidence:.2f}")
                    if confidence < 0.5:
                        # Abstention hint for the agent (ask/verify before relying on
                        # this). NOT a gate signal — confidence never gates (spec §4.3).
                        parts.append("[low confidence — verify before relying]")
```

- [ ] **Step 4: Run the test to verify it PASSES**

Run: `uv run pytest tests/test_entity_confidence_render.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/context_builder.py backend/tests/test_entity_confidence_render.py
git commit -m "feat(rebuild): render evidence-derived entity confidence + abstention hint in to_prompt (Step 4)"
```

---

## Task 7: Post-action reconciliation — the Step-3 verification loop feeds confidence

Spec §4.5/§4.6: a CONFIRMED read-back **raises** a belief; a CONTRADICTED one **lowers** confidence — fed to abstention/ask-the-user only, **never the gate** (D6). Wire at both Step-3 hookpoints (correction #5).

**Files:**
- Create: `backend/src/services/entity_facts/reconciliation.py`
- Modify: `backend/src/services/dag_runner.py`, `backend/src/services/scheduler/deferred_verification_tick.py`
- Test: `backend/tests/entity_facts/test_reconciliation.py`, `backend/tests/test_worldmodel_recon_wiring.py`

- [ ] **Step 1: Write the failing reconciliation unit test**

Create `backend/tests/entity_facts/test_reconciliation.py`:

```python
"""Post-action reconciliation (spec §4.5): CONFIRMED raises a belief, CONTRADICTED
lowers it, no resolvable entity -> no-op. Confidence never touches the gate (D6).
Real Postgres for the raise/lower; the no-op + gate-isolation are structural."""

import os

import pytest

from src.services.verification.readback import VerifyVerdict

pytestmark = pytest.mark.skipif(
    not os.path.exists("alembic.ini"), reason="requires live Postgres at head"
)

WS = "ws_recon"
UID = "user_recon"


async def _seed_entity_with_fact(db):
    import sqlalchemy as sa

    from src.models.entities import Entity
    from src.services.entity_facts.store import EntityFactStore

    await db.execute(
        sa.text(
            "INSERT INTO workspaces (workspace_id, name, created_at, updated_at) "
            "VALUES (:w,'t',now(),now()) ON CONFLICT DO NOTHING"
        ),
        {"w": WS},
    )
    db.add(Entity(entity_id="ent_recon", user_id=UID, workspace_id=WS,
                  entity_type="person", canonical_name="Frank"))
    await db.flush()
    store = EntityFactStore(db)
    fid, _ = await store.record_fact(
        entity_id="ent_recon", workspace_id=WS, user_id=UID,
        attr_key="role", attr_value="CTO", origin="perception",
    )
    await db.commit()
    return fid


async def test_confirmed_raises_belief(db_session):
    from src.services.entity_facts.reconciliation import reconcile_verdict
    from src.services.entity_facts.store import EntityFactStore

    db = db_session
    fid = await _seed_entity_with_fact(db)
    before = (await EntityFactStore(db).get_fact(fid)).confidence
    await reconcile_verdict(
        db, workspace_id=WS, user_id=UID, verdict=VerifyVerdict.CONFIRMED,
        write_input={"entity_id": "ent_recon"}, write_output={},
    )
    await db.commit()
    after = (await EntityFactStore(db).get_fact(fid)).confidence
    assert after > before


async def test_contradicted_lowers_belief(db_session):
    from src.services.entity_facts.reconciliation import reconcile_verdict
    from src.services.entity_facts.store import EntityFactStore

    db = db_session
    fid = await _seed_entity_with_fact(db)
    before = (await EntityFactStore(db).get_fact(fid)).confidence
    await reconcile_verdict(
        db, workspace_id=WS, user_id=UID, verdict=VerifyVerdict.CONTRADICTED,
        write_input={"entity_id": "ent_recon"}, write_output={},
    )
    await db.commit()
    after = (await EntityFactStore(db).get_fact(fid)).confidence
    assert after < before


async def test_no_resolvable_entity_is_a_noop(db_session):
    from src.services.entity_facts.reconciliation import reconcile_verdict

    db = db_session
    # nothing to resolve -> must not raise
    await reconcile_verdict(
        db, workspace_id=WS, user_id=UID, verdict=VerifyVerdict.CONFIRMED,
        write_input={"to": "someone@example.com"}, write_output={},
    )
    await db.commit()


async def test_unverified_verdict_is_a_noop(db_session):
    from src.services.entity_facts.reconciliation import reconcile_verdict
    from src.services.entity_facts.store import EntityFactStore

    db = db_session
    fid = await _seed_entity_with_fact(db)
    before = (await EntityFactStore(db).get_fact(fid)).confidence
    await reconcile_verdict(
        db, workspace_id=WS, user_id=UID, verdict=VerifyVerdict.UNVERIFIED,
        write_input={"entity_id": "ent_recon"}, write_output={},
    )
    await db.commit()
    assert (await EntityFactStore(db).get_fact(fid)).confidence == before
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/entity_facts/test_reconciliation.py -q`
Expected: FAIL — `cannot import name 'reconcile_verdict'`.

- [ ] **Step 3: Write `reconciliation.py`**

Create `backend/src/services/entity_facts/reconciliation.py`:

```python
"""Post-action reconciliation (spec §4.5): the Step-3 verification loop feeds the world
model. A CONFIRMED read-back RAISES the confidence of the beliefs the write concerned;
a CONTRADICTED one LOWERS them. Fed to abstention/ask-the-user ONLY — this module never
touches TrustEngine/PolicyDecision/the gate (§4.3: confidence is a DEFERRED gate
dimension). Narrow and no-op-safe: acts only when an entity resolves from the write."""

import logging

from src.services.entity_facts.store import EntityFactStore
from src.services.verification.readback import VerifyVerdict

logger = logging.getLogger(__name__)


async def reconcile_verdict(
    db,
    *,
    workspace_id: str,
    user_id: str,
    verdict: VerifyVerdict,
    write_input: dict | None,
    write_output: dict | None,
) -> None:
    """Raise/lower world-model beliefs from a verification verdict. No-op unless the
    verdict is CONFIRMED/CONTRADICTED AND an entity resolves from the write. A best-effort
    belief write must never fail an otherwise-successful verification."""
    if verdict not in (VerifyVerdict.CONFIRMED, VerifyVerdict.CONTRADICTED):
        return
    if not workspace_id:
        return  # fail-closed: no cross-workspace belief writes

    entity_id = _resolve_entity_id(write_input or {}, write_output or {})
    if not entity_id:
        return  # narrow by design (D6); richer write->belief lineage is deferred

    try:
        store = EntityFactStore(db)
        facts = await store.current_facts(entity_id, workspace_id)
        if not facts:
            return
        for fact in facts:
            if verdict == VerifyVerdict.CONFIRMED:
                await store.corroborate(fact.fact_id)
            else:
                await store.weaken(fact.fact_id)
        logger.info(
            "reconciled %d belief(s) for entity=%s verdict=%s",
            len(facts), entity_id, verdict.value,
        )
    except Exception:
        logger.debug("Belief reconciliation failed for entity=%s", entity_id, exc_info=True)


def _resolve_entity_id(write_input: dict, write_output: dict) -> str | None:
    """Extract an explicit entity_id from the write's input/output. Deliberately narrow:
    only an explicit id is honoured (name/email resolution is deferred to avoid
    speculative mappings). Returns None when nothing resolves."""
    for src in (write_output, write_input):
        val = src.get("entity_id")
        if isinstance(val, str) and val:
            return val
    return None
```

- [ ] **Step 3b: Extend the package facade with the reconciliation export**

In `backend/src/services/entity_facts/__init__.py`, add (the final incremental-facade step, now that `reconciliation.py` exists):

```python
from src.services.entity_facts.reconciliation import reconcile_verdict
```

and add `"reconcile_verdict"` to `__all__`.

- [ ] **Step 4: Run the unit test to verify it PASSES**

Run: `uv run pytest tests/entity_facts/test_reconciliation.py -q`
Expected: all PASS.

- [ ] **Step 5: Write the failing wiring test**

Create `backend/tests/test_worldmodel_recon_wiring.py`:

```python
"""reconcile_verdict is called from BOTH Step-3 hookpoints (auto-exec finalize +
deferred tick), and confidence is NEVER read by the gate (§4.3). Structural checks
via source inspection + a monkeypatched spy — no live run needed."""

import inspect


def test_dag_runner_calls_reconcile_on_the_autoexec_path():
    import src.services.dag_runner as dr

    src = inspect.getsource(dr)
    assert "reconcile_verdict" in src, "dag_runner must feed the verdict to world-model reconciliation"


def test_deferred_tick_calls_reconcile():
    import src.services.scheduler.deferred_verification_tick as t

    src = inspect.getsource(t)
    assert "reconcile_verdict" in src


def test_trust_engine_never_imports_entity_confidence():
    import src.services.trust_engine as te

    src = inspect.getsource(te)
    # confidence must never become a gate dimension (spec §4.3)
    assert "entity_facts" not in src
    assert "reconcile_verdict" not in src
```

- [ ] **Step 6: Run to verify it FAILS**

Run: `uv run pytest tests/test_worldmodel_recon_wiring.py -q`
Expected: FAIL — `reconcile_verdict` not in `dag_runner`/`deferred_verification_tick`.

- [ ] **Step 7: Wire the auto-exec hookpoint**

In `backend/src/services/dag_runner.py`, the auto-exec caller (lines 401-427) computes `verdict = await self._finalize_with_verification(...)`. Immediately after that call (before the trust-reinforcement block), add the reconciliation feed:

```python
            verdict = await self._finalize_with_verification(
                run, step, output, elapsed_ms, capability, risk
            )

            # Post-action reconciliation (spec §4.5): feed the verdict to the world model
            # so a confirmed read-back RAISES a belief and a divergent one LOWERS it.
            # Abstention feed only — never the gate (§4.3). Best-effort.
            try:
                from src.services.entity_facts.reconciliation import reconcile_verdict

                await reconcile_verdict(
                    self._db,
                    workspace_id=run.workspace_id or "",
                    user_id=run.user_id,
                    verdict=verdict,
                    write_input=step.input_data or {},
                    write_output=output if isinstance(output, dict) else {},
                )
            except Exception:
                logger.debug("world-model reconciliation failed (auto-exec path)", exc_info=True)
```

> `self._db` is the DagRunner's session (grep the ctor: `dag_runner.py:76-101` shows the injected collaborators; confirm the session attribute name — it may be `self._db` or reached via `self._runner`/a factory. If DagRunner has no direct session, open one via the same factory `_finalize_with_verification`/`finalize_step` use, or thread the reconciliation into `_finalize_with_verification` which already has DB access through `self.finalize_step`). Match whatever session the surrounding `finalize_step` writes on so the reconciliation commits in the same transaction.

- [ ] **Step 8: Wire the deferred-tick hookpoint**

In `backend/src/services/scheduler/deferred_verification_tick.py`, `_apply_recheck` (lines 58-113) already branches CONFIRMED/CONTRADICTED and has `db`, `run`, `step`, `verdict` in scope. Add the reconciliation feed at the end of each terminal branch (after the CONFIRMED `transition_step`/trust block and after the CONTRADICTED `transition_step`/notifier block), or once near the top before the branch returns. Cleanest: add right after `capability = meta.get(...)` is computed, guard on the terminal verdicts:

```python
    # Post-action reconciliation feed (spec §4.5): raise on confirmed, lower on
    # contradicted. Abstention feed only — never the gate (§4.3). Best-effort.
    if verdict in (VerifyVerdict.CONFIRMED, VerifyVerdict.CONTRADICTED):
        try:
            from src.services.entity_facts.reconciliation import reconcile_verdict

            await reconcile_verdict(
                db,
                workspace_id=run.workspace_id or "",
                user_id=run.user_id,
                verdict=verdict,
                write_input=step.input_data or {},
                write_output=step.output_data or {},
            )
        except Exception:
            logger.debug("world-model reconciliation failed (deferred tick)", exc_info=True)
```

Place this block at the top of `_apply_recheck` after `capability` is set. (`VerifyVerdict` is already imported in this module — confirm; if not, add `from src.services.verification.readback import VerifyVerdict`.)

- [ ] **Step 9: Run the wiring test + the Step-3 suites (no regression)**

```bash
uv run pytest tests/test_worldmodel_recon_wiring.py -q
uv run pytest tests/test_finalize_verification.py tests/test_deferred_verification_tick.py -q
```
Expected: wiring test PASSES; the Step-3 verification tests still PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/src/services/entity_facts/reconciliation.py backend/src/services/entity_facts/__init__.py backend/src/services/dag_runner.py backend/src/services/scheduler/deferred_verification_tick.py backend/tests/entity_facts/test_reconciliation.py backend/tests/test_worldmodel_recon_wiring.py
git commit -m "feat(rebuild): verification loop reconciles world-model confidence (raise/lower, never the gate) (Step 4)"
```

---

## Task 8: The four workspace-filtered read tools (`get_entity` / `query_facts` / `traverse` / `get_provenance`)

Spec §4.6 item 5 — expose the world model as agent-queryable, each workspace-filtered fail-closed (mirrors the Step-2 hydration gate). The 3-place tool rule + `CAPABILITY_CATALOG` + agent scopes, all `internal.*` read caps (D8), added atomically so `validate_registry()` passes (correction #7).

**Files:**
- Create: `backend/src/tools/intelligence_server/world_model_tools.py`
- Modify: `backend/src/tools/intelligence_server/__init__.py`, `backend/src/tools/schemas.py`, `backend/src/tools/catalog.py`, `backend/src/integrations/capabilities.py`, `backend/src/orchestrator/agents.py`
- Test: `backend/tests/test_world_model_query_tools.py` (real-DB)

- [ ] **Step 1: Write the failing real-DB test**

Create `backend/tests/test_world_model_query_tools.py`:

```python
"""The four world-model read tools are workspace-filtered fail-closed (spec §4.6 item 5).
Real Postgres (migration applied). Tools are configured via the intelligence server."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.path.exists("alembic.ini"), reason="requires live Postgres at head"
)

WS = "ws_query_tools"
OTHER = "ws_other_tools"
UID = "user_query_tools"


async def _seed(db):
    import sqlalchemy as sa

    from src.models.entities import Entity
    from src.services.entity_facts.store import EntityFactStore

    for w in (WS, OTHER):
        await db.execute(
            sa.text(
                "INSERT INTO workspaces (workspace_id, name, created_at, updated_at) "
                "VALUES (:w,'t',now(),now()) ON CONFLICT DO NOTHING"
            ),
            {"w": w},
        )
    db.add(Entity(entity_id="ent_q", user_id=UID, workspace_id=WS,
                  entity_type="person", canonical_name="Grace", attributes={"role": "CTO"}))
    await db.flush()
    store = EntityFactStore(db)
    await store.record_fact(entity_id="ent_q", workspace_id=WS, user_id=UID,
                            attr_key="role", attr_value="CTO", origin="user_message")
    await db.commit()


async def test_get_entity_returns_entity_and_current_facts(db_session, configured_intelligence):
    from src.tools.intelligence_server.world_model_tools import get_entity

    await _seed(db_session)
    res = await get_entity(user_id=UID, entity_id="ent_q", ctx=None, workspace_id=WS)
    assert res["entity"]["canonical_name"] == "Grace"
    assert any(f["attr_key"] == "role" and f["attr_value"] == "CTO" for f in res["facts"])
    assert "confidence" in res["facts"][0]


async def test_get_entity_is_workspace_fail_closed(db_session, configured_intelligence):
    from src.tools.intelligence_server.world_model_tools import get_entity

    await _seed(db_session)
    res = await get_entity(user_id=UID, entity_id="ent_q", ctx=None, workspace_id=OTHER)
    assert res.get("entity") is None  # cross-workspace read returns nothing


async def test_query_facts_as_of_returns_historical_belief(db_session, configured_intelligence):
    from datetime import datetime, timezone

    from src.tools.intelligence_server.world_model_tools import query_facts

    await _seed(db_session)
    res = await query_facts(
        user_id=UID, entity_id="ent_q",
        as_of=datetime.now(timezone.utc).isoformat(), ctx=None, workspace_id=WS,
    )
    assert any(f["attr_key"] == "role" for f in res["facts"])


async def test_get_provenance_returns_origin(db_session, configured_intelligence):
    from src.tools.intelligence_server.world_model_tools import get_provenance

    await _seed(db_session)
    res = await get_provenance(user_id=UID, entity_id="ent_q", ctx=None, workspace_id=WS)
    assert res["provenance"]
    assert res["provenance"][0]["provenance"]["origin"] == "user_message"


async def test_traverse_is_workspace_scoped(db_session, configured_intelligence):
    from src.tools.intelligence_server.world_model_tools import traverse

    await _seed(db_session)
    res = await traverse(user_id=UID, entity_id="ent_q", ctx=None, workspace_id=OTHER)
    assert res["relationships"] == []  # no cross-workspace edges
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_world_model_query_tools.py -q`
Expected: FAIL — `No module named 'src.tools.intelligence_server.world_model_tools'`.

- [ ] **Step 3: Write the tool module**

Create `backend/src/tools/intelligence_server/world_model_tools.py`:

```python
"""World-model read tools (spec §4.6 item 5): get_entity / query_facts(as_of) /
traverse / get_provenance. Each is workspace-filtered fail-closed (mirrors the Step-2
hydration gate). Read-only (read_only=True) → verification-exempt. No writes, no gate."""

import logging
from datetime import datetime, timezone

from fastmcp import Context
from mcp.types import ToolAnnotations
from sqlalchemy import select

from src.models.entities import Entity, EntityRelationship
from src.services.entity_facts.confidence import current_confidence
from src.services.entity_facts.store import EntityFactStore
from src.tools.intelligence_server._shared import _get_db, intelligence

logger = logging.getLogger(__name__)


def _days_since(ts) -> float:
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)


def _fact_dict(f) -> dict:
    return {
        "attr_key": f.attr_key,
        "attr_value": f.attr_value,
        "confidence": current_confidence(f.confidence, age_days=_days_since(f.valid_from)),
        "corroboration_count": f.corroboration_count,
        "valid_from": f.valid_from.isoformat() if f.valid_from else None,
        "valid_to": f.valid_to.isoformat() if f.valid_to else None,
        "provenance": f.provenance,
    }


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_entity(
    user_id: str,
    entity_id: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Fetch a world-model entity and its current attribute beliefs (with confidence
    and provenance). Workspace-scoped."""
    async with _get_db() as db:
        ent = (
            await db.execute(
                select(Entity).where(
                    Entity.entity_id == entity_id,
                    Entity.user_id == user_id,
                    Entity.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if ent is None:
            return {"entity": None, "facts": []}
        facts = await EntityFactStore(db).current_facts(entity_id, workspace_id)
        return {
            "entity": {
                "entity_id": ent.entity_id,
                "entity_type": ent.entity_type,
                "canonical_name": ent.canonical_name,
                "confidence": current_confidence(
                    ent.confidence_score, age_days=_days_since(ent.last_seen_at)
                ),
                "attributes": ent.attributes,
            },
            "facts": [_fact_dict(f) for f in facts],
        }


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def query_facts(
    user_id: str,
    entity_id: str,
    ctx: Context,
    as_of: str = "",
    workspace_id: str = "",
) -> dict:
    """Query an entity's attribute beliefs as-of a timestamp (ISO-8601; empty = now).
    Returns the beliefs valid at that time (bi-temporal). Workspace-scoped."""
    async with _get_db() as db:
        # Fail-closed: confirm the entity is in this workspace before returning facts.
        ent = (
            await db.execute(
                select(Entity.entity_id).where(
                    Entity.entity_id == entity_id,
                    Entity.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if ent is None:
            return {"facts": [], "as_of": as_of}
        when = _parse_iso(as_of) or datetime.now(timezone.utc)
        facts = await EntityFactStore(db).facts_as_of(entity_id, workspace_id, when)
        return {"facts": [_fact_dict(f) for f in facts], "as_of": when.isoformat()}


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def traverse(
    user_id: str,
    entity_id: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """List the relationships incident to an entity (one hop). Workspace-scoped
    fail-closed (explicit workspace_id filter, correction #7)."""
    async with _get_db() as db:
        result = await db.execute(
            select(EntityRelationship).where(
                EntityRelationship.workspace_id == workspace_id,
                (EntityRelationship.from_entity_id == entity_id)
                | (EntityRelationship.to_entity_id == entity_id),
            )
        )
        rels = result.scalars().all()
        return {
            "relationships": [
                {
                    "relation_id": r.relation_id,
                    "from_entity_id": r.from_entity_id,
                    "relation_type": r.relation_type,
                    "to_entity_id": r.to_entity_id,
                    "active": r.active,
                }
                for r in rels
            ]
        }


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_provenance(
    user_id: str,
    entity_id: str,
    ctx: Context,
    attr_key: str = "",
    workspace_id: str = "",
) -> dict:
    """Provenance for an entity's current beliefs — origin, source_ref, observed_at,
    reliability, confidence. Optionally one attr_key. Workspace-scoped."""
    async with _get_db() as db:
        ent = (
            await db.execute(
                select(Entity.entity_id).where(
                    Entity.entity_id == entity_id,
                    Entity.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if ent is None:
            return {"provenance": []}
        records = await EntityFactStore(db).provenance_for(
            entity_id, workspace_id, attr_key or None
        )
        return {"provenance": records}


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
```

> Confirm the FastMCP/annotations imports (`from fastmcp import Context`, `from mcp.types import ToolAnnotations`) match `memory.py`'s import lines exactly — copy them verbatim from `intelligence_server/memory.py`'s header to avoid an import mismatch.

- [ ] **Step 4: Register the tools in the package `__init__`**

In `backend/src/tools/intelligence_server/__init__.py`, add an import so the decorators run, and extend `__all__`:

```python
from src.tools.intelligence_server.world_model_tools import (
    get_entity,
    get_provenance,
    query_facts,
    traverse,
)
```

Add `"get_entity"`, `"get_provenance"`, `"query_facts"`, `"traverse"` to the module's `__all__` list.

- [ ] **Step 5: Add the four Pydantic input models + registry entries**

In `backend/src/tools/schemas.py`, add (near the other read-tool models):

```python
class GetEntityInput(BaseModel):
    """Fetch a world-model entity + its current attribute beliefs."""

    entity_id: str = Field(description="Entity id (ent_...) to fetch")


class QueryFactsInput(BaseModel):
    """Query an entity's attribute beliefs as-of a timestamp (bi-temporal)."""

    entity_id: str = Field(description="Entity id (ent_...) to query")
    as_of: str = Field(default="", description="ISO-8601 timestamp; empty = now")


class TraverseInput(BaseModel):
    """List the relationships incident to an entity (one hop)."""

    entity_id: str = Field(description="Entity id (ent_...) to traverse from")


class GetProvenanceInput(BaseModel):
    """Provenance for an entity's current beliefs."""

    entity_id: str = Field(description="Entity id (ent_...)")
    attr_key: str = Field(default="", description="Optional single attribute key")
```

Then add to the `TOOL_INPUT_MODELS` dict (`schemas.py:241-263`):

```python
    "get_entity": GetEntityInput,
    "query_facts": QueryFactsInput,
    "traverse": TraverseInput,
    "get_provenance": GetProvenanceInput,
```

- [ ] **Step 6: Add the four `InternalToolDef` entries**

In `backend/src/tools/catalog.py`, add to the `INTERNAL_TOOLS` list (near `search`/`build_context`):

```python
    InternalToolDef(
        name="get_entity",
        input_model=GetEntityInput,
        capability="internal.get_entity",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetEntityInput),
        read_only=True,
    ),
    InternalToolDef(
        name="query_facts",
        input_model=QueryFactsInput,
        capability="internal.query_facts",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(QueryFactsInput),
        read_only=True,
    ),
    InternalToolDef(
        name="traverse",
        input_model=TraverseInput,
        capability="internal.traverse",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(TraverseInput),
        read_only=True,
    ),
    InternalToolDef(
        name="get_provenance",
        input_model=GetProvenanceInput,
        capability="internal.get_provenance",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetProvenanceInput),
        read_only=True,
    ),
```

Add the imports for the four new input models to the top of `catalog.py` (match the existing `from src.tools.schemas import (...)` block — add `GetEntityInput`, `QueryFactsInput`, `TraverseInput`, `GetProvenanceInput`). Confirm `_desc` is the helper the file already uses (correction: existing entries call `_desc(SearchInput)`).

- [ ] **Step 7: Add the four capabilities**

In `backend/src/integrations/capabilities.py`, add to `CAPABILITY_CATALOG` (near the `internal.*` read caps):

```python
    "internal.get_entity": _cap(CapabilityFamily.INTERNAL, True),
    "internal.query_facts": _cap(CapabilityFamily.INTERNAL, True),
    "internal.traverse": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_provenance": _cap(CapabilityFamily.INTERNAL, True),
```

- [ ] **Step 8: Add the caps to agent scopes**

In `backend/src/orchestrator/agents.py`, add the four caps to the `librarian`, `perceiver`, and `planner` scope sets (they read the world model). For `librarian`:

```python
    "librarian": {
        "internal.update_entity",
        "internal.search",
        "internal.store_memory",
        "internal.get_entity",
        "internal.query_facts",
        "internal.traverse",
        "internal.get_provenance",
    },
```

Add the same four strings to the `perceiver` set (after `internal.search`) and the `planner` set (after `internal.search`).

- [ ] **Step 9: Run the tool test + the registry validation test**

```bash
uv run pytest tests/test_world_model_query_tools.py -q
uv run pytest tests/test_tool_registry.py tests/test_validation.py -q -k "valid or registry" 2>&1 | tail -20
uv run python -c "from src.tools.validation import validate_registry; errs = validate_registry(); print('ERRORS:', errs)"
```
Expected: the tool test PASSES; `validate_registry()` prints `ERRORS: []` (the new caps are in the catalog + in scope). If a validation test name differs, run the file that exercises `validate_registry`.

- [ ] **Step 10: Commit**

```bash
git add backend/src/tools/intelligence_server/world_model_tools.py backend/src/tools/intelligence_server/__init__.py backend/src/tools/schemas.py backend/src/tools/catalog.py backend/src/integrations/capabilities.py backend/src/orchestrator/agents.py backend/tests/test_world_model_query_tools.py
git commit -m "feat(rebuild): expose workspace-filtered world-model read tools (get_entity/query_facts/traverse/get_provenance) (Step 4)"
```

---

## Task 9: Final holistic verification

Confirm the full step landed: schema clean at head, whole suite green, spec §4.6 items 3-5 each covered.

**Files:** none (verification only).

- [ ] **Step 1: Confirm the DB is at the new head + drift-free**

```bash
uv run alembic current 2>&1 | tail -1   # expect c4f9e2a71b83 (head)
uv run alembic check   2>&1 | tail -1   # expect: No new upgrade operations detected.
```

- [ ] **Step 2: Run the full non-e2e suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -q -p no:cacheprovider 2>&1 | tail -15
```
Expected: green — at least the ~3039 baseline **plus** the new tests, zero failures. Investigate any regression in the ~10 `upsert_entity` callers (should be none — attributes snapshot behaviour preserved, D2).

- [ ] **Step 3: Lint**

```bash
uv run ruff check src/services/entity_facts/ src/tools/intelligence_server/world_model_tools.py src/services/world_model.py src/services/context_builder.py src/services/dag_runner.py 2>&1 | tail -20
uv run ruff format --check src/services/entity_facts/ 2>&1 | tail -5
```
Expected: clean (or auto-fixable with `ruff check --fix` / `ruff format`).

- [ ] **Step 4: Spec coverage self-check (write this into the commit body)**

  - **§4.6 item 3 — contradiction handling via `valid_to` supersede:** `EntityFactStore.record_fact` closes the old fact (`valid_to`/`superseded_by`) and inserts a new current row on a changed value; both attribute-overwrite sites (`upsert_entity`, `update_entity` tool) route through it (Tasks 1/3/4/5). Deterministic structural detection, no LLM (D3). ✅
  - **§4.6 item 4 — evidence-derived confidence + provenance:** `compute_confidence` = source-reliability × corroboration (noisy-OR), age-decayed, never LLM-reported (Task 2); the first-ever `confidence_score` assignment (Task 4); rendered in `to_prompt()` with an abstention hint (Task 6); the Step-3 verification loop reconciles it — CONFIRMED raises, CONTRADICTED lowers — fed to abstention only, never the gate (Task 7, D6). ✅
  - **§4.6 item 5 — workspace-filtered query tools:** `get_entity`/`query_facts(as_of)`/`traverse`/`get_provenance`, each fail-closed workspace-scoped, added via the 3-place rule + capabilities + scopes (Task 8). ✅
  - **§4.3 — confidence stays a DEFERRED gate dimension:** `test_trust_engine_never_imports_entity_confidence` asserts the gate never reads it. ✅
  - **§6 Step 4 — lands before the dual-runtime window; both runtimes share write semantics:** one `record_fact` behind both attribute-write paths (in-flight posture section). ✅
  - **Migration:** single head `c4f9e2a71b83 → b3e8c1f5a9d2`; up/down round-trip + `alembic check` verified against live Postgres (Task 1). ✅
  - **Out of scope (documented):** confidence as a gate dimension (§4.3, deferred); relationship supersede (D7); rich write→belief lineage in reconciliation (D6); AsyncPostgresSaver/dual-runtime wiring (Step 10); the deferred tick's `read_fn=None` live-read seam (Step-3 carry-forward). ✅

- [ ] **Step 5: Final commit (if any lint fixes)**

```bash
git add -A
git commit -m "chore(rebuild): Step 4 final verification — world model as control surface (§4.6 items 3-5)"
```

---

## Self-review notes (author's pass against the spec)

- **Spec coverage:** every §4.6 item 3/4/5 clause maps to a task (see Task 9 Step 4). The §4.5 reconciliation paragraph maps to Task 7; the §4.3 "confidence stays deferred" constraint is asserted by a test (Task 7 Step 5).
- **Type consistency:** `EntityFactStore.record_fact` returns `(fact_id, superseded)` — used consistently in Tasks 3/4/5. `compute_confidence(*, origin, corroboration_count, age_days)` and `current_confidence(base, *, age_days)` signatures are stable across Tasks 2/3/4/6/8. `VerifyVerdict` (`CONFIRMED`/`CONTRADICTED`/`UNVERIFIED`) is the Step-3 enum, imported (not redefined) in Task 7.
- **Two overwrite sites:** both closed (Tasks 4 + 5) — the plan's highest-risk omission if only one were fixed.
- **Placeholders:** none — every code step shows the code; every command shows expected output. The three "confirm against the real signature" notes (db_session fixture name, ULID import path, `configured_intelligence` fixture) are verification prompts for facts that are environment/test-harness specific, not missing implementation.
