# Step 8 — Context JIT-Hybrid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the eager "assemble ALL context every turn" pack (≈55–60 backend round-trips/turn) with a JIT-hybrid model — a slim always-on core (identity, explicit preferences, goals, compact entity summary) plus on-demand retrieval of bulky detail via the **existing** Step-4 read tools — landing **dormant behind a dedicated `deep_context_jit` flag** on the deep chat path, with working-context overflow handled by the **already-installed** `SummarizationMiddleware` (de-risked, not adopted).

**Architecture:** The slim/JIT restructure is gated: `ContextBuilder.build()` / `to_prompt()` gain a `jit: bool = False` parameter, flipped **only** in the `runtime=="deep"` chat branch via `deep_context_jit` (default off) and **only** for the agents that already hold read tools (`planner`, `perceiver`, `librarian`). Legacy `agent_loop` and all three autonomous callers stay byte-identical. Dead-code deletions (`artifacts`, `tool_options`) land live (byte-neutral). Summarization work is limited to a Phase-0 SSE-leak spike + a conditional `stream_adapter.py` metadata filter. **No migration** — Step 5 already owns `TaskRunDetail.context_pack` + TTL; the chat-path `context_block` is never persisted.

**Tech Stack:** Python 3.12, LangChain 1.3.10 / deepagents 0.6.11 (`_DeepAgentsSummarizationMiddleware`, `AnthropicPromptCachingMiddleware`), Deep Agents runtime, FastMCP internal tools, Postgres/Redis/Qdrant/Neo4j. Test harness: custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio), `make_mock_settings()`/`TEST_USER_ID`/`TEST_WORKSPACE_ID` from `tests/conftest.py`. Full gate: `uv run pytest tests/ --ignore=tests/e2e` → **3313 passed / 18 skipped** baseline (18, NOT ~108 — ~108 = infra down).

---

## EXECUTED = SHIP (2026-07-09, subagent-driven)

All 5 phases landed on `rebuild/first-principles` (off `main`, NOT pushed/merged). Full non-e2e gate **3325 passed / 18 skipped / 0 failed**, ruff clean, single migration head `1a2770a28c39` (drift-free — **NO migration**), no new tools. Holistic opus = **SHIP** (independently reproduced all 3 negative controls RED→restore→GREEN; tree clean). All Step-8 machinery is DORMANT behind `deep_context_jit=False` + `runtime=="deep"` — flag flip is a Step-10 activation gate (ledger B11).

**Commits:** P0 spike `77ffe31` (+ nit `98f77b7`) → P3 SSE filter `25f7e16` → P1 dead-`artifacts` cleanup `55a0412` → P2 producer `669f675` + wiring `3fb3bd6` + review-nits `9de00ab` → P4 forced-on e2e + cache guard `0ea0bf2`.

**Two plan-corrections made during execution (verify-don't-trust catches):**
1. **`tool_options` is NOT dead — KEPT it.** The plan (from extraction E1) claimed no `build()` caller passes `task_type`. FALSE: `step_graph_store.py:67` and `step_runner.py:427` both pass it, and `graph_executor_factory.py:98-101` builds the `ContextBuilder` WITH a real `tool_registry` → `pack.tool_options` is live on the autonomous DAG path. Deleting it would have silently regressed autonomous. P1 deletes ONLY the genuinely-dead `artifacts` fetch (`ArtifactStore` has no `.search()` → throws+swallows every chat turn) + its fiction-mock test.
2. **Phase order was P0→P3→P1→P2→P4** (not P0→P1→P2→P3→P4). The P0 spike confirmed the SSE leak, committing a RED spike; P3 (the filter) was pulled forward to flip it GREEN so P1/P2 ran on a fully-green suite ("green at every checkpoint").

**Review tiers actually run:** P0 = re-run-the-probe (verdict self-evident); P3 = independent opus (APPROVE-WITH-NITS — reviewer's "middleware not installed" nit REJECTED after reading `deepagents/graph.py:777`, which unconditionally installs it in the lead stack; spike name/docstring tidied); P1 = self + diff verify; P2 = **2-stage PARALLEL** spec (SPEC-COMPLIANT) + quality (APPROVE-WITH-NITS → per-agent-gate teeth test + `nullslast` + goal-priority comment applied); P4 = forced-on e2e; final holistic opus = SHIP.

**Execution-surfaced carries (added to ledger):** SSE filter matches only `lc_source=="summarization"` (fail-open on tag-key/nesting drift — C11); `_fetch_core_entities` nullable-`last_seen_at` ordering unexercised vs real Postgres (dormant — activation sanity check under B11).

---

## Forks resolved (2026-07-09, with user, grounded in 4-agent extraction)

| # | Fork | Resolution |
|---|------|------------|
| 1 | LIVE vs DORMANT | **Option A — deep-only, dormant behind new `deep_context_jit` flag.** Legacy + autonomous byte-identical; dead-code cleanup rides live. |
| 2 | SummarizationMiddleware | **Keep deepagents' 85% default; de-risk only** (P0 SSE spike + conditional adapter filter). Already installed+active. Trigger-tuning → ledger. |
| 3a | Presenter/Executor scope | **Keep them eager** (rule: an agent gets the slim pack iff it holds JIT read tools). No scope change; no Executor read-surface expansion. |
| 3b | Graph reach | **Graph → JIT one-hop `traverse`**; accept reach downgrade vs eager Neo4j weighted depth-2. Richer depth-2 graph tool → ledger. |
| 4 | Packaging | **One plan, five phases** (P0 spike → P1 live cleanup → P2 slim/JIT behind flag → P3 summarization de-risk → P4 forced-on e2e). |

---

## Verified start-state facts (verify-don't-trust, re-checked at file:line 2026-07-09)

- Branch `rebuild/first-principles`, HEAD `e91d634` (skeleton commit; parent `9e7b57b`). Single migration head `1a2770a28c39`, no drift, ruff clean, baseline 3313/18.
- **STALE ANCHORS CAUGHT:** `src/tools/intelligence_server.py` is a *package* (`.../intelligence_server/`); `ContextPack` is in `src/services/context_builder.py:95` (no `src/contracts/context_pack.py`); `src/services/surface_detail_builders/` is a package.
- **The seam is shared:** `assemble_context` (context_assembler.py:210) is called at `agent_invoker.py:514`, BEFORE the `if runtime=="deep"` branch at :522. Deep consumes at :555-559; legacy `agent_loop` else-branch consumes the same `system_blocks` at :595. Non-streaming twin `call_agent` (:777) is legacy-only.
- **`build()` has 6 callers** (E2): context_assembler.py:241 (chat), intelligence_server/memory.py:247 (`build_context` MCP tool), step_graph_store.py:67 + step_runner.py:427 + graph_executor.py:449 (autonomous, the last two *persist* the pack). `to_prompt` is a shared `@staticmethod`.
- **Cost surface** (E1): ≈55–60 round-trips worst case — ~8 Voyage embeds (same query embedded 3×), ~12 Qdrant, ~6 Neo4j (graph = up to 5 traversals), ~16 PG reads, **~15 PG writes** (`refresh_stability` inside a read). Autonomous path is leaner (no tri_search/graph_engine/artifact_store — graph_executor_factory.py:98).
- **Dead code (E1):** `artifacts` branch throws `AttributeError` every turn (`ArtifactStore` has no `.search()`) and is swallowed → never populated; `tool_options` branch never runs (no caller passes `task_type`; chat path passes no `tool_registry`). `to_prompt` has **no empty-coupling** — every section is `if pack.X:` guarded → dropping a category just omits its section.
- **JIT tools live (E4):** `get_entity`/`query_facts`/`traverse`/`get_provenance` (world_model_tools.py:36-139) + `search` (memory.py:20) + `get_goal_memories` (memory.py:171, direct PG). All workspace-filtered fail-closed. **Planner/Perceiver/Librarian hold all of them in scope** (agents.py:29-98); **Presenter/Executor hold none of the world-model reads** (agents.py:99-164).
- **SummarizationMiddleware already installed+active** (E3): deepagents' `_DeepAgentsSummarizationMiddleware`, base stack (`deepagents/graph.py:702`), `trigger=("fraction",0.85)`/`keep=("fraction",0.10)`, `wrap_model_call`, non-mutating, summarizes **message history + tool results, never the system prompt**. Never fires in tests (< ~170K tokens).
- **Two-block cache layout** (E3): `build_system_prompt` (agent_invoker.py:166-174) → Block 1 `{SOUL_CORE+role, cache_control: ephemeral}` (only cached breakpoint), Block 2 `{context_block}` (volatile). `build_system_message` (prompt_bridge.py:32) preserves per-block `cache_control`.
- **Flag pattern:** `deep_inline_format`/`deep_delegates_enabled`/`deep_readback_enabled` at settings.py:179-193 (`bool = False`); each defaulted `False` in `make_mock_settings` (conftest.py:74-82) with the MagicMock-truthy-hazard comment.
- **SSE frames (E3):** `stream_adapter.py:172-199` streams `stream_mode=["messages","updates"]`, NO `subgraphs=True`. Line 174 `msg = payload[0] if isinstance(payload, tuple) else payload` — **`payload[1]` (metadata) is discarded**. Summary `.ainvoke` is a same-graph nested call → 7B2 subgraph-precedent does NOT cover it → likely leaks `text_delta`/`thinking` frames + inflates `agent_done` usage.

---

## File structure / decomposition map

| File | Change | Phase |
|------|--------|-------|
| `src/services/context_builder.py` | remove dead `artifacts`/`tool_options` fetches (P1); add `jit` param + slim branch + `_fetch_core_goals`/`_fetch_core_entities` + `to_prompt(jit=)` compact rendering (P2) | P1, P2 |
| `src/orchestrator/context_assembler.py` | add `JIT_ENABLED_AGENTS`; thread `jit` through `assemble_context` (P2) | P2 |
| `src/orchestrator/agent_invoker.py` | wire `jit=(runtime=="deep" and deep_context_jit)` at the :514 call site (P2) | P2 |
| `src/config/settings.py` | add `deep_context_jit: bool = False` (P2) | P2 |
| `tests/conftest.py` | default `deep_context_jit=False` in `make_mock_settings` (P2) | P2 |
| `src/deep_runtime/stream_adapter.py` | (conditional) `lc_source=="summarization"` metadata filter (P3) | P3 |
| `tests/deep_runtime/test_summarization_sse_spike.py` | P0 spike (new) | P0 |
| `tests/services/test_context_builder_jit.py` | slim/JIT + char tests (new) | P1, P2 |
| `tests/orchestrator/test_context_jit_wiring.py` | flag wiring + negative controls (new) | P2, P4 |

**HOT FILES** — `context_builder.py`, `context_assembler.py`, `agent_invoker.py` are single-owner, sequenced: P1 touches only `context_builder.py`; P2 touches all three in order builder → assembler → invoker. Dispatch implementers **synchronously** for P2.

---

## Phase 0 — Offline SSE-leak spike (could DISPROVE "no stream_adapter change")

**Rationale:** The one unproven assumption. If the summary `.ainvoke` streams, its chunks leak into the frozen 8-frame SSE contract and inflate cost. The 7B2 subgraph precedent does NOT transfer (same-graph nested call). This spike proves the *adapter's* behavior deterministically (offline, fake streaming summary model); the "does the real model stream under adaptive-thinking config" question is a secondary live check deferred to the ledger.

### Task 0.1: Spike — does a streaming summarization call leak into the SSE contract?

**Files:**
- Create: `tests/deep_runtime/test_summarization_sse_spike.py`

- [ ] **Step 1: Write the spike test**

```python
"""Step 8 P0 spike: prove/disprove that a STREAMING summarization model call
leaks token deltas into the frozen 8-frame SSE contract (stream_adapter).

Offline: we drive the adapter with a fake astream that interleaves a
summarization-sourced AIMessageChunk (metadata lc_source='summarization')
among normal chunks, and assert the adapter's CURRENT behavior. This pins the
leak so P3 can fix it; if the adapter already skips it, P3 becomes a guard test.
"""
from langchain_core.messages import AIMessageChunk

from src.deep_runtime.stream_adapter import stream_deep_agent_events  # verify exact export name


class _FakeAgent:
    """Minimal astream stub emitting (mode, payload) tuples like LangGraph."""

    def __init__(self, events):
        self._events = events

    async def astream(self, graph_input, config=None, **kwargs):
        for ev in self._events:
            yield ev


async def test_summarization_chunk_leaks_into_frozen_frames():
    # normal assistant token, then a summarization-sourced chunk, then normal token
    normal = AIMessageChunk(content="hello ")
    summ = AIMessageChunk(content="[[internal summary]] ")
    normal2 = AIMessageChunk(content="world")
    events = [
        ("messages", (normal, {"lc_source": None})),
        ("messages", (summ, {"lc_source": "summarization"})),
        ("messages", (normal2, {"lc_source": None})),
    ]
    frames = []
    async for f in stream_deep_agent_events(_FakeAgent(events), agent_name="presenter", graph_input={}, config={}):
        frames.append(f)

    text = "".join(f["text"] for f in frames if f.get("event") == "text_delta")
    # THE ASSERTION THAT MAY FAIL (proving the leak): summary text must NOT appear
    assert "[[internal summary]]" not in text, (
        "LEAK CONFIRMED: summarization chunk leaked into text_delta frames — P3 filter REQUIRED"
    )
```

- [ ] **Step 2: Run the spike — RECORD the verdict (may fail; that's the point)**

Run: `uv run pytest tests/deep_runtime/test_summarization_sse_spike.py -v`
Expected: **either** it FAILS (leak confirmed → P3 filter is required) **or** PASSES (adapter already tolerant → P3 is a guard test). Record which in the plan's Phase-3 gate below and in the ledger.

- [ ] **Step 3: Verify the fake-astream shape matches the real adapter contract**

Confirm `stream_deep_agent_events`'s exact name/signature and that `payload` is a `(message, metadata)` tuple in messages-mode (re-read stream_adapter.py:172-174). Fix the stub if the real signature differs. Do NOT proceed to P3 until the spike faithfully reproduces the adapter's messages branch.

- [ ] **Step 4: Commit the spike**

```bash
git add tests/deep_runtime/test_summarization_sse_spike.py
git commit -m "test(rebuild): Step 8 P0 SSE-leak spike for summarization chunks (records leak verdict)"
```

> **Gate:** the spike's verdict decides P3's shape. If leak confirmed, P3 lands the filter. If not, P3 is a regression guard. Either way, run the FULL gate (`uv run pytest tests/ --ignore=tests/e2e`) after committing — expect 3314/18 (spike adds 1).

---

## Phase 1 — Live byte-neutral cleanup (all runtimes)

**Rationale:** Remove provably-dead branches from `build()`. Byte-neutral: output identical (both throw-and-swallow-empty or never-execute), only waste removed. Safe on the live legacy + autonomous paths. Touches ONLY `context_builder.py`.

### Task 1.1: Characterization test pinning `to_prompt` + `build()` output

**Files:**
- Create: `tests/services/test_context_builder_jit.py`

- [ ] **Step 1: Write the characterization test**

```python
"""Step 8 characterization + JIT tests for ContextBuilder."""
from src.services.context_builder import ContextBuilder, ContextPack


def test_to_prompt_renders_stable_sections_for_full_pack():
    pack = ContextPack(
        task_summary="ship the thing",
        goals=[{"title": "launch", "priority": "critical"}],
        entities=[{"canonical_name": "Acme", "entity_type": "org", "importance_score": 0.9}],
        preferences=[{"fact_text": "prefers concise replies"}],
        recent_events=[{"fact_text": "signed contract"}],
    )
    out = ContextBuilder.to_prompt(pack)
    assert "## Task\nship the thing" in out
    assert "## Active Goals" in out and "launch" in out
    assert "## Relevant Entities" in out and "Acme" in out
    assert "## User Preferences" in out and "prefers concise replies" in out
    # artifacts/tool_options were never populated → sections MUST be absent
    assert "## Artifacts" not in out
    assert "## Available Tools" not in out
```

- [ ] **Step 2: Run — verify it PASSES against current code (it's a characterization pin)**

Run: `uv run pytest tests/services/test_context_builder_jit.py::test_to_prompt_renders_stable_sections_for_full_pack -v`
Expected: PASS (pins today's rendering).

### Task 1.2: Remove the dead `artifacts` fetch branch

**Files:**
- Modify: `src/services/context_builder.py:282-295` (the `# Artifacts` block)

- [ ] **Step 1: Delete the dead branch**

Remove the entire block (it calls `self._artifact_store.search`, which does not exist → always `AttributeError`, swallowed):

```python
        # Artifacts
        if self._artifact_store and query:
            try:
                artifacts = await self._artifact_store.search(user_id, query, limit=5)
                pack.artifacts = [
                    {
                        "artifact_id": a.artifact_id,
                        "artifact_type": a.artifact_type,
                        "title": a.title,
                    }
                    for a in artifacts
                ]
            except Exception:
                logger.debug("Artifact search failed", exc_info=True)
```

Keep `pack.artifacts` as its `ContextPack` default `[]` and keep the `to_prompt` `## Artifacts` render (guarded, now always skipped) to avoid rippling into persisted-pack consumers.

- [ ] **Step 2: Run the characterization test — output unchanged**

Run: `uv run pytest tests/services/test_context_builder_jit.py -v`
Expected: PASS (artifacts was never populated; removing the throw changes nothing).

### Task 1.3: Remove the dead `tool_options` fetch branch

**Files:**
- Modify: `src/services/context_builder.py:307-313` (the `# Tool options` block)

- [ ] **Step 1: Delete the dead branch**

Remove (no caller passes `task_type`; chat path passes no `tool_registry`):

```python
        # Tool options — available tools for this task type
        if self._tool_registry and task_type:
            try:
                tools = await self._tool_registry.list_for_task_type(task_type)
                pack.tool_options = [t.name for t in tools]
            except Exception:
                logger.debug("Tool options fetch failed", exc_info=True)
```

Keep `pack.tool_options` default + `to_prompt` render (always skipped).

- [ ] **Step 2: Run tests + full gate**

Run: `uv run pytest tests/services/test_context_builder_jit.py -v && uv run pytest tests/ --ignore=tests/e2e -q`
Expected: char tests PASS; full gate **3314/18** (P0 added 1). ruff clean.

- [ ] **Step 3: Commit**

```bash
git add src/services/context_builder.py tests/services/test_context_builder_jit.py
git commit -m "refactor(rebuild): Step 8 P1 remove dead artifacts/tool_options context fetches (byte-neutral)"
```

> **Review:** P1 is local + byte-neutral → single quality review is sufficient. Full gate after commit.

---

## Phase 2 — Slim/JIT restructure behind `deep_context_jit` (DORMANT)

**Rationale:** The core of Step 8. Add the gated slim pack. Flag-off byte-identical to today (both legacy and deep). Touches all three hot files in order: settings → builder → assembler → invoker.

### Task 2.1: Add the `deep_context_jit` flag

**Files:**
- Modify: `src/config/settings.py` (after :193, next to the other `deep_*` flags)
- Modify: `tests/conftest.py` (in `make_mock_settings`, next to :74-82)

- [ ] **Step 1: Add the setting**

```python
    # Step 8: gate the JIT-hybrid slim context pack. Deep chat path only; when
    # False the deep path builds the full eager pack (byte-identical to legacy).
    deep_context_jit: bool = False  # JARVIS_DEEP_CONTEXT_JIT
```

- [ ] **Step 2: Default it False in `make_mock_settings` (MagicMock-truthy hazard)**

```python
        # Step 8: an unset MagicMock bool is truthy, which would route every
        # runtime="deep" test through the slim JIT pack. Default OFF to mirror prod.
        deep_context_jit=False,
```

- [ ] **Step 3: Run — confirm no regression**

Run: `uv run pytest tests/ --ignore=tests/e2e -q -k "settings or conftest or deep"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/config/settings.py tests/conftest.py
git commit -m "feat(rebuild): Step 8 P2 add deep_context_jit flag (default off)"
```

### Task 2.2: Add `jit` param + slim branch to `ContextBuilder.build()`

**Files:**
- Modify: `src/services/context_builder.py` (`build` signature :138, add slim branch + 2 helpers)
- Test: `tests/services/test_context_builder_jit.py`

- [ ] **Step 1: Write the failing slim-pack test**

```python
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.services.context_builder import ContextBuilder


async def test_build_jit_slim_pack_skips_bulky_categories():
    wm = MagicMock()
    wm.resolve_entities = AsyncMock(return_value=[{"entity_id": "e1", "canonical_name": "Acme"}])
    ge = MagicMock()
    ge.traverse_weighted = AsyncMock(return_value=[{"entity_id": "e2", "name": "Beta"}])
    mem = MagicMock()
    mem.retrieve = AsyncMock(return_value=[{"memory_type": "episodic", "fact_text": "x"}])
    mem.get_user_preferences = AsyncMock(return_value=[{"memory_id": "p1", "fact_text": "concise"}])
    db = MagicMock()  # slim goals/entities helpers use direct queries; stub in Step 3

    builder = ContextBuilder(world_model=wm, memory_service=mem, graph_engine=ge, db=db)
    pack = await builder.build(user_id="u", query="q", workspace_id="w", jit=True)

    # Bulky categories are NOT eagerly populated in slim mode:
    assert pack.graph_relationships == []
    assert pack.related_runs == []
    # resolve_entities / traverse_weighted / semantic memory.retrieve must NOT be called
    wm.resolve_entities.assert_not_called()
    ge.traverse_weighted.assert_not_called()
    mem.retrieve.assert_not_called()
    # Always-on core IS present:
    assert any(p.get("fact_text") == "concise" for p in pack.preferences)
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `uv run pytest tests/services/test_context_builder_jit.py::test_build_jit_slim_pack_skips_bulky_categories -v`
Expected: FAIL (`jit` param unknown / bulky categories still populated).

- [ ] **Step 3: Implement `jit` + slim branch + helpers**

Change the signature and restructure `build()` so the always-on core is unconditional and the bulky front-load is `if not jit:`:

```python
    async def build(
        self,
        user_id: str,
        query: str,
        task_type: str | None = None,
        workspace_id: str = "",
        jit: bool = False,
    ) -> ContextPack:
        """Build a context pack. jit=True → slim always-on core only (bulky
        detail is retrieved on demand via the internal read tools)."""
        pack = ContextPack(task_summary=query)

        if jit:
            # SLIM: always-on core only — cheap, largely query-independent.
            pack.preferences = await self._fetch_core_preferences(user_id, workspace_id)
            pack.goals = await self._fetch_core_goals(user_id, workspace_id)
            pack.entities = await self._fetch_core_entities(user_id, workspace_id)
            return pack

        # EAGER (legacy / flag-off): unchanged full front-load.
        # ... existing body from context_builder.py:148-329 verbatim ...
        return pack
```

Add helpers (direct PG, no embed/Qdrant/graph):

```python
    async def _fetch_core_preferences(self, user_id: str, workspace_id: str) -> list[dict]:
        """All active preferences (D3), non-semantic. Mirrors the eager explicit-pref fetch."""
        if not self._memory_service:
            return []
        try:
            prefs = await self._memory_service.get_user_preferences(
                user_id, workspace_id=workspace_id, max_results=20
            )
            return [
                {"memory_id": p.get("memory_id") or p.get("id", ""), "fact_text": p.get("fact_text", ""), **p}
                for p in prefs
            ][:25]
        except Exception:
            logger.debug("Core preference fetch failed", exc_info=True)
            return []

    async def _fetch_core_goals(self, user_id: str, workspace_id: str) -> list[dict]:
        """Active goal memories via a DIRECT query (mirrors get_goal_memories, memory.py:171)
        — no embed/Qdrant/refresh_stability writes."""
        if not self._db:
            return []
        try:
            from sqlalchemy import select

            from src.models.memory import Memory

            result = await self._db.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.workspace_id == workspace_id,
                    Memory.memory_type == "goal",
                    Memory.status == "active",
                )
                .order_by(Memory.created_at.desc())
                .limit(5)
            )
            return [
                {"memory_id": g.memory_id, "title": g.fact_text, "confidence": g.confidence, "priority": "medium"}
                for g in result.scalars().all()
            ]
        except Exception:
            logger.debug("Core goal fetch failed", exc_info=True)
            return []

    async def _fetch_core_entities(self, user_id: str, workspace_id: str, limit: int = 8) -> list[dict]:
        """Top-N entities by importance/recency via a DIRECT query (NOT semantic resolution).
        Gives ambient awareness; agent calls get_entity/query_facts for detail on demand."""
        if not self._db:
            return []
        try:
            from sqlalchemy import select

            from src.models.entities import Entity  # NOTE: plural module name

            result = await self._db.execute(
                select(Entity)
                .where(Entity.user_id == user_id, Entity.workspace_id == workspace_id)
                .order_by(Entity.importance_score.desc().nullslast(), Entity.last_seen_at.desc().nullslast())
                .limit(limit)
            )
            return [
                {"entity_id": e.entity_id, "canonical_name": e.canonical_name, "entity_type": e.entity_type}
                for e in result.scalars().all()
            ]
        except Exception:
            logger.debug("Core entity fetch failed", exc_info=True)
            return []
```

> **VERIFY at execution:** confirm `Entity` model path + column names (`importance_score`, `last_seen_at`, `canonical_name`, `entity_type`) and `Memory` fields at file:line before writing — anchors rot. If `nullslast()` unsupported on the column type, order by `importance_score.desc()` alone.

- [ ] **Step 4: Run — verify it PASSES**

Run: `uv run pytest tests/services/test_context_builder_jit.py -v`
Expected: PASS. The Task-1.1 characterization test (eager path) still PASSES (eager body unchanged).

### Task 2.3: Compact slim rendering + retrieval hint in `to_prompt`

**Files:**
- Modify: `src/services/context_builder.py` (`to_prompt` :358, add `jit` param)
- Test: `tests/services/test_context_builder_jit.py`

- [ ] **Step 1: Write the failing render test**

```python
def test_to_prompt_jit_renders_compact_entities_and_retrieval_hint():
    pack = ContextPack(
        task_summary="q",
        entities=[{"canonical_name": "Acme", "entity_type": "org"}],
        goals=[{"title": "launch"}],
        preferences=[{"fact_text": "concise"}],
    )
    out = ContextBuilder.to_prompt(pack, jit=True)
    assert "Acme (org)" in out
    # compact: NO importance/last_seen/interactions decoration in slim mode
    assert "importance=" not in out
    # retrieval hint tells the agent the JIT tools exist
    assert "get_entity" in out or "query_facts" in out
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `uv run pytest tests/services/test_context_builder_jit.py::test_to_prompt_jit_renders_compact_entities_and_retrieval_hint -v`
Expected: FAIL (`to_prompt` has no `jit` param; renders full entity decoration).

- [ ] **Step 3: Implement compact rendering**

Add `jit: bool = False` to `to_prompt`; when `jit`, render entities as a terse `- name (type)` list and append a retrieval hint section:

```python
    @staticmethod
    def to_prompt(pack: ContextPack, jit: bool = False) -> str:
        sections = []
        if pack.task_summary:
            sections.append(f"## Task\n{pack.task_summary}")
        # ... goals / preferences sections unchanged ...
        if pack.entities:
            if jit:
                ent_lines = [
                    f"- {e.get('canonical_name') or e.get('name', 'unknown')} ({e.get('entity_type', '?')})"
                    for e in pack.entities
                ]
                sections.append("## Known Entities\n" + "\n".join(ent_lines))
            else:
                # ... existing full entity block (importance/last_seen/confidence) ...
                pass
        # ... graph/recent/artifacts/runs/tools/constraints/risks unchanged (skipped when empty) ...
        if jit:
            sections.append(
                "## Retrieving More Context\n"
                "Only a compact core is preloaded. Use `get_entity`, `query_facts`, "
                "`traverse`, `get_provenance`, and `search` to retrieve entity detail, "
                "facts, relationships, and memories on demand."
            )
        return "\n\n".join(sections) if sections else ""
```

> Keep the eager (`jit=False`) rendering byte-identical — do NOT refactor the shared section bodies; branch only the entity block + the trailing hint.

- [ ] **Step 4: Run — verify PASS + eager char test still green**

Run: `uv run pytest tests/services/test_context_builder_jit.py -v`
Expected: PASS all (including Task-1.1 eager characterization).

- [ ] **Step 5: Commit**

```bash
git add src/services/context_builder.py tests/services/test_context_builder_jit.py
git commit -m "feat(rebuild): Step 8 P2 gated slim JIT context pack + compact rendering (dormant)"
```

### Task 2.4: Thread `jit` through `assemble_context` with per-agent gate

**Files:**
- Modify: `src/orchestrator/context_assembler.py` (add `JIT_ENABLED_AGENTS`; `assemble_context` :210)
- Test: `tests/orchestrator/test_context_jit_wiring.py` (new)

- [ ] **Step 1: Write the failing wiring test**

```python
"""Step 8 flag-wiring + negative controls."""
from unittest.mock import AsyncMock, MagicMock

from src.orchestrator.context_assembler import ContextAssembler, JIT_ENABLED_AGENTS


def test_jit_enabled_agents_excludes_presenter_and_executor():
    assert JIT_ENABLED_AGENTS == {"planner", "perceiver", "librarian"}
    assert "presenter" not in JIT_ENABLED_AGENTS
    assert "executor" not in JIT_ENABLED_AGENTS
```

- [ ] **Step 2: Run — verify FAIL (symbol missing)**

Run: `uv run pytest tests/orchestrator/test_context_jit_wiring.py::test_jit_enabled_agents_excludes_presenter_and_executor -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement the set + threading**

In `context_assembler.py`, next to `CONTEXT_ENRICHED_AGENTS` (:21):

```python
# Step 8: agents that hold the JIT read tools and can therefore run on the slim
# pack. Presenter/Executor lack world-model reads → they keep the eager pack.
JIT_ENABLED_AGENTS = {"planner", "perceiver", "librarian"}
```

Add `jit: bool = False` to `assemble_context` (:210) and apply the per-agent gate before `build()`:

```python
    async def assemble_context(
        self, agent_name: str, message: str, user_id: str, workspace_id: str = "", jit: bool = False
    ) -> str:
        if agent_name not in CONTEXT_ENRICHED_AGENTS:
            return ""
        use_jit = jit and agent_name in JIT_ENABLED_AGENTS
        # ... integration identity block unchanged ...
        pack = await builder.build(user_id=user_id, query=message, workspace_id=workspace_id, jit=use_jit)
        context_text = ContextBuilder.to_prompt(pack, jit=use_jit)
        # ...
```

- [ ] **Step 4: Run — verify PASS**

Run: `uv run pytest tests/orchestrator/test_context_jit_wiring.py -v`
Expected: PASS.

### Task 2.5: Wire the flag at the `agent_invoker` call site

**Files:**
- Modify: `src/orchestrator/agent_invoker.py:514` (stream) — pass computed `jit`; `:777` (batch) leaves default `False`
- Test: `tests/orchestrator/test_context_jit_wiring.py`

- [ ] **Step 1: Write the failing negative-control test (TEETH)**

```python
async def test_flag_off_deep_is_byte_identical_to_eager():
    """Deep runtime + deep_context_jit=False → jit MUST be False (byte-identical to legacy)."""
    from src.orchestrator.context_assembler import ContextAssembler

    calls = {}

    async def fake_assemble(agent_name, message, user_id, workspace_id="", jit=False):
        calls["jit"] = jit
        return ""

    # settings: runtime="deep", deep_context_jit default False (from make_mock_settings)
    # ... construct AgentInvoker with a ContextAssembler whose assemble_context = fake_assemble
    # ... drive call_agent_stream for agent "planner"
    assert calls["jit"] is False  # flag off → eager even under deep
```

```python
async def test_flag_on_deep_planner_gets_jit_true():
    # settings: runtime="deep", deep_context_jit=True; agent "planner"
    # ... assert calls["jit"] is True
    ...

async def test_flag_on_deep_presenter_gets_jit_true_but_assembler_downgrades():
    # jit=True reaches assemble_context, but per-agent gate → use_jit False for presenter.
    # (covered structurally by Task 2.4's JIT_ENABLED_AGENTS test; assert wiring passes jit=True)
    ...
```

- [ ] **Step 2: Run — verify FAIL**

Run: `uv run pytest tests/orchestrator/test_context_jit_wiring.py -v -k flag`
Expected: FAIL (call site doesn't compute `jit` yet).

- [ ] **Step 3: Implement the call-site wiring**

At `agent_invoker.py:514`:

```python
        context_block = await self._context.assemble_context(
            agent_name,
            message,
            user_id=user_id,
            workspace_id=workspace_id,
            jit=(self._settings.runtime == "deep" and self._settings.deep_context_jit),
        )
```

Leave the non-streaming `call_agent` (:777) call unchanged (defaults `jit=False`).

- [ ] **Step 4: Run — verify PASS + full gate**

Run: `uv run pytest tests/orchestrator/test_context_jit_wiring.py -v && uv run pytest tests/ --ignore=tests/e2e -q`
Expected: all PASS; full gate green (3316–3319/18 — new tests added); ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/context_assembler.py src/orchestrator/agent_invoker.py tests/orchestrator/test_context_jit_wiring.py
git commit -m "feat(rebuild): Step 8 P2 wire deep_context_jit (deep chat path, JIT-agent-scoped, dormant)"
```

> **Review:** P2 is the blast-radius seam → **2-stage PARALLEL** review (spec + quality) on the frozen commit. Negative controls WITH TEETH: (a) flag-off deep ⇒ `jit=False`; (b) `make_mock_settings` defaults the flag False; (c) eager characterization (Task 1.1) still green. WIRE-THEN-FULL-GATE: run the FULL gate after Step 4 and expect surprises.

---

## Phase 3 — Summarization de-risk (conditional on P0 verdict)

### Task 3.1: (IF P0 leak confirmed) `lc_source=="summarization"` filter in `stream_adapter.py`

**Files:**
- Modify: `src/deep_runtime/stream_adapter.py:173-174`
- Test: `tests/deep_runtime/test_summarization_sse_spike.py` (promote the spike to a regression test)

- [ ] **Step 1: Turn the P0 spike assertion into the guard (already written in P0)**

The P0 test already asserts no leak. If P0 FAILED, implement the fix; if P0 PASSED, this task is a no-op guard (mark done, keep the test).

- [ ] **Step 2: Implement the metadata filter (only if needed)**

```python
            if mode == "messages":
                meta = payload[1] if isinstance(payload, tuple) and len(payload) > 1 else {}
                if isinstance(meta, dict) and meta.get("lc_source") == "summarization":
                    continue  # Step 8: summarization is an internal call — never a user-visible frame
                msg = payload[0] if isinstance(payload, tuple) else payload
                ...
```

- [ ] **Step 3: Run — verify PASS**

Run: `uv run pytest tests/deep_runtime/test_summarization_sse_spike.py -v`
Expected: PASS (leak filtered, or already absent).

- [ ] **Step 4: Full gate + commit**

Run: `uv run pytest tests/ --ignore=tests/e2e -q`
```bash
git add src/deep_runtime/stream_adapter.py tests/deep_runtime/test_summarization_sse_spike.py
git commit -m "fix(rebuild): Step 8 P3 filter summarization chunks from the SSE contract (deep-path)"
```

> **Review:** stream_adapter is on the frozen SSE seam → independent opus review of the filter (it's the ONLY stream_adapter change the whole deep effort takes). If P0 showed no leak, document that the filter is a defensive guard, not a bugfix.

---

## Phase 4 — Forced-on offline e2e + negative controls + holistic review

### Task 4.1: Forced-on slim-pack e2e (deep + flag on)

**Files:**
- Modify: `tests/orchestrator/test_context_jit_wiring.py`

- [ ] **Step 1: Write the forced-on assertions**

```python
async def test_forced_on_deep_jit_planner_slim_shape():
    # settings runtime="deep", deep_context_jit=True; agent="planner"
    # assemble real ContextAssembler over mocked services; assert:
    #  - build() called with jit=True
    #  - no resolve_entities / traverse_weighted / semantic memory.retrieve
    #  - rendered context contains the "Retrieving More Context" hint
    ...

async def test_forced_on_deep_jit_executor_keeps_eager():
    # agent="executor": use_jit False (not in JIT_ENABLED_AGENTS) → eager build path taken
    ...
```

- [ ] **Step 2: Run — verify PASS**

Run: `uv run pytest tests/orchestrator/test_context_jit_wiring.py -v -k forced_on`
Expected: PASS.

### Task 4.2: Structural caching assertion

- [ ] **Step 1: Assert the two-block cache layout survives the slim pack**

```python
def test_slim_context_preserves_two_block_cache_layout():
    # build_system_prompt(agent, context=<slim to_prompt output>) →
    # Block 0 has cache_control ephemeral (soul+role); Block 1 is the context (no cache_control)
    ...
```

Run: `uv run pytest tests/orchestrator/test_context_jit_wiring.py::test_slim_context_preserves_two_block_cache_layout -v`
Expected: PASS. (Live `cache_read_input_tokens > 0` proof stays deferred → ledger C8, needs an API key.)

- [ ] **Step 2: Full gate**

Run: `uv run pytest tests/ --ignore=tests/e2e -q && uv run ruff check src tests && uv run alembic heads`
Expected: full gate green (18 skipped, NOT ~108); ruff clean; single head `1a2770a28c39` (NO migration).

- [ ] **Step 3: Commit**

```bash
git add tests/orchestrator/test_context_jit_wiring.py
git commit -m "test(rebuild): Step 8 P4 forced-on JIT e2e + cache-layout + eager-agent negative controls"
```

- [ ] **Step 4: Holistic opus review**

Dispatch an independent opus reviewer that INDEPENDENTLY reproduces every negative control: (1) flag-off deep byte-identical; (2) `make_mock_settings` default False (MagicMock hazard); (3) Presenter/Executor stay eager; (4) legacy + autonomous callers unchanged (grep the 6 `build()` callers — none pass `jit=True`); (5) no migration. Address findings before close.

---

## Review strategy (per your carried rhythm)

- **P0 spike** — load-bearing unproven assumption → independent opus review of the spike's faithfulness to the adapter contract.
- **P1** — local byte-neutral → single quality review.
- **P2** — blast-radius seam (3 hot files) → **2-stage PARALLEL** spec + quality review on the frozen commit; single-owner-per-file, **synchronous** implementer dispatch (builder → assembler → invoker).
- **P3** — frozen SSE seam → independent opus review.
- **P4** — final → holistic opus reproducing every negative control.
- **Every checkpoint:** FULL gate `uv run pytest tests/ --ignore=tests/e2e` (18 skipped, not ~108); ruff clean; `alembic heads` single + drift-free.

---

## Deferred → activation-gate ledger (add at plan close)

- **Category B (Step-10 flip):** flip `deep_context_jit` live; slim the AUTONOMOUS path's persisted `context_pack` (step_graph_store/step_runner/graph_executor) + validate surface rendering on slimmer packs; live quality-validation that the slim core + JIT retrieval doesn't regress agent output.
- **Category C (quality):** lower the summarization trigger below 85% (telemetry-driven; `excluded_middleware` swap + a second cache-compose spike); a richer depth-2 weighted graph JIT tool (vs one-hop `traverse`); collapse the eager path's triple query-embed + read-path `refresh_stability` writes (only relevant while legacy eager lives); the `internal.build_context` whole-pack tool is in no agent's scope (orphaned escape hatch); `catalog.py:8` "19 tools" stale count.
- **Category C (secondary live proof):** whether the REAL summary `.ainvoke` streams under `build_chat_model`'s adaptive-thinking config (P0 proves the adapter's behavior offline; the live model-streaming check needs an API key — pair with C8 cache proof).

---

## Self-review (writing-plans checklist)

**Spec §4.7 coverage:** lean always-on core (P2 slim: prefs+goals+compact entities) ✓; recent conversation separate + SummarizationMiddleware `keep=` (untouched — already separate path) ✓; bulky→JIT (P2 + reuse Step-4 tools) ✓; delete relevance-blind (P1 artifacts/tool_options dead; P2 drops related_runs/constraints/risks in slim) ✓; rely on SummarizationMiddleware not the deleted scaffold (P3, no scaffold) ✓; preserve cache discipline + verify (P4.2 structural; live proof → ledger C8) ✓; cache per (workspace_id, run) — N/A chat path (not persisted; autonomous persistence is Step-10) ✓; observability → note as a P4 add-on if cheap, else ledger.

**Placeholder scan:** helpers show full code; slim/eager branch shown; `to_prompt` branch shown; VERIFY-at-execution notes flag the anchors that must be re-checked (Entity/Memory columns, `stream_deep_agent_events` signature).

**Type consistency:** `jit: bool` param name consistent across `build`/`to_prompt`/`assemble_context`; `deep_context_jit` flag name consistent (settings + conftest + call site); `JIT_ENABLED_AGENTS` consistent.
