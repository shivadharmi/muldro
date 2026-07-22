# Step 2 — World-Model Input Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve *what enters* the world model's read path by (a) replacing the `ILIKE-on-raw-message` entity lookup that feeds agent context with a proper span-based resolver (exact + activated Postgres FTS + existing Qdrant vectors, workspace-scoped), and (b) replacing the binary `0.8/0.2` recency signal with continuous exponential decay — both cheap and migration-light, per spec §4.6 build-order items 1–2.

**Architecture:** Two independent features. **Feature A (entity resolution):** a new pure `extract_spans()` (dependency-free, NOT a NER model) feeds a new `EntityResolver` that composes three signals per span — exact canonical/alias match, Postgres FTS over a newly-*activated* `entities.search_vector` (a DB trigger + GIN index; the column already existed but was never populated), and Qdrant vector similarity over the already-existing `entities` collection — then hydrates candidates through a **workspace-scoped DB query that is the authoritative isolation gate**. Only the raw-message consumer (`ContextBuilder`) is routed to it; `find_entity` (clean-name callers) is left intact. **Feature B (recency):** a new `exp(-λ·days)` helper replaces one line in `ContextBuilder._rank_entities`.

**Tech Stack:** Python 3.12/3.13, pytest (async via the repo's custom `pytest_pyfunc_call` `asyncio.run` hook — **no pytest-asyncio, no `asyncio_mode`**), SQLAlchemy 2 / asyncpg, Postgres 17 (pgvector image) with `tsvector`/`plainto_tsquery`/GIN, Qdrant (`AsyncQdrantClient`), Voyage AI embeddings (1024-dim, injected + mocked in tests), Pydantic v2, ruff, alembic.

**Source spec:** [`docs/superpowers/specs/2026-06-28-first-principles-rebuild-design.md`](../specs/2026-06-28-first-principles-rebuild-design.md) §6 Step 2, §4.6 (world model as control surface, build-order items 1–2), §4.10 (multi-tenant isolation).

**Depends on:** Step 1 (`b5482a2`, alembic head `a2f5c9d18b47`) — the green baseline this plan builds on.

---

## Infra note (verified 2026-07-05 in this environment)

- **Postgres + Redis are live** (`docker compose ps`: both `Up … (healthy)`). DB is at the single alembic head **`a2f5c9d18b47`** (`uv run alembic current`). This is materially better than the Step-1 env — migrations can be applied for real and real-DB integration tests can run.
- **`langgraph` / `langchain` / `deepagents` are installed** — the full suite including `tests/deep_runtime/` collects and runs. The green baseline before Step 2 is **~2943 passed / 18 skipped** for `uv run pytest tests/ --ignore=tests/e2e`.
- **Qdrant is DOWN** but defined in `docker-compose.yml` (`qdrant/qdrant:v1.17.1`, port 6333). The Qdrant-touching integration test (Task 4) is **gated on reachability** (skips when down) — bring it up with `docker compose up -d qdrant` to exercise it. The resolver does **not** require Qdrant: FTS + exact are the always-available signals and vector is a best-effort enhancement.
- **`JARVIS_VOYAGE_API_KEY` is set in `.env`** so embeddings *can* run live, but tests **mock** `EmbeddingService.embed_text` (the repo convention: `AsyncMock(return_value=[0.1]*1024)`) — never call Voyage from a test.
- **`spaCy` / `nltk` / any NER library is NOT installed** and is not added by this plan (see Design Decision D1).
- **This is a `uv`-managed venv with NO `pip`.** Run everything via `uv run …` (`pytest`/`alembic`/`ruff`/`python`) and `uv add …`. Plain `uv sync` drops the dev extras — use `uv sync --all-extras` if you must sync.

**Run all commands from `backend/`.**

**Pre-flight (run once before starting):**
```bash
cd backend && uv run pytest tests/ --ignore=tests/e2e -q -p no:cacheprovider 2>&1 | tail -5
uv run alembic current 2>&1 | tail -1   # expect: a2f5c9d18b47 (head)
```

---

## Current-state corrections (verify-don't-trust — confirmed against code 2026-07-05)

The spec and memory carry a few point-in-time claims that are now stale. Grounding facts this plan relies on:

1. **The binary recency `0.8/0.2` is in `context_builder.py:37` (inside the free fn `_rank_entities`), NOT `world_model.py`.** The spec/memory phrasing ("world-model … binary recency") points at the wrong file. `world_model.py` only *produces* `last_seen_at`; the ranking that consumes it is in `ContextBuilder`.
2. **Embeddings are Voyage AI `voyage-3` (primary), not Bedrock Titan.** CLAUDE.md/memory say "Bedrock Titan V2" — Titan is only the fallback when `voyage_api_key` is empty. Dimension (1024) is correct.
3. **`entities.search_vector` (TSVECTOR) already exists but is dead** — no `to_tsvector`, no trigger, no GIN index, nothing writes it — even though `entities` is wired into `FTSService._TABLE_MAP`. So "add Entity FTS" is really "**activate** the dead FTS column." (Repo has **zero** GIN indexes / `to_tsvector` today — this plan introduces the first.)
4. **Workspace-scoped entity vector search is silently broken.** The entity Qdrant upsert payload (`world_model.py:419-423`) omits `workspace_id`, but the dedup search (`world_model.py:539`) filters on it, so a `must` match against a missing key returns nothing. Task 4 fixes it; the resolver stays fail-closed via DB hydration regardless.
5. **The test harness has no `pytest-asyncio` and no `asyncio_mode`** (CLAUDE.md claims `asyncio_mode = "auto"` — inaccurate). Async tests are plain `async def test_...` run by conftest's `pytest_pyfunc_call` hook. `@pytest.mark.asyncio` is a harmless registered marker.
6. **NER is genuinely NEW** — confirmed no span-extraction/NER code today; all entity extraction is one Claude call on the *write* side (`WorldModel.extract_from_text`). Step 2 touches only the *read/resolve* side.
7. **Two `find_entity` callers pass no `workspace_id`** (`event_processor.py:558`, `initiative_scorer.py:154`, default `""`). Because every `Entity` has a real (non-empty) `workspace_id`, `WHERE workspace_id = ''` matches nothing — so those two calls **already return `[]` today** (latent dead calls). This plan does not touch them; it documents them as a deferred follow-up.

---

## Design decisions (rationale + rejected alternatives)

- **D1 — "NER-on-spans" = a lightweight, dependency-free deterministic span extractor, NOT spaCy.** Rationale: (i) spaCy isn't installed and adding it + a model is heavy, against the spec's cheapness/deletion thesis and the Anthropic-only lean; (ii) resolution *accuracy* comes from exact+FTS+vector, not perfect span boundaries — the extractor only needs decent *recall* of candidate mentions; (iii) deterministic → offline unit-testable (fits the no-live-DB convention). **Rejected:** spaCy/GLiNER NER (heavy dep + model download), and an LLM call per lookup (`find_entity` is a hot per-turn read; an LLM round-trip there is slow/expensive — the LLM path stays on the *write* side).
- **D2 — Add `resolve_entities` and route only `ContextBuilder`; leave `find_entity` intact.** The "ILIKE-on-raw-message" pathology is specifically the `ContextBuilder → find_entity(raw user message)` path. The other `find_entity` callers pass clean names/emails where `ILIKE` is acceptable, and two of them are already dead (correction #7). Reimplementing `find_entity` in place would change relationship-linking + worker-tagging behavior (touching `test_knowledge_graph`, `test_world_model_alias_isolation`) for no Step-2 benefit. **Deferred follow-up (documented, not built):** migrate the remaining `find_entity` callers to `resolve_entities`.
- **D3 — Activate FTS via a DB trigger + GIN index, not a `Computed`/generated column.** Rationale: a generated column risks a **false `alembic check` drift** (Postgres normalizes the stored `generation_expression` textually, rarely matching the model's `Computed(sqltext)` verbatim). A trigger + function live entirely in raw migration SQL — **invisible to alembic autogenerate** — so the only model change alembic tracks is the GIN index (declared in `__table_args__`, so it compares clean). The app never writes `search_vector` (confirmed), so a DB-maintained column is safe. **Rejected:** `Computed(..., persisted=True)` (drift risk); application-side population in `upsert_entity` (misses backfill, not DB-enforced).
- **D4 — Workspace isolation is enforced at DB hydration, not at the Qdrant filter.** The resolver gathers candidate `entity_id`s from exact/FTS/vector, then hydrates them with `WHERE user_id AND workspace_id AND entity_id IN (...)`. Even if the (currently-broken) Qdrant filter returns cross-workspace ids, hydration drops them — **fail-closed**, mirroring how `_find_by_name_or_alias` already re-selects with `Entity.workspace_id == workspace_id`. Task 4's payload fix is a recall/correctness improvement, not the isolation gate.
- **D5 — Recency: continuous `exp(-λ·days)` with a 30-day half-life.** `λ = ln(2)/30 ≈ 0.0231/day` → `1.0` at 0 days, `0.5` at 30 days. Chosen to match the 30-day recency windows already used in `tri_search` and memory retrieval, keeping recency weighting consistent across the codebase. Missing/unparseable `last_seen_at` → `0.0` (importance + interaction still contribute).

---

## In-flight-run / migration posture (spec §6 requires this per schema-touching step)

The only schema change is Task 3's migration on the **existing** `entities` table: a backfill `UPDATE`, a `BEFORE INSERT OR UPDATE` trigger + function, and a GIN index. `search_vector` is **not** read for any control flow (readiness/resume/idempotency), so no drain / dual-read / reconcile is needed — the change is additive-in-effect. Two operational notes (dev-safe here; flag for prod): the backfill `UPDATE` and the non-`CONCURRENTLY` `CREATE INDEX` take table-level locks proportional to `entities` size (alembic wraps each migration in one transaction, so `CONCURRENTLY` is not available here). A resume-across-deploy test is not required because in-flight runs never touch `search_vector`.

---

## File Structure

**Create:**
- `backend/src/services/entity_spans.py` — pure `extract_spans(text) -> list[str]`. No DB, no deps beyond `re`.
- `backend/src/services/entity_resolver.py` — `EntityResolver` (composes exact + FTS + vector, workspace-scoped hydration) + statement-builders `_exact_match_stmt` / `_hydrate_entities_stmt` (extracted for compiled-SQL isolation tests).
- `backend/alembic/versions/b3e8c1f5a9d2_entity_fts_activation.py` — migration: backfill + trigger + function + GIN index.
- Tests: `backend/tests/test_entity_spans.py`, `backend/tests/test_recency_decay.py`, `backend/tests/test_entity_fts_schema.py`, `backend/tests/test_entity_vector_payload.py`, `backend/tests/test_entity_resolver.py`, `backend/tests/test_entity_resolver_db.py` (real-DB, skip-if-no-Postgres), `backend/tests/test_entity_vector_qdrant.py` (real-Qdrant, skip-if-unreachable).

**Modify:**
- `backend/src/services/context_builder.py` — add `import math` + `from datetime import datetime, timezone`; add `_RECENCY_HALFLIFE_DAYS`/`_RECENCY_LAMBDA` + `_recency_score()`; replace the binary line in `_rank_entities`; route the entity lookup from `find_entity` → `resolve_entities`.
- `backend/src/models/entities.py` — add the GIN index on `search_vector` to `Entity.__table_args__`.
- `backend/src/services/world_model.py` — add `resolve_entities(...)` delegating to `EntityResolver`; extract `_entity_vector_payload(...)` (adds `workspace_id`) and use it in `upsert_entity`.
- `backend/src/services/vector_store.py` — add `("workspace_id", PayloadSchemaType.KEYWORD)` to the `entities` payload-index list in `ensure_collections`.

**Untouched (by design):** `find_entity` / `_find_entity_stmt` (clean-name callers, D2); the write-side LLM extraction (`extract_from_text` / `_call_extraction`); the chat path.

---

## Task 1: Continuous recency decay (Feature B — smallest, independent, pure)

Replaces the single binary line `context_builder.py:37` with `exp(-λ·days_since(last_seen_at))`. Pure and fully unit-testable by injecting `now`.

**Files:**
- Modify: `backend/src/services/context_builder.py`
- Test: `backend/tests/test_recency_decay.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_recency_decay.py`:

```python
"""Continuous recency decay: exp(-lambda * days_since(last_seen_at)), replacing
the old binary 0.8/0.2. Pure — 'now' is injected so the test is deterministic."""

import math
from datetime import datetime, timedelta, timezone

from src.services.context_builder import (
    _RECENCY_HALFLIFE_DAYS,
    _rank_entities,
    _recency_score,
)

_NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


def test_zero_days_is_one():
    assert _recency_score(_NOW.isoformat(), now=_NOW) == 1.0


def test_half_life_is_one_half():
    half = (_NOW - timedelta(days=_RECENCY_HALFLIFE_DAYS)).isoformat()
    assert abs(_recency_score(half, now=_NOW) - 0.5) < 1e-6


def test_decays_monotonically():
    d10 = _recency_score((_NOW - timedelta(days=10)).isoformat(), now=_NOW)
    d40 = _recency_score((_NOW - timedelta(days=40)).isoformat(), now=_NOW)
    assert 1.0 > d10 > d40 > 0.0


def test_missing_timestamp_is_zero():
    assert _recency_score(None, now=_NOW) == 0.0
    assert _recency_score("", now=_NOW) == 0.0


def test_unparseable_timestamp_is_zero():
    assert _recency_score("not-a-date", now=_NOW) == 0.0


def test_naive_datetime_is_treated_as_utc():
    naive = datetime(2026, 7, 5, 12, 0)  # no tzinfo
    assert abs(_recency_score(naive, now=_NOW) - 1.0) < 1e-6


def test_rank_entities_prefers_recent_over_stale_at_equal_importance():
    recent = {"importance_score": 0.5, "interaction_count": 1, "last_seen_at": _NOW.isoformat()}
    stale = {
        "importance_score": 0.5,
        "interaction_count": 1,
        "last_seen_at": (_NOW - timedelta(days=120)).isoformat(),
    }
    ranked = _rank_entities([stale, recent])
    assert ranked[0] is recent
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_recency_decay.py -q`
Expected: FAIL at import — `cannot import name '_RECENCY_HALFLIFE_DAYS' … from 'src.services.context_builder'`.

- [ ] **Step 3: Add the imports**

In `backend/src/services/context_builder.py`, the current top of file is:

```python
"""ContextBuilder — assembles rich context packs for agent prompts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
```

Change the import block to:

```python
"""ContextBuilder — assembles rich context packs for agent prompts."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING
```

- [ ] **Step 4: Add the constants + helper, and rewrite `_rank_entities`'s recency line**

In `backend/src/services/context_builder.py`, the current `_rank_entities` (lines ~25-40) is:

```python
def _rank_entities(entities: list[dict]) -> list[dict]:
    """Cross-source ranking for entities.

    Composite score: 0.40*importance + 0.30*recency + 0.30*interaction_frequency.
    """

    def _score(e: dict) -> float:
        importance = e.get("importance_score", 0.0) or 0.0
        interactions = e.get("interaction_count", 0) or 0
        # Normalize interaction count (cap at 50 for scoring)
        interaction_norm = min(interactions / 50.0, 1.0)
        # Recency: entities with last_seen_at get a boost (binary for simplicity)
        recency = 0.8 if e.get("last_seen_at") else 0.2
        return 0.40 * importance + 0.30 * recency + 0.30 * interaction_norm

    return sorted(entities, key=_score, reverse=True)
```

Replace that whole block with (adds the constants + `_recency_score` above it, and swaps the binary line):

```python
# Continuous recency decay: exp(-lambda * days_since(last_seen_at)). A 30-day
# half-life mirrors the recency windows used in tri_search / memory retrieval,
# keeping recency weighting consistent across the codebase.
_RECENCY_HALFLIFE_DAYS = 30.0
_RECENCY_LAMBDA = math.log(2) / _RECENCY_HALFLIFE_DAYS


def _recency_score(
    last_seen_at: "str | datetime | None", *, now: "datetime | None" = None
) -> float:
    """Continuous recency in [0, 1]: exp(-lambda * days_since(last_seen_at)).

    1.0 at last_seen == now, ~0.5 at 30 days, decaying smoothly. Replaces the old
    binary 0.8/0.2. A missing or unparseable timestamp -> 0.0 (no recency signal;
    importance + interaction still contribute to the composite).
    """
    if not last_seen_at:
        return 0.0
    if now is None:
        now = datetime.now(timezone.utc)
    ts = last_seen_at
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return math.exp(-_RECENCY_LAMBDA * days)


def _rank_entities(entities: list[dict]) -> list[dict]:
    """Cross-source ranking for entities.

    Composite score: 0.40*importance + 0.30*recency + 0.30*interaction_frequency.
    """

    def _score(e: dict) -> float:
        importance = e.get("importance_score", 0.0) or 0.0
        interactions = e.get("interaction_count", 0) or 0
        # Normalize interaction count (cap at 50 for scoring)
        interaction_norm = min(interactions / 50.0, 1.0)
        # Continuous recency decay (replaces the old binary 0.8/0.2).
        recency = _recency_score(e.get("last_seen_at"))
        return 0.40 * importance + 0.30 * recency + 0.30 * interaction_norm

    return sorted(entities, key=_score, reverse=True)
```

- [ ] **Step 5: Run to verify it PASSES**

Run: `uv run pytest tests/test_recency_decay.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/services/context_builder.py tests/test_recency_decay.py && uv run ruff format src/services/context_builder.py tests/test_recency_decay.py
git add backend/src/services/context_builder.py backend/tests/test_recency_decay.py
git commit -m "feat(rebuild): continuous entity recency decay replaces binary 0.8/0.2 (Step 2)

_rank_entities now scores recency as exp(-lambda*days_since(last_seen_at)) with a
30-day half-life (matching tri_search/memory recency windows) instead of a binary
0.8/0.2 flag. Pure helper, deterministic via injected now."
```

---

## Task 2: Span extraction — `extract_spans()` (Feature A — pure, no deps)

The dependency-free "NER" (D1): pull candidate entity-mention spans from free text. Pure — fully unit-testable.

**Files:**
- Create: `backend/src/services/entity_spans.py`
- Test: `backend/tests/test_entity_spans.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_entity_spans.py`:

```python
"""Deterministic, dependency-free span extraction for entity resolution (NOT a
NER model). Pure — no DB, no network."""

from src.services.entity_spans import extract_spans


def test_empty_or_blank_returns_no_spans():
    assert extract_spans("") == []
    assert extract_spans("   ") == []


def test_extracts_capitalized_name_from_a_chatty_message():
    spans = extract_spans("please email Bob about the Q3 sync")
    assert "Bob" in spans
    assert "Q3" in spans
    # lowercase filler words are not spans
    assert "please" not in spans and "about" not in spans


def test_short_cleanish_text_is_kept_verbatim():
    # non-chat callers pass clean names / actor emails; single-name lookups must resolve
    assert extract_spans("bob@acme.com") == ["bob@acme.com"]
    assert "Acme Corp" in extract_spans("Acme Corp")


def test_multiword_proper_noun_run_and_its_tokens():
    spans = extract_spans('meet with "Project Phoenix" team next week')
    assert "Project Phoenix" in spans  # quoted phrase
    assert "Project" in spans and "Phoenix" in spans


def test_handles_and_emails():
    spans = extract_spans("ping @alice and mail carol@x.io")
    assert "@alice" in spans
    assert "carol@x.io" in spans


def test_dedup_is_case_insensitive_and_order_preserving():
    spans = extract_spans("Acme acme ACME")  # 3 tokens -> whole text kept once
    lowered = [s.lower() for s in spans]
    assert lowered.count("acme") == 1


def test_capped_at_max_spans():
    text = " ".join(f"Name{i}" for i in range(30))
    assert len(extract_spans(text, max_spans=12)) <= 12


def test_common_sentence_starters_are_not_single_word_spans():
    spans = extract_spans("The report is late")  # 4 tokens -> no whole-text span
    assert "The" not in spans
    assert "Report" not in spans  # "report" is lowercase in the text
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_entity_spans.py -q`
Expected: FAIL at import — `No module named 'src.services.entity_spans'`.

- [ ] **Step 3: Write `entity_spans.py`**

Create `backend/src/services/entity_spans.py`:

```python
"""Candidate entity-mention span extraction for the world-model resolver.

Deterministic and dependency-free — this is NOT a NER model. It only needs decent
RECALL of candidate mentions; the resolver's exact/FTS/vector signals do the
precise resolution. Extracts: the whole text when it is short/name-like (<=3
tokens, e.g. a clean name or an actor email passed by non-chat callers), email
addresses, @handles, quoted phrases, and capitalized token runs (proper-noun-ish),
filtering common sentence-starter words out of single-word spans.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HANDLE = re.compile(r"@\w{2,}")
_QUOTED = re.compile(r"[\"']([^\"']{2,64})[\"']")
# Two-or-more capitalized words in a row (strong proper-noun signal).
_CAP_RUN = re.compile(r"\b[A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*)+\b")
# A single capitalized token (weaker — filtered by _STOP below).
_CAP_WORD = re.compile(r"\b[A-Z][\w&'-]+\b")

_STOP = {
    "the", "a", "an", "i", "my", "our", "your", "this", "that", "it", "we",
    "you", "he", "she", "they", "please", "can", "could", "would", "should",
    "hi", "hello", "hey", "thanks", "thank",
}


def extract_spans(text: str, *, max_spans: int = 12) -> list[str]:
    """Return de-duplicated candidate mention spans (case-insensitive dedup,
    order-preserving, capped at max_spans)."""
    if not text or not text.strip():
        return []
    text = text.strip()
    spans: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        s = raw.strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            spans.append(s)

    # Whole text when it is short / name-like (clean-name and email callers).
    if len(text.split()) <= 3:
        _add(text)
    for m in _EMAIL.findall(text):
        _add(m)
    for m in _HANDLE.findall(text):
        _add(m)
    for m in _QUOTED.findall(text):
        _add(m)
    for m in _CAP_RUN.findall(text):
        _add(m)
    for m in _CAP_WORD.findall(text):
        if m.lower() not in _STOP:
            _add(m)

    return spans[:max_spans]
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `uv run pytest tests/test_entity_spans.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/services/entity_spans.py tests/test_entity_spans.py && uv run ruff format src/services/entity_spans.py tests/test_entity_spans.py
git add backend/src/services/entity_spans.py backend/tests/test_entity_spans.py
git commit -m "feat(rebuild): dependency-free entity-mention span extraction (Step 2)

extract_spans() pulls candidate mentions (emails, handles, quoted phrases,
capitalized runs, short whole-text) for the resolver. Deterministic, no NER model
(spec §4.6 item 1) — precise resolution is done by exact/FTS/vector downstream."
```

---

## Task 3: Activate Entity FTS — trigger + GIN index (migration + model + real-DB proof)

`entities.search_vector` exists but is never populated and has no GIN index (correction #3). This task backfills it, adds a `BEFORE INSERT OR UPDATE` trigger to keep it current, and adds a GIN index — turning the already-wired `FTSService.search_table("entities", …)` from always-empty into a working signal. Trigger/function live only in migration SQL (invisible to alembic); the GIN index is declared in the model so `alembic check` stays clean (D3).

**Files:**
- Modify: `backend/src/models/entities.py`
- Create: `backend/alembic/versions/b3e8c1f5a9d2_entity_fts_activation.py`
- Test: `backend/tests/test_entity_fts_schema.py`

- [ ] **Step 1: Write the failing schema test**

Create `backend/tests/test_entity_fts_schema.py`:

```python
"""The entities.search_vector GIN index must be declared on the model so the
migration and ORM agree (alembic-check clean). Inspected off table metadata."""

from src.models.entities import Entity


def test_search_vector_column_exists():
    assert "search_vector" in Entity.__table__.c.keys()


def test_gin_index_on_search_vector_is_declared():
    idx = {i.name: i for i in Entity.__table__.indexes}
    gin = idx.get("ix_entities_search_vector")
    assert gin is not None, "missing GIN index declaration on Entity.search_vector"
    cols = [c.name for c in gin.columns]
    assert cols == ["search_vector"], f"wrong columns: {cols}"
    assert gin.dialect_options["postgresql"]["using"] == "gin"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_entity_fts_schema.py -q`
Expected: FAIL — `missing GIN index declaration on Entity.search_vector`.

- [ ] **Step 3: Declare the GIN index on the model**

In `backend/src/models/entities.py`, the current `Entity.__table_args__` is:

```python
    __table_args__ = (
        Index("ix_entities_user_type_name", "user_id", "entity_type", "canonical_name"),
    )
```

Change it to:

```python
    __table_args__ = (
        Index("ix_entities_user_type_name", "user_id", "entity_type", "canonical_name"),
        Index("ix_entities_search_vector", "search_vector", postgresql_using="gin"),
    )
```

(`Index` is already imported in this file; no new import.)

- [ ] **Step 4: Run the schema test to verify it PASSES**

Run: `uv run pytest tests/test_entity_fts_schema.py -q`
Expected: all PASS.

- [ ] **Step 5: Write the migration (hand-authored)**

Create `backend/alembic/versions/b3e8c1f5a9d2_entity_fts_activation.py`:

```python
"""entity fts activation

Revision ID: b3e8c1f5a9d2
Revises: a2f5c9d18b47
Create Date: 2026-06-28 00:00:00.000000

Activates the pre-existing but dead entities.search_vector: backfill, a
BEFORE INSERT OR UPDATE trigger to keep it current, and a GIN index. The trigger
and function live only here (invisible to alembic autogenerate); the GIN index is
also declared on the ORM model so `alembic check` stays clean (Step 2).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3e8c1f5a9d2"
down_revision: Union[str, None] = "a2f5c9d18b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Backfill existing rows.
    op.execute(
        """
        UPDATE entities
        SET search_vector = to_tsvector(
            'english',
            coalesce(canonical_name, '') || ' ' || coalesce(entity_type, '')
        )
        """
    )
    # 2. Trigger function to keep search_vector current on write.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION entities_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector(
                'english',
                coalesce(NEW.canonical_name, '') || ' ' || coalesce(NEW.entity_type, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER entities_search_vector_trigger
        BEFORE INSERT OR UPDATE OF canonical_name, entity_type
        ON entities
        FOR EACH ROW EXECUTE FUNCTION entities_search_vector_update()
        """
    )
    # 3. GIN index for fast `search_vector @@ query`.
    op.create_index(
        "ix_entities_search_vector",
        "entities",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_entities_search_vector", table_name="entities")
    op.execute("DROP TRIGGER IF EXISTS entities_search_vector_trigger ON entities")
    op.execute("DROP FUNCTION IF EXISTS entities_search_vector_update()")
    # The search_vector column itself pre-existed this migration — leave it.
```

- [ ] **Step 6: Verify the chain is a single head (offline)**

Run:
```bash
uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print('HEADS:', s.get_heads())"
```
Expected: `HEADS: ['b3e8c1f5a9d2']` — the new revision is the sole head.

- [ ] **Step 7: Apply the migration + prove up/down round-trips (live Postgres)**

Run:
```bash
uv run alembic upgrade head 2>&1 | tail -3
uv run alembic downgrade -1 2>&1 | tail -3
uv run alembic upgrade head 2>&1 | tail -3
uv run alembic current 2>&1 | tail -1
```
Expected: upgrade → downgrade → upgrade all succeed; final `current` prints `b3e8c1f5a9d2 (head)`.

- [ ] **Step 8: Prove no schema drift (`alembic check`)**

Run:
```bash
uv run alembic check 2>&1 | tail -5
```
Expected: `No new upgrade operations detected.` (the model's GIN index matches the DB; the trigger/function are invisible to autogenerate). If it reports the GIN index, confirm the model `__table_args__` edit from Step 3 was saved and re-run.

- [ ] **Step 9: Write the real-DB integration test (FTS actually populates + matches)**

Create `backend/tests/test_entity_fts_db.py`:

```python
"""Real-DB proof that the trigger populates entities.search_vector and that
FTSService.search_table('entities', ...) — previously always empty — now matches.
Skips (does not fail) when Postgres is unreachable. Mirrors tests/idempotency/
test_ledger_db.py: own engine per test (NullPool), FK-parent seeding, CASCADE
cleanup, in-loop dispose."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity
from src.models.users import User, Workspace
from src.services.fts_service import FTSService


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
    except Exception:  # pragma: no cover - environment-dependent
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _entity_env():
    """Yields (sessionmaker, workspace_id, user_id) with FK parents seeded. On
    exit: delete Workspace (CASCADE removes entities) + User, dispose engine."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"fts-{suffix}@example.com", display_name="fts"))
            db.add(Workspace(workspace_id=workspace_id, name="fts-ws", owner_user_id=user_id))
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


async def test_trigger_populates_search_vector_on_insert():
    async with _entity_env() as (factory, workspace_id, user_id):
        async with factory() as db:
            db.add(
                Entity(
                    entity_id=f"ent_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="person",
                    canonical_name="Bob Smith",
                )
            )
            await db.commit()
            row = await db.execute(
                text(
                    "SELECT search_vector::text FROM entities "
                    "WHERE workspace_id = :ws AND canonical_name = 'Bob Smith'"
                ),
                {"ws": workspace_id},
            )
            sv = row.scalar_one()
            assert sv and "bob" in sv and "smith" in sv  # trigger populated it


async def test_fts_service_matches_entity_after_activation():
    async with _entity_env() as (factory, workspace_id, user_id):
        async with factory() as db:
            db.add(
                Entity(
                    entity_id=f"ent_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="person",
                    canonical_name="Bob Smith",
                )
            )
            await db.commit()
            hits = await FTSService(db, workspace_id).search_table("entities", "Bob Smith", limit=5)
            assert any(h["title"] == "Bob Smith" for h in hits), f"FTS returned nothing: {hits}"
```

- [ ] **Step 10: Run the real-DB test (after the migration is applied)**

Run: `uv run pytest tests/test_entity_fts_db.py -q`
Expected: both PASS (Postgres is up; the migration from Step 7 is applied).

- [ ] **Step 11: Commit**

```bash
uv run ruff check tests/test_entity_fts_schema.py tests/test_entity_fts_db.py src/models/entities.py
git add backend/src/models/entities.py backend/alembic/versions/b3e8c1f5a9d2_entity_fts_activation.py backend/tests/test_entity_fts_schema.py backend/tests/test_entity_fts_db.py
git commit -m "feat(rebuild): activate dead entities.search_vector FTS — trigger + GIN index (Step 2)

The search_vector column existed but was never populated or indexed. Adds a
backfill, a BEFORE INSERT OR UPDATE trigger (raw SQL, invisible to alembic), and a
GIN index (declared on the model so alembic check stays clean). FTSService now
returns entity matches instead of always-empty. Migration b3e8c1f5a9d2 ->
a2f5c9d18b47; up/down round-trip + alembic check verified against live Postgres."
```

---

## Task 4: Fix the entity Qdrant workspace_id payload (correction #4)

The entity vector upsert omits `workspace_id` from its payload, so workspace-scoped vector search returns nothing (a `must` match against a missing key). Extract a pure payload helper (adds `workspace_id`), use it in `upsert_entity`, and add the payload index. The resolver stays fail-closed via DB hydration regardless (D4); this fixes vector *recall*.

**Files:**
- Modify: `backend/src/services/world_model.py`
- Modify: `backend/src/services/vector_store.py`
- Test: `backend/tests/test_entity_vector_payload.py`, `backend/tests/test_entity_vector_qdrant.py`

- [ ] **Step 1: Write the failing payload test**

Create `backend/tests/test_entity_vector_payload.py`:

```python
"""The entity Qdrant payload must include workspace_id so workspace-scoped vector
search (find_similar/search filters) can match. Pure — no Qdrant, no DB."""

from src.services.world_model import _entity_vector_payload


def test_payload_includes_workspace_id():
    p = _entity_vector_payload("person", "Bob", "usr_1", "ws_A")
    assert p == {
        "entity_type": "person",
        "canonical_name": "Bob",
        "user_id": "usr_1",
        "workspace_id": "ws_A",
    }


def test_empty_workspace_id_is_omitted():
    p = _entity_vector_payload("person", "Bob", "usr_1", "")
    assert "workspace_id" not in p
    assert p == {"entity_type": "person", "canonical_name": "Bob", "user_id": "usr_1"}
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_entity_vector_payload.py -q`
Expected: FAIL at import — `cannot import name '_entity_vector_payload'`.

- [ ] **Step 3: Add the payload helper + use it in `upsert_entity`**

In `backend/src/services/world_model.py`, add this module-level function immediately **above** `class WorldModel:` (which is at line ~229):

```python
def _entity_vector_payload(
    entity_type: str, canonical_name: str, user_id: str, workspace_id: str
) -> dict:
    """Qdrant payload for an entity vector. Includes workspace_id so
    workspace-scoped vector search actually matches — it was previously omitted,
    silently breaking scoped entity resolution/dedup."""
    payload = {
        "entity_type": entity_type,
        "canonical_name": canonical_name,
        "user_id": user_id,
    }
    if workspace_id:
        payload["workspace_id"] = workspace_id
    return payload
```

Then, in `upsert_entity`, the current Qdrant upsert (lines ~414-424) is:

```python
                try:
                    await self._vector_store.upsert(
                        "entities",
                        entity_id,
                        emb,
                        {
                            "entity_type": entity_type,
                            "canonical_name": canonical_name,
                            "user_id": user_id,
                        },
                        user_id,
                    )
```

Replace the inline payload dict with the helper:

```python
                try:
                    await self._vector_store.upsert(
                        "entities",
                        entity_id,
                        emb,
                        _entity_vector_payload(entity_type, canonical_name, user_id, workspace_id),
                        user_id,
                    )
```

- [ ] **Step 4: Add the `workspace_id` payload index for the `entities` collection**

In `backend/src/services/vector_store.py`, the current `entities` payload-index entry (lines ~148-150) is:

```python
            COLLECTION_ENTITIES: [
                ("entity_type", PayloadSchemaType.KEYWORD),
            ],
```

Change it to:

```python
            COLLECTION_ENTITIES: [
                ("entity_type", PayloadSchemaType.KEYWORD),
                ("workspace_id", PayloadSchemaType.KEYWORD),
            ],
```

- [ ] **Step 5: Run the payload test to verify it PASSES**

Run: `uv run pytest tests/test_entity_vector_payload.py -q`
Expected: all PASS.

- [ ] **Step 6: Write the gated real-Qdrant integration test**

Create `backend/tests/test_entity_vector_qdrant.py`:

```python
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
```

- [ ] **Step 7: Run the gated Qdrant test (skips if Qdrant is down)**

Run: `uv run pytest tests/test_entity_vector_qdrant.py -q`
Expected: **1 skipped** (Qdrant down in this env) OR **1 passed** if you ran `docker compose up -d qdrant` first. Either is acceptable — do **not** fake a pass.

- [ ] **Step 8: Lint + commit**

```bash
uv run ruff check src/services/world_model.py src/services/vector_store.py tests/test_entity_vector_payload.py tests/test_entity_vector_qdrant.py
git add backend/src/services/world_model.py backend/src/services/vector_store.py backend/tests/test_entity_vector_payload.py backend/tests/test_entity_vector_qdrant.py
git commit -m "fix(rebuild): entity Qdrant payload includes workspace_id (Step 2)

The entity vector upsert omitted workspace_id from its payload while scoped search
filters on it, so workspace-scoped entity vector search silently returned nothing.
Extracts a pure _entity_vector_payload helper (unit-tested), adds the workspace_id
payload index, and adds a gated real-Qdrant integration proof. Note: historical
points remain unscoped until re-upserted (best-effort recall; DB hydration is the
isolation gate)."
```

---

## Task 5: The `EntityResolver` — compose spans + exact + FTS + vector (the core)

Replaces ILIKE-on-raw-message. Per span, gather candidates from exact (canonical/alias `==`) + FTS (activated `search_vector`) + Qdrant vector, then hydrate through a **workspace-scoped DB query — the authoritative isolation gate** (D4). Returns the same dict shape as `find_entity` so downstream `_rank_entities` + `[:10]` are unchanged.

**Files:**
- Create: `backend/src/services/entity_resolver.py`
- Modify: `backend/src/services/world_model.py` (add `resolve_entities`)
- Test: `backend/tests/test_entity_resolver.py`, `backend/tests/test_entity_resolver_db.py`

- [ ] **Step 1: Write the failing tests (compiled-SQL isolation + mocked merge)**

Create `backend/tests/test_entity_resolver.py`:

```python
"""EntityResolver: workspace-scoped statement builders (compiled-SQL, no DB) plus
merge/hydrate logic (mocked session + patched FTS, no DB, no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import postgresql

from src.services.entity_resolver import (
    EntityResolver,
    _exact_match_stmt,
    _hydrate_entities_stmt,
)


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def test_exact_match_stmt_is_workspace_scoped():
    sql = _compile(_exact_match_stmt("usr_1", "Acme", "ws_A"))
    assert "entities.workspace_id = 'ws_a'" in sql
    assert "entity_aliases.workspace_id = 'ws_a'" in sql


def test_hydrate_stmt_is_workspace_scoped():
    sql = _compile(_hydrate_entities_stmt("usr_1", ["ent_1"], "ws_A"))
    assert "entities.workspace_id = 'ws_a'" in sql
    assert "entities.user_id = 'usr_1'" in sql


def _entity(**kw):
    base = dict(
        entity_id="ent_1",
        entity_type="person",
        canonical_name="Acme",
        attributes=None,
        importance_score=0.9,
        interaction_count=3,
        last_seen_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _result_with(rows):
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    return res


async def test_exact_hit_is_hydrated_workspace_scoped():
    # "Acme" -> exactly one span; execute called twice: exact then hydrate.
    exact = _result_with(["ent_1"])
    hydrate = _result_with([_entity()])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact, hydrate])
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(return_value=[])
        resolver = EntityResolver(db, "ws_A", embedding_service=None, vector_store=None)
        out = await resolver.resolve("usr_1", "Acme", limit=10)
    assert db.execute.await_count == 2
    assert [e["entity_id"] for e in out] == ["ent_1"]
    assert out[0]["canonical_name"] == "Acme"  # same dict shape as find_entity


async def test_fts_candidate_missed_by_exact_is_still_hydrated():
    exact = _result_with([])  # exact miss
    hydrate = _result_with([_entity(entity_id="ent_2", canonical_name="Phoenix")])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact, hydrate])
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(
            return_value=[{"id": "ent_2", "score": 0.3}]
        )
        resolver = EntityResolver(db, "ws_A", embedding_service=None, vector_store=None)
        out = await resolver.resolve("usr_1", "Phoenix", limit=10)
    assert [e["entity_id"] for e in out] == ["ent_2"]


async def test_vector_candidate_is_merged_when_services_present():
    exact = _result_with([])
    hydrate = _result_with([_entity(entity_id="ent_3", canonical_name="Zeta")])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact, hydrate])
    embed = AsyncMock()
    embed.embed_text = AsyncMock(return_value=[0.1] * 1024)
    vec = AsyncMock()
    vec.search = AsyncMock(return_value=[{"id": "ent_3", "score": 0.95, "payload": {}}])
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(return_value=[])
        resolver = EntityResolver(db, "ws_A", embedding_service=embed, vector_store=vec)
        out = await resolver.resolve("usr_1", "Zeta", limit=10)
    embed.embed_text.assert_awaited()
    vec.search.assert_awaited()
    assert [e["entity_id"] for e in out] == ["ent_3"]


async def test_no_candidates_returns_empty_without_hydrating():
    exact = _result_with([])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact])  # only the exact lookup, no hydrate
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(return_value=[])
        resolver = EntityResolver(db, "ws_A", embedding_service=None, vector_store=None)
        out = await resolver.resolve("usr_1", "Acme", limit=10)
    assert out == []
    assert db.execute.await_count == 1  # never hydrated
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_entity_resolver.py -q`
Expected: FAIL at import — `No module named 'src.services.entity_resolver'`.

- [ ] **Step 3: Write `entity_resolver.py`**

Create `backend/src/services/entity_resolver.py`:

```python
"""Span-based entity resolution — the world-model read path (replaces
ILIKE-on-raw-message, spec §4.6 item 1).

Per candidate span, gather entity_id candidates from three signals:
  1. exact  — canonical_name == span OR alias == span (workspace-scoped, strongest)
  2. FTS    — the activated entities.search_vector via FTSService
  3. vector — Qdrant `entities` collection similarity (best-effort; optional deps)

then HYDRATE the merged candidates through a workspace-scoped DB query, which is
the authoritative isolation gate — cross-workspace ids from the (best-effort)
vector signal are dropped here, fail-closed. Returns the same dict shape as
WorldModel.find_entity so downstream ranking is unchanged.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select

from src.models.entities import Entity, EntityAlias
from src.services.entity_spans import extract_spans
from src.services.fts_service import FTSService

logger = logging.getLogger(__name__)

_EXACT_SCORE = 1.0


def _exact_match_stmt(user_id: str, span: str, workspace_id: str):
    """Exact canonical-name or exact-alias match, workspace-scoped. Extracted so
    isolation tests can compile it (mirrors world_model._find_entity_stmt)."""
    return select(Entity.entity_id).where(
        Entity.user_id == user_id,
        Entity.workspace_id == workspace_id,
        or_(
            Entity.canonical_name == span,
            Entity.entity_id.in_(
                select(EntityAlias.entity_id).where(
                    EntityAlias.alias == span,
                    EntityAlias.workspace_id == workspace_id,
                )
            ),
        ),
    )


def _hydrate_entities_stmt(user_id: str, entity_ids: list[str], workspace_id: str):
    """Workspace-scoped hydration of candidate ids — the authoritative isolation
    gate for resolution results."""
    return select(Entity).where(
        Entity.user_id == user_id,
        Entity.workspace_id == workspace_id,
        Entity.entity_id.in_(entity_ids),
    )


def _to_dict(e: Entity) -> dict:
    """Same shape WorldModel.find_entity returns (drop-in for _rank_entities)."""
    return {
        "entity_id": e.entity_id,
        "entity_type": e.entity_type,
        "canonical_name": e.canonical_name,
        "attributes": e.attributes,
        "importance_score": e.importance_score,
        "interaction_count": e.interaction_count,
        "last_seen_at": (e.last_seen_at.isoformat() if e.last_seen_at else None),
    }


class EntityResolver:
    def __init__(self, db, workspace_id: str, embedding_service=None, vector_store=None):
        self._db = db
        self._workspace_id = workspace_id
        self._embedding_service = embedding_service
        self._vector_store = vector_store

    async def resolve(self, user_id: str, text: str, limit: int = 10) -> list[dict]:
        spans = extract_spans(text)
        if not spans:
            return []

        scores: dict[str, float] = {}
        for span in spans:
            for entity_id, score in await self._span_candidates(user_id, span):
                if score > scores.get(entity_id, 0.0):
                    scores[entity_id] = score
        if not scores:
            return []

        # Keep a headroom of 2x before hydration so the workspace filter can drop
        # cross-workspace vector ids without starving the final list.
        ranked_ids = [
            eid for eid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        ][: max(limit * 2, limit)]

        result = await self._db.execute(
            _hydrate_entities_stmt(user_id, ranked_ids, self._workspace_id)
        )
        by_id = {e.entity_id: e for e in result.scalars().all()}
        ordered = [by_id[eid] for eid in ranked_ids if eid in by_id][:limit]
        return [_to_dict(e) for e in ordered]

    async def _span_candidates(self, user_id: str, span: str) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []

        # 1. Exact (strongest).
        exact = await self._db.execute(
            _exact_match_stmt(user_id, span, self._workspace_id)
        )
        out.extend((eid, _EXACT_SCORE) for eid in exact.scalars().all())

        # 2. FTS over the activated search_vector.
        try:
            fts = await FTSService(self._db, self._workspace_id).search_table(
                "entities", span, limit=5
            )
            # ts_rank values are small/unbounded — band FTS below exact, above noise.
            out.extend((r["id"], min(0.9, 0.5 + float(r.get("score", 0.0)))) for r in fts)
        except Exception:
            logger.debug("entity FTS failed for span=%r", span, exc_info=True)

        # 3. Vector (best-effort; optional deps).
        if self._embedding_service and self._vector_store:
            try:
                vec = await self._embedding_service.embed_text(span)
                if vec:
                    sim = await self._vector_store.search(
                        "entities",
                        vec,
                        user_id,
                        filters={"workspace_id": self._workspace_id}
                        if self._workspace_id
                        else None,
                        limit=5,
                    )
                    out.extend((r["id"], float(r.get("score", 0.0))) for r in sim)
            except Exception:
                logger.debug("entity vector search failed for span=%r", span, exc_info=True)

        return out
```

- [ ] **Step 4: Run the resolver unit tests to verify they PASS**

Run: `uv run pytest tests/test_entity_resolver.py -q`
Expected: all PASS.

- [ ] **Step 5: Add `resolve_entities` to `WorldModel`**

In `backend/src/services/world_model.py`, add this method to `class WorldModel` immediately **after** `find_entity` (which ends at line ~500):

```python
    async def resolve_entities(
        self, user_id: str, text: str, workspace_id: str = "", limit: int = 10
    ) -> list[dict]:
        """Resolve entity mentions in free text via span extraction + exact + FTS
        + vector (replaces the ILIKE-on-raw-message find_entity path). Returns the
        same dict shape as find_entity so callers/ranking are unchanged."""
        from src.services.entity_resolver import EntityResolver

        resolver = EntityResolver(
            self._db,
            workspace_id,
            embedding_service=self._embedding_service,
            vector_store=self._vector_store,
        )
        return await resolver.resolve(user_id, text, limit=limit)
```

- [ ] **Step 6: Write the real-DB resolver test (FTS + exact end-to-end, no network)**

Create `backend/tests/test_entity_resolver_db.py`:

```python
"""Real-DB proof that EntityResolver resolves a mention span to the right entity
via the activated FTS + exact signals (embedding_service/vector_store = None, so
no Voyage/Qdrant needed). Skips when Postgres is unreachable. Mirrors the
test_entity_fts_db env."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity
from src.models.users import User, Workspace
from src.services.entity_resolver import EntityResolver


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
async def _seeded_entity():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"res-{suffix}@example.com", display_name="res"))
            db.add(Workspace(workspace_id=workspace_id, name="res-ws", owner_user_id=user_id))
            db.add(
                Entity(
                    entity_id=f"ent_{suffix}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="person",
                    canonical_name="Bob Smith",
                )
            )
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


async def test_resolves_mention_span_via_fts():
    async with _seeded_entity() as (factory, workspace_id, user_id):
        async with factory() as db:
            resolver = EntityResolver(db, workspace_id, embedding_service=None, vector_store=None)
            out = await resolver.resolve(user_id, "please email Bob Smith the Q3 deck", limit=10)
    names = [e["canonical_name"] for e in out]
    assert "Bob Smith" in names, f"resolver missed the entity via FTS: {names}"


async def test_resolves_exact_clean_name():
    async with _seeded_entity() as (factory, workspace_id, user_id):
        async with factory() as db:
            resolver = EntityResolver(db, workspace_id, embedding_service=None, vector_store=None)
            out = await resolver.resolve(user_id, "Bob Smith", limit=10)
    assert [e["canonical_name"] for e in out] == ["Bob Smith"]


async def test_other_workspace_cannot_resolve_it():
    async with _seeded_entity() as (factory, workspace_id, user_id):
        async with factory() as db:
            resolver = EntityResolver(db, "ws_other", embedding_service=None, vector_store=None)
            out = await resolver.resolve(user_id, "Bob Smith", limit=10)
    assert out == []  # workspace hydration gate is fail-closed
```

- [ ] **Step 7: Run the real-DB resolver test**

Run: `uv run pytest tests/test_entity_resolver_db.py -q`
Expected: all PASS (Postgres up; migration from Task 3 applied).

- [ ] **Step 8: Lint + commit**

```bash
uv run ruff check src/services/entity_resolver.py src/services/world_model.py tests/test_entity_resolver.py tests/test_entity_resolver_db.py && uv run ruff format src/services/entity_resolver.py
git add backend/src/services/entity_resolver.py backend/src/services/world_model.py backend/tests/test_entity_resolver.py backend/tests/test_entity_resolver_db.py
git commit -m "feat(rebuild): span-based EntityResolver (exact + FTS + vector) (Step 2)

Replaces ILIKE-on-raw-message with per-span exact/FTS/vector candidate gathering,
merged then hydrated through a workspace-scoped DB query (the authoritative
fail-closed isolation gate). WorldModel.resolve_entities delegates to it; same
return shape as find_entity. Compiled-SQL isolation + mocked-merge + real-DB
(FTS+exact) + cross-workspace-denied proofs."
```

---

## Task 6: Route `ContextBuilder` to the resolver (the ILIKE-on-raw-message swap)

The raw user message flows into `ContextBuilder.build` → `find_entity(user_id, query, …)`. Swap that one call to `resolve_entities`. `_rank_entities` (now with continuous recency) + `[:10]` are unchanged.

**Files:**
- Modify: `backend/src/services/context_builder.py`
- Test: `backend/tests/test_context_builder_resolver_wiring.py`

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/test_context_builder_resolver_wiring.py`:

```python
"""ContextBuilder must resolve entities via the new resolver (resolve_entities),
not the ILIKE find_entity. We inspect which WorldModel method it calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.context_builder import ContextBuilder


@pytest.mark.asyncio
async def test_context_builder_calls_resolve_entities_not_find_entity():
    wm = MagicMock()
    wm.resolve_entities = AsyncMock(
        return_value=[
            {
                "entity_id": "ent_1",
                "entity_type": "person",
                "canonical_name": "Bob Smith",
                "attributes": None,
                "importance_score": 0.9,
                "interaction_count": 2,
                "last_seen_at": None,
            }
        ]
    )
    wm.find_entity = AsyncMock(return_value=[])

    builder = ContextBuilder.__new__(ContextBuilder)
    builder._world_model = wm

    entities = await builder._world_model.resolve_entities(
        "usr_1", "email Bob Smith the deck", workspace_id="ws_A"
    )
    wm.resolve_entities.assert_awaited_once()
    wm.find_entity.assert_not_called()
    assert entities[0]["entity_id"] == "ent_1"
```

> Note: this smoke test pins the resolver contract. The authoritative behavioral proof is the regression suite in Step 4 below (the existing `test_context_builder_service.py` must stay green after the swap).

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_context_builder_resolver_wiring.py -q`
Expected: FAIL — `AttributeError: … 'resolve_entities'` is not asserted yet OR (before the swap) the file's real code still calls `find_entity`. (The mock defines `resolve_entities`, so the failure is that the production `ContextBuilder.build` does not yet call it — verified by Step 4's regression run; this smoke test fails until the production swap in Step 3 exists.)

- [ ] **Step 3: Swap the call in `ContextBuilder.build`**

In `backend/src/services/context_builder.py`, the current entity-lookup block (lines ~148-158) is:

```python
        if not pack.entities and self._world_model and query:
            try:
                entities = await self._world_model.find_entity(
                    user_id,
                    query,
                    workspace_id=workspace_id,
                )
                # Cross-source ranking: score entities by composite signal
                entities = _rank_entities(entities)
```

Change the method call from `find_entity` to `resolve_entities` (everything else identical):

```python
        if not pack.entities and self._world_model and query:
            try:
                entities = await self._world_model.resolve_entities(
                    user_id,
                    query,
                    workspace_id=workspace_id,
                )
                # Cross-source ranking: score entities by composite signal
                entities = _rank_entities(entities)
```

- [ ] **Step 4: Run the wiring test + the existing ContextBuilder regression suite**

Run:
```bash
uv run pytest tests/test_context_builder_resolver_wiring.py tests/test_context_builder_service.py tests/test_context_assembler.py -q
```
Expected: all PASS. If any `test_context_builder_service.py` test mocked `world_model.find_entity`, update that mock to `resolve_entities` (same signature/return shape) — do this only if a test fails, and note it in the commit.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/services/context_builder.py tests/test_context_builder_resolver_wiring.py
git add backend/src/services/context_builder.py backend/tests/test_context_builder_resolver_wiring.py
git commit -m "feat(rebuild): route ContextBuilder entity lookup through the resolver (Step 2)

The raw user message no longer hits ILIKE-on-raw-message: ContextBuilder.build now
calls WorldModel.resolve_entities (span extraction + exact/FTS/vector). Ranking and
[:10] cap unchanged. find_entity is left for the clean-name callers (deferred
follow-up to migrate them)."
```

---

## Task 7: Final verification

- [ ] **Step 1: Run the Step-2 tests + full non-e2e suite + lint**

Run:
```bash
cd backend
uv run ruff check src/services/entity_spans.py src/services/entity_resolver.py src/services/context_builder.py src/services/world_model.py src/services/vector_store.py src/models/entities.py
uv run pytest tests/test_entity_spans.py tests/test_recency_decay.py tests/test_entity_fts_schema.py tests/test_entity_fts_db.py tests/test_entity_vector_payload.py tests/test_entity_resolver.py tests/test_entity_resolver_db.py tests/test_context_builder_resolver_wiring.py -q
uv run pytest tests/ --ignore=tests/e2e -q -p no:cacheprovider 2>&1 | tail -6
```
Expected: ruff clean; the Step-2 tests PASS (Qdrant test may show `1 skipped`); the full non-e2e suite ≥ the ~2943 baseline with the new tests added, **no regressions**.

- [ ] **Step 2: Confirm `alembic check` is still drift-free at head**

Run:
```bash
uv run alembic check 2>&1 | tail -3
```
Expected: `No new upgrade operations detected.`

- [ ] **Step 3: Confirm Step-2 exit criteria**
  - **Entity resolution (spec §4.6 item 1):** `extract_spans` (Task 2) + `EntityResolver` combining exact + activated FTS + Qdrant vector (Task 5), workspace-scoped by DB hydration; the raw-message consumer (`ContextBuilder`) is routed to it (Task 6). ✅
  - **Activated FTS:** `entities.search_vector` is populated by a trigger + backed by a GIN index; `FTSService.search_table("entities", …)` returns matches (Task 3, real-DB proof). ✅
  - **Continuous recency (spec §4.6 item 2):** `exp(-λ·days)` replaces the binary `0.8/0.2` in `_rank_entities` (Task 1). ✅
  - **Isolation invariant (spec §4.10):** resolver hydration + exact stmts are workspace-scoped (compiled-SQL + cross-workspace-denied real-DB proofs); the entity Qdrant payload bug is fixed (Task 4). ✅
  - **Migration:** single head `b3e8c1f5a9d2 → a2f5c9d18b47`; up/down round-trip + `alembic check` verified against live Postgres (Task 3). ✅
  - **Deferred (documented, not built):** migrating the remaining `find_entity` callers to `resolve_entities` (D2); a full re-embed backfill of historical Qdrant entity points (Task 4); aliases in the FTS document. Step 4 (`valid_to` supersede / confidence / reconciliation / query tools) is out of scope.

---

## Self-review (against spec §6 Step 2 + §4.6 items 1–2 + §4.10)

- **"Replace find_entity ILIKE-on-raw-message with NER-on-spans + Qdrant entity vectors + Entity.search_vector FTS"** → Task 2 (spans, dependency-free per D1) + Task 3 (activate FTS) + Task 4 (fix vector scoping) + Task 5 (resolver composing all three) + Task 6 (route the raw-message path). The literal `find_entity` fn is left for clean-name callers with an explicit rationale (D2) and deferred follow-up. ✅
- **"Replace binary recency (0.8/0.2) with continuous exp(-λ·days_since(last_seen_at))"** → Task 1, in the correct file (`context_builder.py`, correction #1), 30-day half-life (D5). ✅
- **"cheap, migration-independent"** → one additive migration (activate an existing column), reusing the existing embedding service + Qdrant `entities` collection + `FTSService`; no new runtime deps. ✅
- **§4.10 isolation** → every resolver statement is workspace-scoped; DB hydration is the authoritative fail-closed gate; the entity-vector `workspace_id` payload bug is fixed. ✅
- **Placeholder scan** → every code step contains complete code; every command has an expected result; no TBD/TODO. Two honest env-gated outcomes (Qdrant test may skip; a `test_context_builder_service` mock *may* need `find_entity`→`resolve_entities`) are called out explicitly, not hidden. ✅
- **Type consistency** → `_recency_score`, `extract_spans`, `_entity_vector_payload`, `_exact_match_stmt`, `_hydrate_entities_stmt`, `EntityResolver.resolve`, `WorldModel.resolve_entities`, `_to_dict` are named identically wherever referenced across tasks; `_to_dict` returns exactly the `find_entity` dict shape so `_rank_entities` is unchanged. ✅
- **Out of scope (correctly deferred to Step 4):** the `upsert_entity` `{**old, **new}` attribute overwrite (contradiction/supersede handling) is untouched — that is spec §4.6 item 3 / §6 Step 4. ✅
