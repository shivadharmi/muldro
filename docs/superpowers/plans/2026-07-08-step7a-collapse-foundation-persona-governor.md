# Step 7A — Cognitive-Collapse Foundation (Persona full-trace + kill dead Governor agent) + live pre-batch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Single-owner-per-file + SYNCHRONOUS implementer dispatch** (`run_in_background: false`) — a background SendMessage-resumed subagent once produced F811 duplicate defs (6B lesson). **VERIFY-DON'T-TRUST every current-state claim against code before building on it** — this plan's own anchors are `file:line` from `rebuild/first-principles` @ `228a443`; re-confirm before editing.

**Goal:** Land the two *runtime-independent* pieces of the Step-7 cognitive-agent collapse on the LIVE legacy path — upgrade Persona to learn over the full interaction signal (not a lossy one-liner), and delete the dead Governor LLM agent (7 agents → 6) — plus fix one live latent trust-write session-poisoning risk (6C follow-up #4). No deep-runtime work; no runtime flip.

**Architecture:** All changes are on the LIVE legacy path or standalone. Persona stays a Haiku scheduler job — only its *read scope + formatting* changes. The Governor LLM *agent* is deleted surgically (identity + prompt + seed + soul-core table + its exclusive `report_governor_verdict` structured-output machinery + a data migration to drop the DB row), while the Governor *service*, the audit *hook*, and the `evaluate_policy`/`approve_action`/`get_plan_details` tools are KEPT (orphaned-but-harmless; `validate_registry` tolerates capabilities without an agent-scope holder). Pre-batch #4 wraps one trust-write in a `begin_nested()` SAVEPOINT to match its two siblings.

**Tech Stack:** Python 3.12, async SQLAlchemy (asyncpg), pytest (custom `pytest_pyfunc_call` asyncio hook — NO pytest-asyncio), alembic, `uv` (NO pip). Full gate: `uv run pytest tests/ --ignore=tests/e2e` from `backend/`.

**Baseline at plan time:** `rebuild/first-principles` @ `228a443`; **3237 passed / 18 skipped**; single alembic head `574f6c145bca`; `alembic check` drift-free; ruff clean.

---

## 0. How this fits the rebuild (context — READ FIRST)

Step 7 (spec T1) collapses the 7-agent zoo into one lead + read-only workers, with cognition moving to **middleware / tools / jobs**, *preserving* per-agent model/budget specialization. Per the user forks resolved for this step:

- **Q1 = STAY DORMANT.** The full collapse builds on the deep runtime (`JARVIS_RUNTIME=deep`, default `legacy`) and stays dormant/proven; **no runtime flip in Step 7** (cutover ≤ Step 10). The 6B gate + the deep-path 6C follow-ups stay dormant.
- **Q2 = SPLIT** (like Step 6 → 6A/6A.5/6B/6C). **This plan is 7A** — the runtime-independent slice. **7B** (deep collapse: Presenter→tool, Librarian→middleware, Governor→audit+critique middleware, delegate workers, per-child models) and **7C** (T2 inline read-back parity + wiring the unwired `budget`/`unavailable_server` middlewares) are scoped in §7 below and get their own committed plans in later sessions.
- **Q3 = INLINE READ-BACK** (7C): reuse the path-agnostic `ReadBackVerifier`; add a post-execute deep middleware + Governor critique; defer the durable deferred-recheck loop. (Not 7A.)
- **Q4 = FOLD + PRE-BATCH:** #4 (`record_auto_execution_outcome` missing SAVEPOINT — LIVE autonomous) → **pre-batch in 7A (Phase 0)**. #1 (double capability resolution) → fold into **7B**. #2 (write-lock fail-open) + #3 (contended-blocked shape) → **leave until activation (Step 10)**, documented as activation gates.

**Why Persona + dead-Governor are 7A (not 7B):** they are the two collapse targets that are *already* jobs/dead scaffolding on the LIVE path — the deep single-lead runtime is irrelevant to them. Presenter and Librarian only have a coherent "tool/middleware" home on the deep lead (7B). Doing the runtime-independent pieces first is a low-risk foundation that shrinks the agent set before the big deep work.

---

## 1. Ground-truth current state (verify-don't-trust anchors)

All `file:line` from `backend/` @ `228a443`. Re-confirm before editing.

### Persona (already ~80% a job)
- Tick: `src/services/scheduler/persona_tick.py` — `PersonaTickMixin._tick_persona_batch` (whole file, 75 lines). Modulo gate `% 10` at `:19`; min-5 global `:44` + per-group `:54`; groups by `(workspace_id, user_id)` `:47-51`; **caps the query at `.limit(20)` `:38`**; builds a **lossy** summary `f"- {i.message_preview or '(no preview)'} → {i.intent or 'unknown'}"` `:56-59` (discards `response_preview`, `plan_summary`, timestamps); invokes `self._orchestrator._call_agent("persona", ...)` `:60-68` (legacy Haiku, already background); stamps `_last_persona_batch_at` `:70`.
- Model/budget: `haiku` (`agents.py:23`), thinking `2048` (`agents.py:196`) — **PRESERVE**.
- `InteractionLog` model (`src/models/interaction_log.py`) stores **only previews**: `message_preview` String(500) `:27`, `response_preview` String(500) `:32`, `plan_summary` String(500) `:28`, `intent` String(32) `:31`, `created_at` `:37`. **NO full message body column** — so "full trace" from InteractionLog is bounded by previews (see Phase 1 note on the deferred richer read).

### Governor — three distinct things sharing the name
- **DEAD LLM agent (REMOVE):**
  - `agents.py:20` `AGENT_MODEL_TIERS["governor"]="sonnet"`; `:100-105` `AGENT_CAPABILITY_SCOPES["governor"]` (`internal.evaluate_policy/report_verdict/approve_action/get_plan_details`); `:194` `AGENT_THINKING["governor"]=2048`; `:236` `temperature=0.1 if name=="governor" else 0.3`; `:238` `edge_case_only=(name=="governor")`; `:211` the `edge_case_only` field on `SubAgent`; `:227` create docstring "all 7".
  - `prompts.py:499-541` `GOVERNOR_PROMPT`; `:759` `AGENT_PROMPTS["governor"]`; `:21` soul-core `<agents>` table Governor row; `:31` rule 4 "…Governor handles edge cases only".
  - `agent_registry.py:26` display name; `:36` description; `:74` seed `temperature=0.1 if name=="governor"`; `:50` docstring "7 default agents".
  - `context_assembler.py:27` `"governor"` in `CONTEXT_ENRICHED_AGENTS`.
  - **Exclusive verdict machinery** (dead once the agent is gone — nothing else holds `internal.report_verdict`): `catalog.py:308-314` `report_governor_verdict` `InternalToolDef` (`server="_special"`, capability `internal.report_verdict`); `agent_loop.py:79-82` `GOVERNOR_VERDICT_TOOL` const; `agent_loop.py:496-510` forced `tool_choice` branch; `schemas.py` `ReportGovernorVerdictInput` (+ `TOOL_INPUT_MODELS` entry); `intelligence_server/*` its impl; `integrations/capabilities.py:151` `internal.report_verdict`; `verification/predicate.py:40` `internal.report_verdict` in `REVERSIBLE_INTERNAL_CAPABILITIES`; `models/tool_definitions.py:24` `SPECIAL` enum comment; `tool_registry.py:69-72` + `tool_executor.py:331-334` the `_special` dispatch branch (`_special` has exactly ONE tool — `report_governor_verdict`).
- **LIVE — KEEP (explicit non-goals):** the audit **hook** `governor_pre_tool_hook` (`hooks.py:31`, invoked for EVERY agent at `agent_loop.py:656`, always `allowed:True` except disabled tools) — this IS the spec's "Governor→audit middleware" on legacy; the Governor **service** `services/governor.py` (`Governor.evaluate_plan` → `trust_engine.evaluate_plan_risk`), wired `runtime.py:237`; the `evaluate_policy` tool (`planning.py:135-155`, capability `internal.evaluate_policy`); the HTTP handler `routes_approvals.approve_action` (`:117`, user-clicks-approve — a DIFFERENT `approve_action` from the dead agent tool; do NOT touch).
- **validate_registry** (`tools/validation.py:63-66`) only checks *agent-scope caps ∈ catalog* (reverse direction). There is **NO** "every catalog capability needs an agent holder" rule → dropping the Governor agent's scope leaves `internal.evaluate_policy/approve_action/get_plan_details` orphaned-but-harmless. **Confirmed safe.**

### #4 pre-batch (LIVE autonomous)
- `src/services/trust_gate.py:224-243` `record_auto_execution_outcome` — calls `record_approval_decision(self._db, ...)` directly, **NO `begin_nested()`**. Sibling `record_user_approval_outcome:245-264` DOES wrap it (`async with self._db.begin_nested()` `:259`); the deferred tick (`deferred_verification_tick.py:94`) also uses `begin_nested`. This is the lone trust-write site without SAVEPOINT protection. Caller: `dag_runner.py:437-440` (auto-exec CONFIRMED path).

---

## 2. Scope

**7A IS:** (Phase 0) SAVEPOINT-wrap `record_auto_execution_outcome`; (Phase 1) Persona learns over the full interaction *record signal* (all fields, richer window) instead of the lossy `preview→intent` one-liner; (Phase 2) delete the dead Governor LLM agent + its exclusive `report_governor_verdict` machinery + a data migration; (Phase 3) holistic review + full gate.

**7A IS NOT:** any `src/deep_runtime/` change; any runtime flip; Presenter/Librarian collapse (7B); T2 read-back (7C); wiring `budget`/`unavailable_server` middlewares (7C); removing the Governor *service*/`evaluate_policy`/`approve_action`/`get_plan_details` tools (a separate "is the deterministic Governor service vestigial?" sweep — flagged, not done); reading full conversation-message content for Persona (deferred richer read, §Phase 1 note).

---

## 3. File structure / blast radius

| Phase | Create | Modify |
|---|---|---|
| 0 | — | `src/services/trust_gate.py:239-241` |
| 1 | — | `src/services/scheduler/persona_tick.py` |
| 2 | `alembic/versions/<new>_drop_governor_agent_row.py` | `src/orchestrator/agents.py`, `src/orchestrator/prompts.py`, `src/services/agent_registry.py`, `src/orchestrator/context_assembler.py`, `src/tools/catalog.py`, `src/tools/schemas.py`, `src/tools/intelligence_server/*` (report_verdict impl + exports), `src/orchestrator/agent_loop.py`, `src/integrations/capabilities.py`, `src/services/verification/predicate.py`, `src/models/tool_definitions.py` (comment), `src/services/tool_registry.py` + `src/orchestrator/tool_executor.py` (`_special` branch — verify sole user), + test cleanup (~21 files, boundary rule below) |
| Tests | `tests/test_persona_full_trace.py` (new), `tests/test_trust_gate_savepoint.py` (new) | agent-count assertions across existing tests |

**Migrations:** ONE — `574f6c145bca → <new> drop_governor_agent_row` (data-only DELETE, template = `574f6c145bca_drop_operator_agent_row.py`). Single head preserved.

---

## Phase 0 — Pre-batch: `record_auto_execution_outcome` SAVEPOINT (6C follow-up #4)

**Files:** Modify `src/services/trust_gate.py:239-241`. Test `tests/test_trust_gate_savepoint.py` (new, real-DB).

**Rationale:** `record_auto_execution_outcome` is the only trust-write without a `begin_nested()` SAVEPOINT. If `record_approval_decision` flushes-and-errors, the `except` swallows it but the shared session is left poisoned → the run's own later `commit()` raises `PendingRollbackError` (the exact failure the sibling + deferred tick already guard). This fires on the LIVE autonomous auto-exec CONFIRMED path (`dag_runner.py:437`).

- [ ] **Step 1: Write the failing test (reproduce poisoning).** Model on the Phase-1 CF-1/CF-2 pattern (`tests/` real-DB via self-contained `_db_reachable` + NullPool + User→Workspace FK seed — copy the harness from `tests/test_deep_gate_*.py` / `test_step_runner_readback.py`). The test: build a `TrustGate` on a real session; monkeypatch `record_approval_decision` to `await db.execute(<statement that flushes and raises>)` (e.g. an INSERT violating a constraint) so the failure poisons the session; call `record_auto_execution_outcome("email.send", "high", ws)`; then assert the SAME session can still `flush()`/`commit()` a trivial write (i.e. `session.is_active` and no `PendingRollbackError`). Without the fix this FAILS (session poisoned).

```python
# tests/test_trust_gate_savepoint.py (skeleton — fill real-DB harness from test_step_runner_readback.py)
async def test_auto_execution_outcome_savepoint_isolates_poisoning(real_session, seeded_ws):
    tg = TrustGate(real_session)  # confirm ctor: TrustGate(db) — verify against services/trust_gate.py
    async def _poison(db, ws, cap, risk, decision):
        # a flush that errors, poisoning the session if not in a SAVEPOINT
        await db.execute(text("INSERT INTO trust_states (id) VALUES (NULL)"))  # NOT NULL violation
    with patch("src.services.risk_assessor.record_approval_decision", _poison):
        await tg.record_auto_execution_outcome("email.send", "high", seeded_ws.workspace_id)
    # session must survive: a healthy write still commits
    await real_session.execute(text("SELECT 1"))
    await real_session.commit()  # FAILS with PendingRollbackError pre-fix
    assert real_session.is_active
```

- [ ] **Step 2: Run it — expect FAIL** (`PendingRollbackError`/`InFailedSQLTransactionError`). `uv run pytest tests/test_trust_gate_savepoint.py -v`.

- [ ] **Step 3: Apply the fix** — wrap the call in a SAVEPOINT, matching the sibling verbatim:

```python
# src/services/trust_gate.py — record_auto_execution_outcome body
        if not capability:
            return
        try:
            from src.services.risk_assessor import record_approval_decision

            async with self._db.begin_nested():
                await record_approval_decision(
                    self._db, workspace_id, capability, risk_level, "approved"
                )
        except Exception:
            logger.debug("Failed to record auto-execution trust outcome", exc_info=True)
```

- [ ] **Step 4: Run it — expect PASS.**

- [ ] **Step 5: Negative control** — revert the `begin_nested()` line, re-run, confirm the test FAILS again, restore. (Load-bearing guard has teeth.)

- [ ] **Step 6: Commit** `fix(rebuild): SAVEPOINT-wrap record_auto_execution_outcome (Step 7A P0, 6C follow-up #4)`.

---

## Phase 1 — Persona → full interaction-signal job

**Files:** Modify `src/services/scheduler/persona_tick.py`. Test `tests/test_persona_full_trace.py` (new).

**Design:** Keep the harness (modulo gate, min-5, tenant grouping, Haiku `_call_agent`, `_last_persona_batch_at`). Change only (a) the per-interaction formatting — from the lossy `message_preview → intent` one-liner to a full-record trace line that also carries `response_preview` + `plan_summary` + `intent` + `created_at`; (b) raise the window cap from `20` to a budget-bounded `50` (still Haiku, still batched — "sampled subset, full trace of each" per the locked fork). Preserve immutability and the existing exception guard.

> **Deferred richer read (NOT 7A):** `InteractionLog` stores only 500-char previews (no full message body). A truly full-content trace would read the conversation/`messages` store by `conversation_id`/`user_id`. That cross-table read is a deferred enhancement — 7A only stops discarding the signal InteractionLog already has.

- [ ] **Step 1: Write the failing test.** Seed ≥5 `InteractionLog` rows for one `(ws,user)` with distinct `response_preview`/`plan_summary` values; patch `self._orchestrator._call_agent` with an `AsyncMock`; run `_tick_persona_batch` on a mixin instance with `_tick_count=0`; assert the `message=` passed to `_call_agent` CONTAINS each row's `response_preview` and `plan_summary` (proving the trace is richer than `preview→intent`). Use `make_mock_settings()` + a real-DB session (persona reads InteractionLog).

```python
# assertion core
called_msg = orchestrator._call_agent.call_args.kwargs["message"]
assert "resp-sentinel-A" in called_msg and "plan-sentinel-A" in called_msg
assert "resp-sentinel-B" in called_msg
```

- [ ] **Step 2: Run — expect FAIL** (current one-liner drops response/plan).

- [ ] **Step 3: Implement.** Replace the `.limit(20)` and the summary builder:

```python
# persona_tick.py — query cap
                    .order_by(InteractionLog.created_at.desc())
                    .limit(50)  # budget-bounded window (was 20); Haiku, batched

# persona_tick.py — per-interaction trace formatter (module-level helper, testable)
def _format_interaction(i) -> str:
    parts = [f"[{i.created_at:%Y-%m-%d %H:%M}] user: {i.message_preview or '(no preview)'}"]
    if i.intent:
        parts.append(f"intent={i.intent}")
    if i.plan_summary:
        parts.append(f"plan: {i.plan_summary}")
    if i.response_preview:
        parts.append(f"jarvis: {i.response_preview}")
    return " | ".join(parts)

# in the group loop, replace `summary = ...` with:
                    trace = "\n".join(_format_interaction(i) for i in group)
                    # ...message=(f"...preference patterns:\n{trace}")
```
Update the prompt wording from "recent user interactions" to "the recent interaction trace" for honesty. Keep `_call_agent("persona", ...)` unchanged.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Regression** — run the existing scheduler/persona tests (`uv run pytest tests/ -k persona -v`); confirm the modulo/min-5/grouping behavior is unchanged.

- [ ] **Step 6: Commit** `feat(rebuild): Persona learns over the full interaction trace, not a lossy one-liner (Step 7A P1)`.

---

## Phase 2 — Kill the dead Governor LLM agent (blast-radius phase → 2-stage parallel review)

**Review discipline (mirror the 6C Operator kill):** structure/behavior/data separation across commits; assign the live-DB migration round-trip to ONE reviewer; the other reviews statically. Reviewer A = routing/registry/agents/prompts/context; Reviewer B = verdict-machinery removal + migration + validate_registry + tests. Single-owner-per-file, synchronous dispatch.

> **KEEP (do not touch):** `services/governor.py`, `hooks.py::governor_pre_tool_hook`, `evaluate_policy` tool + `internal.evaluate_policy` cap, `routes_approvals.approve_action`. **Orphaned-but-harmless after removal (leave):** `evaluate_policy`/`approve_action`/`get_plan_details` tools + their caps (validate_registry tolerates; a "Governor service liveness" sweep is a SEPARATE deferred plan).

### Task 2.1 — Remove the agent identity from `agents.py`
- [ ] **Step 1:** Remove `AGENT_MODEL_TIERS["governor"]` (`:20`), `AGENT_CAPABILITY_SCOPES["governor"]` (`:100-105`), `AGENT_THINKING["governor"]` (`:194`).
- [ ] **Step 2:** Simplify `create_sub_agents`: `temperature=0.1 if name=="governor" else 0.3` → `temperature=0.3` (`:236`); drop `edge_case_only=(name=="governor")` (`:238`). **VERIFY-DON'T-TRUST:** `grep -rn edge_case_only src` — extraction claims only `agents.py`. If confirmed sole-file, ALSO remove the `edge_case_only` field (`:211`) and its `SubAgent` usages; else leave the field, set it always-`False`. Update docstrings "7 sub-agents"/"all 7" → "6".
- [ ] **Step 3:** `uv run pytest tests/ -k "agents or sub_agent" -v` — fix agent-count assertions (7→6). Commit `refactor(rebuild): remove Governor from agent identity dicts (Step 7A P2)`.

### Task 2.2 — Remove `GOVERNOR_PROMPT` + soul-core references
- [ ] **Step 1:** Delete `GOVERNOR_PROMPT` (`prompts.py:499-541`) and the `AGENT_PROMPTS["governor"]` entry (`:759`).
- [ ] **Step 2:** Edit the soul-core `<agents>` table (`prompts.py:15-25`): remove the `| Governor | Edge-case safety fallback… |` row (`:21`). Edit rule 4 (`:31`) `"4. TrustEngine gates every external write - Governor handles edge cases only"` → `"4. TrustEngine gates every external write"`. Renumber if needed (keep 1-9 contiguous).
- [ ] **Step 3:** `grep -rn "GOVERNOR_PROMPT" src tests` — confirm no dangling import. Commit `refactor(rebuild): remove GOVERNOR_PROMPT + soul-core agent-table row (Step 7A P2)`.

### Task 2.3 — Remove seed + context-enrichment entries
- [ ] **Step 1:** `agent_registry.py`: remove `_DEFAULT_DISPLAY_NAMES["governor"]` (`:26`), `_DEFAULT_DESCRIPTIONS["governor"]` (`:36`); simplify seed `temperature=0.1 if name=="governor" else 0.3` → `temperature=0.3` (`:74`); docstring "7"→"6" (`:50`).
- [ ] **Step 2:** `context_assembler.py`: remove `"governor"` from `CONTEXT_ENRICHED_AGENTS` (`:27`).
- [ ] **Step 3:** Commit `refactor(rebuild): remove Governor from seed + context enrichment (Step 7A P2)`.

### Task 2.4 — Remove the exclusive `report_governor_verdict` machinery (each gated on verify-don't-trust)
- [ ] **Step 1: Prove it's dead.** After 2.1, `grep -rn "internal.report_verdict\|report_governor_verdict\|GOVERNOR_VERDICT" src` — confirm NO agent scope holds `internal.report_verdict` and no live (non-test) caller of the forced-tool-choice branch beyond the now-removed agent. Record findings in the commit body.
- [ ] **Step 2:** Remove: `catalog.py:308-314` (`report_governor_verdict` `InternalToolDef` + its `ReportGovernorVerdictInput` import `:35`); `schemas.py` `ReportGovernorVerdictInput` + its `TOOL_INPUT_MODELS` entry; its impl + exports in `intelligence_server/*`; `agent_loop.py:79-82` `GOVERNOR_VERDICT_TOOL` const + `:496-510` forced-tool-choice branch; `capabilities.py:151` `internal.report_verdict`; `verification/predicate.py:40` `internal.report_verdict` from `REVERSIBLE_INTERNAL_CAPABILITIES`.
- [ ] **Step 3: `_special` backend.** `grep -rn "_special\|ToolBackend.SPECIAL\|SPECIAL" src` — confirm `report_governor_verdict` was the SOLE `_special` tool (comment `models/tool_definitions.py:24` + catalog `:10` say so). If sole: remove the `_special` dispatch branch (`tool_registry.py:69-72`, `tool_executor.py:331-334`, `models/tool_definitions.py` `SPECIAL` enum member) as dead infra; else leave. **Belt-and-suspenders:** run `validate_registry` in a test to confirm zero errors after removal.
- [ ] **Step 4:** `uv run pytest tests/ -k "catalog or schemas or unified_dispatch or agent_loop or tool_registry" -v` — fix/remove tests asserting the verdict tool/forced-tool-choice. Commit `refactor(rebuild): remove dead report_governor_verdict verdict machinery (Step 7A P2)`.

### Task 2.5 — Data migration: drop the governor agent row
- [ ] **Step 1:** `uv run alembic revision -m "drop governor agent row"` (autogenerate NOT needed — data-only). Author the body from the `574f6c145bca_drop_operator_agent_row.py` template: `down_revision='574f6c145bca'`; `upgrade` = `op.execute("DELETE FROM agents WHERE agent_id = 'governor' OR name = 'governor'")`; `downgrade` = intentional no-op (documented). Include the same docstring rationale (seed CREATES/UPDATES-never-DELETES; no FK to `agents`; row `name='governor'`, `agent_id=agt_<ULID>`).
- [ ] **Step 2:** `uv run alembic upgrade head` → `uv run alembic downgrade -1` → `uv run alembic upgrade head`; `uv run alembic heads` (single head); `uv run alembic check` (drift-free). Commit `feat(rebuild): migration — drop dangling governor agents row (Step 7A P2)`.

### Task 2.6 — Test cleanup (the ~21 governor-referencing test files)
- [ ] **Step 1: Categorize by the boundary rule.** For each of the 21 files (`grep -rln -iE "governor|report_verdict|edge_case_only" tests`): does it test the **AGENT** (`AGENTS["governor"]`, `call_agent("governor")`, `GOVERNOR_PROMPT`, `edge_case_only`, `report_governor_verdict`, the forced tool_choice, agent-count==7) → DELETE/ADJUST; or the **SERVICE/HOOK** (`services.governor`, `Governor(...)`, `evaluate_plan`, `evaluate_policy` tool, `governor_pre_tool_hook`, trust demotion) → KEEP untouched.
- [ ] **Step 2:** Delete the agent-only tests (candidates by name, VERIFY each: `test_governor.py`, `test_governor_v2.py`, `test_governor_capability.py`, `golden/test_governor_policies.py` — but several of these likely test the SERVICE; read before deleting). Update agent-count assertions 7→6 in `test_agent_registry.py`, `test_orchestrator.py`, `test_perceiver_agent.py`, `test_runtime_container.py`, etc. KEEP `test_hooks_audit_only.py` (audit hook stays).
- [ ] **Step 3:** `uv run pytest tests/ --ignore=tests/e2e -q` — full gate GREEN. Commit `test(rebuild): prune Governor-agent tests, keep service/hook (Step 7A P2)`.

---

## Phase 3 — Holistic review + full gate

- [ ] **Step 1:** Final holistic **opus** review over the whole 7A diff: re-run the full gate, `alembic heads` (single) + `alembic check` (drift-free) + `ruff check src tests`; independently reproduce the Phase-0 negative control; grep-prove no live consumer of the removed Governor agent/verdict machinery; confirm `services/governor.py` + audit hook + `evaluate_policy` untouched.
- [ ] **Step 2:** Confirm **3237 + Δ passed / 18 skipped / 0 failed** (net +tests from the two new test files, −pruned agent tests). Record the exact count.
- [ ] **Step 3:** CLAUDE.md — the "Agent Boundaries" table lists 7 agents incl. Governor. Per doc policy (durable arch facts only, NO step-migration notes): update the table + `Agent Prompt Architecture` list to reflect **6 agents** (Governor row removed; note the audit hook + Governor service remain as non-agent machinery). This is a durable arch change (an agent removed), so it earns the edit — mirror the 6C Operator→Executor edit style.

---

## 7. Downstream sub-plans (SCOPED here, written later)

### 7B — Deep collapse (dormant, proven): Presenter→tool, Librarian→middleware, Governor→audit+critique middleware, delegate workers, per-child models
- **Presenter→tool:** a cheap-model (sonnet/4096) formatting tool via the standard 3-place internal-tool path (`catalog.py`+`schemas.py`+`intelligence_server.py`) → inert shell + central dispatch (`agent_invoker.py:218`). **DESIGN CONSTRAINT (extraction):** a tool result surfaces as a `tool_result` SSE frame, NOT `text_delta` — the user-visible streamed reply comes only from the lead model's `AIMessageChunk` text. So a Presenter-tool whose output IS the reply does not map to the frozen adapter contract; 7B must decide: lead formats inline (Presenter becomes a prompt section, not a tool) vs. a tool whose result the lead re-streams. Spike-first.
- **Librarian→middleware:** a post-turn `@after_model` extraction middleware (template: `deep_runtime/middleware/budget.py` — currently UNWIRED) appended to `extra_middleware` at `agent_invoker.py:247`. Unifies two divergent behaviors today: perception `call_agent("librarian")` (`perception_runner.py:277`) vs chat's `InteractionLearner` service (`chat_processor.py:621`). Keep the write path (`update_entity`/`store_memory` tools → `entity_resolver`/`world_model`/`entity_facts.store`/`memory_service`).
- **Governor→middleware:** the audit hook already exists on legacy (`hooks.py`); the **deep-path** audit middleware + the **delegate-summary critique pass** are NET-NEW (`@wrap_tool_call` shape). Fail-closed for writes / fail-open-annotated for read summaries (spec degradation mode).
- **Delegate / read-only workers:** deepagents 0.6.11 supports `create_deep_agent(subagents=[...])` with per-child `model`/`tools`/`middleware` (`deepagents/graph.py:242`), NOT passed today (`agent_builder.py:120-127`). Perceiver + research stay agents as depth-1 read-only delegates. **SECURITY:** a Jarvis subagent with its own tools/model needs its OWN `capability_scope` + dispatcher middleware or it bypasses fail-closed scope. The deepagents `task` built-in is currently ungated (exempted in `builtins.py`) — decide keep/replace/disable.
- **Per-child model specialization:** the affordance exists (`SubAgent["model"]`) but is UNUSED — everything runs on one lead model today. 7B wires it to preserve Presenter=sonnet/4096, Persona=haiku/2048, research=right-sized.
- **Fold 6C follow-up #1** (double capability resolution) while rebuilding the middleware chain: resolve capability once in `trust_gate` and thread the value to `write_lock` (currently `write_lock` takes a `resolve_capability` fn, so both call `get_tool`).

### 7C — Verification parity (T2 inline) + middleware wiring (dormant, proven)
- **Inline read-back:** a post-execute deep middleware (INNER of `write_lock`) that runs `ReadBackVerifier.verify_step` (path-agnostic, reusable) after `handler(request)` for irreversible writes → CONFIRMED/CONTRADICTED/UNVERIFIED + escalate-first (`compensation.build_divergence_escalation`). **Defer** the durable deferred-recheck loop (deep has no `TaskStep`/`completed_unverified` surface; the autonomous deferred tick itself is half-wired, `read_fn=None`).
- **Governor delegate-critique** (the T2 inspection-seam replacement) lands with 7B's Governor middleware.
- **Wire the two UNWIRED deep middlewares** so activation isn't a regression: `budget` (`make_budget_middleware`, `budget.py` — no per-call `TokenUsage` today on deep) + `unavailable_server` (`make_unavailable_server_middleware` — no MCP re-auth breaker). Also fix `middleware/__init__.py __all__` (omits `trust_gate`/`write_lock`).
- **Trust-increment-on-CONFIRMED for the deep path** is net-new; use `begin_nested()` from the start (mirror Phase 0).

### Activation gates (Step 10 — NOT 7A/7B/7C)
- 6C follow-up **#2** (write-lock fail-open under Redis outage) — decide fail-open vs hard-serialize before `deep` is the live default.
- 6C follow-up **#3** (contended-write `blocked` shape: deep `ToolMessage` vs autonomous dict) — confirm both drive retry-not-giveup; add telemetry.

---

## Self-Review (run after drafting, before execution)

1. **Spec coverage:** T1 Persona→job ✓ (Phase 1, runtime-independent slice); T1 Governor→(audit middleware stays on legacy; dead agent removed) ✓ (Phase 2); Presenter/Librarian/delegate/per-child/T2 → scoped to 7B/7C (§7). #4 pre-batch ✓ (Phase 0). No orphaned requirement.
2. **Placeholder scan:** every code step has real code or an exact grep/command; test skeletons name real harness patterns (`test_step_runner_readback.py`, CF-1/CF-2 poisoning pattern). The one intentional latitude — Task 2.6's per-file categorization — is bounded by an explicit boundary rule + a KEEP list (this is verify-don't-trust work the review owns, not a placeholder).
3. **Type/anchor consistency:** `record_auto_execution_outcome`/`record_user_approval_outcome` signatures match `trust_gate.py:224/245`; `_format_interaction`/`_tick_persona_batch` match `persona_tick.py`; migration `down_revision='574f6c145bca'` matches the current head; KEEP/REMOVE boundary consistent across all Phase-2 tasks.
4. **Load-bearing guards have teeth:** Phase 0 negative control (revert SAVEPOINT → test fails); Task 2.4 gated on grep-proof of deadness + a `validate_registry`-returns-empty assertion.
