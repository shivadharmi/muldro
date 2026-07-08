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

---
