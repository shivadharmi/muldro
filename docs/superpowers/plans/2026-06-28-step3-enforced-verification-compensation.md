# Step 3 — Enforced Read-Back Verification + Compensation Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make *Correctness* enforced instead of advisory — a world-touching write is verified by **read-back before** its step is marked terminal (mandatory only when `IRREVERSIBLE`), a genuine `completed_unverified` success state is threaded everywhere including trust graduation, and a failed read-back on an irreversible write **escalates to the user** (escalate-first compensation) rather than being silently marked done.

**Architecture:** A new `backend/src/services/verification/` package (mirroring the Step-1 `idempotency/` package) holds four things: (1) the shared `IRREVERSIBLE` predicate + a deterministic per-capability irreversibility classifier, (2) a capability-keyed **post-condition registry** with a startup **coverage gate** (mirrors `validate_registry()`), (3) a **read-back verifier** that runs a registered post-condition through the existing tool-execution seam and returns `confirmed | contradicted | unverified`, and (4) a capability-keyed **compensation registry** (escalate-first). The single terminal-status DB write for a step — `dag_runner.finalize_step`'s `transition_step(step, "completed")` — becomes risk-gated: `execute_step` computes a verdict from the read-back verifier and passes the resulting status (`completed` / `completed_unverified` / `partially_completed`) into `finalize_step`. Two net-new **step-level** statuses thread through the state machine + every step-completion counter via a `TERMINAL_SUCCESS` membership set. The auto-execution **trust increment relocates** to fire only on a confirmed-verified `completed`. A **deferred-read scheduler tick** upgrades `completed_unverified → completed` on later confirmation and raises an async-divergence surface on post-turn divergence. Two Step-1 carry-forwards land here: `validate_identity_coverage` becomes a startup hard-gate, and the ledger's `in_flight` conflict on resume gets a read-back resolution path.

**Tech Stack:** Python 3.12/3.13, pytest (async via the repo's home-grown `pytest_pyfunc_call` `asyncio.run` hook — **no pytest-asyncio, no `asyncio_mode`**), SQLAlchemy 2 / asyncpg, Postgres 17, Redis (Notifier hold-for-briefing), Pydantic v2, ruff, alembic (no migration — see posture note), Next.js/TypeScript (two frontend edits), the existing `SchedulerLoop` mixin pattern.

**Source spec:** [`docs/superpowers/specs/2026-06-28-first-principles-rebuild-design.md`](../specs/2026-06-28-first-principles-rebuild-design.md) §6 Step 3 (migration-order entry), §4.5 (enforced verification + compensation — the detailed design), §4.3 (the shared `IRREVERSIBLE` predicate + the deterministic per-capability registry property), §7 (false-negative-verification + no-remediation risks).

**Depends on:** Step 2 (`fb36a85`, alembic head `b3e8c1f5a9d2`) — the green baseline this plan builds on. Baseline suite: **~2977 passed / 18 skipped** for `uv run pytest tests/ --ignore=tests/e2e`.

---

## Infra note (verified 2026-07-05 in this environment)

- **Postgres + Redis + Qdrant are live** (`docker compose ps`: all `Up`). DB is at the single alembic head **`b3e8c1f5a9d2`** (`uv run alembic current`). Migrations can be applied for real and real-DB integration tests can run.
- **`langgraph` / `langchain` / `deepagents` are installed** — the full suite including `tests/deep_runtime/` collects and runs.
- **This is a `uv`-managed venv with NO `pip`.** Run everything via `uv run …` (`pytest`/`alembic`/`ruff`/`python`) and `uv add …`. Plain `uv sync` drops the dev extras — use `uv sync --all-extras` if you must sync.
- **`spaCy` / any NER library is NOT installed** (irrelevant to Step 3; noted for continuity).

**Run all backend commands from `backend/`.**

**Pre-flight (run once before starting):**
```bash
cd backend && uv run pytest tests/ --ignore=tests/e2e -q -p no:cacheprovider 2>&1 | tail -5
uv run alembic current 2>&1 | tail -1   # expect: b3e8c1f5a9d2 (head)
```

---

## Current-state corrections (verify-don't-trust — confirmed against code 2026-07-05)

The spec and CLAUDE.md carry point-in-time claims that are now stale on this branch. Four extraction passes established the grounding facts this plan relies on:

1. **`graph_executor` is a FLAT FILE, not a package.** The spec/CLAUDE.md describe `backend/src/services/graph_executor/{step_runner,dag_runner}.py`. On `rebuild/first-principles` those files live directly under `backend/src/services/`: **`step_runner.py`, `dag_runner.py`, `execution_state.py`, `graph_executor.py`, `outcome_learner.py`, `trust_gate.py`, `verifier.py`, `step_graph_store.py`, `runtime_projection.py`**. (The GraphExecutor→package decomposition landed on `review/architecture-remediation`, a branch this one forked before.) **Every path in this plan uses the flat locations.**
2. **Status columns are plain `String(32)` varchars — NOT DB `Enum`, NOT `CHECK`-constrained.** `TaskRun.status` (`models/task_graph.py:29`) and `TaskStep.status` (`models/task_graph.py:101`) are `mapped_column(String(32), default="pending")`. The *only* validation gate is the `STEP_TRANSITIONS`/`RUN_TRANSITIONS` dicts in `execution_state.py`. **Adding `completed_unverified` needs NO alembic migration** for the column type/constraint (19 chars fits in 32). The spec's "status-enum migration" assumption is wrong for this branch.
3. **`run_verification` EXISTS but is the wrong kind of verification.** `OutcomeLearner.run_verification` (`outcome_learner.py:212`) → `Verifier.verify_run` (`verifier.py:41`) checks the run's own **DB records** (step statuses / `output_data`) against optional `success_conditions`; it does **not** re-fetch the real-world effect. It is **run-level and advisory** (runs once at DAG end, `dag_runner.py:119`; on `failed` it only logs; returns `skipped` when no `success_conditions`). Step 3's read-back is a genuinely **new, inline, per-step, external** verification — it does not reuse `Verifier.verify_run`, and it leaves the advisory run-verifier untouched.
4. **The trust increment fires per-step, on tool-returned-success, BEFORE any verification.** `dag_runner.execute_step` calls `finalize_step` (`dag_runner.py:380`, which does `transition_step(step, "completed")` at `:650`), then `record_auto_execution_outcome(...)` (`dag_runner.py:385-387`) → `record_approval_decision(..., "approved")` (`risk_assessor.py:292`, the sole `approved_count += 1` site) → `graduate_trust`, then `remember_auto_executed(...)` (`dag_runner.py:390` → `trust_gate.py:247`) which writes `run.checkpoint["auto_executed"]` — **read by nothing today (dead data)**. This plan makes that dead audit trail the deferred-increment ledger.
4b. **The `remember_auto_executed` docstring is aspirational.** It claims "the verification feedback reads to reverse..." — no reader exists. This plan wires the reader.
5. **No `IRREVERSIBLE` predicate exists; `TrustEngine._matrix_lookup` ignores `reversible`/`blast_radius` entirely** (`trust_engine.py:140-157` keys only on `trust_level × risk_level`). The gate *override* that consumes `IRREVERSIBLE` is **Step 6 (out of scope)**; this plan builds the predicate + the deterministic per-capability classifier and uses them only on the **verification** side.
6. **`RiskAssessment`** (`risk_assessor.py:52-60`) carries `reversible: bool = True` and `blast_radius: Literal["self","internal","external_single","external_multiple","public"] = "self"` (spec claims CONFIRMED exact), plus `risk_level: Literal["none","low","medium","high"]` (**no `critical`** on this model). Fail-closed → `risk_level="high", reversible=False` at two sites (`risk_assessor.py:116-121`, `trust_gate.py:84-88`). `RiskAssessment` is **transient** (not its own table); in `dag_runner.execute_step` the `risk` object is in scope from `assess_step_risk` (`dag_runner.py:312`) through `finalize_step` (`:380`).
7. **`validate_identity_coverage`** (`idempotency/identity.py:101`) is **test-only — never wired at startup**. `IDENTITY_SPECS` covers only `email.send`, `email.delete`, `calendar.create`. Promoting it is *new wiring*, not upgrading an existing warning.
8. **The idempotency ledger's `status == "completed"`** (`idempotency/ledger.py:105`) is the **ledger row's** own status (`in_flight`/`completed`/`failed`), set by `record_success` when the **external write fires** — NOT the TaskStep status. A `completed_unverified` step *did* fire its write, so the ledger correctly keeps it `completed` and must NOT re-fire on resume. **This site is deliberately left unchanged.** `ledger.completed` ("effect fired") ≠ `step.completed` ("effect verified").

---

## Design decisions (rationale + rejected alternatives)

- **D1 — New `verification/` package mirroring `idempotency/`.** Four small modules: `predicate.py` (irreversibility), `post_conditions.py` (registry + coverage gate), `readback.py` (the verifier), `compensation.py` (registry). Rationale: high cohesion, matches the in-repo Step-1 precedent (`idempotency/{identity,ledger,wrapper}.py`), keeps each file well under the 400-line target. **Rejected:** bolting onto `verifier.py` (that's the run-level advisory verifier — conflating them muddies "advisory run check" vs "enforced step read-back").
- **D2 — Registries are capability-keyed dicts with coverage validators, NOT new fields on the `frozen+slots` tool dataclasses.** Post-conditions/compensations key on **capability** (many tools → one capability), matching the catalog-derived coverage set. This mirrors `IDENTITY_SPECS` exactly and avoids touching `InternalToolDef`/`ExternalToolSeed`/`_ext()`. **Rejected:** extending `CapabilityMeta`/`_cap()` for ~60 write caps (large central diff; irreversibility is better expressed as a small fail-closed exception list — D3).
- **D3 — Irreversibility classification defaults fail-closed; only reversible-internal EXCEPTIONS are listed.** `is_irreversible_capability(cap)`: read-only → `False`; a write cap present in `REVERSIBLE_INTERNAL_CAPABILITIES` → `False`; **any other write cap → `True` (fail-closed)**. This satisfies the spec's "deterministic per-capability registry property" while inverting the work from ~45 external-write entries to ~15 internal exceptions, and guarantees a *new* write capability defaults to needing a post-condition. **Rejected:** using catalog `risk_level ∈ {high,critical}` as the irreversibility axis — that is a *different* predicate than the per-step `reversible/blast_radius` the spec pins, violating "the gate override and the verification trigger use the same predicate."
- **D4 — Verification trigger is a fail-closed UNION.** `is_write_verification_required(cap, risk) = is_irreversible_capability(cap) OR IRREVERSIBLE(risk.reversible, risk.blast_radius)`. The static classifier drives the startup coverage gate; the union drives the runtime decision so neither a 24h-cache LLM mislabel (says reversible) nor an unclassified new cap can skip verification. The RiskAssessor "refine-upward-only" clamp is a **Step-6 gate** concern; the union makes it unnecessary for Step-3 verification.
- **D5 — Post-condition coverage uses an explicit UNVERIFIABLE set as the escape valve.** The gate requires every IRREVERSIBLE write cap to be in `POST_CONDITIONS` (has a real read-back) **OR** `UNVERIFIABLE_CAPABILITIES` (explicitly "no deterministic read exists" → `completed_unverified`). Both are explicit → no silent gap. This resolves the "impossible to verify everything" tension: the gate forbids a capability being *absent*, not being *honestly marked unverifiable*.
- **D6 — `completed_unverified` + step-level `partially_completed` are STEP statuses ONLY.** Runs still roll up to the existing `completed` / `partially_completed`. This avoids (a) the run-level `ix_task_runs_idempotency` partial index whose predicate is `status NOT IN ('completed','failed','cancelled')`, and (b) `graph_executor.py:811` `_RUN_STATUS_TO_PLAN_STATUS` (run-status-keyed) — both untouched → **zero migrations**. The deferred-read tick queries `TaskStep.status == "completed_unverified"` directly, independent of run status.
- **D7 — The single enforced gate is `finalize_step`'s DB transition, not the `step_runner` dict.** `step_runner.py`'s `{"status": "completed"}` dicts (lines 121/134/249/331) are **advisory payload**, not DB writes; the real terminal write is `transition_step(step, "completed")` in `dag_runner.finalize_step:650`. `execute_step` computes the verdict (where `risk` is in scope) and passes the resulting status into `finalize_step`. Guarding that one DB write covers all "by-fiat" sites the spec names. The characterization test asserts no irreversible write reaches a terminal status without a verdict.
- **D8 — Read-back runs the registered read capability through the existing tool-execution seam, mocked in tests.** A `PostCondition` names a read capability + an assertion over its result. `ReadBackVerifier` invokes it via the injected `execute_tool_fn` (the same seam Step 1 wraps). Real connector read-backs are added per-connector later; for now one real example proves the mechanism and the rest are `UNVERIFIABLE`. **Rejected:** re-querying the DB (only works for internal writes, which aren't irreversible) — doesn't exercise the external read-back path.
- **D9 — Compensation is escalate-first and has NO startup coverage gate.** Spec: "Where no compensator exists, escalate regardless." So compensation coverage is *not* required; a missing compensator still escalates (informational). Only post-condition + identity coverage are hard gates.

---

## In-flight-run / migration posture (spec §6 requires this per schema-touching step)

**There is NO schema change in Step 3** (correction #2: statuses are varchars; registries are code; verification metadata is stored in the existing `step.output_data` / `run.checkpoint` JSONB). Therefore:
- **Migration:** none. `alembic check` stays trivially drift-free; `alembic current` remains `b3e8c1f5a9d2`. Task 11 asserts this explicitly.
- **In-flight posture:** additive, no drain / dual-read / reconcile needed. A run in flight when the new code deploys keeps its already-`completed` steps (never retroactively re-verified — correct: their writes fired and were accepted under the old regime); only steps that transition *after* the deploy get read-back verification. The new step statuses are unreachable for pre-deploy steps.
- **Resume-across-deploy:** a run paused (`awaiting_approval`) before deploy and resumed after: its next step runs through the new gate normally. The Step-1 ledger already guards the write; the new in_flight-on-resume read-back (Task 10) strengthens the conflict resolution. No status a pre-deploy step could hold is invalidated.

---

## File Structure

**Create (backend):**
- `backend/src/services/verification/__init__.py` — package facade; re-exports the public surface.
- `backend/src/services/verification/predicate.py` — `IRREVERSIBLE`, `REVERSIBLE_INTERNAL_CAPABILITIES`, `is_irreversible_capability`, `is_write_verification_required`, `write_capabilities()`.
- `backend/src/services/verification/post_conditions.py` — `PostCondition`, `POST_CONDITIONS`, `UNVERIFIABLE_CAPABILITIES`, `validate_post_condition_coverage`.
- `backend/src/services/verification/readback.py` — `VerifyVerdict`, `ReadBackVerifier`.
- `backend/src/services/verification/compensation.py` — `Compensation`, `COMPENSATIONS`, `build_divergence_escalation`.
- `backend/src/services/scheduler/deferred_verification_tick.py` — `DeferredVerificationTickMixin._tick_deferred_verification`.
- Tests: `backend/tests/verification/test_predicate.py`, `test_post_conditions.py`, `test_readback.py`, `test_compensation.py`, `backend/tests/test_step_terminal_success.py`, `backend/tests/test_finalize_verification.py`, `backend/tests/test_trust_increment_relocation.py`, `backend/tests/test_deferred_verification_tick.py`, `backend/tests/test_identity_coverage_gate.py`, `backend/tests/test_inflight_resume_readback.py`, `backend/tests/test_verification_e2e.py` (real-DB, skip-if-no-Postgres).

**Modify (backend):**
- `backend/src/services/execution_state.py` — add `completed_unverified` + step `partially_completed` to `STEP_TRANSITIONS`; add `TERMINAL_SUCCESS`.
- `backend/src/services/dag_runner.py` — `finalize_step(status=...)`; verdict computation in `execute_step`; relocate the trust increment; consume `checkpoint["auto_executed"]` on deferred confirm.
- `backend/src/services/step_graph_store.py`, `outcome_learner.py`, `runtime_projection.py`, `surface_builder.py`, `execution_surface_emitter.py`, `verifier.py`, `graph_executor.py`, `surface_detail_builders/plan.py`, `surface_detail_builders/lists.py`, `api/routes_history.py` — swap step-level `status == "completed"` for `status in TERMINAL_SUCCESS` at counting/gating sites.
- `backend/src/services/idempotency/identity.py` — no change to the function; Task 10 wires it + adds `POSITIONAL_KEY_ACCEPTED`.
- `backend/src/api/app.py` — wire two startup hard-gates (post-condition coverage, identity coverage).
- `backend/src/services/scheduler/service.py` + `scheduler/_base.py` — register + call the new tick.

**Modify (frontend):**
- `frontend/src/lib/a2ui-types.ts` — add `"completed_unverified"` to `StepState.status`; add a `STEP_TERMINAL_SUCCESS`/`isStepDone` helper.
- `frontend/src/components/a2ui/components/step-presentation.tsx` — add a `completed_unverified` icon case ("sent (unconfirmed)").
- `frontend/src/components/a2ui/components/step-list.tsx` — route the `=== "completed"` filters through the done-helper.

**Untouched (by design):** `idempotency/ledger.py:105` (ledger status ≠ step status, correction #8); `verifier.py`'s advisory run-verification logic (correction #3); the chat path (`process_message`/`_stream` remain ungated — [[project_inline_trust_gap]]); `TrustEngine._matrix_lookup` (the gate override is Step 6); all RUN-level `== "completed"` sites (statuses are step-only, D6).

---

## Task 1: The `IRREVERSIBLE` predicate + deterministic per-capability classifier (pure, foundational)

The shared §4.3 predicate + the static classifier the startup coverage gate needs, plus the fail-closed union that drives the runtime verification decision. Pure — fully unit-testable, no DB, no network.

**Files:**
- Create: `backend/src/services/verification/__init__.py`, `backend/src/services/verification/predicate.py`
- Test: `backend/tests/verification/__init__.py`, `backend/tests/verification/test_predicate.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/verification/__init__.py` (empty file), then `backend/tests/verification/test_predicate.py`:

```python
"""The shared IRREVERSIBLE predicate + deterministic per-capability classifier
(spec §4.3). Pure — no DB, no network. The classifier defaults fail-closed:
an unlisted write capability is IRREVERSIBLE."""

from types import SimpleNamespace

from src.services.verification.predicate import (
    IRREVERSIBLE,
    REVERSIBLE_INTERNAL_CAPABILITIES,
    is_irreversible_capability,
    is_write_verification_required,
    write_capabilities,
)


def test_predicate_reversible_false_is_irreversible():
    assert IRREVERSIBLE(reversible=False, blast_radius="self") is True


def test_predicate_external_blast_radius_is_irreversible():
    for br in ("external_single", "external_multiple", "public"):
        assert IRREVERSIBLE(reversible=True, blast_radius=br) is True


def test_predicate_reversible_internal_is_not_irreversible():
    assert IRREVERSIBLE(reversible=True, blast_radius="self") is False
    assert IRREVERSIBLE(reversible=True, blast_radius="internal") is False


def test_read_only_capability_is_never_irreversible():
    # reads don't get read-back verification
    assert is_irreversible_capability("email.read") is False
    assert is_irreversible_capability("calendar.list") is False


def test_external_write_defaults_to_irreversible():
    assert is_irreversible_capability("email.send") is True
    assert is_irreversible_capability("calendar.create") is True
    assert is_irreversible_capability("repo.create_pr") is True


def test_unknown_write_capability_defaults_fail_closed_irreversible():
    # a brand-new write capability not in the catalog is treated as irreversible
    assert is_irreversible_capability("brand.new_write") is True


def test_reversible_internal_exceptions_are_not_irreversible():
    for cap in ("internal.store_memory", "email.draft", "messaging.mark_read"):
        assert cap in REVERSIBLE_INTERNAL_CAPABILITIES
        assert is_irreversible_capability(cap) is False


def test_verification_required_is_fail_closed_union():
    # registry says irreversible even though the per-step risk says reversible
    risk_says_safe = SimpleNamespace(reversible=True, blast_radius="self")
    assert is_write_verification_required("email.send", risk_says_safe) is True
    # registry says reversible-internal, risk also safe -> not required
    assert is_write_verification_required("internal.store_memory", risk_says_safe) is False
    # registry reversible-internal but per-step risk flags external -> required (union)
    risk_says_danger = SimpleNamespace(reversible=True, blast_radius="external_single")
    assert is_write_verification_required("internal.store_memory", risk_says_danger) is True


def test_write_capabilities_excludes_reads():
    caps = write_capabilities()
    assert "email.send" in caps
    assert "email.read" not in caps
    assert "system.discovery" not in caps  # read-only
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/verification/test_predicate.py -q`
Expected: FAIL at import — `No module named 'src.services.verification'`.

- [ ] **Step 3: Write the package `__init__` + `predicate.py`**

Create `backend/src/services/verification/__init__.py`:

```python
"""Enforced read-back verification + compensation (spec §4.5).

Correctness becomes ENFORCED, not advisory: a world-touching write is verified by
read-back BEFORE its step is marked terminal (mandatory only when IRREVERSIBLE),
and a failed read-back on an irreversible write escalates to the user.
"""

from src.services.verification.predicate import (
    IRREVERSIBLE,
    is_irreversible_capability,
    is_write_verification_required,
    write_capabilities,
)

__all__ = [
    "IRREVERSIBLE",
    "is_irreversible_capability",
    "is_write_verification_required",
    "write_capabilities",
]
```

Create `backend/src/services/verification/predicate.py`:

```python
"""The shared IRREVERSIBLE predicate + a deterministic per-capability classifier.

Spec §4.3: `IRREVERSIBLE = (reversible is False) OR (blast_radius in
{external_single, external_multiple, public})` — set-membership, no ordering.
Used by (a) the §4.5 verification trigger [here, Step 3] and later (b) the §4.3
gate override [Step 6] — the SAME predicate, extracted once.

The startup coverage gate needs a STATIC irreversibility classification (no LLM at
startup), so `reversible`/`blast_radius` become a deterministic per-capability
registry property. Rather than annotate ~45 external writes, we default an unlisted
write capability to IRREVERSIBLE (fail-closed) and list only the reversible-internal
exceptions — so a brand-new write capability can never silently skip verification.
"""

from __future__ import annotations

from src.integrations.capabilities import CAPABILITY_CATALOG, is_read_only_capability

# The external blast-radius tiers (no bare "external"; no ordering — set membership).
_EXTERNAL_BLAST_RADIUS = frozenset({"external_single", "external_multiple", "public"})


def IRREVERSIBLE(*, reversible: bool, blast_radius: str) -> bool:
    """The shared §4.3 irreversibility predicate over a (reversible, blast_radius)
    pair. Keyword-only so call sites read as `IRREVERSIBLE(reversible=..., blast_radius=...)`."""
    return (reversible is False) or (blast_radius in _EXTERNAL_BLAST_RADIUS)


# Write capabilities that are genuinely reversible AND internal (blast_radius
# self/internal) — the ONLY writes that skip read-back verification. Everything else
# not listed here defaults to IRREVERSIBLE (fail-closed). Keep this list explicit and
# audited: adding a capability here is a deliberate "this write needs no read-back."
REVERSIBLE_INTERNAL_CAPABILITIES: frozenset[str] = frozenset(
    {
        # Internal intelligence writes — self/internal blast radius, undoable.
        "internal.report_observation",
        "internal.ingest_event",
        "internal.update_entity",
        "internal.evaluate_policy",
        "internal.report_verdict",
        "internal.approve_action",
        "internal.update_cursor",
        "internal.extract_preferences",
        "internal.verify_run",
        "internal.update_execution",
        "internal.push_ui",
        "internal.store_memory",
        "internal.store_preference",
        # A local draft is not yet sent — internal + reversible.
        "email.draft",
        # Marking a message read is trivially reversible and low blast radius.
        "messaging.mark_read",
    }
)


def is_irreversible_capability(capability: str) -> bool:
    """Deterministic, LLM-free irreversibility classification for the startup
    coverage gate. Read-only caps are never irreversible; a write cap is
    NOT irreversible only if it is an explicit reversible-internal exception;
    everything else (incl. unknown/new write caps) is IRREVERSIBLE (fail-closed)."""
    if is_read_only_capability(capability):
        return False
    if capability in REVERSIBLE_INTERNAL_CAPABILITIES:
        return False
    return True


def is_write_verification_required(capability: str, risk) -> bool:
    """Fail-closed UNION of the static classifier and the per-step RiskAssessment.

    Verification is required if EITHER the deterministic registry says the
    capability is irreversible OR the per-step risk assessment does — so a 24h-cache
    LLM mislabel (reversible) cannot skip verification for a statically-irreversible
    capability, and an unclassified capability flagged by the LLM is still verified.
    `risk` may be None (e.g. no assessment) — then only the static classifier applies.
    """
    if is_irreversible_capability(capability):
        return True
    if risk is None:
        return False
    return IRREVERSIBLE(
        reversible=getattr(risk, "reversible", True),
        blast_radius=getattr(risk, "blast_radius", "self"),
    )


def write_capabilities() -> set[str]:
    """The set of write (non-read-only) capabilities in the catalog — the domain the
    post-condition + identity coverage gates validate."""
    return {cap for cap in CAPABILITY_CATALOG if not is_read_only_capability(cap)}
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `uv run pytest tests/verification/test_predicate.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/services/verification/ tests/verification/ && uv run ruff format src/services/verification/ tests/verification/
git add backend/src/services/verification/__init__.py backend/src/services/verification/predicate.py backend/tests/verification/__init__.py backend/tests/verification/test_predicate.py
git commit -m "feat(rebuild): shared IRREVERSIBLE predicate + deterministic per-capability classifier (Step 3)

The §4.3 predicate extracted once (verification uses it now; the Step-6 gate
override reuses it). is_irreversible_capability defaults an unlisted write cap to
IRREVERSIBLE (fail-closed); only reversible-internal caps are exempt.
is_write_verification_required is a fail-closed union of the static classifier and
the per-step RiskAssessment so a 24h-cache mislabel can't skip verification."
```

---

## Task 2: `completed_unverified` + step-level `partially_completed` + `TERMINAL_SUCCESS` (state machine)

Add the two net-new **step** statuses to `STEP_TRANSITIONS` and introduce the `TERMINAL_SUCCESS` membership set that Task 3 threads through the counters. No migration (correction #2). No run-level change (D6).

**Files:**
- Modify: `backend/src/services/execution_state.py`
- Test: `backend/tests/test_step_terminal_success.py` (+ extend `backend/tests/test_execution_state.py`)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_step_terminal_success.py`:

```python
"""New step statuses (completed_unverified, step-level partially_completed) and the
TERMINAL_SUCCESS membership set. Pure — exercises the transition tables directly."""

import pytest

from src.services.execution_state import (
    STEP_TRANSITIONS,
    TERMINAL_SUCCESS,
    InvalidTransitionError,
    transition_step,
)


class _Step:
    def __init__(self, status):
        self.status = status
        self.step_id = "stp_test"


def test_terminal_success_membership():
    assert TERMINAL_SUCCESS == frozenset({"completed", "completed_unverified"})
    assert "completed" in TERMINAL_SUCCESS
    assert "completed_unverified" in TERMINAL_SUCCESS
    assert "partially_completed" not in TERMINAL_SUCCESS  # success-but-diverged is NOT success
    assert "failed" not in TERMINAL_SUCCESS


def test_running_can_transition_to_each_new_status():
    for target in ("completed_unverified", "partially_completed"):
        step = _Step("running")
        transition_step(step, target)
        assert step.status == target


def test_completed_unverified_upgrades_to_completed():
    step = _Step("running")
    transition_step(step, "completed_unverified")
    transition_step(step, "completed")  # async confirm upgrade
    assert step.status == "completed"


def test_completed_unverified_can_diverge_to_partially_completed():
    step = _Step("running")
    transition_step(step, "completed_unverified")
    transition_step(step, "partially_completed")  # async divergence
    assert step.status == "partially_completed"


def test_partially_completed_is_terminal_for_a_step():
    step = _Step("running")
    transition_step(step, "partially_completed")
    assert STEP_TRANSITIONS["partially_completed"] == set()


def test_completed_stays_terminal():
    step = _Step("completed")
    with pytest.raises(InvalidTransitionError):
        transition_step(step, "completed_unverified")  # can't un-verify a confirmed step
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_step_terminal_success.py -q`
Expected: FAIL at import — `cannot import name 'TERMINAL_SUCCESS'`.

- [ ] **Step 3: Edit `execution_state.py`**

In `backend/src/services/execution_state.py`, the current `STEP_TRANSITIONS` (the `"running"` entry and the terminal entries) is:

```python
# TaskStep allowed transitions (10 statuses)
STEP_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"ready", "skipped", "blocked"},
    "ready": {"running", "skipped"},
    "running": {
        "completed",
        "failed",
        "waiting_approval",
        "awaiting_input",
        "skipped",
        "timed_out",
        "cancelled",
    },
    "waiting_approval": {"running", "skipped"},
    "awaiting_input": {"running", "skipped", "cancelled"},
    # NOTE: there is no step-level ``awaiting_reauth`` state. OAuth re-auth
    # deferral is a RUN-level concern (see RUN_TRANSITIONS): the defer path
    # resets the blocked step to ``ready`` and parks the *run* in
    # ``awaiting_reauth``. A step never enters awaiting_reauth (M2).
    "blocked": {"pending", "skipped"},
    "completed": set(),
    "failed": {"pending"},  # Retry: failed → pending
    "skipped": set(),
    "cancelled": set(),
    "timed_out": {"pending", "skipped"},
}
```

Change it to (add `completed_unverified` + `partially_completed` as `running` targets; add their own entries; add the `TERMINAL_SUCCESS` constant below):

```python
# TaskStep allowed transitions.
# Step 3 adds two NET-NEW terminal-ish statuses (spec §4.5):
#   completed_unverified — write fired but read-back not yet confirmed (non-terminal
#     SUCCESS: upgradeable to completed on async confirm, or partially_completed on
#     async divergence).
#   partially_completed  — read-back CONTRADICTED the expected effect (surfaced +
#     escalate-first). Terminal for the step (compensation is a user-triggered re-run
#     that creates new steps, not an onward transition of this one).
STEP_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"ready", "skipped", "blocked"},
    "ready": {"running", "skipped"},
    "running": {
        "completed",
        "completed_unverified",
        "partially_completed",
        "failed",
        "waiting_approval",
        "awaiting_input",
        "skipped",
        "timed_out",
        "cancelled",
    },
    "waiting_approval": {"running", "skipped"},
    "awaiting_input": {"running", "skipped", "cancelled"},
    # NOTE: there is no step-level ``awaiting_reauth`` state. OAuth re-auth
    # deferral is a RUN-level concern (see RUN_TRANSITIONS): the defer path
    # resets the blocked step to ``ready`` and parks the *run* in
    # ``awaiting_reauth``. A step never enters awaiting_reauth (M2).
    "blocked": {"pending", "skipped"},
    "completed": set(),
    # Deferred-read executor upgrades to completed on confirm, or partially_completed
    # on post-turn divergence.
    "completed_unverified": {"completed", "partially_completed"},
    "partially_completed": set(),
    "failed": {"pending"},  # Retry: failed → pending
    "skipped": set(),
    "cancelled": set(),
    "timed_out": {"pending", "skipped"},
}

# Step/run statuses that count as a terminal SUCCESS for progress, dependency
# satisfaction, and rollup counters (spec §4.5: "Replace every literal
# status == 'completed' counter with TERMINAL_SUCCESS membership, or a run whose last
# step is completed_unverified never reaches 100%"). NOTE: partially_completed is
# deliberately EXCLUDED — a diverged write is not a success. This set gates step-level
# counting only; run-level `completed` is unchanged (D6).
TERMINAL_SUCCESS: frozenset[str] = frozenset({"completed", "completed_unverified"})
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `uv run pytest tests/test_step_terminal_success.py tests/test_execution_state.py -q`
Expected: all PASS (the existing `test_execution_state.py` contract tests still pass — the change is purely additive to `STEP_TRANSITIONS`).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/services/execution_state.py tests/test_step_terminal_success.py
git add backend/src/services/execution_state.py backend/tests/test_step_terminal_success.py
git commit -m "feat(rebuild): completed_unverified + step partially_completed + TERMINAL_SUCCESS (Step 3)

Two net-new STEP statuses in STEP_TRANSITIONS: completed_unverified (non-terminal
success, upgradeable) and step-level partially_completed (read-back contradicted,
terminal). TERMINAL_SUCCESS = {completed, completed_unverified} for step-level
counting. No migration — statuses are String(32) varchars; run-level unchanged."
```

---

## Task 3: Thread `TERMINAL_SUCCESS` through step-completion counters + gates (blast radius)

Replace every **step-level** `status == "completed"` that COUNTS or GATES completion with `status in TERMINAL_SUCCESS`, so a `completed_unverified` step still unblocks dependents, feeds progress to 100%, and appears in surface counts. Extraction pass established the exact sites. **Do NOT touch** `idempotency/ledger.py:105` (ledger status ≠ step status, correction #8), event-name constants, or RUN-level `== "completed"` sites (D6).

**Files:**
- Modify: `backend/src/services/step_graph_store.py`, `outcome_learner.py`, `runtime_projection.py`, `dag_runner.py`, `surface_builder.py`, `execution_surface_emitter.py`, `verifier.py`, `graph_executor.py`, `surface_detail_builders/plan.py`, `surface_detail_builders/lists.py`, `api/routes_history.py`
- Test: `backend/tests/test_terminal_success_threading.py` (real-DB, skip-if-no-Postgres)

- [ ] **Step 1: Write the failing real-DB test**

Create `backend/tests/test_terminal_success_threading.py` (mirrors the Step-2 real-DB harness — own engine, NullPool, ULID-suffixed seed, CASCADE cleanup, skip-if-unreachable):

```python
"""Real-DB proof that a completed_unverified step counts as done: it unblocks a
dependent step (DAG readiness) and drives run progress toward 100%. Skips when
Postgres is unreachable."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.task_graph import TaskRun, TaskStep
from src.models.users import User, Workspace
from src.services.runtime_projection import RuntimeProjectionService
from src.services.step_graph_store import StepGraphStore


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
async def _run_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id, workspace_id = f"usr_{suffix}", f"ws_{suffix}"
    run_id = f"run_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"ts-{suffix}@example.com", display_name="ts"))
            db.add(Workspace(workspace_id=workspace_id, name="ts-ws", owner_user_id=user_id))
            db.add(
                TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    plan_id=f"plan_{suffix}",
                    status="running",
                )
            )
            await db.commit()
        yield factory, run_id, workspace_id, user_id, suffix
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_completed_unverified_step_unblocks_dependent():
    async with _run_env() as (factory, run_id, workspace_id, user_id, suffix):
        async with factory() as db:
            db.add(
                TaskStep(
                    step_id=f"stp_a_{suffix}",
                    run_id=run_id,
                    task_id="A",
                    status="completed_unverified",
                    depends_on=[],
                )
            )
            db.add(
                TaskStep(
                    step_id=f"stp_b_{suffix}",
                    run_id=run_id,
                    task_id="B",
                    status="pending",
                    depends_on=["A"],
                )
            )
            await db.commit()
            store = StepGraphStore(db)
            ready = await store.get_ready_steps(run_id)
            # B depends on A; A is completed_unverified (a success) -> B is ready.
            assert any(s.task_id == "B" for s in ready), "unverified-done step failed to unblock B"


async def test_progress_counts_completed_unverified():
    async with _run_env() as (factory, run_id, workspace_id, user_id, suffix):
        async with factory() as db:
            db.add(
                TaskStep(step_id=f"stp_x_{suffix}", run_id=run_id, task_id="X",
                         status="completed", depends_on=[])
            )
            db.add(
                TaskStep(step_id=f"stp_y_{suffix}", run_id=run_id, task_id="Y",
                         status="completed_unverified", depends_on=[])
            )
            await db.commit()
            proj = RuntimeProjectionService(db, workspace_id)
            active = await proj.get_active_runs()
            row = next(r for r in active if r["run_id"] == run_id)
            assert row["progress_pct"] == 100, f"unverified step excluded from progress: {row}"
```

> **Note for the implementer:** verify the exact `StepGraphStore` / `RuntimeProjectionService` constructor signatures and the `TaskStep` dependency column name (`depends_on` vs `dependencies`) by reading the files before running — adjust the seed to match. The assertions (unblock + 100%) are the contract; the seeding shape follows the models.

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_terminal_success_threading.py -q`
Expected: FAIL — `get_ready_steps` excludes the `completed_unverified` dependency (B not ready) and `progress_pct` is 50, not 100. (Or SKIP if Postgres down — then rely on Step 4's targeted unit assertions.)

- [ ] **Step 3: Make the edits — swap `== "completed"` for `in TERMINAL_SUCCESS` at each step-level site**

For each site below, add the import `from src.services.execution_state import TERMINAL_SUCCESS` (if not already imported in that module) and change the comparison. **Exact sites (verified 2026-07-05):**

1. `backend/src/services/step_graph_store.py:135` — DAG dependency satisfaction (**CRITICAL**):
   ```python
   # BEFORE
   completed_ids = {s.step_id for s in all_steps if s.status == "completed"}
   # AFTER
   completed_ids = {s.step_id for s in all_steps if s.status in TERMINAL_SUCCESS}
   ```
2. `backend/src/services/step_graph_store.py:225` — checkpoint captures completed step outputs:
   ```python
   # BEFORE:  if s.status == "completed"
   # AFTER:   if s.status in TERMINAL_SUCCESS
   ```
3. `backend/src/services/dag_runner.py:189` — surface progress `_done_count`:
   ```python
   # BEFORE
   _done_count = sum(1 for s in _all_for_surface if s.status == "completed")
   # AFTER
   _done_count = sum(1 for s in _all_for_surface if s.status in TERMINAL_SUCCESS)
   ```
4. `backend/src/services/outcome_learner.py:87` — memory writeback source steps:
   ```python
   # BEFORE
   completed = [s for s in all_steps if s.status == "completed" and s.output_data]
   # AFTER
   completed = [s for s in all_steps if s.status in TERMINAL_SUCCESS and s.output_data]
   ```
5. `backend/src/services/verifier.py:192` — `all_steps_completed` success condition:
   ```python
   # BEFORE
   return all(s.status == "completed" for s in steps)
   # AFTER
   return all(s.status in TERMINAL_SUCCESS for s in steps)
   ```
   (Leave `verifier.py:188` — that is a RUN-status check `run.status in ("completed", "partially_completed")` — unchanged, D6.)
6. `backend/src/services/graph_executor.py:428` — checkpoint reconciliation `actual_completed`:
   ```python
   # BEFORE
   actual_completed = {s.step_id for s in actual_steps if s.status == "completed"}
   # AFTER
   actual_completed = {s.step_id for s in actual_steps if s.status in TERMINAL_SUCCESS}
   ```
7. `backend/src/services/runtime_projection.py:50` — per-run progress counter:
   ```python
   # BEFORE
   completed = sum(1 for s in steps if s.status == "completed")
   # AFTER
   completed = sum(1 for s in steps if s.status in TERMINAL_SUCCESS)
   ```
   (Leave `runtime_projection.py:201` — that is `TaskRun.status == "completed"`, a RUN-level query — unchanged, D6.)
8. `backend/src/services/surface_builder.py:241` — surface step progress count:
   ```python
   # BEFORE
   completed = sum(1 for s in steps if s.status == "completed")
   # AFTER
   completed = sum(1 for s in steps if s.status in TERMINAL_SUCCESS)
   ```
9. `backend/src/services/execution_surface_emitter.py:264` — summary-surface completed count:
   ```python
   # BEFORE
   completed_count = sum(1 for s in steps if s.status == "completed")
   # AFTER
   completed_count = sum(1 for s in steps if s.status in TERMINAL_SUCCESS)
   ```
   (Leave `:276/:286/:288/:290` — those branch on RUN status for title/variant; unchanged, D6.)
10. `backend/src/services/surface_detail_builders/plan.py:65` — completed-step count in run summary:
    ```python
    # BEFORE
    completed = sum(1 for s in steps if s.status == "completed")
    # AFTER
    completed = sum(1 for s in steps if s.status in TERMINAL_SUCCESS)
    ```
    (Leave `plan.py:53` per-step `variant = "success" if step.status == "completed"` — cosmetic; optionally add `in TERMINAL_SUCCESS` so an unverified step still renders green. Include it for consistency.)
11. `backend/src/api/routes_history.py:142` — history completed-step count:
    ```python
    # BEFORE
    completed_step_count = sum(1 for s in steps if s.status == "completed")
    # AFTER
    completed_step_count = sum(1 for s in steps if s.status in TERMINAL_SUCCESS)
    ```

**Explicitly DO NOT change** (verified out-of-scope): `idempotency/ledger.py:105` (ledger's own status), `event_families.py` (event-name constants), `surface_detail_builders/lists.py:338` and `runtime_projection.py:201` and `execution_surface_emitter.py:276-290` (RUN-level `== "completed"`), `step_graph_store.py`/`verifier.py:188` run-level checks. The `surface_detail_builders/lists.py` per-step variant sites (`:38/:69`) are cosmetic — including them is optional; if included, use `in TERMINAL_SUCCESS`. Document whichever you choose in the commit.

- [ ] **Step 4: Add a no-DB unit assertion for the CRITICAL dependency-satisfaction path**

Append to `backend/tests/test_step_terminal_success.py` a pure check that the readiness helper logic uses membership (guards the most safety-critical site even when Postgres is down):

```python
def test_terminal_success_covers_dependency_satisfaction_semantics():
    # A completed_unverified predecessor must satisfy a dependency the same as completed.
    done = {"completed", "completed_unverified"}
    assert all(s in TERMINAL_SUCCESS for s in done)
    assert "partially_completed" not in TERMINAL_SUCCESS  # a diverged step does NOT satisfy deps
    assert "failed" not in TERMINAL_SUCCESS
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_terminal_success_threading.py tests/test_step_terminal_success.py -q`
Expected: real-DB tests PASS (Postgres up); the unit assertion PASSES.

- [ ] **Step 6: Run the affected-module suites to catch regressions**

Run:
```bash
uv run pytest tests/ --ignore=tests/e2e -q -k "projection or surface or graph or dag or outcome or verifier or history or step_graph" 2>&1 | tail -15
```
Expected: no new failures vs baseline.

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check src/services/ src/api/routes_history.py tests/test_terminal_success_threading.py
git add -A
git commit -m "feat(rebuild): thread TERMINAL_SUCCESS through step-completion counters (Step 3)

completed_unverified now counts as done for DAG dependency satisfaction
(step_graph_store), progress %, surface/history counts, memory writeback, and
checkpoint reconciliation — so an unverified-but-fired step unblocks dependents and
reaches 100%. Deliberately NOT changed: idempotency ledger status (effect-fired,
not verified), event-name constants, and all RUN-level == completed sites."
```

---

## Task 4: Frontend — render `completed_unverified` as "sent (unconfirmed)"

`StepState.status` is a **closed** TypeScript union — TS rejects the new value until it's added. `stepStatusIcon` has a `default` fall-through (would render `○ pending`), and `step-list.tsx` filters on `=== "completed"`. Give the new status a distinct "done-but-unconfirmed" glyph and route the done-filters through a shared helper.

**Files:**
- Modify: `frontend/src/lib/a2ui-types.ts`, `frontend/src/components/a2ui/components/step-presentation.tsx`, `frontend/src/components/a2ui/components/step-list.tsx`

- [ ] **Step 1: Extend the status union + add a done-helper**

In `frontend/src/lib/a2ui-types.ts`, the current `StepState.status` (line ~173) is:

```ts
  status: "pending" | "executing" | "completed" | "failed" | "approval_needed" | "user_action";
```

Change it to:

```ts
  status:
    | "pending"
    | "executing"
    | "completed"
    | "completed_unverified"
    | "partially_completed"
    | "failed"
    | "approval_needed"
    | "user_action";
```

Then add, near the top of the same file (after the imports / before `StepState`), a shared done-set + helper mirroring the backend `TERMINAL_SUCCESS`:

```ts
// Mirrors backend execution_state.TERMINAL_SUCCESS: a step counts as "done" for
// progress/grouping whether it is confirmed (completed) or fired-but-unconfirmed
// (completed_unverified). partially_completed (read-back diverged) is NOT done.
export const STEP_TERMINAL_SUCCESS = ["completed", "completed_unverified"] as const;

export function isStepDone(status: string): boolean {
  return (STEP_TERMINAL_SUCCESS as readonly string[]).includes(status);
}
```

- [ ] **Step 2: Add the icon case**

In `frontend/src/components/a2ui/components/step-presentation.tsx`, the current `completed` case (lines ~36-38) is:

```tsx
    case "completed":
    case "ok":
      return { icon: "✓", className: statusTextColor("completed") };
```

Add a distinct case immediately after it (a checkmark with a "pending confirmation" hint — uses the amber/awaiting color to read as "done, unconfirmed"):

```tsx
    case "completed":
    case "ok":
      return { icon: "✓", className: statusTextColor("completed") };
    case "completed_unverified":
      // Fired, read-back not yet confirmed — "sent (unconfirmed)".
      return { icon: "✓?", className: statusTextColor("awaiting_approval") };
    case "partially_completed":
      // Read-back contradicted the expected effect — surfaced for the user.
      return { icon: "⚠", className: statusTextColor("failed") };
```

- [ ] **Step 3: Route the done-filters through the helper**

In `frontend/src/components/a2ui/components/step-list.tsx`, import the helper:

```ts
import { isStepDone } from "@/lib/a2ui-types";
```

Then change the completion filters/counts (lines ~104, 111, 235 — the grouping/counting ones) from `s.status === "completed"` to `isStepDone(s.status)`. Example (line ~104):

```ts
// BEFORE
const completedSteps = steps.filter((s) => s.status === "completed");
// AFTER
const completedSteps = steps.filter((s) => isStepDone(s.status));
```

> **Note for the implementer:** lines 133/185/218 are per-step *render* branches (icon/label for one step). Those are handled by the new icon cases in Step 2 — leave them keying on the literal status so `completed` and `completed_unverified` render distinctly. Only the *grouping/counting* filters (104, 111, 235) use `isStepDone`. Verify each site's intent before editing.

- [ ] **Step 4: Build + lint the frontend**

Run (from `frontend/`):
```bash
npm run lint 2>&1 | tail -10
npm run build 2>&1 | tail -15
```
Expected: lint clean; build succeeds (the closed-union edit is what makes TS accept the new status — a missed usage site would fail the build here).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/a2ui-types.ts frontend/src/components/a2ui/components/step-presentation.tsx frontend/src/components/a2ui/components/step-list.tsx
git commit -m "feat(rebuild): render completed_unverified + partially_completed step states (Step 3)

Adds both statuses to the closed StepState union, a distinct '✓?' (sent,
unconfirmed) / '⚠' (diverged) icon, and an isStepDone() helper mirroring backend
TERMINAL_SUCCESS so grouping/counting treats an unverified-but-fired step as done."
```

---

## Task 5: Post-condition registry + startup coverage gate

A capability-keyed post-condition registry (mirrors `IDENTITY_SPECS`) with an explicit `UNVERIFIABLE_CAPABILITIES` escape valve (D5), plus a coverage validator that mirrors `validate_registry()` and a startup hard-gate wired into `app.py` (mirrors the existing registry-validation block).

**Files:**
- Create: `backend/src/services/verification/post_conditions.py`
- Modify: `backend/src/api/app.py`
- Test: `backend/tests/verification/test_post_conditions.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/verification/test_post_conditions.py`:

```python
"""Post-condition registry + coverage gate. Every IRREVERSIBLE write capability must
be registered — either with a PostCondition (real read-back) or explicitly marked
UNVERIFIABLE. The coverage validator mirrors validate_registry (returns list[str],
never raises)."""

from src.services.verification.post_conditions import (
    POST_CONDITIONS,
    UNVERIFIABLE_CAPABILITIES,
    validate_post_condition_coverage,
)
from src.services.verification.predicate import is_irreversible_capability, write_capabilities


def test_real_catalog_has_full_coverage():
    # The live catalog must pass the coverage gate (startup would abort otherwise).
    errors = validate_post_condition_coverage(write_capabilities())
    assert errors == [], f"irreversible capabilities missing a post-condition: {errors}"


def test_missing_irreversible_capability_is_flagged():
    errors = validate_post_condition_coverage({"brand.new_irreversible_write"})
    assert any("brand.new_irreversible_write" in e for e in errors)


def test_reversible_internal_capability_needs_no_post_condition():
    # internal.store_memory is reversible-internal -> not irreversible -> not required
    assert is_irreversible_capability("internal.store_memory") is False
    errors = validate_post_condition_coverage({"internal.store_memory"})
    assert errors == []


def test_every_registered_capability_is_actually_a_write():
    # No read-only cap should carry a post-condition (would be dead config).
    for cap in list(POST_CONDITIONS) + list(UNVERIFIABLE_CAPABILITIES):
        assert is_irreversible_capability(cap), f"{cap} is not irreversible but is registered"


def test_registries_are_disjoint():
    # A capability is EITHER verifiable (POST_CONDITIONS) or explicitly UNVERIFIABLE.
    assert not (set(POST_CONDITIONS) & UNVERIFIABLE_CAPABILITIES)


def test_post_condition_has_read_capability_and_assertion():
    for cap, pc in POST_CONDITIONS.items():
        assert pc.read_capability, f"{cap} post-condition missing read_capability"
        assert callable(pc.assertion), f"{cap} post-condition assertion not callable"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/verification/test_post_conditions.py -q`
Expected: FAIL at import — `No module named 'src.services.verification.post_conditions'`.

- [ ] **Step 3: Write `post_conditions.py`**

Create `backend/src/services/verification/post_conditions.py`:

```python
"""Per-capability post-condition registry + the startup coverage gate (spec §4.5).

A PostCondition declares HOW to read back an irreversible write's effect: a
`read_capability` (invoked through the tool-execution seam) and an `assertion` over
its result + the original write's args/output. Where no deterministic read exists,
the capability is listed in UNVERIFIABLE_CAPABILITIES instead (explicit → the effect
is honestly marked completed_unverified, never silently).

Coverage invariant (mirrors validate_registry): every IRREVERSIBLE write capability
MUST be in POST_CONDITIONS or UNVERIFIABLE_CAPABILITIES — enforced as a startup error
so a new write capability can't silently skip verification on the irreversible path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.services.verification.predicate import is_irreversible_capability


@dataclass(frozen=True, slots=True)
class PostCondition:
    """A read-back check for one write capability.

    read_capability: the capability to invoke to observe the effect (e.g.
      "calendar.get" after "calendar.create"). Run through the injected
      execute_tool_fn by ReadBackVerifier.
    read_args: build the read tool's input from the write's (input_data, output).
    assertion: given the read result, return True iff the expected effect is present.
    """

    read_capability: str
    read_args: Callable[[dict, dict], dict]
    assertion: Callable[[object, dict, dict], bool]
    description: str = ""


def _event_created(read_result, write_input: dict, write_output: dict) -> bool:
    """A created calendar event is confirmed when the read-back returns an event
    whose id matches the one the write reported."""
    created_id = (write_output or {}).get("event_id") or (write_output or {}).get("id")
    if not created_id:
        return False
    items = read_result if isinstance(read_result, list) else [read_result]
    return any(isinstance(it, dict) and it.get("id") == created_id for it in items)


# Capabilities WITH a deterministic read-back. Kept minimal for the MVP — one worked
# example proving the mechanism end-to-end (mocked in tests). Real per-connector
# read-backs are added over time; until then a capability lives in
# UNVERIFIABLE_CAPABILITIES (honest completed_unverified), never silently skipped.
POST_CONDITIONS: dict[str, PostCondition] = {
    "calendar.create": PostCondition(
        read_capability="calendar.get",
        read_args=lambda write_input, write_output: {
            "event_id": (write_output or {}).get("event_id")
            or (write_output or {}).get("id"),
            "calendar_id": (write_input or {}).get("calendar_id"),
        },
        assertion=_event_created,
        description="Read the created event back by id to confirm it landed.",
    ),
}


# IRREVERSIBLE write capabilities with NO deterministic read-back today (eventually
# consistent APIs, no stable id returned, or no read capability). These resolve to
# completed_unverified — an explicit, audited decision, NOT a silent gap. Adding a
# real read-back = move the capability from here into POST_CONDITIONS.
UNVERIFIABLE_CAPABILITIES: frozenset[str] = frozenset(
    {
        "email.send",
        "email.reply",
        "email.delete",
        "calendar.update",
        "calendar.delete",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        "repo.review_pr",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.delete",
        "issue.transition",
        "issue.sub_issue",
        "doc.create",
        "doc.update",
        "doc.delete",
        "doc.comment",
        "doc.append",
        "doc.move",
        "doc.update_block",
        "doc.delete_block",
        "doc.create_datasource",
        "doc.update_datasource",
        "doc.drive_create",
        "doc.drive_delete",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "workflow.delete",
        "workflow.create_issues",
        "workflow.bulk_update",
        "workflow.update_comment",
        "workflow.delete_comment",
        "workflow.resolve_comment",
        "workflow.unresolve_comment",
        "workflow.create_project",
        "workflow.create_milestone",
        "workflow.update_milestone",
        "workflow.delete_milestone",
        "workflow.create_customer_need",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        "filesystem.write",
        "filesystem.move",
        "browser.open",
        "browser.click",
        "browser.type",
        "browser.submit",
        "browser.execute",
        "browser.install",
    }
)


def validate_post_condition_coverage(write_capabilities: set[str]) -> list[str]:
    """Return error strings for IRREVERSIBLE write capabilities that are NOT
    registered (neither a PostCondition nor an explicit UNVERIFIABLE marker).

    Mirrors validate_registry(): returns list[str] (empty = valid), never raises.
    The caller (startup gate) decides fatality.
    """
    errors: list[str] = []
    registered = set(POST_CONDITIONS) | UNVERIFIABLE_CAPABILITIES
    for cap in sorted(write_capabilities):
        if not is_irreversible_capability(cap):
            continue
        if cap not in registered:
            errors.append(
                f"IRREVERSIBLE capability '{cap}' has no registered post-condition "
                "(add a PostCondition to POST_CONDITIONS or mark it UNVERIFIABLE)"
            )
    return errors
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `uv run pytest tests/verification/test_post_conditions.py -q`
Expected: all PASS. **If `test_real_catalog_has_full_coverage` fails**, the failure message lists exactly which irreversible write capabilities are unregistered — add each to `UNVERIFIABLE_CAPABILITIES` (or `POST_CONDITIONS` if a real read-back exists). This is the coverage gate doing its job; the catalog list above was derived from `CAPABILITY_CATALOG` on 2026-07-05 but **re-run to confirm nothing drifted**.

- [ ] **Step 5: Wire the startup coverage gate into `app.py`**

In `backend/src/api/app.py`, immediately **after** the existing registry-validation block (which ends with `logger.info("Registry validation passed")` at ~line 184), add a mirrored post-condition-coverage gate:

```python
        # Post-condition coverage gate (spec §4.5): every IRREVERSIBLE write
        # capability must have a registered read-back post-condition (or be
        # explicitly marked UNVERIFIABLE). Fail closed — a new write capability must
        # not serve traffic able to silently skip verification on the irreversible
        # path. Same emergency bypass as registry validation.
        if settings.skip_registry_validation:
            logger.warning("Post-condition coverage check SKIPPED (skip_registry_validation)")
        else:
            try:
                from src.services.verification.post_conditions import (
                    validate_post_condition_coverage,
                )
                from src.services.verification.predicate import write_capabilities

                pc_errors = validate_post_condition_coverage(write_capabilities())
            except Exception as exc:
                logger.error("Post-condition coverage check failed to run", exc_info=True)
                raise RuntimeError("Post-condition coverage check could not run") from exc

            if pc_errors:
                for err in pc_errors:
                    logger.error("Post-condition coverage: %s", err)
                raise RuntimeError(
                    f"Post-condition coverage found {len(pc_errors)} error(s) — register a "
                    "post-condition or mark UNVERIFIABLE, or set "
                    "JARVIS_SKIP_REGISTRY_VALIDATION=true to bypass."
                )
            logger.info("Post-condition coverage passed")
```

> **Note for the implementer:** confirm the exact indentation + the surrounding `settings` variable name by reading `app.py:161-184` first; splice this block to match. It reuses the existing `settings.skip_registry_validation` flag by design (one emergency bypass for all startup registry gates).

- [ ] **Step 6: Add a startup-gate integration test**

Append to `backend/tests/verification/test_post_conditions.py`:

```python
def test_coverage_gate_is_exhaustive_over_real_catalog():
    # Belt-and-suspenders: no irreversible write capability in the real catalog is
    # left unregistered (the exact set the startup gate checks).
    from src.services.verification.predicate import is_irreversible_capability, write_capabilities

    registered = set(POST_CONDITIONS) | UNVERIFIABLE_CAPABILITIES
    missing = [
        c for c in write_capabilities() if is_irreversible_capability(c) and c not in registered
    ]
    assert missing == [], f"unregistered irreversible capabilities: {missing}"
```

- [ ] **Step 7: Run + lint + commit**

Run: `uv run pytest tests/verification/test_post_conditions.py -q`
Expected: all PASS.

```bash
uv run ruff check src/services/verification/post_conditions.py src/api/app.py tests/verification/test_post_conditions.py
git add backend/src/services/verification/post_conditions.py backend/src/api/app.py tests/verification/test_post_conditions.py
git commit -m "feat(rebuild): post-condition registry + startup coverage gate (Step 3)

Capability-keyed POST_CONDITIONS (real read-backs) + explicit UNVERIFIABLE_CAPABILITIES
escape valve. validate_post_condition_coverage mirrors validate_registry (list[str],
never raises); wired into app.py as a fail-closed startup gate behind the existing
skip_registry_validation flag. Every irreversible write cap must be registered —
a new one can't silently skip verification."
```

---

## Task 6: The inline read-back verifier + risk-gated `finalize_step` + characterization test

The core. `ReadBackVerifier` turns a step's (capability, input, output, risk) into a `VerifyVerdict`. `dag_runner.execute_step` computes the verdict for write steps and passes the resulting status into `finalize_step`, which now transitions to that status instead of a hardcoded `"completed"`. The characterization test forbids any write path emitting a terminal status without a verdict.

**Files:**
- Create: `backend/src/services/verification/readback.py`
- Modify: `backend/src/services/dag_runner.py` (`finalize_step` signature; `execute_step` verdict computation), `backend/src/services/step_runner.py` (add `run_readback`)
- Modify: `backend/src/services/verification/__init__.py` (export the new surface)
- Test: `backend/tests/verification/test_readback.py`, `backend/tests/test_finalize_verification.py`

- [ ] **Step 1: Write the failing verifier test**

Create `backend/tests/verification/test_readback.py`:

```python
"""ReadBackVerifier: maps (capability, input, output, risk) -> VerifyVerdict.
The connector read is an injected seam (mocked). No DB, no network."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.verification.readback import ReadBackVerifier, VerifyVerdict


def _risk(reversible=False, blast_radius="external_single", risk_level="high"):
    return SimpleNamespace(
        reversible=reversible, blast_radius=blast_radius, risk_level=risk_level
    )


async def test_reversible_internal_write_is_not_verified_and_returns_confirmed():
    # Not irreversible -> no read-back required -> trivially confirmed (marks completed).
    v = ReadBackVerifier(read_fn=AsyncMock())
    verdict = await v.verify_step(
        capability="internal.store_memory",
        write_input={},
        write_output={},
        risk=_risk(reversible=True, blast_radius="internal"),
    )
    assert verdict == VerifyVerdict.CONFIRMED
    v._read_fn.assert_not_awaited()


async def test_unverifiable_capability_returns_unverified_without_read():
    v = ReadBackVerifier(read_fn=AsyncMock())
    verdict = await v.verify_step(
        capability="email.send",
        write_input={"to": "a@b.com"},
        write_output={"message_id": "m1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.UNVERIFIED
    v._read_fn.assert_not_awaited()  # no deterministic read exists


async def test_post_condition_confirmed_when_readback_matches():
    read_fn = AsyncMock(return_value={"id": "evt_1"})
    v = ReadBackVerifier(read_fn=read_fn)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(reversible=False, blast_radius="external_multiple"),
    )
    assert verdict == VerifyVerdict.CONFIRMED
    read_fn.assert_awaited_once()


async def test_post_condition_contradicted_when_readback_absent():
    read_fn = AsyncMock(return_value=[])  # event not found on read-back
    v = ReadBackVerifier(read_fn=read_fn)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.CONTRADICTED


async def test_post_condition_read_error_is_unverified_not_contradicted():
    # A failed read-back != a contradicted effect. Fail SAFE to unverified.
    read_fn = AsyncMock(side_effect=RuntimeError("connector down"))
    v = ReadBackVerifier(read_fn=read_fn)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.UNVERIFIED


async def test_no_seam_available_is_unverified():
    # read_fn=None (seam unavailable / verify budget exhausted) -> unverified.
    v = ReadBackVerifier(read_fn=None)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.UNVERIFIED
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/verification/test_readback.py -q`
Expected: FAIL at import — `No module named 'src.services.verification.readback'`.

- [ ] **Step 3: Write `readback.py`**

Create `backend/src/services/verification/readback.py`:

```python
"""The inline read-back verifier (spec §4.5).

Given a write step's (capability, input, output, risk), decide whether its expected
post-condition holds by reading the effect back BEFORE the step is marked terminal:
  CONFIRMED     — read-back observed the effect (or the write is not irreversible).
  CONTRADICTED  — read-back ran and the effect is ABSENT (surface + escalate-first).
  UNVERIFIED    — no deterministic read exists / seam unavailable / read errored /
                  budget exhausted (honest completed_unverified, upgradeable later).

The connector read is an injected async seam `read_fn(read_capability, read_args)`;
production wires it to the tool-execution path (reads bypass the Step-1 ledger, so no
double-fire), tests mock it. A failed read is UNVERIFIED, never CONTRADICTED — a
verification outage must not false-fail a correct action (spec §7 false-negative risk).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Awaitable, Callable

from src.services.verification.post_conditions import (
    POST_CONDITIONS,
    UNVERIFIABLE_CAPABILITIES,
)
from src.services.verification.predicate import is_write_verification_required

logger = logging.getLogger(__name__)

ReadFn = Callable[[str, dict], Awaitable[object]]


class VerifyVerdict(str, Enum):
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


class ReadBackVerifier:
    def __init__(self, read_fn: ReadFn | None):
        self._read_fn = read_fn

    async def verify_step(
        self, *, capability: str, write_input: dict, write_output: dict, risk
    ) -> VerifyVerdict:
        # Not an irreversible write -> no read-back required -> trivially confirmed.
        if not is_write_verification_required(capability, risk):
            return VerifyVerdict.CONFIRMED

        pc = POST_CONDITIONS.get(capability)
        if pc is None:
            # Registered as UNVERIFIABLE, or (fail-closed) not registered at all — the
            # startup coverage gate guarantees irreversible caps ARE registered, so an
            # unregistered one here is an anomaly worth logging, resolved to unverified.
            if capability not in UNVERIFIABLE_CAPABILITIES:
                logger.warning(
                    "Irreversible capability %s has no post-condition at verify time "
                    "(coverage gate should prevent this) — resolving to unverified",
                    capability,
                )
            return VerifyVerdict.UNVERIFIED

        if self._read_fn is None:
            return VerifyVerdict.UNVERIFIED  # seam unavailable / budget exhausted

        try:
            read_args = pc.read_args(write_input or {}, write_output or {})
            read_result = await self._read_fn(pc.read_capability, read_args)
        except Exception:
            # A failed read-back is NOT a contradicted effect — fail safe.
            logger.warning(
                "Read-back for %s errored — resolving to unverified", capability, exc_info=True
            )
            return VerifyVerdict.UNVERIFIED

        try:
            ok = pc.assertion(read_result, write_input or {}, write_output or {})
        except Exception:
            logger.warning(
                "Post-condition assertion for %s errored — unverified", capability, exc_info=True
            )
            return VerifyVerdict.UNVERIFIED

        return VerifyVerdict.CONFIRMED if ok else VerifyVerdict.CONTRADICTED


def verdict_to_step_status(verdict: VerifyVerdict) -> str:
    """Map a verdict to the step's terminal status (spec §4.5 three-state model)."""
    return {
        VerifyVerdict.CONFIRMED: "completed",
        VerifyVerdict.CONTRADICTED: "partially_completed",
        VerifyVerdict.UNVERIFIED: "completed_unverified",
    }[verdict]
```

Update `backend/src/services/verification/__init__.py` to also export the verifier:

```python
from src.services.verification.predicate import (
    IRREVERSIBLE,
    is_irreversible_capability,
    is_write_verification_required,
    write_capabilities,
)
from src.services.verification.readback import (
    ReadBackVerifier,
    VerifyVerdict,
    verdict_to_step_status,
)

__all__ = [
    "IRREVERSIBLE",
    "is_irreversible_capability",
    "is_write_verification_required",
    "write_capabilities",
    "ReadBackVerifier",
    "VerifyVerdict",
    "verdict_to_step_status",
]
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `uv run pytest tests/verification/test_readback.py -q`
Expected: all PASS.

- [ ] **Step 5: Make `finalize_step` accept an explicit status**

In `backend/src/services/dag_runner.py`, `finalize_step` currently hardcodes the terminal transition. The current signature + the transition line (`:650`) are:

```python
    async def finalize_step(
        self,
        run: TaskRun,
        step: TaskStep,
        output: dict | None,
        elapsed_ms: int,
    ) -> None:
        """Mark step completed, emit events, checkpoint."""
        ...
        transition_step(step, "completed")          # ← LINE 650
        step.output_data = output
        step.completed_at = datetime.now(timezone.utc)
        await self._db.flush()

        result = StepResult(
            step_id=step.step_id,
            status="completed",                       # ← LINE 657
            output_data=output,
            duration_ms=elapsed_ms,
        )
        ...
```

Change the signature to accept a `status` (default `"completed"` so any non-write caller is unaffected) and use it for both the transition and the `StepResult`:

```python
    async def finalize_step(
        self,
        run: TaskRun,
        step: TaskStep,
        output: dict | None,
        elapsed_ms: int,
        status: str = "completed",
    ) -> None:
        """Mark step terminal (status defaults to completed; a write step passes the
        read-back verdict status — completed / completed_unverified / partially_completed).
        Emit events, checkpoint."""
        ...
        transition_step(step, status)                # was: transition_step(step, "completed")
        step.output_data = output
        step.completed_at = datetime.now(timezone.utc)
        await self._db.flush()

        result = StepResult(
            step_id=step.step_id,
            status=status,                            # was: status="completed"
            output_data=output,
            duration_ms=elapsed_ms,
        )
        ...
```

> **Note for the implementer:** `finalize_step` is called from `execute_step` (Step 6 below wires the status there). Grep for any OTHER `finalize_step(` callers in `dag_runner.py` / tests and confirm they still pass 4 positional args (the default keeps them at `completed`). The `StepResult` contract accepts a free `status: str`, so no contract change.

- [ ] **Step 6: Add `run_readback` to `step_runner.py` (the connector read seam)**

`ReadBackVerifier`'s `read_fn` seam must invoke a read *capability* through the real tool path. `StepRunner` already owns tool execution (`build_operator_tools`, the injected `self._execute_tool_fn`). Add a method that resolves the read capability to a discovered tool and calls it. **Reads bypass the Step-1 idempotency ledger by construction**, so this cannot double-fire.

Add to `backend/src/services/step_runner.py` (class `StepRunner`):

```python
    async def run_readback(self, read_capability: str, read_args: dict, run: TaskRun) -> object:
        """Invoke a READ capability (post-condition read-back) via the tool path and
        return its raw result. Best-effort: raises on any failure so ReadBackVerifier
        resolves it to UNVERIFIED (never a false CONTRADICTED). Reads never go through
        the idempotency ledger, so this is side-effect free."""
        if self._execute_tool_fn is None:
            raise RuntimeError("no execute_tool_fn available for read-back")
        tools = await self.build_operator_tools()
        tool = next(
            (t for t in tools if _tool_capability(t) == read_capability),
            None,
        )
        if tool is None:
            raise RuntimeError(f"no tool serves read capability {read_capability}")
        return await self._execute_tool_fn(_tool_name(tool), read_args, run.user_id, run.workspace_id or "")
```

> **Note for the implementer (single integration point to verify against live signatures):** the exact accessors `_tool_capability(t)` / `_tool_name(t)` and the `execute_tool_fn(name, input, user_id, workspace_id)` argument order must match how `build_operator_tools()` shapes tools and how `agent_loop` calls `execute_tool_fn` in this same file. Read `step_runner.build_operator_tools` + the `execute_tool_fn` usage in `run_step_via_agent_loop` (both already in this file) and adjust the two helpers + the call to match verbatim. Add small module-level `_tool_capability`/`_tool_name` helpers if the tool objects are dicts (`t["capability"]` / `t["name"]`) vs objects. This is the ONLY spot in Step 3 that touches the connector-call shape; everything else is seam-injected + mocked.

- [ ] **Step 7: Wire the verdict into `execute_step`**

In `backend/src/services/dag_runner.py`, `execute_step`'s auto-execute branch currently calls `finalize_step` then unconditionally increments trust (`:380-390`, verbatim from extraction):

```python
            await self.finalize_step(run, step, output, elapsed_ms)
            # Reinforce trust: a successful auto-execution graduates trust the
            # same way an explicit user approval does...
            risk_level = getattr(risk, "risk_level", risk)
            await self._trust_gate.record_auto_execution_outcome(
                capability, risk_level, run.workspace_id or ""
            )
            # Remember the auto-executed (capability, risk_level) so a later
            # verification failure can reverse this reinforcement (SVC).
            self._trust_gate.remember_auto_executed(run, capability, risk_level)
            return
```

Replace it with a version that verifies BEFORE finalize and threads the verdict status (the trust-increment relocation is Task 7 — for now, gate it on CONFIRMED so this task is self-consistent):

```python
            # Read-back verification BEFORE marking terminal (spec §4.5). Only
            # irreversible writes are verified; everything else is trivially CONFIRMED.
            from src.services.verification import ReadBackVerifier, VerifyVerdict
            from src.services.verification.readback import verdict_to_step_status

            verifier = ReadBackVerifier(
                read_fn=lambda cap, args: self._runner.run_readback(cap, args, run)
            )
            verdict = await verifier.verify_step(
                capability=capability,
                write_input=step.input_data or {},
                write_output=output if isinstance(output, dict) else {},
                risk=risk,
            )
            step_status = verdict_to_step_status(verdict)

            await self.finalize_step(run, step, output, elapsed_ms, status=step_status)

            # Trust reinforcement fires ONLY on a confirmed-verified completion
            # (Task 7 moves the completed_unverified deferred-increment to the tick).
            risk_level = getattr(risk, "risk_level", risk)
            if verdict == VerifyVerdict.CONFIRMED:
                await self._trust_gate.record_auto_execution_outcome(
                    capability, risk_level, run.workspace_id or ""
                )
            # Record the auto-executed (capability, risk_level) regardless, so the
            # deferred-read tick can fire the increment when a completed_unverified
            # step is later confirmed (Task 7/9). Now finally has a reader.
            self._trust_gate.remember_auto_executed(run, capability, risk_level)

            # A read-back that CONTRADICTED an irreversible write escalates to the
            # user (escalate-first compensation) — wired in Task 8.
            if verdict == VerifyVerdict.CONTRADICTED:
                await self._escalate_divergence(run, step, capability, risk, output)
            return
```

> **Note:** `_escalate_divergence` is added in Task 8. To keep THIS task's commit green, add a temporary no-op stub `async def _escalate_divergence(self, *a, **k): return None` on `DagRunner` now (Task 8 replaces the body). Verify `self._runner` is the `StepRunner` instance on `DagRunner` (it is — `execute_step` calls `self._runner.run_step_action`).

- [ ] **Step 8: Write the characterization test (the spec's mandated guard)**

Create `backend/tests/test_finalize_verification.py`:

```python
"""Characterization test (spec §4.5): no write path emits a terminal step status
without a passing post-condition OR an explicit completed_unverified verdict.

Drives finalize_step through the three verdicts and asserts the step lands in the
matching status — and, critically, that an irreversible write is NEVER marked bare
'completed' when its read-back did not confirm."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.verification.readback import VerifyVerdict, verdict_to_step_status


def test_verdict_status_mapping_is_total_and_correct():
    assert verdict_to_step_status(VerifyVerdict.CONFIRMED) == "completed"
    assert verdict_to_step_status(VerifyVerdict.CONTRADICTED) == "partially_completed"
    assert verdict_to_step_status(VerifyVerdict.UNVERIFIED) == "completed_unverified"


async def test_irreversible_write_never_bare_completed_without_confirmation():
    """The characterization invariant: for an irreversible capability, a
    non-CONFIRMED verdict must NOT map to 'completed'."""
    from src.services.verification.readback import ReadBackVerifier

    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")

    # No seam -> UNVERIFIED -> completed_unverified (NOT completed).
    v = ReadBackVerifier(read_fn=None)
    verdict = await v.verify_step(
        capability="email.send", write_input={"to": "x"}, write_output={}, risk=risk
    )
    assert verdict_to_step_status(verdict) == "completed_unverified"

    # Contradicted read-back -> partially_completed (NOT completed).
    v2 = ReadBackVerifier(read_fn=AsyncMock(return_value=[]))
    verdict2 = await v2.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "c"},
        write_output={"event_id": "e"},
        risk=risk,
    )
    assert verdict_to_step_status(verdict2) == "partially_completed"


def test_only_confirmed_maps_to_completed():
    # Enumerate: exactly one verdict yields the terminal 'completed'.
    completed = [v for v in VerifyVerdict if verdict_to_step_status(v) == "completed"]
    assert completed == [VerifyVerdict.CONFIRMED]
```

- [ ] **Step 9: Run the verifier + characterization tests + the dag_runner suite**

Run:
```bash
uv run pytest tests/verification/test_readback.py tests/test_finalize_verification.py -q
uv run pytest tests/ --ignore=tests/e2e -q -k "dag or step_runner or trust or execution" 2>&1 | tail -15
```
Expected: verification tests PASS; no new failures in the execution suites (the `finalize_step` default keeps non-write callers at `completed`).

- [ ] **Step 10: Lint + commit**

```bash
uv run ruff check src/services/verification/readback.py src/services/dag_runner.py src/services/step_runner.py tests/verification/test_readback.py tests/test_finalize_verification.py
git add -A
git commit -m "feat(rebuild): inline read-back verifier gates finalize_step terminal status (Step 3)

ReadBackVerifier maps (capability,input,output,risk) -> confirmed/contradicted/
unverified; execute_step verifies BEFORE finalize and threads the verdict status
into finalize_step (now status-parametrized). Irreversible writes can no longer be
marked bare 'completed' without a confirming read-back (characterization test).
A failed read-back is UNVERIFIED, never a false CONTRADICTED (§7 false-negative).
step_runner.run_readback invokes the read capability via the ledger-bypassing tool
path. Trust increment gated on CONFIRMED (Task 7 relocates the deferred case)."
```

---

## Task 7: Persist verification metadata + prove the trust-increment relocation

Task 6 already gated the trust increment on `CONFIRMED`. This task (a) persists a `verification` metadata block into `step.output_data` so the deferred tick (Task 9) and the escalation (Task 8) can act on a `completed_unverified` / `partially_completed` step, and (b) adds the focused regression test proving the relocation the spec names as mis-wired (`approved_count` must NOT increment on an unverified write).

**Files:**
- Modify: `backend/src/services/dag_runner.py` (`execute_step` — enrich output before finalize)
- Test: `backend/tests/test_trust_increment_relocation.py`

- [ ] **Step 1: Write the failing metadata + relocation test**

Create `backend/tests/test_trust_increment_relocation.py`:

```python
"""Spec §4.5: approved_count must count only VERIFIED writes. A completed_unverified
write must NOT increment trust at finalize; the metadata needed by the deferred tick
must be persisted on the step. Mirrors the DagRunner construction in
tests/test_trust_feedback.py (read that file for the fixture shape)."""

from types import SimpleNamespace

from src.services.verification.readback import VerifyVerdict


def _verification_meta(capability, risk, verdict, output):
    from src.services.dag_runner import build_verification_meta

    return build_verification_meta(capability, risk, verdict, output)


def test_verification_meta_captures_deferred_recheck_inputs():
    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")
    meta = _verification_meta(
        "calendar.create", risk, VerifyVerdict.UNVERIFIED, {"event_id": "e1"}
    )
    assert meta["capability"] == "calendar.create"
    assert meta["risk_level"] == "high"
    assert meta["verdict"] == "unverified"
    assert meta["reversible"] is False
    assert meta["blast_radius"] == "external_single"
    assert meta["artifact_ref"]["event_id"] == "e1"


def test_confirmed_write_needs_no_deferred_recheck():
    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")
    meta = _verification_meta("calendar.create", risk, VerifyVerdict.CONFIRMED, {})
    assert meta["verdict"] == "confirmed"
```

Also add a relocation assertion mirroring `tests/test_trust_feedback.py`'s DagRunner harness (the implementer copies that construction):

```python
async def test_unverified_write_does_not_increment_trust():
    """Build a DagRunner whose write-step read-back is UNVERIFIED and assert
    record_auto_execution_outcome is NEVER awaited (trust must not graduate on an
    unverified write) and the step lands 'completed_unverified'. Copy the DagRunner
    fixture from tests/test_trust_feedback.py; force the step capability irreversible
    with read_fn=None so the verdict is UNVERIFIED."""
    # Harness-copy stub — replace with the real DagRunner construction + assertions:
    #   dag._trust_gate.record_auto_execution_outcome.assert_not_awaited()
    #   assert step.status == "completed_unverified"
    assert True
```

> **Note for the implementer:** the third test is a harness-copy stub — replace its body with the real DagRunner construction from `tests/test_trust_feedback.py`, driving an irreversible write step with `read_fn=None` (UNVERIFIED) and asserting `record_auto_execution_outcome` is not awaited + the step is `completed_unverified`. The first two tests are complete and must pass as written.

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_trust_increment_relocation.py -q`
Expected: FAIL at import — `cannot import name 'build_verification_meta'`.

- [ ] **Step 3: Add `build_verification_meta` + attach it in `execute_step`**

Add a module-level helper in `backend/src/services/dag_runner.py` (near the top, after imports):

```python
def build_verification_meta(capability: str, risk, verdict, output: dict | None) -> dict:
    """Metadata attached to a verified write's output_data so the deferred-read tick
    and escalation can act on a completed_unverified / partially_completed step
    without re-deriving risk. artifact_ref carries the exact observed effect."""
    out = output if isinstance(output, dict) else {}
    return {
        "capability": capability,
        "risk_level": getattr(risk, "risk_level", "high"),
        "reversible": getattr(risk, "reversible", False),
        "blast_radius": getattr(risk, "blast_radius", "self"),
        "verdict": verdict.value if hasattr(verdict, "value") else str(verdict),
        "attempts": 1,
        "artifact_ref": {
            k: out.get(k)
            for k in ("event_id", "id", "message_id", "url", "thread_id")
            if out.get(k) is not None
        },
    }
```

Then in `execute_step` (the Task-6 block), immediately **before** `await self.finalize_step(...)`, enrich the output when verification produced a non-`completed` status:

```python
            step_status = verdict_to_step_status(verdict)

            # Attach verification metadata so the deferred tick / escalation can act
            # on a completed_unverified / partially_completed step (JSONB, no migration).
            if step_status != "completed" and isinstance(output, dict):
                output = {
                    **output,
                    "verification": build_verification_meta(capability, risk, verdict, output),
                }

            await self.finalize_step(run, step, output, elapsed_ms, status=step_status)
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `uv run pytest tests/test_trust_increment_relocation.py -q`
Expected: the two `build_verification_meta` tests PASS (after the implementer fills the harness-copy test, all three pass).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/services/dag_runner.py tests/test_trust_increment_relocation.py
git add backend/src/services/dag_runner.py tests/test_trust_increment_relocation.py
git commit -m "feat(rebuild): count only verified writes toward trust + persist verify metadata (Step 3)

Confirms the relocation of the auto-exec trust increment to fire only on a CONFIRMED
read-back (the spec-named mis-wire: approved_count previously incremented at finalize
BEFORE verification). A completed_unverified/partially_completed step carries a
build_verification_meta block (capability, risk_level, reversible, blast_radius,
verdict, artifact_ref) in output_data so the deferred tick can re-check + the
escalation can cite the exact effect. JSONB — no migration."
```

---

## Task 8: Compensation registry + escalate-first on step `partially_completed`

Escalate-first (§4.5): on a `partially_completed` (read-back contradicted) irreversible write, escalate to the **present** user with the exact `artifact_ref` + observed divergence; offer the registered compensator if one exists (no compensator → escalate anyway). No startup coverage gate for compensation (D9).

**Files:**
- Create: `backend/src/services/verification/compensation.py`
- Modify: `backend/src/services/dag_runner.py` (`_escalate_divergence` real body — replaces the Task-6 stub)
- Modify: `backend/src/services/verification/__init__.py` (export)
- Test: `backend/tests/verification/test_compensation.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/verification/test_compensation.py`:

```python
"""Compensation registry (escalate-first). A partially_completed irreversible write
builds a divergence escalation carrying the artifact_ref + observed divergence + the
compensator (if any). No compensator -> still escalates."""

from src.services.verification.compensation import (
    COMPENSATIONS,
    build_divergence_escalation,
    get_compensation,
)
from src.services.verification.predicate import is_irreversible_capability


def test_every_registered_compensation_is_a_write():
    for cap in COMPENSATIONS:
        assert is_irreversible_capability(cap), f"{cap} is not irreversible"


def test_compensation_lookup_returns_none_for_unregistered():
    assert get_compensation("email.reply") is None  # not registered -> escalate anyway


def test_escalation_includes_artifact_ref_and_divergence_without_compensator():
    payload = build_divergence_escalation(
        capability="email.reply",
        artifact_ref={"message_id": "m1"},
        observed="read-back could not confirm the reply was sent",
    )
    assert payload["capability"] == "email.reply"
    assert payload["artifact_ref"] == {"message_id": "m1"}
    assert payload["observed"]
    assert payload["compensator"] is None  # no compensator registered -> escalate regardless


def test_escalation_includes_compensator_when_registered():
    payload = build_divergence_escalation(
        capability="calendar.create",
        artifact_ref={"event_id": "e1"},
        observed="event not found on read-back",
    )
    assert payload["compensator"] is not None
    assert payload["compensator"]["capability"] == "calendar.delete"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/verification/test_compensation.py -q`
Expected: FAIL at import — `No module named 'src.services.verification.compensation'`.

- [ ] **Step 3: Write `compensation.py`**

Create `backend/src/services/verification/compensation.py`:

```python
"""Per-capability compensation registry (escalate-first, spec §4.5).

Each write capability MAY declare a compensating action (delete the draft, cancel the
invite). On a partially_completed irreversible write the engine escalates to the
present user with the exact artifact_ref + observed divergence; the user decides
whether to run the compensator (itself gated + idempotent). No compensator ->
escalate regardless (informational). There is deliberately NO startup coverage gate
for compensation — a missing compensator is allowed (D9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class Compensation:
    """A compensating action for a write capability.

    capability: the compensator capability to run (gated + idempotent when executed).
    build_input: derive the compensator's input from the failed write's artifact_ref.
    description: user-facing explanation of what the compensator does.
    """

    capability: str
    build_input: Callable[[dict], dict]
    description: str = ""


COMPENSATIONS: dict[str, Compensation] = {
    "calendar.create": Compensation(
        capability="calendar.delete",
        build_input=lambda artifact_ref: {
            "event_id": artifact_ref.get("event_id") or artifact_ref.get("id")
        },
        description="Delete the calendar event that the read-back could not confirm.",
    ),
}


def get_compensation(capability: str) -> Compensation | None:
    return COMPENSATIONS.get(capability)


def build_divergence_escalation(*, capability: str, artifact_ref: dict, observed: str) -> dict:
    """Build the escalate-first payload for a contradicted irreversible write. Carries
    the exact artifact_ref + observed divergence, and the compensator if registered
    (None otherwise — escalate regardless)."""
    comp = get_compensation(capability)
    compensator = None
    if comp is not None:
        compensator = {
            "capability": comp.capability,
            "input": comp.build_input(artifact_ref or {}),
            "description": comp.description,
        }
    return {
        "capability": capability,
        "artifact_ref": artifact_ref or {},
        "observed": observed,
        "compensator": compensator,
    }
```

Add to `backend/src/services/verification/__init__.py` exports (import block + `__all__`):

```python
from src.services.verification.compensation import (
    COMPENSATIONS,
    build_divergence_escalation,
    get_compensation,
)
```
(add `"COMPENSATIONS"`, `"build_divergence_escalation"`, `"get_compensation"` to `__all__`.)

- [ ] **Step 4: Replace the `_escalate_divergence` stub in `dag_runner.py`**

Replace the Task-6 no-op stub with a real body that emits a divergence surface via the existing `self._emitter` path (the same one `finalize_step` uses for `surface_created`). The user is present (escalate-first) → immediate delivery:

```python
    async def _escalate_divergence(self, run, step, capability, risk, output) -> None:
        """Escalate-first: a contradicted read-back on an irreversible write surfaces
        the exact artifact_ref + observed divergence to the present user, offering the
        registered compensator (if any). No compensator -> escalate anyway (§4.5)."""
        from src.services.verification.compensation import build_divergence_escalation

        out = output if isinstance(output, dict) else {}
        meta = out.get("verification", {})
        artifact_ref = meta.get("artifact_ref") or {}
        escalation = build_divergence_escalation(
            capability=capability,
            artifact_ref=artifact_ref,
            observed="Read-back could not confirm the expected effect of this write.",
        )
        try:
            await self._emitter.emit_event(
                "surface_created",
                run.user_id,
                {
                    "run_id": run.run_id,
                    "step_id": step.step_id,
                    "surface_type": "verification_divergence",
                    "preview": f"Could not confirm {capability} — review needed",
                    "escalation": escalation,
                },
                workspace_id=run.workspace_id,
            )
        except Exception:
            logger.warning(
                "Failed to emit divergence escalation for step %s", step.step_id, exc_info=True
            )
```

> **Note for the implementer:** confirm `self._emitter.emit_event(event, user_id, payload, workspace_id=...)` matches the call shape in `dag_runner.finalize_step` (it does — copy it). `logger` is already module-level.

- [ ] **Step 5: Run + lint + commit**

Run: `uv run pytest tests/verification/test_compensation.py -q`
Expected: all PASS.

```bash
uv run ruff check src/services/verification/compensation.py src/services/verification/__init__.py src/services/dag_runner.py tests/verification/test_compensation.py
git add backend/src/services/verification/compensation.py backend/src/services/verification/__init__.py backend/src/services/dag_runner.py tests/verification/test_compensation.py
git commit -m "feat(rebuild): compensation registry + escalate-first divergence surface (Step 3)

Capability-keyed COMPENSATIONS (escalate-first). A partially_completed irreversible
write emits a verification_divergence surface with the exact artifact_ref + observed
divergence + the registered compensator (or None -> escalate anyway). No startup
coverage gate for compensation (a missing compensator is allowed, §4.5)."
```

---

## Task 9: Async deferred-read scheduler tick (upgrade `completed_unverified`; async-divergence surface)

The chat/fast path returns immediately; a `SchedulerLoop` tick re-attempts read-back on `completed_unverified` steps. On confirmation it upgrades → `completed` and fires the **deferred** trust increment; on post-turn divergence it → `partially_completed` and raises an **async-divergence surface** via the Notifier hold-for-briefing path (the user may be absent); past a give-up TTL it stops re-checking (the step stays `completed_unverified` — still a success, just permanently unconfirmed).

**Files:**
- Create: `backend/src/services/scheduler/deferred_verification_tick.py`
- Modify: `backend/src/services/scheduler/service.py` (add mixin to MRO), `backend/src/services/scheduler/_base.py` (wire the tick call)
- Test: `backend/tests/test_deferred_verification_tick.py`

- [ ] **Step 1: Write the failing test (mocked verifier + notifier)**

Create `backend/tests/test_deferred_verification_tick.py`:

```python
"""Deferred-read tick: re-checks completed_unverified steps. Confirmed -> completed +
deferred trust increment; contradicted -> partially_completed + async-divergence
surface; past TTL -> give up. Logic tested via the pure _resolve_recheck helper +
_apply_recheck with mocked collaborators (no DB, no scheduler bootstrap)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.services.scheduler.deferred_verification_tick import (
    DEFERRED_VERIFICATION_TTL_S,
    _is_past_give_up_ttl,
)
from src.services.verification.readback import VerifyVerdict


def _step(status="completed_unverified", age_s=120.0, verdict_meta=None):
    from datetime import datetime, timedelta, timezone

    completed_at = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return SimpleNamespace(
        step_id="stp_1",
        run_id="run_1",
        status=status,
        completed_at=completed_at,
        input_data={"capability": "calendar.create"},
        output_data={"verification": verdict_meta or {
            "capability": "calendar.create",
            "risk_level": "medium",
            "reversible": False,
            "blast_radius": "external_multiple",
            "verdict": "unverified",
            "attempts": 1,
            "artifact_ref": {"event_id": "evt_1"},
        }},
    )


def test_give_up_ttl_boundary():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    fresh = _step(age_s=DEFERRED_VERIFICATION_TTL_S - 10)
    stale = _step(age_s=DEFERRED_VERIFICATION_TTL_S + 10)
    assert _is_past_give_up_ttl(fresh, now=now) is False
    assert _is_past_give_up_ttl(stale, now=now) is True


async def test_confirmed_recheck_upgrades_and_increments_trust():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    db = MagicMock()
    trust_gate = MagicMock()
    trust_gate.record_auto_execution_outcome = AsyncMock()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    await _apply_recheck(
        db, run, step, VerifyVerdict.CONFIRMED, trust_gate=trust_gate, notifier=notifier
    )
    assert step.status == "completed"
    trust_gate.record_auto_execution_outcome.assert_awaited_once()
    notifier.notify.assert_not_awaited()


async def test_contradicted_recheck_diverges_and_holds_for_briefing():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    db = MagicMock()
    trust_gate = MagicMock()
    trust_gate.record_auto_execution_outcome = AsyncMock()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    await _apply_recheck(
        db, run, step, VerifyVerdict.CONTRADICTED, trust_gate=trust_gate, notifier=notifier
    )
    assert step.status == "partially_completed"
    trust_gate.record_auto_execution_outcome.assert_not_awaited()
    notifier.notify.assert_awaited_once()  # async-divergence surface raised


async def test_still_unverified_recheck_leaves_status_unchanged():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")
    await _apply_recheck(
        MagicMock(), run, step, VerifyVerdict.UNVERIFIED,
        trust_gate=MagicMock(), notifier=MagicMock(),
    )
    assert step.status == "completed_unverified"  # will retry next tick until TTL
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_deferred_verification_tick.py -q`
Expected: FAIL at import — `No module named 'src.services.scheduler.deferred_verification_tick'`.

- [ ] **Step 3: Write `deferred_verification_tick.py`**

Create `backend/src/services/scheduler/deferred_verification_tick.py`:

```python
"""Deferred-read verification tick (spec §4.5 async fast-path loop).

Re-checks completed_unverified steps with a give-up TTL. On confirmation: upgrade to
completed + fire the DEFERRED trust increment (trust graduates only on verified
writes). On post-turn divergence: partially_completed + async-divergence surface via
the Notifier hold-for-briefing path (the user may be absent). Past the TTL: stop
re-checking — the step stays completed_unverified (a success, permanently unconfirmed).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import transition_step
from src.services.verification.compensation import build_divergence_escalation
from src.services.verification.readback import ReadBackVerifier, VerifyVerdict

logger = logging.getLogger(__name__)

# Eventual-consistency window before the FIRST re-check, and the give-up ceiling.
DEFERRED_VERIFICATION_MIN_AGE_S = 60.0
DEFERRED_VERIFICATION_TTL_S = 3600.0  # 1h — stop re-checking after this


def _age_seconds(step, *, now: datetime) -> float:
    completed_at = getattr(step, "completed_at", None)
    if completed_at is None:
        return 0.0
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - completed_at).total_seconds())


def _is_past_give_up_ttl(step, *, now: datetime) -> bool:
    return _age_seconds(step, now=now) > DEFERRED_VERIFICATION_TTL_S


async def _apply_recheck(db, run, step, verdict: VerifyVerdict, *, trust_gate, notifier) -> None:
    """Apply a re-check verdict to a completed_unverified step."""
    meta = (step.output_data or {}).get("verification", {})
    capability = meta.get("capability") or (step.input_data or {}).get("capability", "")

    if verdict == VerifyVerdict.CONFIRMED:
        transition_step(step, "completed")
        # Deferred trust increment: trust graduates now that the write is verified.
        try:
            await trust_gate.record_auto_execution_outcome(
                capability, meta.get("risk_level", "high"), run.workspace_id or ""
            )
        except Exception:
            logger.debug("Deferred trust increment failed for %s", step.step_id, exc_info=True)
        await db.flush()
        return

    if verdict == VerifyVerdict.CONTRADICTED:
        transition_step(step, "partially_completed")
        await db.flush()
        # Async-divergence surface via hold-for-briefing (user may be absent).
        escalation = build_divergence_escalation(
            capability=capability,
            artifact_ref=meta.get("artifact_ref") or {},
            observed="Post-turn read-back could not confirm this write's effect.",
        )
        try:
            await notifier.notify(
                user_id=run.user_id,
                workspace_id=run.workspace_id or "",
                title=f"Could not confirm {capability}",
                body=escalation["observed"],
                notification_type="verification_divergence",
                metadata=escalation,
            )
        except Exception:
            logger.warning("Failed to raise async-divergence surface", exc_info=True)
        return

    # UNVERIFIED — leave completed_unverified; the next tick retries until TTL.
    return


class DeferredVerificationTickMixin:
    """Re-checks completed_unverified steps (spec §4.5). Confirmed -> completed +
    deferred trust increment; contradicted -> partially_completed + async surface."""

    async def _tick_deferred_verification(self, factory) -> None:
        now = datetime.now(timezone.utc)
        try:
            async with factory() as db:
                result = await db.execute(
                    select(TaskStep).where(TaskStep.status == "completed_unverified")
                )
                steps = list(result.scalars().all())
                if not steps:
                    return

                from src.services.notifier import Notifier
                from src.services.trust_gate import TrustGate

                notifier = Notifier(getattr(self, "_redis", None))
                trust_gate = TrustGate(db)
                verifier = self._build_deferred_verifier(db)

                for step in steps:
                    age = _age_seconds(step, now=now)
                    if age < DEFERRED_VERIFICATION_MIN_AGE_S:
                        continue  # inside the eventual-consistency window
                    if _is_past_give_up_ttl(step, now=now):
                        continue  # gave up — stays completed_unverified
                    run = (
                        await db.execute(select(TaskRun).where(TaskRun.run_id == step.run_id))
                    ).scalar_one_or_none()
                    if run is None:
                        continue
                    meta = (step.output_data or {}).get("verification", {})
                    verdict = await verifier.verify_step(
                        capability=meta.get("capability", ""),
                        write_input=step.input_data or {},
                        write_output=step.output_data or {},
                        risk=_Risk(meta),
                    )
                    await _apply_recheck(
                        db, run, step, verdict, trust_gate=trust_gate, notifier=notifier
                    )
                await db.commit()
        except Exception:
            logger.warning("Deferred verification tick failed", exc_info=True)

    def _build_deferred_verifier(self, db) -> ReadBackVerifier:
        """Build the re-check verifier. read_fn reuses the same read path as the inline
        gate (step_runner.run_readback); if unavailable in this context, read_fn=None
        means POST_CONDITIONS caps stay unverified until they age out (safe)."""
        return ReadBackVerifier(read_fn=None)


class _Risk:
    """Reconstruct a RiskAssessment-shaped object from the persisted verification meta
    so the verifier's irreversibility union sees the original (reversible, blast_radius)."""

    def __init__(self, meta: dict):
        self.reversible = meta.get("reversible", False)
        self.blast_radius = meta.get("blast_radius", "self")
        self.risk_level = meta.get("risk_level", "high")
```

> **Note for the implementer:** confirm `Notifier(...)` and `TrustGate(db)` constructor signatures (read `notifier.py` + `trust_gate.py` — `Notifier` takes a redis client; the scheduler exposes one as `self._redis` or via settings) and the `notifier.notify(...)` kwargs (Task-9 uses `notification_type`/`metadata`; the real signature from the extraction is `notify(user_id, workspace_id, title, body, notification_type, ...)` — align the kwargs). `_build_deferred_verifier` returns `read_fn=None` for the first landing (POST_CONDITIONS caps age out safely); wiring the real read seam here is the SAME follow-up as `step_runner.run_readback` (construct a `StepRunner` in the tick or a lightweight read executor) and is the one place production confirmation of POST_CONDITIONS caps switches on.

- [ ] **Step 4: Wire the tick into the scheduler**

In `backend/src/services/scheduler/service.py`, add the mixin to the `SchedulerLoop` MRO (alongside the other `*TickMixin`s):

```python
from src.services.scheduler.deferred_verification_tick import DeferredVerificationTickMixin

class SchedulerLoop(
    PerceptionTickMixin,
    BackgroundTasksTickMixin,
    LifecycleTickMixin,
    DlqTickMixin,
    NotificationTickMixin,
    RunHealthTickMixin,
    PersonaTickMixin,
    ScheduleDispatchMixin,
    WebhookRenewalTickMixin,
    DeferredVerificationTickMixin,
    SchedulerBase,
):
```

In `backend/src/services/scheduler/_base.py`, wire the call into `_tick` at the every-5th-tick cadence (next to eviction/dlq, ~line 109-111):

```python
        if self._tick_count % 5 == 0:
            await self._run_subtick("eviction", self._tick_eviction(factory))
            await self._run_subtick("dlq_retry", self._tick_dlq_retry(factory))
            await self._run_subtick(
                "deferred_verification", self._tick_deferred_verification(factory)
            )
            ...
```

- [ ] **Step 5: Run + lint + commit**

Run: `uv run pytest tests/test_deferred_verification_tick.py -q`
Expected: all PASS.

```bash
uv run ruff check src/services/scheduler/deferred_verification_tick.py src/services/scheduler/service.py src/services/scheduler/_base.py tests/test_deferred_verification_tick.py
git add backend/src/services/scheduler/deferred_verification_tick.py backend/src/services/scheduler/service.py backend/src/services/scheduler/_base.py tests/test_deferred_verification_tick.py
git commit -m "feat(rebuild): deferred-read verification tick (upgrade/diverge/give-up) (Step 3)

A SchedulerLoop tick (every 5th, ~150s) re-checks completed_unverified steps:
confirmed -> completed + deferred trust increment (trust graduates only on verified
writes); post-turn contradicted -> partially_completed + async-divergence surface via
Notifier hold-for-briefing; past a 1h give-up TTL -> stops re-checking (stays a
success, unconfirmed). Reconstructs the original risk from persisted verify meta."
```

---

## Task 10: Step-1 carry-forwards — identity-coverage startup hard-gate + in-flight-on-resume read-back

Two carry-forwards the Step-1 memo explicitly parks for Step 3: (A) promote `validate_identity_coverage` to a startup **hard-gate** (alongside the post-condition gate), scoped to irreversible writes with an explicit positional-accepted escape valve; (B) on the ledger's `in_flight` conflict at resume, run the post-condition read-back so a confirmed prior effect resolves to already-done instead of blindly failing closed.

**Files:**
- Modify: `backend/src/services/idempotency/identity.py` (add `POSITIONAL_KEY_ACCEPTED` + `validate_identity_coverage_strict`), `backend/src/api/app.py` (wire the gate)
- Create: `backend/src/services/verification/inflight.py` (`resolve_inflight_on_resume`)
- Test: `backend/tests/test_identity_coverage_gate.py`, `backend/tests/test_inflight_resume_readback.py`

### Part A — identity-coverage startup hard-gate

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_identity_coverage_gate.py`:

```python
"""validate_identity_coverage_strict: every IRREVERSIBLE write capability must have a
semantic IdentitySpec OR be explicitly positional-accepted. Mirrors validate_registry
(list[str], never raises). Wired as a startup hard-gate."""

from src.services.idempotency.identity import (
    IDENTITY_SPECS,
    POSITIONAL_KEY_ACCEPTED,
    validate_identity_coverage_strict,
)
from src.services.verification.predicate import is_irreversible_capability, write_capabilities


def test_real_catalog_identity_coverage_is_complete():
    irreversible = {c for c in write_capabilities() if is_irreversible_capability(c)}
    errors = validate_identity_coverage_strict(irreversible)
    assert errors == [], f"irreversible caps with no identity strategy: {errors}"


def test_missing_irreversible_cap_is_flagged():
    errors = validate_identity_coverage_strict({"brand.new_irreversible_write"})
    assert any("brand.new_irreversible_write" in e for e in errors)


def test_spec_covered_cap_passes():
    assert "email.send" in IDENTITY_SPECS
    assert validate_identity_coverage_strict({"email.send"}) == []


def test_positional_accepted_cap_passes():
    # e.g. messaging.send has no semantic spec but is explicitly positional-accepted
    assert "messaging.send" in POSITIONAL_KEY_ACCEPTED
    assert validate_identity_coverage_strict({"messaging.send"}) == []
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run pytest tests/test_identity_coverage_gate.py -q`
Expected: FAIL at import — `cannot import name 'POSITIONAL_KEY_ACCEPTED'`.

- [ ] **Step 3: Add `POSITIONAL_KEY_ACCEPTED` + `validate_identity_coverage_strict` to `identity.py`**

In `backend/src/services/idempotency/identity.py`, after `IDENTITY_SPECS` (line ~47), add:

```python
# Irreversible write capabilities for which the positional/native-token idempotency
# key is DELIBERATELY accepted (no semantic IdentitySpec authored — typically external
# MCP tools whose arg schema we don't control). Explicit + audited: the startup gate
# forbids an irreversible write cap that is neither spec'd NOR listed here, so a new
# write cap can't silently fall back to positional keying unnoticed. Mirrors the
# verification UNVERIFIABLE escape valve.
POSITIONAL_KEY_ACCEPTED: frozenset[str] = frozenset(
    {
        "email.reply",
        "calendar.update",
        "calendar.delete",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        "repo.review_pr",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.delete",
        "issue.transition",
        "issue.sub_issue",
        "doc.create",
        "doc.update",
        "doc.delete",
        "doc.comment",
        "doc.append",
        "doc.move",
        "doc.update_block",
        "doc.delete_block",
        "doc.create_datasource",
        "doc.update_datasource",
        "doc.drive_create",
        "doc.drive_delete",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "workflow.delete",
        "workflow.create_issues",
        "workflow.bulk_update",
        "workflow.update_comment",
        "workflow.delete_comment",
        "workflow.resolve_comment",
        "workflow.unresolve_comment",
        "workflow.create_project",
        "workflow.create_milestone",
        "workflow.update_milestone",
        "workflow.delete_milestone",
        "workflow.create_customer_need",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        "filesystem.write",
        "filesystem.move",
        "browser.open",
        "browser.click",
        "browser.type",
        "browser.submit",
        "browser.execute",
        "browser.install",
    }
)


def validate_identity_coverage_strict(irreversible_write_capabilities: set[str]) -> list[str]:
    """Startup HARD-GATE (spec §6 Step-3 carry-forward): every IRREVERSIBLE write
    capability must have a semantic IdentitySpec OR be explicitly positional-accepted.
    The caller supplies the irreversible set (keeps this module free of a verification
    import). Returns list[str] (empty = valid), never raises."""
    allowed = set(IDENTITY_SPECS) | POSITIONAL_KEY_ACCEPTED
    return [
        f"IRREVERSIBLE write capability '{cap}' has no identity strategy "
        "(add an IdentitySpec or list it in POSITIONAL_KEY_ACCEPTED)"
        for cap in sorted(irreversible_write_capabilities)
        if cap not in allowed
    ]
```

- [ ] **Step 4: Wire the identity hard-gate into `app.py`**

In `backend/src/api/app.py`, after the post-condition coverage block (Task 5), add:

```python
        # Identity coverage gate (spec §6 Step-3 carry-forward): every IRREVERSIBLE
        # write capability must have a deliberate idempotency-key strategy (semantic
        # IdentitySpec or explicit positional-accepted). Fail closed.
        if settings.skip_registry_validation:
            logger.warning("Identity coverage check SKIPPED (skip_registry_validation)")
        else:
            try:
                from src.services.idempotency.identity import validate_identity_coverage_strict
                from src.services.verification.predicate import (
                    is_irreversible_capability,
                    write_capabilities,
                )

                irreversible = {
                    c for c in write_capabilities() if is_irreversible_capability(c)
                }
                id_errors = validate_identity_coverage_strict(irreversible)
            except Exception as exc:
                logger.error("Identity coverage check failed to run", exc_info=True)
                raise RuntimeError("Identity coverage check could not run") from exc

            if id_errors:
                for err in id_errors:
                    logger.error("Identity coverage: %s", err)
                raise RuntimeError(
                    f"Identity coverage found {len(id_errors)} error(s) — add an IdentitySpec "
                    "or list the capability in POSITIONAL_KEY_ACCEPTED, or set "
                    "JARVIS_SKIP_REGISTRY_VALIDATION=true to bypass."
                )
            logger.info("Identity coverage passed")
```

- [ ] **Step 5: Run to verify Part A PASSES**

Run: `uv run pytest tests/test_identity_coverage_gate.py -q`
Expected: all PASS. If `test_real_catalog_identity_coverage_is_complete` fails, its message lists the irreversible caps missing a strategy — add each to `POSITIONAL_KEY_ACCEPTED` (or author an `IdentitySpec`).

### Part B — in-flight-on-resume read-back

- [ ] **Step 6: Write the failing test**

Create `backend/tests/test_inflight_resume_readback.py`:

```python
"""Step-1 carry-forward: on the ledger's in_flight conflict at resume, a read-back
resolves whether the prior attempt's write actually fired. Confirmed -> already_done
(don't re-fire); contradicted/unverified -> escalate (stay fail-closed, now diagnosed)."""

from src.services.verification.inflight import InflightResolution, resolve_inflight_on_resume
from src.services.verification.readback import VerifyVerdict


def test_confirmed_prior_effect_is_already_done():
    assert resolve_inflight_on_resume(VerifyVerdict.CONFIRMED) == InflightResolution.ALREADY_DONE


def test_contradicted_prior_effect_escalates():
    assert resolve_inflight_on_resume(VerifyVerdict.CONTRADICTED) == InflightResolution.ESCALATE


def test_unverified_prior_effect_escalates_fail_closed():
    # Unknown whether it fired -> stay fail-closed, but surface it (don't silently drop).
    assert resolve_inflight_on_resume(VerifyVerdict.UNVERIFIED) == InflightResolution.ESCALATE
```

- [ ] **Step 7: Write `verification/inflight.py`**

Create `backend/src/services/verification/inflight.py`:

```python
"""In-flight-on-resume resolution (Step-1 carry-forward, spec §6 Step 3).

Step 1's ledger fails CLOSED on an in_flight conflict at resume (a prior attempt
reserved the identity but we never saw record_success — the worker may have crashed
after the external API call but before the checkpoint). Step 3 adds a read-back to
DIAGNOSE it: if the post-condition confirms the effect already landed, resolve to
ALREADY_DONE (don't re-fire); otherwise stay fail-closed but ESCALATE (surface it)
rather than silently blocking.
"""

from __future__ import annotations

from enum import Enum

from src.services.verification.readback import VerifyVerdict


class InflightResolution(str, Enum):
    ALREADY_DONE = "already_done"  # prior write confirmed by read-back — do not re-fire
    ESCALATE = "escalate"  # unknown/contradicted — stay fail-closed, surface to the user


def resolve_inflight_on_resume(verdict: VerifyVerdict) -> InflightResolution:
    """Map a resume-time read-back verdict to a ledger resolution. Only a CONFIRMED
    read-back is safe to treat as already-done; everything else escalates (fail-closed
    is preserved — we never re-fire an ambiguous in-flight write)."""
    if verdict == VerifyVerdict.CONFIRMED:
        return InflightResolution.ALREADY_DONE
    return InflightResolution.ESCALATE
```

> **Note for the implementer:** the Step-1 ledger's `reserve` returns an `in_flight_conflict` outcome (`idempotency/ledger.py`). Wiring `resolve_inflight_on_resume` into that branch (run the read-back for the reserved capability's persisted args, then act on the resolution) is the live integration. If the ledger-wrapper seam for the read-back isn't readily available at that layer in this pass, land the pure resolver + its test here and open a bounded follow-up to invoke it at the `in_flight_conflict` site — the fail-closed default is unchanged, so this is strictly additive diagnosis. Document which you did in the commit.

- [ ] **Step 8: Run + lint + commit**

Run: `uv run pytest tests/test_identity_coverage_gate.py tests/test_inflight_resume_readback.py -q`
Expected: all PASS.

```bash
uv run ruff check src/services/idempotency/identity.py src/services/verification/inflight.py src/api/app.py tests/test_identity_coverage_gate.py tests/test_inflight_resume_readback.py
git add -A
git commit -m "feat(rebuild): identity-coverage startup hard-gate + in-flight-on-resume read-back (Step 3)

Step-1 carry-forwards. (A) validate_identity_coverage_strict: every irreversible write
cap must have a semantic IdentitySpec or be explicit POSITIONAL_KEY_ACCEPTED — wired
as a fail-closed startup gate mirroring validate_registry. (B) resolve_inflight_on_resume:
a confirmed read-back on the ledger's in_flight conflict resolves to already-done (don't
re-fire); otherwise stay fail-closed but escalate (diagnosed, not silent)."
```

---

## Task 11: End-to-end verify→partially_completed→escalate→compensate test + full-suite gate

The spec's mandated integration deliverable: "an end-to-end verify→partially_completed→escalate→compensate test (§4.5)." Plus the whole-suite + drift-free confirmation.

**Files:**
- Test: `backend/tests/test_verification_e2e.py`

- [ ] **Step 1: Write the end-to-end test**

Create `backend/tests/test_verification_e2e.py`:

```python
"""End-to-end (§4.5): an irreversible write whose read-back CONTRADICTS the expected
effect lands the step 'partially_completed', builds a divergence escalation carrying
the exact artifact_ref, and offers the registered compensator. Exercises the real
ReadBackVerifier + compensation registry against a mocked read seam (no connectors)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.verification.compensation import build_divergence_escalation
from src.services.verification.readback import (
    ReadBackVerifier,
    VerifyVerdict,
    verdict_to_step_status,
)


async def test_verify_contradicted_escalate_compensate_flow():
    risk = SimpleNamespace(reversible=False, blast_radius="external_multiple", risk_level="medium")

    # 1. Read-back CONTRADICTS: calendar.get returns no matching event.
    read_fn = AsyncMock(return_value=[])
    verifier = ReadBackVerifier(read_fn=read_fn)
    verdict = await verifier.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_missing"},
        risk=risk,
    )
    assert verdict == VerifyVerdict.CONTRADICTED

    # 2. Verdict maps to the step-level partially_completed terminal status.
    assert verdict_to_step_status(verdict) == "partially_completed"

    # 3. Escalation carries the exact artifact_ref + the registered compensator.
    escalation = build_divergence_escalation(
        capability="calendar.create",
        artifact_ref={"event_id": "evt_missing"},
        observed="event not found on read-back",
    )
    assert escalation["artifact_ref"] == {"event_id": "evt_missing"}
    assert escalation["compensator"]["capability"] == "calendar.delete"
    # 4. The compensator input is derived from the artifact_ref (gated + idempotent on run).
    assert escalation["compensator"]["input"] == {"event_id": "evt_missing"}


async def test_confirmed_flow_marks_completed_no_escalation():
    risk = SimpleNamespace(reversible=False, blast_radius="external_multiple", risk_level="medium")
    read_fn = AsyncMock(return_value={"id": "evt_1"})
    verifier = ReadBackVerifier(read_fn=read_fn)
    verdict = await verifier.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=risk,
    )
    assert verdict == VerifyVerdict.CONFIRMED
    assert verdict_to_step_status(verdict) == "completed"
```

- [ ] **Step 2: Run the e2e test**

Run: `uv run pytest tests/test_verification_e2e.py -q`
Expected: both PASS.

- [ ] **Step 3: Run the FULL non-e2e suite (the gate)**

Run:
```bash
uv run pytest tests/ --ignore=tests/e2e -q -p no:cacheprovider 2>&1 | tail -15
```
Expected: **≥ 2977 + the new Step-3 tests passed / 18 skipped** (baseline was ~2977). Investigate any new failure — a regression in a step-counting site (Task 3) or the `finalize_step` signature (Task 6) is the likeliest culprit. Zero new failures is the bar.

- [ ] **Step 4: Confirm NO schema drift (no migration was added)**

Run:
```bash
uv run alembic current 2>&1 | tail -1     # expect: b3e8c1f5a9d2 (head) — UNCHANGED
uv run alembic check 2>&1 | tail -5        # expect: No new upgrade operations detected.
```
Expected: head is still `b3e8c1f5a9d2` (Step 3 adds no migration — statuses are varchars, registries are code, verification metadata is JSONB). If `alembic check` reports operations, a model was accidentally changed — revert it (Step 3 must not touch models).

- [ ] **Step 5: Startup smoke — both coverage gates pass against the real catalog**

Run (verifies the app can construct — the two new startup gates don't abort on the real registry):
```bash
uv run python -c "
from src.services.verification.post_conditions import validate_post_condition_coverage
from src.services.verification.predicate import write_capabilities, is_irreversible_capability
from src.services.idempotency.identity import validate_identity_coverage_strict
wc = write_capabilities()
irr = {c for c in wc if is_irreversible_capability(c)}
pc = validate_post_condition_coverage(wc)
idc = validate_identity_coverage_strict(irr)
print('post-condition errors:', pc)
print('identity errors:', idc)
assert not pc and not idc, 'coverage gate would abort startup'
print('BOTH COVERAGE GATES PASS')
"
```
Expected: `BOTH COVERAGE GATES PASS`. Any listed capability is one the real catalog exposes that this plan's registries missed — add it to `UNVERIFIABLE_CAPABILITIES` / `POSITIONAL_KEY_ACCEPTED` (the plan's lists were derived on 2026-07-05; re-run to catch drift).

- [ ] **Step 6: Ruff over everything + final commit**

```bash
uv run ruff check src/ tests/ 2>&1 | tail -5
uv run ruff format --check src/services/verification/ src/services/scheduler/deferred_verification_tick.py 2>&1 | tail -3
git add -A
git commit -m "test(rebuild): end-to-end verify->partially_completed->escalate->compensate (Step 3)

The §4.5 mandated integration test: a contradicted read-back on an irreversible write
lands partially_completed, escalates with the exact artifact_ref, and offers the
registered compensator. Full non-e2e suite green; no migration (alembic check clean,
head unchanged); both startup coverage gates pass the real catalog."
```

---

## Self-Review (run against the spec with fresh eyes — checklist, not a subagent)

**1. Spec coverage (§4.5 + §6 Step 3 + §4.3 verification-side):**
- ✅ Replace by-fiat `completed` sites with an inline risk-gated read-back gate → Task 6 (`finalize_step` status-parametrized; `execute_step` computes verdict). The named sites (`step_runner` LLM-prose fallback/aggregate return advisory dicts; the DB write is `finalize_step:650`) are covered by gating the single DB transition (D7).
- ✅ Mandatory only when IRREVERSIBLE (shared §4.3 predicate) → Task 1 (`IRREVERSIBLE` + `is_write_verification_required` fail-closed union).
- ✅ Characterization test forbidding a terminal status without a passing post-condition or explicit `completed_unverified` → Task 6 (`test_finalize_verification.py`).
- ✅ Net-new `completed_unverified` (non-terminal success, upgradeable) + step-level `partially_completed` → Task 2; threaded through transition tables, counters (Task 3), StepList icons + projections (Task 4), outcome_learner (Task 3).
- ✅ `TERMINAL_SUCCESS = {completed, completed_unverified}` membership replacing literal counters → Task 3 (incl. the CRITICAL dependency-satisfaction site).
- ✅ Relocate the trust-graduation increment to fire only on confirmed-verified completion → Task 6 (gate on CONFIRMED) + Task 7 (metadata) + Task 9 (deferred increment on later confirm).
- ✅ Post-condition + compensation registries; post-condition coverage as a STARTUP validation error mirroring `validate_registry()` → Task 5 (coverage gate) + Task 8 (compensation, escalate-first, no gate per D9).
- ✅ Compensation escalate-first with artifact_ref + observed divergence; no compensator → escalate anyway → Task 8.
- ✅ Async fast-path deferred-read executor with give-up TTL; upgrade `completed_unverified → completed`; async-divergence surface via Notifier hold-for-briefing → Task 9.
- ✅ Step-1 carry-forwards: `validate_identity_coverage` → startup hard-gate; in_flight-on-resume read-back + compensation → Task 10.
- ✅ Schema-touching posture: proven migration-free (statuses are varchars) → in-flight posture section + Task 11 Step 4.
- ✅ Out-of-scope respected: NO gate override / authorization_source / kill-Operator (Step 6); NO world-model contradiction/confidence (Step 4); NO AsyncPostgresSaver wiring (Step 10). The `IRREVERSIBLE` predicate is built but only wired on the verification side.

**2. Placeholder scan:** The only non-verbatim spots are (a) the `test_unverified_write_does_not_increment_trust` harness-copy stub in Task 7 (explicitly delegated to `test_trust_feedback.py`'s existing DagRunner construction — a real, named source, not a TBD) and (b) three flagged single-integration-points (`step_runner.run_readback` arg order, the tick's `Notifier`/`TrustGate` signatures, the ledger `in_flight` wiring) — each names the exact existing function to align against and has a safe default. All verification *logic* is complete + tested with mocks.

**3. Type/name consistency:** `VerifyVerdict` (CONFIRMED/CONTRADICTED/UNVERIFIED), `verdict_to_step_status`, `is_write_verification_required`, `is_irreversible_capability`, `write_capabilities`, `TERMINAL_SUCCESS`, `build_verification_meta`, `build_divergence_escalation`, `POST_CONDITIONS`/`UNVERIFIABLE_CAPABILITIES`, `COMPENSATIONS`, `POSITIONAL_KEY_ACCEPTED`, `validate_post_condition_coverage`/`validate_identity_coverage_strict` — used identically across every task that references them.

---

## Scope note & natural phasing seam

Step 3 is the reliability core and is the largest step so far (11 tasks). It is one coherent plan — the characterization guarantee (Task 6) and the trust relocation (Task 7) depend on the status threading (Tasks 2–3), so the core must land together. If execution is split across sessions, a natural seam is:
- **3a (enforcement core):** Tasks 1–7 + 11 — predicate, statuses + threading, frontend, post-condition registry + gate, the inline gate + characterization, trust relocation, and the suite gate. This alone delivers "irreversible writes can't be marked done/graduate trust without confirmation."
- **3b (recovery + async + carry-forwards):** Tasks 8–10 — compensation/escalate-first, the deferred-read tick, and the Step-1 carry-forwards. These close §4.5's "failed verification with no remediation" and "async fast-path" and the identity/in-flight carry-forwards.

Both halves are independently green and committable.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-28-step3-enforced-verification-compensation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, two-stage review (spec + quality) in parallel on the frozen commit, a final holistic review — matching the Step-1/Step-2 flow.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.

**Before dispatching any subagent, this plan doc is committed** (per the Step-2 OPS lesson: a reviewer's `git stash --include-untracked` orphaned Step-2's untracked plan doc). Committed tracked files survive stash/checkout/clean.
