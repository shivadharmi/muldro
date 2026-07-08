# Step 7B1 — Deep-runtime collapse foundation (Presenter inline + Librarian extraction middleware + Governor audit middleware + fold 6C #1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Single-owner-per-file + SYNCHRONOUS implementer dispatch** (`run_in_background: false`) — a background SendMessage-resumed subagent once produced F811 duplicate defs (6B lesson). **VERIFY-DON'T-TRUST every current-state claim against code before building on it** — this plan's anchors are `file:line` from `rebuild/first-principles` @ `f10f4b1`; re-confirm before editing.

**Goal:** Build the DEEP-RUNTIME machinery for three of the four Step-7 cognitive collapses — Presenter→inline formatting, Librarian→turn-scoped extraction middleware, Governor→deep audit middleware — plus fold 6C follow-up #1 (double capability resolution). All **DORMANT/proven** behind `JARVIS_RUNTIME=deep` (default `legacy`), NO runtime flip, chat path **byte-neutral on `legacy`**. Delegates + per-child models + the Governor delegate-critique are **7B2** (separate plan); inline read-back is **7C**.

**Architecture:** The deep/legacy seam is `agent_invoker.call_agent_stream:283` (`if self._settings.runtime == "deep":`). Everything above it (`build_system_prompt`, tool resolution, context assembly) is SHARED legacy+deep, so **byte-neutral collapses must live INSIDE the deep branch (`:283-328`) or the deep middleware chain (`_build_deep_agent_for:169-250`)** — never in shared code or the runtime-agnostic `chat_processor`. Governor-audit + fold-#1 are clean deep-only changes. Presenter-inline + Librarian-extraction are built as wired-but-DORMANT machinery (the 6B gate pattern: wired into the live deep seam, short-circuited/gated off on the live direct-chat path, exercised only by a forced/offline test), with LIVE activation (which would touch runtime-agnostic `chat_processor`) deferred as a documented activation gate.

**Tech Stack:** Python 3.13 (venv is 3.13, not 3.12), async SQLAlchemy (asyncpg), LangGraph/`deepagents` 0.6.11, langchain middleware (`@wrap_tool_call`, `@after_model`), pytest (custom `pytest_pyfunc_call` asyncio hook — NO pytest-asyncio), `uv` (NO pip). Full gate: `uv run pytest tests/ --ignore=tests/e2e` from `backend/`.

**Baseline at plan time:** `rebuild/first-principles` @ `f10f4b1`; **3232 passed / 18 skipped**; single alembic head `1a2770a28c39`; `alembic check` drift-free; ruff clean. **NO migration in 7B1** (no agent removed — agents stay as routing targets until activation; head unchanged).

---

## 0. How this fits the rebuild (context — READ FIRST)

Step 7 (spec T1) collapses the 6 cognitive agents into one lead + read-only workers, cognition moving to **middleware / tools / jobs**, PRESERVING model/budget specialization. Forks resolved (this session, via AskUserQuestion):

- **Q1 = STAY DORMANT** (Step-7 lock): build the collapse on the deep lead (`JARVIS_RUNTIME=deep`, default `legacy`), prove via forced/offline tests, **NO runtime flip** (cutover ≤ Step 10).
- **Q2 = SPLIT 7A / 7B / 7C**; 7A shipped (`aca6e75..f10f4b1`, Persona full-trace + dead-Governor-agent kill 7→6). **Fork-3 this session SPLIT 7B further → 7B1 (this plan) + 7B2 (delegates).**
- **Fork-1 (Presenter streaming) = INLINE-PROMPT.** The frozen `stream_adapter` contract makes reply text come ONLY from the lead's `AIMessageChunk` (`stream_adapter.py:184-186`); a tool return is a `tool_result` frame (`:188-199`), never `text_delta`, no precedent for re-surfacing. So Presenter becomes an inline formatting responsibility (prompt), NOT a result-returning tool.
- **Fork-2 (subagent security) = PER-CHILD MIDDLEWARE + DISABLE `task`.** Deferred to **7B2** (no delegates in 7B1).
- **Fork-3 (packaging) = SPLIT.** 7B1 = the delegate-free collapses (Presenter inline, Librarian extraction middleware, Governor deep-audit middleware, fold 6C #1). 7B2 = `create_deep_agent(subagents=…)` scaffolding + per-child model + Perceiver-as-delegate + Governor delegate-critique. **Governor splits: audit → 7B1, critique → 7B2.**
- **Fork-4 (T2 boundary) = DECOUPLED.** The Governor delegate-critique is INDEPENDENT of `ReadBackVerifier` (zero code coupling; `verify_step` is a self-contained predicate; Librarian's own writes are `REVERSIBLE_INTERNAL` so never trigger read-back). Inline read-back stays **7C**; the critique lands in **7B2** (needs delegates, not read-back).

**Why these four are 7B1:** Governor-audit + fold-#1 are pure deep-only wins (additive/ refactor, no shared-code touch). Presenter-inline + Librarian-extraction are the two "cognition → tool/middleware" collapses that have a home on the deep chat turn; they are built + proven dormant here so 7B2's delegate work builds on a settled middleware chain.

---

## 1. Ground-truth current state (verify-don't-trust anchors)

All `file:line` from `backend/` @ `f10f4b1`. Re-confirm before editing. Cross-verified by 4 parallel extraction passes this session.

### The seam + what is SHARED vs deep-only
- `agent_invoker.py:117-143` `build_system_prompt(agent, context, capability_summary)` → `list[dict]` blocks (`JARVIS_SOUL_CORE` + `--- YOUR ROLE ---` + role prompt, `cache_control` ephemeral; planner gets capability-summary injected). **Called at `:278-280`, BEFORE the seam → SHARED by legacy AND deep** (deep wraps it via `build_system_message(system_blocks)` `:298`; legacy passes `system_blocks=system_blocks` `:334`). **⇒ folding any prompt here is NOT byte-neutral-legacy.**
- **Seam = `agent_invoker.py:283`** `if self._settings.runtime == "deep":` (`:283-328` deep branch; `:330-347+` legacy `agent_loop`). `AGENT_RUNTIME_CALLS.labels(runtime=…).inc()` at `:282`.
- Deep build helper `_build_deep_agent_for` (`:169-250`): builds `trust_gate` (`:208-217`), `dispatcher` (`:218-222`), `write_lock` (`:233-237`) with `_resolve_cap` (`:229-231`), then `build_deep_agent(..., extra_middleware=(trust_gate, write_lock, dispatcher), system_prompt=…, checkpointer=…)` (`:242-250`). Chain (outer→inner): **`capability_scope → trust_gate → write_lock → dispatcher`** (`capability_scope` prepended by `build_deep_agent`; comment `:238-241`).

### Presenter (Fork-1)
- Agent def: `agents.py:21` tier `sonnet`; `agents.py:186` thinking `4096`; `agents.py:159-164` scope `{internal.get_briefing, internal.search, internal.push_ui, messaging.send}`.
- Prompt `PRESENTER_PROMPT` `prompts.py:522`; registered `AGENT_PROMPTS` `prompts.py:715`. Large `<surface_generation>` block (`prompts.py:545+`) instructs fenced ` ```json:surface ` / ` ```json:surface_data ` emission.
- **How the reply is produced (both runtimes):** `chat_processor.py:583-595` — a terminal `call_agent_stream("presenter", presenter_msg)`; on `agent_done` → `presenter_text = evt["text"]`; `yield Presentation(strip_surface_blocks(presenter_text))`. Surface extraction from raw `presenter_text` at `:609`. **This step is runtime-agnostic** (chat_processor delegates runtime to `call_agent_stream`). Also captured earlier at `:534-535` for `reason`/`respond` steps. Latency skip for single-read plans → `direct_answer` (`:552-568`, `presenter_skip.py`).
- Deep-path fact: reply text = lead `AIMessageChunk` text (`stream_adapter.py:99-110, 184-186`); tool return = `tool_result` frame (`:188-199`); no re-surface path. The Presenter's own model text already streams as the reply on deep TODAY (each routed agent is its own `build_deep_agent` call; NO single lead yet — `agent_builder.py` never passes `subagents=`).
- Non-collapse callers to LEAVE: daily briefing `jarvis.py:553` uses **non-streaming `call_agent`** (no deep branch — always `agent_loop`); the separate `Presenter` **service** (`services/presenter.py`) used by `routes_meetings.py:22`, `intelligence_server/persona.py:80`, `runtime.py:242` — a DIFFERENT object, untouched.
- CLAUDE.md STALE: `system.respond` does NOT route to Presenter — `chat_pipeline.py:46-47` sends `system.*` to a no-op system handler; only bare `reason`/`respond` reach Presenter (`chat_pipeline.py:48-49`, `capability_resolver.py:123-124`).

### Librarian (extraction middleware)
- Agent def: `agents.py:18` tier `sonnet`; `agents.py:185` thinking `4096`; scope `agents.py:77-86` — write tools = exactly `internal.update_entity` + `internal.store_memory` (rest read).
- Prompt `LIBRARIAN_PROMPT` `prompts.py:39-56`; registered `prompts.py:712`.
- **Two divergent extraction paths:**
  - **Perception (the real agent):** `perception_runner.py:277-284` `call_agent("librarian", …)` — **LEGACY ALWAYS** (`call_agent` has NO deep branch; `agent_invoker.py:494+`). Result also at `:487`. Untouched by 7B1 (perception cutover ≥ Step 10).
  - **Chat (a deterministic SERVICE, not the agent):** `chat_processor.py:621-633` fires `InteractionLearner.learn(...)` as background (`_spawn_background`). `InteractionLearner` (`interaction_learner.py:45`) has NO LLM agent — it calls `MemoryService.extract_and_store(...)` (`:122`) + `WorldModel.extract_from_text(...)` (`:152`), gated (skip trivial intents `:21-29`, empty-response gate, 60s Redis cooldown `:42/92`). Wired `chat_processor.py:117,136`; constructed `jarvis.py:172`. **Runtime-agnostic.**
- **Write path a Librarian middleware must preserve:** `update_entity` (`catalog.py:163-165`, cap `internal.update_entity` write `capabilities.py:149`) → `intelligence_server/memory.py:71-164` → `EntityFactStore.record_fact` + snapshot `entity.attributes` + `EntityAlias` insert + `GraphSyncService.sync_entity_by_id`. `store_memory` (`catalog.py:223-225`, cap `internal.store_memory` write `capabilities.py:158`) → `memory.py:271-370` → `MemoryService.store_*` + `WorldModel.extract_from_text` + `GraphSyncService.batch_sync_entities`. Both are `REVERSIBLE_INTERNAL_CAPABILITIES` (`predicate.py:39,47`) → never trigger read-back.
- Middleware template: `deep_runtime/middleware/budget.py:38-101` `make_budget_middleware` (`@after_model`, closure-bound deps, reads `state["messages"]`, best-effort try/except, returns `None`). `middleware/__init__.py:21-26` `__all__` omits `trust_gate`/`write_lock`.

### Governor (audit middleware — 7B1) — post-7A state
- LEGACY audit hook `hooks.py:31-96` `governor_pre_tool_hook`: looks up tool in `ToolRegistry`, disabled → `{"allowed": False}` (`:80-83`), else audit-log + `{"allowed": True}` (`:96`). Invoked in `agent_loop.py:630-641` (pre-tool). **The deep path has NO equivalent audit middleware today.**
- Dead Governor LLM agent GONE (7A). Orphaned-but-harmless tool/cap/service layer (`evaluate_policy`/`approve_action`/`get_plan_details`/`report_governor_verdict` + `services/governor.py`) LEFT — `validate_registry` (`validation.py`) tolerates orphaned caps (no "every cap needs a holder" rule). Do NOT touch.
- Governor delegate-critique = NET-NEW, does NOT exist (grep empty). → **7B2** (needs delegates).

### Fold 6C #1 (double capability resolution — deep-only)
- `trust_gate` resolves capability via `trust_gate.py:67-97` `_resolve_capability` → own session → `ToolRegistry(...).get_tool(name)`; called `:263`.
- `write_lock` resolves via injected `resolve_capability(name)` (`write_lock.py:50`), which is `_resolve_cap` (`agent_invoker.py:229-231`) → calls the SAME `_resolve_capability` with a SECOND session. **⇒ two `ToolRegistry.get_tool` lookups + two sessions per gated write.** Both also independently call `is_read_only_capability` (`trust_gate.py:279`, `write_lock.py:51`). Single-resolution seam = `_build_deep_agent_for` (`:169-250`) which owns both middleware constructions + `_resolve_cap`.

### Per-child model / tiers (PRESERVE — mostly a 7B2 concern; confirm not regressed)
- `model_factory.py:21-25` `MODEL_TIER_IDS = {opus: claude-opus-4-8, sonnet: claude-sonnet-4-6, haiku: claude-haiku-4-5-20251001}`; `:28-61` `build_chat_model(agent)`. `agents.py:16-23` tiers, `:182-189` thinking (planner 8192, perceiver 6144, librarian 4096, presenter 4096, executor 2048, persona 2048). Per-child model is UNUSED on deep today (one model per routed-agent graph; no `subagents=`). **7B1 must not regress these.**

---

## 2. Scope

**7B1 IS** (all deep-runtime / behind `JARVIS_RUNTIME=deep`, dormant on default `legacy`, chat byte-neutral on legacy):
- (Phase 0) SPIKE-FIRST proofs of the two unproven-offline assumptions (deep inline-format streams reply+surface; `@after_model` extraction middleware runs the InteractionLearner primitives offline).
- (Phase 1) Governor → deep audit middleware (`make_governor_audit_middleware`, `@wrap_tool_call`) wired into the deep chain; forced-test proven.
- (Phase 2) Fold 6C #1 — single shared capability resolution for `trust_gate` + `write_lock`.
- (Phase 3) Librarian → deep `@after_model` extraction middleware, WIRED-BUT-DORMANT (gated off on live direct chat so it never double-fires with `InteractionLearner`), forced-test proven; preserves the write path.
- (Phase 4) Presenter → deep-only inline-format prompt augmentation (applied ONLY in the deep branch so legacy is byte-identical), forced-test proven (deep agent streams reply + parseable surface blocks).
- (Phase 5) holistic opus review + full gate + `middleware/__init__.py __all__` hygiene + docs.

**7B1 IS NOT:** any runtime flip; any migration (agents stay); removing the Presenter/Librarian AGENTS or the terminal `chat_processor` presenter step or `InteractionLearner` (that's LIVE ACTIVATION — needs a runtime-agnostic `chat_processor` branch — deferred, see §Activation gates); `create_deep_agent(subagents=…)`/per-child model/Perceiver-as-delegate/Governor delegate-critique (**7B2**); inline `ReadBackVerifier` on deep + wiring `budget`/`unavailable_server` (**7C**); touching the perception Librarian agent (`perception_runner.py:277`, legacy) or the orphaned Governor tool/service layer.

---

## 3. File structure / blast radius

| Phase | Create | Modify |
|---|---|---|
| 0 | `backend/spikes/deep_collapse/inline_format_probe.py`, `backend/spikes/deep_collapse/extraction_mw_probe.py` | — |
| 1 | `src/deep_runtime/middleware/governor_audit.py` | `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for` chain), `src/deep_runtime/middleware/__init__.py` |
| 2 | — | `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for` — shared resolver), `src/deep_runtime/middleware/trust_gate.py` (inject `resolve_capability`) |
| 3 | `src/deep_runtime/middleware/librarian_extract.py` | `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for` — wire dormant), `src/deep_runtime/middleware/__init__.py` |
| 4 | — | `src/orchestrator/agent_invoker.py` (deep-branch prompt augmentation `:283-303`), `src/orchestrator/prompts.py` (extract a shared `PRESENTER_VOICE` fragment — additive, no legacy prompt change) |
| Tests | `tests/deep_runtime/test_governor_audit.py`, `tests/deep_runtime/test_capability_resolution_fold.py`, `tests/deep_runtime/test_librarian_extract.py`, `tests/deep_runtime/test_presenter_inline.py` | — |

**Migrations:** NONE (no agent removed; head stays `1a2770a28c39`).

**Same-file sequencing (single-owner-per-file):** `agent_invoker.py` is touched by Phases 1→2→3→4 in that order; `middleware/__init__.py` by 1→3→5. Dispatch these SYNCHRONOUSLY and in order; never two owners of one file concurrently.

---

## Phase 0 — SPIKE-FIRST (prove the two unproven-offline assumptions)

**Rationale:** 6A/6B/6C each had a core assumption DISPROVEN by a Task-0 spike. The two assumptions 7B1 rests on are UNPROVEN offline: (1) a deep agent whose system prompt carries the Presenter voice streams a reply AS `text_delta` whose joined text still yields parseable ` ```json:surface ` blocks (Phase 4); (2) an `@after_model` middleware attached to a compiled deep agent actually fires and can drive async extraction from `state["messages"]` (Phase 3). Prove both with an OFFLINE scripted-streaming fake `BaseChatModel` (no API key), reusing the fake-model pattern from `backend/spikes/deep_stream/probe.py` (6A) and `backend/spikes/deep_stream/central_dispatcher_proof.py` (6A.5). **If a spike disproves an assumption, STOP and re-scope that phase** (do not paper over).

### Task 0.1 — Spike: deep inline-format streams reply + surface blocks
**Files:** Create `backend/spikes/deep_collapse/inline_format_probe.py`.

- [ ] **Step 1: Write the probe.** Build a real `create_deep_agent` (or `build_deep_agent`) whose `system_prompt` includes a Presenter-voice instruction, driven by a fake `BaseChatModel` that STREAMS (yields `AIMessageChunk`s) a short reply followed by a fenced ` ```json:surface ` block (copy the fake-streaming-model scaffold from `central_dispatcher_proof.py`). Run it through `stream_deep_agent_events(...)` (`src/deep_runtime/stream_adapter.py`) and collect frames.

```python
# backend/spikes/deep_collapse/inline_format_probe.py — skeleton (fill fake-model from central_dispatcher_proof.py)
import asyncio, json
from src.deep_runtime.stream_adapter import stream_deep_agent_events
# VERIFIED (chat_processor.py:62): both live in src.services.surface_mapping
from src.services.surface_mapping import extract_surface_spec, strip_surface_blocks

REPLY = "Here is your summary.\n\n```json:surface\n{\"kind\":\"summary\",\"should_surface\":true}\n```"

async def main():
    agent = ...  # build_deep_agent(responder_agent, [], system_prompt=SystemMessage(<soul + PRESENTER_VOICE>), db_factory=None ...)
    frames = [f async for f in stream_deep_agent_events(agent, {"messages":[{"role":"user","content":"summarize"}]}, {"configurable":{"thread_id":"spike"}}, agent_name="presenter", model="claude-sonnet-4-6")]
    text = "".join(f.get("text","") for f in frames if f.get("event")=="text_delta")
    done = next((f for f in frames if f.get("event")=="agent_done"), None)
    assert "Here is your summary" in (done or {}).get("text","") == False or "summary" in text  # reply streamed as text_delta
    assert strip_surface_blocks(done["text"]).strip() == "Here is your summary."  # surface stripped for chat
    assert extract_surface_spec(done["text"]).should_surface is True  # surface parses from raw text
    print("SPIKE PASS")

asyncio.run(main())
```

- [ ] **Step 2: Run it.** `uv run python backend/spikes/deep_collapse/inline_format_probe.py`. **Expected: `SPIKE PASS`** — reply text arrives as `text_delta`/`agent_done.text`, `strip_surface_blocks` yields the clean reply, `extract_surface_spec` parses the block. **If instead** the surface block is dropped/mangled by streaming, or the text never surfaces → RE-SCOPE Phase 4 (the inline-format assumption is wrong).
- [ ] **Step 3: Record the DECISION** in a short doc `docs/superpowers/spikes/2026-07-08-deep-inline-format.md` (pass/fail + the exact frame shapes observed), mirroring the 6A/6A.5 spike-decision docs. Commit `spike(rebuild): prove deep inline-format streams reply + surface blocks (Step 7B1 P0)`.

### Task 0.2 — Spike: `@after_model` extraction middleware fires + drives async extraction
**Files:** Create `backend/spikes/deep_collapse/extraction_mw_probe.py`.

- [ ] **Step 1: Write the probe.** Construct a middleware exactly like `make_budget_middleware` (`src/deep_runtime/middleware/budget.py:38-101`) — `@after_model` — but whose body calls an injected `AsyncMock` "extractor" with the first-human + last-AI text pulled from `state["messages"]`. Attach it via `create_deep_agent(..., middleware=[the_mw])` with a fake non-streaming/streaming model, invoke one turn, assert the extractor was awaited once with the turn's text.

```python
# extraction_mw_probe.py — skeleton
from langchain.agents.middleware import after_model
async def probe():
    calls = []
    @after_model(name="ExtractProbe")
    async def mw(state, runtime):
        msgs = state.get("messages") or []
        human = next((m for m in msgs if getattr(m,"type",None)=="human"), None)
        ai = next((m for m in reversed(msgs) if getattr(m,"type",None)=="ai"), None)
        calls.append((getattr(human,"content",None), getattr(ai,"content",None)))
        return None
    agent = ...  # create_deep_agent(model=<fake>, tools=[], middleware=[mw])
    await agent.ainvoke({"messages":[{"role":"user","content":"remember Bob"}]}, {"configurable":{"thread_id":"spike2"}})
    assert calls and "remember Bob" in str(calls[0][0]); print("SPIKE PASS")
```

- [ ] **Step 2: Run it.** `uv run python backend/spikes/deep_collapse/extraction_mw_probe.py`. **Expected: `SPIKE PASS`** — the `@after_model` hook fires after the model turn with the turn messages in `state`. **If** it never fires or `state["messages"]` lacks the AI turn → RE-SCOPE Phase 3.
- [ ] **Step 3: Record + commit** `spike(rebuild): prove @after_model extraction middleware fires offline (Step 7B1 P0)` (append findings to the same spike doc).

---

## Phase 1 — Governor → deep audit middleware (clean deep-only win)

**Files:** Create `src/deep_runtime/middleware/governor_audit.py`. Modify `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for`), `src/deep_runtime/middleware/__init__.py`. Test `tests/deep_runtime/test_governor_audit.py`.

**Design:** Port the legacy `governor_pre_tool_hook` (`hooks.py:31-96`) to a deep `@wrap_tool_call` middleware modeled EXACTLY on `jarvis_tool_dispatcher.py` (built-in fall-through, `request.tool_call["name"]`/`["id"]`). Placed FIRST in `extra_middleware` (so chain = `capability_scope → governor_audit → trust_gate → write_lock → dispatcher`). It (a) falls through for deepagents built-ins, (b) blocks disabled tools → `ToolMessage(status="error")`, (c) audit-logs all others, (d) `return await handler(request)`. This is the spec's "Governor → audit middleware on tool calls" on the deep path (legacy already has the hook; the deep path had NONE).

- [ ] **Step 1: Write the failing test.** In `tests/deep_runtime/test_governor_audit.py`: build the middleware standalone, drive it with a fake `request` (a small object exposing `.tool_call={"name","id","args"}`) and a fake `handler` (`AsyncMock` returning a sentinel `ToolMessage`). Cases: (i) a built-in name (`"task"`) → `handler` called, passthrough; (ii) a Jarvis tool whose registry `get_tool(...).enabled is False` (patch `ToolRegistry.get_tool` via a fake `db_factory`) → returns a `ToolMessage` with `status=="error"` and `handler` NOT called; (iii) an enabled tool → `handler` called + a `tool_audit` log emitted (assert via `caplog`).

```python
# assertion cores
assert (await mw_builtin).status is None  # built-in fell through to handler sentinel
blocked = await mw.middleware  # invoke wrap_tool_call fn with disabled tool
assert blocked.status == "error" and "blocked" in blocked.content.lower()
handler.assert_not_awaited()  # blocked path never calls handler
# enabled path:
handler.assert_awaited_once()
assert any(r.msg == "tool_audit" for r in caplog.records)
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: governor_audit`). `uv run pytest tests/deep_runtime/test_governor_audit.py -v`.

- [ ] **Step 3: Implement `governor_audit.py`.**

```python
"""Deep-runtime Governor audit middleware (Step 7B1).

Port of the legacy governor_pre_tool_hook (hooks.py) to a @wrap_tool_call middleware for the
deep path: audit-log every Jarvis tool call, BLOCK disabled tools (→ ToolMessage error), fall
through for deepagents built-ins. Audit-only — approval gating is trust_gate's job (6B).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def make_governor_audit_middleware(
    *, agent_name: str, workspace_id: str, db_factory: Callable[[], Any]
) -> AgentMiddleware:
    @wrap_tool_call
    async def governor_audit(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        is_blocked = False
        risk_level = "low"
        try:
            async with db_factory() as db:
                tool_def = await ToolRegistry(db, workspace_id or None).get_tool(name)
                if tool_def:
                    is_blocked = not tool_def.enabled
                    risk_level = tool_def.risk_level
        except Exception:
            logger.debug("[deep_runtime] governor_audit lookup failed for %s", name, exc_info=True)

        if is_blocked:
            logger.warning("governor_blocked_tool", extra={"tool": name, "agent": agent_name})
            return ToolMessage(
                content=json.dumps({"error": f"Tool '{name}' is blocked by policy", "blocked": True}),
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )

        logger.info(
            "tool_audit",
            extra={"tool": name, "agent": agent_name, "risk_level": risk_level, "workspace_id": workspace_id},
        )
        return await handler(request)

    return governor_audit
```
(VERIFY `ToolRegistry(db, workspace_id or None)` ctor against `trust_gate.py:85`; add `from typing import Any` import.)

- [ ] **Step 4: Wire it FIRST in `extra_middleware`.** In `agent_invoker._build_deep_agent_for` (`:208-250`), construct `governor_audit = make_governor_audit_middleware(agent_name=agent.name, workspace_id=workspace_id, db_factory=self._db_factory)` and change `extra_middleware=(trust_gate, write_lock, dispatcher)` → `extra_middleware=(governor_audit, trust_gate, write_lock, dispatcher)`. Update the chain-order comment (`:238-241`) to `capability_scope → governor_audit → trust_gate → write_lock → dispatcher`.

- [ ] **Step 5: Export.** Add `make_governor_audit_middleware` to `src/deep_runtime/middleware/__init__.py` import + `__all__`.

- [ ] **Step 6: Run — expect PASS.** `uv run pytest tests/deep_runtime/test_governor_audit.py -v`.

- [ ] **Step 7: Forced-integration guard (deep chain).** Add a test building a REAL deep agent via `_build_deep_agent_for` with a fake streaming model that calls a DISABLED Jarvis tool; assert the streamed `tool_result` frame has `blocked: true` and the dispatcher never executed it. **NEGATIVE CONTROL:** remove `governor_audit` from `extra_middleware`, re-run → the disabled tool reaches the dispatcher (no block) → test FAILS → restore.

- [ ] **Step 8: Commit** `feat(rebuild): Governor deep audit middleware on the deep tool chain (Step 7B1 P1)`.

---

## Phase 2 — Fold 6C follow-up #1 (single capability resolution for trust_gate + write_lock)

**Files:** Modify `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for`), `src/deep_runtime/middleware/trust_gate.py`. Test `tests/deep_runtime/test_capability_resolution_fold.py`.

**Design:** Today one gated write hits `ToolRegistry.get_tool` TWICE (trust_gate `_resolve_capability:263` + write_lock via `_resolve_cap`→`_resolve_capability` at `agent_invoker.py:230`), each opening its own session. Introduce ONE per-turn memoized async resolver in `_build_deep_agent_for` and inject it into BOTH. `write_lock` already accepts an injected `resolve_capability`; `trust_gate` must be changed to accept an injected resolver instead of calling its internal `_resolve_capability`. **Preserve trust_gate's N1 fail-closed semantics** (lookup error → block): the shared resolver must signal failure and trust_gate must still fail closed.

- [ ] **Step 1: Write the failing spy test.** Build `_build_deep_agent_for` (or just the resolver closure it will expose) and drive a fake gated write that goes through BOTH trust_gate and write_lock for the SAME tool name; patch `_resolve_capability`/`ToolRegistry.get_tool` with a spy; assert the underlying registry lookup happens EXACTLY ONCE per distinct tool name (not twice). Also assert a lookup EXCEPTION still makes trust_gate block (fail-closed preserved).

```python
# assertion cores
assert spy_get_tool.call_count == 1  # one lookup shared by trust_gate + write_lock
# fail-closed: with the resolver raising, the gated write is blocked, not executed
assert result_frame["blocked"] is True
```

- [ ] **Step 2: Run — expect FAIL** (two lookups today). `uv run pytest tests/deep_runtime/test_capability_resolution_fold.py -v`.

- [ ] **Step 3: Implement the shared resolver.** In `_build_deep_agent_for`, replace the `_resolve_cap` closure with a memoized resolver that both middlewares share:

```python
        # 6C #1 fold: resolve each tool's capability ONCE per turn, shared by trust_gate + write_lock.
        _cap_cache: dict[str, tuple[bool, str | None]] = {}
        async def _resolve_cap_shared(name: str) -> tuple[bool, str | None]:
            if name not in _cap_cache:
                _cap_cache[name] = await _resolve_capability(name, workspace_id, self._db_factory)
            return _cap_cache[name]

        async def _resolve_cap(name: str):  # write_lock's existing (name)->cap interface
            _ok, cap = await _resolve_cap_shared(name)
            return cap
```
Then change `make_trust_gate_middleware(...)` to accept an injected `resolve_capability=_resolve_cap_shared` (returning `(lookup_ok, cap)`), and inside `trust_gate.py` replace the internal `await _resolve_capability(name, workspace_id, db_factory)` call (`:263`) with `await resolve_capability(name)`. Keep the `(lookup_ok, capability)` unpacking + the N1 fail-closed branch (`lookup_ok is False → block`) EXACTLY as-is. Drop `db_factory` from trust_gate's signature ONLY if no longer used elsewhere in the file (VERIFY — `grep -n db_factory src/deep_runtime/middleware/trust_gate.py`; trust_gate may still open sessions for approval persistence — if so KEEP `db_factory`, only swap the capability lookup).

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Regression + negative control.** `uv run pytest tests/deep_runtime/ -k "trust_gate or write_lock or capability_resolution" -v` — the existing 6B/6C gate/lock tests stay green (behavior identical, only lookup count changed). Negative control: revert the memoization (call `_resolve_capability` directly in both) → the spy test FAILS (2 lookups) → restore.

- [ ] **Step 6: Commit** `refactor(rebuild): fold double capability resolution — one shared lookup for trust_gate + write_lock (Step 7B1 P2, 6C #1)`.

---

## Phase 3 — Librarian → deep extraction middleware (WIRED-BUT-DORMANT, forced-proven)

**Files:** Create `src/deep_runtime/middleware/librarian_extract.py`. Modify `src/orchestrator/agent_invoker.py` (`_build_deep_agent_for`), `src/deep_runtime/middleware/__init__.py`. Test `tests/deep_runtime/test_librarian_extract.py`.

**Design:** A turn-scoped `@after_model` middleware (template = `budget.py:38-101`) that, when ACTIVE, reads first-human + last-AI text from `state["messages"]` and runs the SAME extraction primitives the chat `InteractionLearner` uses — `MemoryService.extract_and_store(...)` (`interaction_learner.py:122`) + `WorldModel.extract_from_text(...)` (`interaction_learner.py:152`) — best-effort (try/except, never breaks the turn). **DORMANCY (6B gate pattern):** a closure flag `active: bool = False` — on the LIVE direct-chat deep path it is `False` (returns `None` immediately, so it NEVER double-fires with the runtime-agnostic `InteractionLearner` at `chat_processor.py:621`); the forced test constructs it with `active=True`. **LIVE ACTIVATION is a documented gate** (§Activation gates): flipping `active=True` requires `chat_processor` to SKIP the `InteractionLearner` background spawn on `runtime=="deep"` — a runtime-agnostic-file change deferred to the Step-10 cutover.

> **Why wired-but-dormant, not just unwired:** unlike `budget`/`unavailable_server` (written, never wired, never exercised — the dead-wiring the rebuild fights), this middleware is WIRED into the live deep seam and PROVEN by a forced `active=True` test with a real activation path. That is the 6B/6C dormant-but-proven standard.

- [ ] **Step 1: Write the failing test.** In `tests/deep_runtime/test_librarian_extract.py`: build the middleware with `active=True`, injected `AsyncMock`s for `memory_service.extract_and_store` and `world_model.extract_from_text`; call its `@after_model` body with a fake `state={"messages":[Human("remember Bob works at Acme"), AI("Noted — Bob @ Acme.")]}` and a fake `runtime`; assert BOTH mocks awaited with the turn's text. Then build with `active=False` and assert NEITHER mock is awaited (dormancy has teeth).

```python
# assertion cores
await mw_active(state, runtime)
memory_service.extract_and_store.assert_awaited()
world_model.extract_from_text.assert_awaited()
# dormant path:
await mw_dormant(state, runtime)
memory_service.extract_and_store.assert_not_awaited()
world_model.extract_from_text.assert_not_awaited()
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`). `uv run pytest tests/deep_runtime/test_librarian_extract.py -v`.

- [ ] **Step 3: Implement `librarian_extract.py`.**

```python
"""Deep-runtime Librarian extraction middleware (Step 7B1).

Turn-scoped @after_model post-processing that extracts entities + memories from a completed
deep turn — the middleware form of the chat InteractionLearner. WIRED-BUT-DORMANT: on the
live direct-chat deep path ``active=False`` so it never double-fires with InteractionLearner
(chat_processor.py:621). Live activation (skip InteractionLearner on runtime=="deep") is a
Step-10 gate. Best-effort: extraction failure never breaks the turn.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, after_model

logger = logging.getLogger(__name__)


def make_librarian_extract_middleware(
    *,
    workspace_id: str,
    user_id: str,
    memory_service: Any,
    world_model: Any,
    active: bool = False,
) -> AgentMiddleware:
    @after_model(name="JarvisLibrarianExtract")
    async def _extract(state: dict[str, Any], runtime: Any) -> None:
        if not active:
            return None
        try:
            messages = state.get("messages") or []
            user_text = next(
                (str(m.content) for m in messages if getattr(m, "type", None) == "human"), ""
            )
            agent_text = next(
                (str(m.content) for m in reversed(messages) if getattr(m, "type", None) == "ai"),
                "",
            )
            if not user_text and not agent_text:
                return None
            # SAME primitives as InteractionLearner (interaction_learner.py:122,152).
            await memory_service.extract_and_store(
                user_id=user_id, workspace_id=workspace_id, text=f"{user_text}\n{agent_text}"
            )  # VERIFY exact kwargs against interaction_learner.py:122
            await world_model.extract_from_text(
                text=f"{user_text}\n{agent_text}", workspace_id=workspace_id, user_id=user_id
            )  # VERIFY exact kwargs against interaction_learner.py:152
        except Exception:
            logger.debug("[deep_runtime] librarian extraction failed", exc_info=True)
        return None

    return _extract
```
**VERIFY-DON'T-TRUST:** the exact signatures of `MemoryService.extract_and_store` and `WorldModel.extract_from_text` — READ `interaction_learner.py:122,152` and match kwargs precisely (the skeleton's kwargs are illustrative). Resolve `memory_service`/`world_model` the same way `InteractionLearner` deps are resolved (`chat_processor._ensure_learner_deps`, `jarvis.py:172`) — inject from `self._services` in `_build_deep_agent_for`.

- [ ] **Step 4: Wire dormant + export.** In `_build_deep_agent_for`, construct `librarian_extract = make_librarian_extract_middleware(workspace_id=workspace_id, user_id=user_id, memory_service=<from self._services>, world_model=<from self._services>, active=False)` and append to `extra_middleware` (order: `(governor_audit, trust_gate, write_lock, dispatcher, librarian_extract)` — `@after_model` runs post-turn, position among `@wrap_tool_call` entries is irrelevant, but keep it LAST for readability). Add `make_librarian_extract_middleware` to `middleware/__init__.py` `__all__`.

- [ ] **Step 5: Run — expect PASS.**

- [ ] **Step 6: Forced-integration guard.** Add a test building a real deep agent via `_build_deep_agent_for` with the middleware forced `active=True` (monkeypatch the closure or add a test-only construction path) and injected extraction spies; run one fake turn via `stream_deep_agent_events`; assert the extraction spies fired. **NEGATIVE CONTROL:** with `active=False`, the spies do NOT fire (dormancy proven) → confirms no live double-extraction.

- [ ] **Step 7: Commit** `feat(rebuild): Librarian deep extraction middleware, wired-but-dormant (Step 7B1 P3)`.

---

## Phase 4 — Presenter → deep-only inline-format augmentation (off-by-default, forced-proven)

**Files:** Modify `src/orchestrator/prompts.py` (extract a NEW `PRESENTER_VOICE` fragment — additive), `src/orchestrator/agent_invoker.py` (deep-branch augmentation `:283-303`). Test `tests/deep_runtime/test_presenter_inline.py`.

**Design (Fork-1 = INLINE-PROMPT):** The frozen stream contract means a deep lead formats inline by carrying the Presenter VOICE in its system prompt (reply = its `AIMessageChunk` text; surface blocks parse from that same text — proven in Phase 0). Two changes: (1) extract the voice + surface-generation rules from `PRESENTER_PROMPT` into a NEW `PRESENTER_VOICE` constant in `prompts.py` — **additive; `PRESENTER_PROMPT` itself is UNCHANGED so the legacy Presenter is byte-identical**; (2) in the deep branch ONLY, append `PRESENTER_VOICE` as an extra system block, gated by a closure/param `inline_format: bool = False` (off by default → deep behavior unchanged; forced on in the test). **Immutability:** do NOT mutate the shared `system_blocks` (built at `:278`, used by legacy at `:334`) — build an augmented COPY in the deep branch.

> **Scope honesty:** 7B1 EXTRACTS `PRESENTER_VOICE` + PROVES a deep lead can format inline + emit surfaces (the de-risking Phase 0 promotes to a real test). It does NOT remove the terminal `chat_processor.py:583` presenter step (runtime-agnostic) — that live activation is a §Activation gate.

- [ ] **Step 1: Extract `PRESENTER_VOICE` (structure-only, byte-neutral).** In `prompts.py`, pull the voice + `<surface_generation>` rules (`prompts.py:522-...`) into a module constant `PRESENTER_VOICE`, and have `PRESENTER_PROMPT` reference it so its rendered text is BYTE-IDENTICAL (e.g. `PRESENTER_PROMPT = f"...{PRESENTER_VOICE}..."`). Verify byte-identity: `uv run python -c "from src.orchestrator.prompts import PRESENTER_PROMPT; print(hash(PRESENTER_PROMPT))"` before/after must match a saved baseline, OR assert in a test that `PRESENTER_PROMPT` contains `PRESENTER_VOICE` and the full prompt string is unchanged vs a captured golden. Commit this as a SEPARATE structure-only commit `refactor(rebuild): extract PRESENTER_VOICE fragment (byte-neutral) (Step 7B1 P4)`.

- [ ] **Step 2: Write the failing test.** In `tests/deep_runtime/test_presenter_inline.py`: assert that `call_agent_stream` on `runtime=="deep"` with `inline_format` forced on builds a deep agent whose `system_prompt` (the `SystemMessage`) CONTAINS `PRESENTER_VOICE`, while `runtime=="legacy"` (and deep with `inline_format=False`) does NOT. Simplest: unit-test a small helper `_augment_system_blocks_for_inline(system_blocks, inline_format) -> list[dict]` that appends the voice block iff `inline_format`; assert deep+on adds it, off/legacy leaves `system_blocks` unchanged (same object/content).

```python
# assertion cores
augmented = _augment_system_blocks_for_inline(blocks, inline_format=True)
assert any(PRESENTER_VOICE in b["text"] for b in augmented)
assert _augment_system_blocks_for_inline(blocks, inline_format=False) == blocks  # byte-neutral off
```

- [ ] **Step 3: Run — expect FAIL** (`_augment_system_blocks_for_inline` undefined).

- [ ] **Step 4: Implement.** Add the pure helper + wire it into the deep branch only:

```python
# agent_invoker.py — module-level pure helper (immutable: returns a NEW list)
def _augment_system_blocks_for_inline(system_blocks: list[dict], inline_format: bool) -> list[dict]:
    if not inline_format:
        return system_blocks
    from src.orchestrator.prompts import PRESENTER_VOICE
    return [*system_blocks, {"type": "text", "text": PRESENTER_VOICE}]

# in call_agent_stream deep branch (:298), replace build_system_message(system_blocks) with:
                system_prompt=build_system_message(
                    _augment_system_blocks_for_inline(system_blocks, self._settings.deep_inline_format)
                ),
```
Add a settings flag `deep_inline_format: bool = False` (`src/config/settings.py`, `JARVIS_` prefix) — default False so deep behavior is unchanged. **Legacy path (`:334`) uses the ORIGINAL `system_blocks` — untouched, byte-identical.**

- [ ] **Step 5: Run — expect PASS.**

- [ ] **Step 6: Forced-integration guard (promote the Phase-0 spike to a test).** Port `inline_format_probe.py` into `tests/deep_runtime/test_presenter_inline.py` as a real test: with `deep_inline_format=True` + a fake streaming model emitting reply + ` ```json:surface `, assert the streamed `agent_done.text` yields the reply via `strip_surface_blocks` and a parseable surface via `extract_surface_spec`. **NEGATIVE CONTROL:** with `deep_inline_format=False`, the augmentation is absent (the voice block is not in the system prompt) — proves the flag gates it.

- [ ] **Step 7: Commit** `feat(rebuild): deep-only inline-format prompt augmentation, off-by-default (Step 7B1 P4)`.

---

## Phase 5 — Holistic review + full gate + docs

- [ ] **Step 1: Final holistic OPUS review** over the whole 7B1 diff: re-run the full gate (`uv run pytest tests/ --ignore=tests/e2e`); `uv run alembic heads` (single `1a2770a28c39`, UNCHANGED — no migration) + `uv run alembic check` (drift-free) + `uv run ruff check src tests`; independently reproduce EACH negative control (P1 governor_audit removed → disabled tool executes; P2 memoization reverted → 2 lookups; P3 `active=False` → no extraction; P4 flag off → no voice block). Confirm legacy byte-neutrality: `grep` that `build_system_prompt`, `chat_processor.py:583`, `InteractionLearner` at `:621`, and the perception Librarian (`perception_runner.py:277`) are UNCHANGED.
- [ ] **Step 2: Blast-radius review discipline.** P1 (new deep middleware in the chain) + P4 (prompt extraction + deep branch) are the blast-radius phases → 2-stage PARALLEL spec+quality review on the frozen commit. P2/P3 = combined single review. (Mirror 6A.5/6B/6C.)
- [ ] **Step 3: Confirm counts.** Expect **3232 + Δ passed / 18 skipped / 0 failed** (Δ = the new deep_runtime tests; NO tests deleted — no agent removed). Record the exact count.
- [ ] **Step 4: `middleware/__init__.py __all__` hygiene.** Add the two NEW factories (`make_governor_audit_middleware`, `make_librarian_extract_middleware`). Leave `trust_gate`/`write_lock` omitted (imported directly by `agent_invoker`; consistent with the existing convention — do NOT "fix" that here, it is 7C's call).
- [ ] **Step 5: Docs.** NO CLAUDE.md edit (nothing DURABLE changed — no agent removed, no contract/invariant flipped; the collapse machinery is dormant behind `JARVIS_RUNTIME=deep`, and per doc policy dormant deep-runtime internals are NOT durable arch facts until the rebuild MERGES). The design record lives in THIS plan + memory. If a reviewer insists on a pointer, the existing "Deep Agents runtime" note in CLAUDE.md's two-execution-paths section already covers the tools-are-schemas/central-dispatch/capability-scope invariant — do not append step notes.

---

## 7. Downstream sub-plans (SCOPED here, written later)

### 7B2 — Delegates + per-child model + Governor delegate-critique (dormant, proven)
- **Delegate/read-only workers** via `create_deep_agent(subagents=[...])` (deepagents 0.6.11, `graph.py:242` — VERIFIED; `SubAgent` supports per-child `model`/`tools`/`middleware`, `subagents.py:92/98/104`). `build_deep_agent` does NOT pass `subagents=` today (`agent_builder.py:120-127`). Perceiver + custom research stay depth-1 read-only delegates.
- **SUBAGENT SECURITY (Fork-2 = per-child middleware + disable `task`):** each SubAgent compiles into its OWN isolated graph — the parent's `capability_scope`/`trust_gate`/`dispatcher` do NOT reach it (`graph.py:619-641`). So give each Jarvis delegate its OWN `capability_scope` + `jarvis_tool_dispatcher` in `SubAgent["middleware"]` (read-only children → NO trust_gate/write_lock). DISABLE the ambient general-purpose subagent (`HarnessProfile(general_purpose=GeneralPurposeSubagentProfile(enabled=False))`, `graph.py:367-370`) so the ungated `task`/GP child does not exist. (Mitigating fact: Jarvis tools are tripwire SHELLS — a child with parent shells but no dispatcher FAILS LOUD, not silent; the silent hole is only real tools + no policy middleware.)
- **Per-child model specialization:** `subagent["model"] = build_chat_model(child_agent)` (`model_factory.py:28-61`; `ChatAnthropic` is a `BaseChatModel`, valid for `SubAgent["model"]`). Preserve Perceiver sonnet/6144 etc. UNUSED today (one model per routed-agent graph).
- **Governor delegate-critique** (NET-NEW; Fork-4 = decoupled from read-back): a critique pass over delegate-returned summaries — fail-closed for writes / fail-open-annotated ("unreviewed") for read summaries (spec degradation mode). Lands here because it needs delegates to exist. INDEPENDENT of 7C's `ReadBackVerifier` (`verify_step` is self-contained; grep-empty coupling).
- **Spike-first:** `create_deep_agent(subagents=[...])` with per-child gated middleware streaming through `stream_deep_agent_events` is UNPROVEN offline — 6A/6B/6C precedent says a Task-0 spike may DISPROVE the assumption.

### 7C — Verification parity (T2 inline) + middleware wiring (dormant, proven)
- **Inline read-back:** a post-execute deep middleware (INNER of `write_lock`) running `ReadBackVerifier.verify_step` (`readback.py:39-84`, path-agnostic) for IRREVERSIBLE writes → CONFIRMED/CONTRADICTED/UNVERIFIED + escalate-first. DEFER the durable deferred-recheck loop (deep has no `TaskStep`/`completed_unverified` surface; autonomous deferred tick is half-wired).
- **Wire the two UNWIRED middlewares** so activation isn't a regression: `budget` (`make_budget_middleware`, `budget.py:38` — no per-call `TokenUsage` on deep) + `unavailable_server` (`make_unavailable_server_middleware`, `unavailable_server.py:114` — no MCP re-auth breaker). Both WRITTEN, in `__all__`, NOT in any `extra_middleware`.
- **Deep trust-increment-on-CONFIRMED** is net-new; use `begin_nested()` from the start (mirror 7A P0 / 6C P5).

### Activation gates (Step 10 / deep-default — NOT 7B1/7B2/7C)
- **Presenter live activation:** `chat_processor` skips the terminal `call_agent_stream("presenter")` (`:583`) on `runtime=="deep"` and lets the inline-format lead's own text be the reply (flip `deep_inline_format`). Needs a runtime-agnostic-file branch (byte-neutral legacy else-branch).
- **Librarian live activation:** `chat_processor` skips the `InteractionLearner` background spawn (`:621`) on `runtime=="deep"` (flip `active=True`) so extraction runs via the middleware, not the service. Same runtime-branch requirement; reconcile InteractionLearner's intent-gating + 60s cooldown into the middleware.
- **Agent-count reduction (6→N):** removing the Presenter/Librarian AGENT identities + their seed rows needs a data migration (7A Governor precedent) — only AFTER live activation proves the collapse. NOT 7B1.
- **6C follow-ups #2/#3** (write-lock fail-open under Redis outage; contended-blocked shape) — decide before `deep` is the live default.

---

## Self-Review (run after drafting, before execution)

1. **Spec coverage (T1):** Presenter→inline ✓ (P4, dormant proof; live activation gated); Librarian→extraction middleware ✓ (P3, wired-dormant); Governor→audit middleware ✓ (P1, deep-only, live on deep); Governor delegate-critique → 7B2 (needs delegates); Persona→job ✓ (7A shipped); Operator deleted ✓ (6C); delegate/per-child/T2 → 7B2/7C. #1 fold ✓ (P2). No orphaned 7B1 requirement.
2. **Placeholder scan:** every code step has real code or an exact grep/command; test skeletons name real harness patterns (`central_dispatcher_proof.py`, `budget.py`, `jarvis_tool_dispatcher.py`, `hooks.py:31-96`). The two explicit VERIFY-DON'T-TRUST latitudes (Task 3.3 `extract_and_store`/`extract_from_text` kwargs; Task 2.3 whether trust_gate still needs `db_factory`) are bounded by exact anchors to read — verify-don't-trust work the implementer/reviewer owns, not placeholders.
3. **Type/anchor consistency:** `_resolve_capability` returns `(lookup_ok, cap)` (`trust_gate.py:67-97`) — the shared resolver + trust_gate consumer agree; `@wrap_tool_call(request, handler)` shape matches `jarvis_tool_dispatcher.py:53-76`; `@after_model(state, runtime)->None` matches `budget.py:68-99`; `extra_middleware` grows `(governor_audit, trust_gate, write_lock, dispatcher, librarian_extract)` consistently across P1/P3; `PRESENTER_VOICE` referenced identically in P4 helper + test.
4. **Load-bearing guards have teeth (negative controls):** P1 (remove governor_audit → disabled tool executes); P2 (revert memoization → 2 lookups); P3 (`active=False` → no extraction, proves no double-fire); P4 (flag off → no voice block). Each is a revert-the-fix-and-watch-it-fail control, reproduced independently by the holistic opus (Phase 5 Step 1).
5. **Dormancy is PROVEN, not dead-wired:** every dormant piece (P3 middleware, P4 augmentation) is WIRED into the live deep seam + exercised by a forced test + has a documented activation path (§Activation gates) — the 6B/6C standard, distinct from the `budget`/`unavailable_server` dead-wiring 7C fixes.
6. **Byte-neutral legacy:** no change to `build_system_prompt`, `chat_processor` (`:583`/`:621`), `perception_runner.py:277`, or `PRESENTER_PROMPT`'s rendered text; all divergence is inside the `runtime=="deep"` branch or the deep middleware chain. Phase 5 Step 1 greps to confirm.

