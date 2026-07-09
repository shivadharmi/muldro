# Step 9 — A2UI Layer Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **STATUS: SKELETON (committed early per the Step-2 orphan lesson). Forks NOT yet resolved with
> user; extraction grounding NOT yet folded. Do NOT execute from this skeleton — the full plan
> replaces it in a follow-up commit.**

**Goal:** Complete the A2UI layer split (spec §4.9 / roadmap Step 9) — make the Step-0 `version`
field drive a real render branch (or prune via the 24h TTL), route narrative content to markdown,
unify the approval surface into one representation, and rename the homegrown "A2UI" layer — WITHOUT
deleting the `SurfaceUpdate` phase machine (deferred to after Step 10) and WITHOUT a new migration.

**Architecture:** The A2UI system has TWO frontend consumption paths that this plan must NOT
conflate: (1) the **autonomous path** emits the `SurfaceUpdate` phase machine (`plan_ready →
executing → approval_needed → completed/failed`) from `graph_executor.py`, consumed by the
workspace `execution-surface.tsx` — this path is **entirely legacy until Step 10**; (2) the **deep
chat path** streams inline ` ```json:surface ` blocks reconstructed by `stream_deep_agent_events`
and consumed by `surface_mapping` (7B1 P0 proved "no adapter change needed"), with approvals via the
6B `approval_needed` SSE frame. Step 9's LIVE, runtime-agnostic work (version render-branch,
narrative→markdown, rename, one-approval-event) is separable from the deep-specific
native-stream→surface_update adapter, which is a **Step-10 gate** unless grounding disproves it.

**Tech Stack:** Python 3.13 (backend, `src/ui/`, `src/services/surface_*`, `src/contracts/`),
Next.js/React/TypeScript (frontend, `frontend/src/components/a2ui/`), Zustand (surface store),
LangGraph/deepagents (deep runtime, dormant behind `JARVIS_RUNTIME=deep`).

---

## Baseline (verified at plan-open, HEAD `1f343cc`)

- Branch `rebuild/first-principles` (off `main`, NOT pushed). Backend baseline: **3325 passed / 18
  skipped** (`uv run pytest tests/ --ignore=tests/e2e`). Single alembic head `1a2770a28c39`,
  `alembic check` drift-free, `ruff check src tests` clean.
- Frontend baseline (from `frontend/`): `npm run lint` = 0 errors (2 pre-existing warnings),
  `npm run build` = green.
- **INFRA GOTCHA:** `:6379` may be served by `hyperlocal-redis` OR `jarvis-redis-1`; either fine if
  published. A gate with ~108 skipped = redis/postgres DOWN = NOT green.

## Verify-don't-trust catches (already found at plan-open; more expected from extraction)

1. **CLAUDE.md is STALE:** `SurfaceUpdate` lives in `src/contracts/__init__.py:450`, NOT
   `src/ui/contracts.py`. The A2UI `version` field lives on the A2UI contracts (`src/ui/contracts.py`).
2. **The frontend does NOT consume `version` on render today** (grep empty across
   `frontend/src/components/a2ui/` + `surface-store.ts`) → Fork 2 is a genuine open design choice.
3. **`SurfaceUpdate`/`surface_update` is referenced in ~9 backend files** (communication_server,
   routes_ws, routes_history, dag_runner, graph_executor, trust_gate, execution_surface_emitter,
   surface_builder, surface_detail_builders/_shared) → the phase machine is a genuinely shared seam;
   any change is LIVE-blast-radius unless a flag/param engineers dormancy (Step-8 key fact).

---

## Forks to resolve with user (ONE BY ONE, prose — NOT batched)

### Fork 1 (LOAD-BEARING) — the dual-runtime surface pipeline: LIVE vs DORMANT, per-part
_Preliminary recommendation (pending grounding): split by part. The `version` render-branch,
narrative→markdown, and rename land **LIVE + runtime-agnostic** (like Steps 2/4/8-cleanup). The
native-stream→surface_update **translation adapter** is a **Step-10 gate** (the autonomous path is
legacy until Step 10; the deep chat path needs no adapter per 7B1 P0) — Step 9 should NOT build a
dormant adapter with nothing to translate. TO CONFIRM at file:line via extraction (c)._

### Fork 2 — the `version` field: real legacy/new render-branch vs 24h-TTL prune
_Preliminary recommendation (pending grounding): TTL-prune-leaning. "New" renders nothing different
today, so a legacy/new branch would be a branch with one live arm. TO DECIDE after extraction (b)/(d)
establish what "new" would even render._

### Fork 3 — rename / standards: keep "A2UI" vs rename toward AG-UI/MCP-Apps
_Preliminary recommendation (pending grounding): rename to a neutral local name (spec suggests
`SurfaceKit`) as a MECHANICAL rename ONLY; DEFER the AG-UI transport / MCP-Apps artifact adoption
(that is week-3-standards scope creep — transport/artifact changes, not a rename). TO CONFIRM._

### Fork 4 — packaging: ONE plan vs split
_Preliminary recommendation (pending grounding): ONE plan, N phases (matches Step-8 Fork-4). TO
CONFIRM after the scope of each part is grounded._

---

## Extraction findings (TBD — 4 parallel passes, cross-verified)

- (a) SurfaceUpdate contract + phase machine + ALL emission points + consumers — TBD
- (b) surface build/push pipeline + `ui_surfaces` storage + `version` reality — TBD
- (c) deep-runtime stream_adapter 8-frame contract + adapter-seam feasibility + shared-vs-deep — TBD
- (d) FRONTEND render path + `version` consumption + narrative rendering — TBD

## Phases (TBD — populated after fork resolution)

- Phase 0 (if needed): SPIKE — offline probe of any unproven assumption (adapter feasibility). TBD
- Phase 1..N: TBD

## Review strategy (TBD — shared-seam = 2-stage parallel spec+quality on frozen commit;
load-bearing = independent opus; final holistic opus reproduces every negative control RED→GREEN)

## Deferred / ledger updates (TBD)

- Phase-machine DELETION → after Step 10 (spec-explicit).
- Any native-stream→surface_update adapter → likely Step-10 gate (Category B). TBD.
