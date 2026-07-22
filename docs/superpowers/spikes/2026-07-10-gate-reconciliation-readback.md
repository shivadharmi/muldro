# Spike — Gate reconciliation + read-back seam (Step 10C Phase 0.3, SQ2/SQ3)

Date: 2026-07-10
Probe: `backend/spikes/deep_autonomous/probe_gate_reconcile.py`
Run: `uv run python -m spikes.deep_autonomous.probe_gate_reconcile` → exit 0, `RESULT=CONFIRMED`

## Question (SQ2)

When the autonomous step-executor becomes a deep agent with
`authorization_source=AUTONOMOUS`, TWO approval gates fire for a write step:

- **STEP gate** (`dag_runner.execute_step`, `dag_runner.py:337-346`): `assess_step_risk`
  → `TrustEngine.evaluate` → `approval_required` pauses the run to `awaiting_approval`
  and persists `Approval(run_id, step_id)`. The durable, scheduler-driven mechanism the
  autonomous system relies on. **Stays.**
- **Deep TOOL-CALL gate** (`trust_gate.make_trust_gate_middleware`): `is_gated_source(
  "autonomous") == True` (`authorization.py:23`), so for an AUTONOMOUS write it resolves
  the capability, assesses risk, persists `Approval(workspace_id, thread_id,
  tool_call_id)`, and `interrupt()`s.

⇒ Autonomous write = **double approval**. How to reconcile without a migration?

## Constraint that kills the obvious fix

`make_thread_id(ws)` = `c:{workspace_id}:{ulid}` ≈ **58 chars** (measured: `THREAD_ID_UNCHANGED_LEN=58`) in an `Approval.thread_id = String(64)` column — ~6
chars headroom. Embedding `run_id`/`step_id` into the thread_id to correlate the two
gates would overflow `String(64)` and force a migration 10C forbids. So the reconciler
must correlate by **capability captured at the seam**, not by a thread_id lookup.

## What the probe proved (verbatim final block)

```
DEEP_GATE_INTERRUPTS_FOR_AUTONOMOUS_WRITE=True
PREAPPROVED_CAP_SHORT_CIRCUITS=True
UNAPPROVED_WRITE_STILL_GATES=True
THREAD_ID_UNCHANGED_LEN=58
THREAD_ID_LEQ_64=True
WS_RECOVERABLE_FROM_THREAD_ID=True
FINALIZE_INPUT_KEYS=['errors', 'result', 'status', 'tools_called']
FINALIZE_SEAM_CONSUMES_SHAPE=True
```

- **Obs 1 — double-gate EXISTS.** REAL `make_trust_gate_middleware`, `AUTONOMOUS` + a
  write (`email.send`): the deep gate reaches `interrupt()` (graph paused, tool NOT
  executed, exactly ONE `Approval` persisted for the thread). Confirms the SECOND gate
  fires independently of the step gate.
- **Obs 2a — pre-approved capability short-circuits.** Branch-C gate variant with
  `pre_approved_capabilities={"email.send"}`: `email.send` passes through — tool
  executes, NO interrupt, **NO second Approval row**.
- **Obs 2b — un-approved write STILL gates.** Same gate, model calls a DIFFERENT write
  (`payment.send` ∉ pre-approved): STILL interrupts (Approval persisted, tool NOT
  executed). The gate is **not dead-wired** — it still gates within-step capability
  expansion.
- **thread_id unchanged.** All three scenarios use `make_thread_id(ws)` verbatim; max
  length 58 ≤ 64; `workspace_of_thread_id` recovers the workspace for all three. No
  migration.
- **Obs 3 — read-back seam shape (SQ3).** The deep step-output dict
  `{"status","result","tools_called","errors"}` (+ `auth_required` passthrough) is fed
  through `dag_runner.build_verification_meta` and `execution_support._detect_auth_required`
  with no KeyError.

## Branch-C mechanism (the delta from `src`)

The probe's `make_branch_c_trust_gate` is the REAL gate body with **one** added line
inserted right after the read-only pass and before the replay-find / risk / persist /
interrupt sequence, plus one closure param:

```python
# capability captured at the seam = the step's already-TrustEngine-approved capability
if capability in pre_approved_capabilities:
    return await handler(request)      # pass through: no risk, no Approval, no interrupt
```

Everything else (`_resolve_capability`, `is_read_only_capability`,
`_find_existing_approval`, `_decide_and_maybe_persist`, `interrupt`) is reused verbatim
from `src/deep_runtime/middleware/trust_gate.py`. Production shape for 10C P1: add a
`pre_approved_capabilities: frozenset[str] = frozenset()` param to
`make_trust_gate_middleware` (default empty ⇒ byte-neutral for the chat path, which
already short-circuits earlier on `direct_user_request` anyway); the autonomous step
seam passes `{step.capability}`.

## Read-back seam (SQ3) — the `_finalize_with_verification` input contract

Producer — `step_runner.run_step_via_agent_loop` (`src/services/step_runner.py:440-453`):

```python
output = {"status": "completed", "result": text, "tools_called": tools_called, "errors": errors}
# auth path also sets: status="error", error_code="auth_required", provider, server, auth_required
```

Consumers in `dag_runner` read from that dict:

- pre-finalize `_defer_for_reauth` → `execution_support._detect_auth_required`
  (`execution_support.py:28-46`) reads `output["auth_required"]` (nested) + `output["error_code"]`.
- `_finalize_with_verification` (`dag_runner.py:552-594`) passes `write_output=output` to
  `ReadBackVerifier.verify_step`; `build_verification_meta` (`dag_runner.py:53-70`) reads
  optional artifact-ref keys `output.get(k)` for `k in ("event_id","id","message_id","url","thread_id")`.
- `finalize_step` (`dag_runner.py:778-844`) stores `output` to `step.output_data` and reads
  `output.get("result")`, `output.get("summary")`, membership of `("draft","report","summary","result","view")`.

`status`/`tools_called`/`errors` are carried into `step.output_data` (history + surfaces:
`_step_to_state` reads `output_data["result"]`) but `_finalize_with_verification` itself
branches only on `result`/`summary`/the artifact-ref set (+ pre-finalize
`auth_required`/`error_code`). SQ3 stays **Branch A**: keep the inline
`dag_runner._finalize_with_verification` seam (TaskStep-bound: `finalize_step`,
world-model reconcile, trust reinforcement, escalation); the deep `read_back` middleware
stays DORMANT (`read_fn=None`, `deep_readback_enabled=False`). No read-back change in 10C.

## Decision

```
DECISION: double-gate observed = YES; SQ2 -> Branch C; Branch-C mechanism = capability-set short-circuit at deep trust_gate, NO thread_id change; read-back unification -> DEFER to B4/10D (SQ3 Branch A); _finalize_with_verification input keys = ['errors', 'result', 'status', 'tools_called'].
```

## Consequence for P2 (Branch A avoidance)

Branch A (interrupt-based reconciliation, requiring a `GraphInterrupt` → run-pause bridge
in P2) is **AVOIDABLE**. Because the capability-set short-circuit (Branch C) is proven
feasible, the autonomous executor never reaches `interrupt()` for the step's
already-approved capability — the step-level TrustEngine gate remains the *sole* durable
pause. P2 needs **NO** `GraphInterrupt` → run-pause bridge. The deep gate still interrupts
only for un-approved within-step capability *expansion* (Obs 2b) — a rare edge that P1 can
handle by fail-blocking (return a blocked `ToolMessage`) rather than building a full
run-pause bridge; unified interrupt-driven pause can be revisited later if that edge ever
needs a durable approval round-trip.

## Files

- `backend/spikes/deep_autonomous/probe_gate_reconcile.py` (throwaway probe)
- `docs/superpowers/spikes/2026-07-10-gate-reconciliation-readback.md` (this doc)
