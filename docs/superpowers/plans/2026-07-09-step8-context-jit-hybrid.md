# Step 8 — Context JIT-Hybrid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **STATUS: SKELETON (committed early per the Step-2 orphan lesson). Forks UNRESOLVED pending user + 4 parallel code-extraction passes. Do NOT execute until this banner is removed and the fork table is resolved.**

**Goal:** Replace the eager "assemble ALL context every turn" pack with a JIT-hybrid model — a slim eager pack (identity, active goals, recent turns, explicit preferences) plus on-demand retrieval of the rest via the Step-4 read tools, with message/tool-result overflow handled by LangChain's `SummarizationMiddleware` in the deep chain.

**Architecture (provisional — see forks):** The slim-pack + JIT machinery is built runtime-agnostic (both legacy `agent_loop` and deep consume the same `context_block` from `agent_invoker.py:514`), but the eager→slim *switch* is gated behind a default-off flag so the default (legacy) runtime stays byte-identical until the Step-10 activation gate. `SummarizationMiddleware` is deep-chain-only. No migration (Step 5 already owns `TaskRunDetail.context_pack` + TTL).

**Tech Stack:** Python 3.12, LangChain 1.3.10 (`langchain.agents.middleware.SummarizationMiddleware`), Deep Agents runtime, FastMCP internal tools, Postgres/Redis/Qdrant/Neo4j.

---

## Verified start-state facts (verify-don't-trust, re-checked at file:line 2026-07-09)

- Branch `rebuild/first-principles`, HEAD `9e7b57b`. Single migration head `1a2770a28c39`, no drift, ruff clean.
- **STALE ANCHORS CAUGHT:** `src/tools/intelligence_server.py` does NOT exist (it's a package `src/tools/intelligence_server/`); `src/contracts/context_pack.py` does NOT exist (`ContextPack` is defined in `src/services/context_builder.py:95`); `src/services/surface_detail_builders/` is a package, not `.py`.
- **Eager fetch set** (`ContextBuilder.build`, context_builder.py:138-329): TriSearch (limit 20) → entities (resolve_entities, top 10) → **≤5 Neo4j traversals depth-2, ≤8 each** → memory retrieve (10) → explicit prefs (get_user_preferences, 20, capped 25) → goals (5) → artifacts (5) → related_runs (DB, 5) → tool_options → derived constraints/risks. ~8-10 round-trips/turn.
- **Shared seam:** `assemble_context` (context_assembler.py:210) is called at `agent_invoker.py:514` BEFORE the `if runtime == "deep"` branch at :521. Feeds BOTH runtimes.
- **CONTEXT_ENRICHED_AGENTS** = {planner, presenter, perceiver, librarian, executor} (context_assembler.py:21).
- **Conversation history** is a SEPARATE path (`load_conversation_history`, 20 msgs / 20000 chars, Haiku summarization of overflow) — already effectively "slim eager".
- **JIT tools already live (Step 4):** `get_entity`, `query_facts`, `traverse`, `get_provenance`, `get_goals` (world_model_tools.py; capabilities `internal.*`). GAP: memories / artifacts / related_runs may lack JIT tools (extraction to confirm).
- **Storage exists (Step 5):** `TaskRunDetail.context_pack` JSONB + `context_pack_expires_at` TTL (task_graph.py:117-137). No migration expected.
- **SummarizationMiddleware INSTALLED:** `from langchain.agents.middleware import SummarizationMiddleware` imports on langchain 1.3.10.

---

## FORKS (UNRESOLVED — resolve with user before finalizing)

| # | Fork | Pre-recommendation (grounded, pending user) |
|---|------|---------------------------------------------|
| 1 | LIVE (2/4) vs DORMANT (6/7) | **Build machinery live/runtime-agnostic, gate eager→slim SWITCH behind default-off flag** (dormant-but-proven; flag flip = Step-10). SummarizationMiddleware inherently deep-only. |
| 2 | SummarizationMiddleware: adopt vs hand-roll | **ADOPT** (installed). Phase-0 spike must prove it survives frozen 8-frame SSE contract + 2-block cache layout. Do NOT resurrect deleted scaffold. |
| 3 | JIT seam: reuse Step-4 tools vs new surface | **REUSE Step-4 tools**; fill gaps (memory/artifact/run retrieval) only if extraction proves them missing. Always-eager: identity, goals, recent turns, explicit prefs. |
| 4 | Packaging: one plan vs split | **ONE phased plan** (hot files context_builder/context_assembler/agent_invoker must be sequenced). |

---

## Extraction plan (4 parallel passes — findings fold in here)

- E1: `ContextBuilder.build` eager fetch set + per-category cost + which are cheap-always vs expensive-on-demand.
- E2: `assemble_context` / `ContextPack` / `CONTEXT_ENRICHED_AGENTS` + exactly where the pack renders into the prompt on BOTH paths.
- E3: deep-runtime context threading (`build_system_prompt` / `build_system_message` / `_build_deep_agent_for`) + `SummarizationMiddleware` wiring point + SSE/cache-layout risk.
- E4: Step-4 JIT tools as retrieval seam + gap analysis (memory/artifact/run) + what's already live on both paths.

---

## Out of scope (deferred — add to activation-gate ledger)

- Step-10 runtime cutover / flag flips.
- A2UI split (Step 9).
- Activation-gate ledger Category A/B items.
- Any new persisted context entity / migration (Step 5 owns the storage).

---

## Phases (outline — filled after forks resolved)

- Phase 0 — offline spike (SummarizationMiddleware ⊗ deep chain ⊗ SSE/cache) IF unproven.
- Phase 1 — slim eager pack (flag-gated, byte-neutral default).
- Phase 2 — JIT retrieval tools/wiring (reuse Step-4 + gap fills).
- Phase 3 — SummarizationMiddleware in deep chain (dormant).
- Phase 4 — forced-on offline e2e + negative controls with teeth; full-gate at every checkpoint.

<!-- TASKS TO BE FILLED AFTER FORK RESOLUTION -->
