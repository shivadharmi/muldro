# Step 7C — Inline Read-Back + Middleware Wiring + Deep Trust-Increment Implementation Plan

> **STATUS: SKELETON (committed early per the Step-2 orphan lesson).** Scope + forks +
> file structure are locked below; the per-phase TDD tasks are filled in AFTER the 4
> parallel code-extraction passes land and the 4 forks are resolved with the user.
> Do NOT execute from this skeleton — execution is a later session.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Step 7 (cognitive-agent collapse) by adding the last deep-runtime
correctness machinery: an inline `ReadBackVerifier` post-execute middleware (inner of
write_lock) for irreversible/external writes, escalate-first compensation on a contradicted
effect, deep trust-increment-on-CONFIRMED (SAVEPOINT from the start), and wiring the two
written-but-unwired deep middlewares (`budget`, `unavailable_server`) — all DORMANT on the
default `legacy` runtime + flag-off, proven via forced/offline tests, activated only at
Step 10.

**Architecture:** 7C extends the deep `wrap_tool_call` onion assembled in
`AgentInvoker._build_deep_agent_for`. Today the chain is
`capability_scope → governor_audit → trust_gate → write_lock → dispatcher` (+ `librarian_extract`
`@after_model`, + `critique` prepended when `deep_delegates_enabled`). 7C inserts a read-back
middleware **inner of write_lock, outer of dispatcher** (post-execute: it calls the handler,
then verifies the effect while the lock is held), and appends/threads the budget
(`@after_model`) + unavailable_server (`@wrap_tool_call`) middlewares. All of
`src/services/verification/` (the path-agnostic Step-3 machinery) is reused verbatim — no
new verification logic, only a deep host for it.

**Tech Stack:** Python 3.13, LangChain/LangGraph deep-agents 0.6.11 middleware
(`wrap_tool_call`/`after_model`), the existing `src/services/verification/` package
(`ReadBackVerifier`/`VerifyVerdict`/`predicate`/`post_conditions`/`compensation`/`inflight`),
`src/services/write_lock.py`, `TrustGate` SAVEPOINT pattern, pytest (custom asyncio hook, NO
pytest-asyncio), real Postgres+Redis+Qdrant for the DB/lock tests.

---

## Baseline (verified at scoping, 2026-07-08)

- Branch `rebuild/first-principles` (off `main`, NOT pushed), HEAD `acb6058` (Step 7B2 P6).
- `uv run pytest tests/ --ignore=tests/e2e` → **3299 passed / 18 skipped** (18 = true green;
  ~108 skipped ⇒ redis/postgres DOWN ⇒ NOT green; `docker start hyperlocal-redis` serves :6379).
- `uv run alembic heads` → single `1a2770a28c39`; `alembic check` drift-free; `ruff check src tests` clean.
- INFRA: uv venv has NO pip → `uv sync --all-extras`; custom `pytest_pyfunc_call` asyncio hook;
  real-DB/Redis tests self-contained (`_db_reachable`/`_redis_reachable` + NullPool + User→Workspace
  FK seed + UUID-suffixed Redis keys). Do NOT edit backend/ while a `uvicorn --reload` worker runs.

## Scope

### IN SCOPE
1. **Inline read-back middleware** — a NEW `src/deep_runtime/middleware/readback.py`
   (`make_readback_middleware`), `@wrap_tool_call`, placed **inner of write_lock, outer of
   dispatcher**. Post-execute: run the write via `handler(request)`, then feed
   `(capability, write_input, write_output, risk)` to a `ReadBackVerifier` and annotate the
   verdict onto the `ToolMessage` content (mirrors the `critique` annotation pattern). Reuses
   the whole `src/services/verification/` package verbatim.
2. **Escalate-first compensation** — on `CONTRADICTED`, build `build_divergence_escalation(...)`
   (exact artifact_ref + observed divergence + registered compensator, escalate regardless)
   and surface it via the annotation (interactive compensator EXECUTION + divergence UI remain
   deferred — Step-3 carry-forward #4).
3. **Deep trust-increment-on-CONFIRMED** — on `CONFIRMED` for a GATED write, increment trust
   with a `begin_nested()` SAVEPOINT **from the start** (the 6C #4 / 7A-P0 session-poisoning
   lesson — do NOT ship the sibling without the SAVEPOINT).
4. **Wire the two unwired deep middlewares** — `make_budget_middleware` (`@after_model`,
   records `TokenUsage`; additive — `stream_adapter` only `calculate_cost`s for the frame,
   never `record_usage`s) + `make_unavailable_server_middleware` (`@wrap_tool_call`, per-turn
   auth-breaker) into the `_build_deep_agent_for` chain.

### OUT OF SCOPE (deferred, confirm during extraction)
- The DURABLE deferred-recheck loop (deep has no `TaskStep`/`completed_unverified` surface, and
  the autonomous deferred tick is itself half-wired `read_fn=None`) — Fork 4.
- Any live activation / runtime flip (Step 10): flipping `JARVIS_RUNTIME=deep`, single-lead
  routing, dropping the presenter step / InteractionLearner spawn, agent-count reduction / row-drop.
- The 7B1/7B2 activation gates (deep redis carry — already fixed `80825fb`; P4 lead-scoping;
  delegate error-path hardening; critique prompt-injection hardening; etc.).
- Interactive compensator EXECUTION + `verification_divergence` frontend rendering (Step-3 CF #4).
- Live per-connector read-back seam (all real writes are `UNVERIFIABLE` today; `calendar.create`
  is mock-only — `calendar.get`=`query_freebusy` risks false-CONTRADICT) — Step-3 CF #1.

## Forks (resolved with user before the full plan is written)

> Recommendations grounded in the scoping reads; final answers folded in post-AskUserQuestion.

- **Fork 1 — Dormancy shape.** Does the read-back run LIVE-inline on deep chat writes, or stay
  DORMANT/scaffold-proven behind a flag (`deep_readback_enabled=False`)? Read-back is CORRECTNESS
  (not authorization), so it *could* fire on any deep write regardless of the ungated-direct-chat
  rule. **Recommendation: DORMANT behind a flag.** With a `read_fn=None` (or thin) seam, every real
  write is `UNVERIFIABLE` → read-back would only annotate "unverified" noise with no correctness
  value today, and the trust-increment-on-CONFIRMED has no live gated producer until Step 10.
  Consistent with 6B/6C/7B1/7B2 byte-neutral-flag-off discipline. *(To confirm: read_fn=None vs a
  real execute_tool read_fn seam.)*
- **Fork 2 — Wire budget + unavailable_server now vs defer.** **Recommendation: WIRE now.** Both
  deps already exist on `AgentInvoker` (`self._budget`, `self._db_factory`, `workspace_id`,
  `agent.name`, model id, trace_id); the 7A "needs a TokenUsage/re-auth breaker that doesn't exist"
  worry is stale (both middlewares self-contain their deps). Budget is additive (no double-count).
  Sub-decision: wire them UNCONDITIONALLY into the deep chain (pure hygiene, not a behavior gate)
  vs behind the readback/deep flag.
- **Fork 3 — Packaging: ONE 7C plan vs split.** **Recommendation: ONE combined plan** (like 7B2).
  The three pieces share the same chain-assembly seam (`_build_deep_agent_for`, the HOT file);
  splitting adds overhead. Phase-order handles the small dependency (trust-increment invoked from
  the read-back CONFIRMED branch).
- **Fork 4 — Deferred-recheck loop truly deferred?** **Recommendation: TRULY DEFERRED** (no minimal
  deep equivalent in 7C). Deep has no `TaskStep`/`completed_unverified` persistence surface and no
  live read_fn; a deep deferred-recheck has nowhere to record "pending recheck." Matches the 7A
  fork lock (Q3).

## File Structure (locked)

- **Create:** `src/deep_runtime/middleware/readback.py` — `make_readback_middleware` (@wrap_tool_call,
  post-execute verdict + annotate + escalate-first; invokes the trust-increment on CONFIRMED-gated).
- **Modify:** `src/orchestrator/agent_invoker.py` `_build_deep_agent_for` — insert read_back inner of
  write_lock; thread budget (`@after_model`) + unavailable_server (`@wrap_tool_call`) into the chain.
  **THE HOT SHARED FILE — single-owner + SYNCHRONOUS implementer dispatch; sequence its touches.**
- **Modify:** `src/deep_runtime/middleware/__init__.py` `__all__` — add `make_readback_middleware`.
- **Modify:** `src/config/settings.py` — add `deep_readback_enabled: bool = False` (if Fork 1 = dormant).
- **Create:** `backend/spikes/deep_readback/probe.py` + `docs/superpowers/spikes/2026-07-08-deep-readback.md`
  (IF Phase-0 spike is needed — see below).
- **Create:** `tests/deep_runtime/test_readback_middleware.py` + a forced-on offline e2e guard.
- **Reuse verbatim (NO change):** all of `src/services/verification/` +
  `src/deep_runtime/middleware/{budget,unavailable_server}.py`.

## Spike-first decision

The read-back **annotation-through-SSE** mechanism is already PROVEN (the 7B2 `critique`
middleware annotates a `task` result the identical way; the `write_lock` middleware proves
inner-of-write_lock placement; DB writes from a `wrap_tool_call` are proven by `trust_gate`).
The ONE genuinely unproven bit is a **nested `execute_tool` call for a real `read_fn` from
INSIDE a `wrap_tool_call`** — needed only if Fork 1 chooses a real read_fn seam. **Phase-0
offline probe (could DISPROVE the real-read_fn approach)** is included conditionally; if
Fork 1 = `read_fn=None` scaffold, no probe is needed and the plan opens at Phase 1.

## Phases (to be filled after extraction + forks)

- **Phase 0 (conditional):** offline spike — nested `execute_tool` read_fn from a middleware +
  read-back verdict/annotation through the frozen SSE contract + SAVEPOINT trust-write from a middleware.
- **Phase 1:** `make_readback_middleware` — verdict + annotate + escalate-first, inner of write_lock.
- **Phase 2:** deep trust-increment-on-CONFIRMED (`begin_nested()` SAVEPOINT from the start).
- **Phase 3:** wire budget + unavailable_server into `_build_deep_agent_for`.
- **Phase 4:** forced-on offline e2e guard + negative controls WITH TEETH.
- **Phase 5:** holistic opus + full gate + `__all__` hygiene (NO CLAUDE.md edit — dormant deep internals).

## Review strategy (locked, per 7A/7B1/7B2 rhythm)

- Blast-radius seam touch (`agent_invoker._build_deep_agent_for`) = 2-stage PARALLEL spec+quality
  review on the frozen commit (prove flag-off / legacy byte-identical).
- Load-bearing read-back + trust-increment = independent opus review.
- Final holistic opus that INDEPENDENTLY reproduces EVERY negative control.
- Single-owner-per-file + SYNCHRONOUS implementer dispatch (agent_invoker.py is HOT — sequence touches).
- FULL non-e2e gate at EVERY checkpoint (18 skipped, not ~108 — subset-green ≠ gate-green, the 7B1 lesson).
- Every load-bearing guard needs a negative control WITH TEETH (revert-fix → test fails → restore).

## Guardrails (carried lessons)

- VERIFY-DON'T-TRUST every current-state claim at file:line — spec/memory/CLAUDE.md/extraction all rot.
- STAY DORMANT unless a fork decides otherwise; byte-neutral on legacy + flag-off; dormant-but-PROVEN.
- Any new cheap-model/redis cache MUST source redis from `self._services.extras.get("redis")` (the 6C bug).
- Any session-poisoning-prone write (trust increment, verdict record) needs `begin_nested()` SAVEPOINT
  from the START (Step-4/6C/7A lesson — revert-and-rerun-prove it load-bearing).
- Expect NO migration (read-back is inline, not a persisted entity on the deep path). If a task seems
  to need one, STOP and re-check. Do NOT edit CLAUDE.md for dormant deep internals (durable only at MERGE).
