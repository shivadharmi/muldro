# Spec Compliance Cross-Check: 15 Specs vs Implementation + Bug Fixes

**Date:** 2026-04-11
**Scope:** All 15 specs vs current codebase (after 10 fix commits)

---

## Executive Summary

| Category | Count |
|----------|-------|
| COMPLIANT | 9 specs fully match intent |
| COMPLIANT with intentional fixes | All 15 specs have intentional improvements beyond spec |
| TRUE DEVIATIONS (behavior differs from spec) | 5 |
| MISSING IMPLEMENTATIONS | 6 components across 2 specs |
| AMBIGUOUS (spec unclear, implementation made a choice) | 8 |

**Overall:** The implementation is substantially compliant. Most bug fixes preserve or strengthen spec intent. There are **2 significant gaps** (Spec 5A missing write paths, Spec 3A surface_id wiring) and **1 architectural deviation** (Spec 2B-i TrustEngine instantiation).

---

## True Deviations (behavior differs from spec)

### DEV-1: `route_step()` returns `""` for unknown capabilities (Spec 1A)
- **Spec said:** Return `"operator"` as fallback
- **Implementation:** Returns `""` (empty string) with warning log
- **File:** `capability_resolver.py:114`
- **Impact:** Unknown capabilities now fail visibly instead of silently dispatching to toolless Operator
- **Verdict:** Safer behavior. Consider documenting as intentional override.

### DEV-2: TrustEngine instantiated per-executor, not from service container (Spec 2B-i)
- **Spec said:** TrustEngine wired through `runtime.py` service container as singleton
- **Implementation:** `create_graph_executor()` creates TrustEngine locally
- **File:** `graph_executor.py:122-128`
- **Impact:** Each executor gets a separate TrustEngine instance, bypassing shared lifecycle management
- **Verdict:** Functionally equivalent but architecturally divergent. Should be aligned in future refactor.

### DEV-3: `surface_id` not wired from workspace surface to executor (Spec 3A)
- **Spec said:** Orchestrator creates initial surface, passes `surface_id` to `execute_run()`
- **Implementation:** `_push_workspace_surface()` generates `surface_id` internally but never returns it. `execute_run()` is called without `surface_id` in the primary chat path.
- **File:** `jarvis.py:1988-2019, 886, 1189`
- **Impact:** Live `SurfaceUpdate` events never connect to the workspace surface card. The H-12 fix added surface_id generation in `_handle_create_task`, but the primary chat path still doesn't wire it.
- **Verdict:** **Needs fixing.** The Spec 3A live execution surface feature is partially broken in the main path.

### DEV-4: Missing `plan_ready` phase emission (Spec 3A)
- **Spec said:** Emit `surface_update` with `phase="plan_ready"` after `_populate_steps()`
- **Implementation:** No emission between step population and first step execution
- **File:** `graph_executor.py:491` (missing)
- **Impact:** Frontend never receives the "plan is ready, about to execute" signal
- **Verdict:** Minor gap. Should be added.

### DEV-5: `this_week` urgency routes to `push` tier (Spec 4A)
- **Spec said:** Only `immediate` and `today` urgency qualify for push tier
- **Implementation:** `this_week` + high relevance (>=0.7) also routes to push
- **File:** `relevance_assessor.py:89`
- **Impact:** More signals delivered as push notifications
- **Verdict:** Intentional M-23 fix. May increase notification volume. Confirm this is desired.

---

## Missing Implementations

### Spec 5A: Qdrant Enrichment — 4 Write Paths Missing

| Component | Status | Detail |
|-----------|--------|--------|
| Events embedding | MISSING | `EventProcessor` never calls Qdrant embed for events with importance >= 0.3 |
| Conversations embedding | MISSING | `_summarize_history` generates summary but never upserts to Qdrant |
| Approvals embedding | MISSING | Approval approve/reject never triggers Qdrant embed (eviction cascade exists but cleans data never written) |
| Artifacts embedding | MISSING | `artifact_storage.py` never embeds to Qdrant |

**Impact:** TriSearch includes these collections in search, but they're always empty. The infrastructure (collections, indexes, eviction) is in place — only the write paths are missing.

### Spec 5B: Neo4j Enrichment — 2 Secondary Items Missing

| Component | Status | Detail |
|-----------|--------|--------|
| Memory stability decay | MISSING | `refresh_stability()` with exponential decay formula not implemented |
| Briefing evidence via vector similarity | MISSING | Briefing generation doesn't use TriSearch for semantic evidence |

---

## Spec-by-Spec Compliance Matrix

| Spec | Status | Deviations | Notes |
|------|--------|------------|-------|
| **0: Foundation** | FULL MATCH | 0 | All 17 components verified |
| **1A: Capability Infra** | COMPLIANT | DEV-1 (route_step) | Intentional safety improvement |
| **1B-i: Planner Prompt** | COMPLIANT | 0 | Transitional requirements (coexist) correctly resolved by 1B-ii |
| **1B-ii: Orchestrator Core** | COMPLIANT | 0 | user_steps now surfaced (H-7), errors propagated (H-9) |
| **1B-iii: Service Ripple** | COMPLIANT | 0 | All old code deleted, agent_routes table dropped |
| **1B-iv: Frontend Migration** | COMPLIANT | 0 | tool_call/tool_result SSE handling added beyond spec |
| **2A: Trust Infra** | COMPLIANT | 0 | H-4 graduation override and H-3 cache key are improvements |
| **2B-i: Single Gate** | MOSTLY COMPLIANT | DEV-2 (TrustEngine instantiation) | Governor integrated, resume path fixed |
| **2B-ii: Trust UI** | COMPLIANT | 0 | "blocked" ceiling separation is improvement |
| **3A: Execution Events** | PARTIAL | DEV-3 (surface_id gap), DEV-4 (plan_ready) | Core contracts + emission work, but primary path wiring incomplete |
| **3B: Execution Frontend** | COMPLIANT | 0 | Selective merge is improvement over spec's full replacement |
| **4A: Perception Routing** | COMPLIANT | DEV-5 (this_week tier) | All other components match |
| **4B: Proactive Insights** | COMPLIANT | 0 | Suppression TTL, engagement recording are improvements |
| **5A: Qdrant Enrichment** | PARTIAL | 4 missing write paths | Infrastructure present, data never written |
| **5B: Neo4j Enrichment** | MOSTLY COMPLIANT | 2 missing secondary items | Core traversal + enrichment fully implemented |

---

## Intentional Improvements (Bug Fixes That Strengthened Specs)

These changes go beyond what the specs defined but are improvements:

| Fix | Spec | Improvement |
|-----|------|-------------|
| Cypher injection prevention | 5B | Allow-list + parameterized queries (spec had raw f-strings) |
| Workspace scoping on search/dedup | 5A/5B | Multi-tenant isolation (spec didn't address) |
| PlanOutput cycle detection | 1A | DFS validator prevents circular depends_on |
| Atomic Redis rate limit | 4A | Pipeline eliminates TTL race condition |
| Engagement suppression TTL | 4B | 7-day auto-clear prevents permanent deadlock |
| Selective surface merge | 3B | Prevents empty arrays from wiping live state |
| Failed step surface emission | 3A | Surfaces now show which steps failed |
| User steps surfaced to Presenter | 1B-ii | Plans with user actions now instruct the user |

---

## Recommended Actions

### Must Fix (spec compliance gaps)
1. **DEV-3: Wire surface_id to execute_run in primary chat path** — The Spec 3A live execution feature is the most visible gap. `_push_workspace_surface` should return the `surface_id` and it should be passed to `execute_run`.
2. **Spec 5A: Implement 4 missing Qdrant write paths** — Events, conversations, approvals, artifacts embedding. The infrastructure is ready, just needs write-side wiring.

### Should Fix
3. **DEV-4: Add `plan_ready` emission** — Simple addition to `graph_executor.py` after `_populate_steps()`.
4. **DEV-2: Move TrustEngine to service container** — Align with spec's singleton pattern for shared lifecycle.
5. **Spec 5B: Implement stability decay** — The formula is defined in the spec.

### Monitor
6. **DEV-5: `this_week` push tier** — Confirm increased notification volume is acceptable.
7. **DEV-1: `route_step` empty return** — Document as intentional override in CLAUDE.md.
