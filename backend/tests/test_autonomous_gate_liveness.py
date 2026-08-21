"""Autonomous runs must make progress with no human present.

Regression for the perception deadlock observed live (2026-08-18/19): every
perception run parked on its FIRST step — a pure read (``email.list`` /
``email.read``) that the RiskAssessor scored ``risk_level="none"`` — because
``TrustEngine._matrix_lookup`` returns ``approval_required`` for EVERY risk at
``first_use``, and every capability starts at ``first_use``. The run froze at
``awaiting_approval``, nobody was reachable to answer, the approval expired 24h
later, and the run was cancelled having executed 0 of N steps.

Two independent defects produced that, and both are covered here:

* **B — the gate asked at all.** Pure reads and Muldro's own internal
  ``system.*`` action capabilities are exempt on the chat path
  (``permission_gate``); the DAG gate had no such exemption.
* **C — a CONFIRM verdict froze the run.** With no human on an autonomous run,
  ``approval_required`` must PREPARE (stage the real tool call for review and
  carry on), never interrupt into a void. The step still runs, but WITHOUT its
  capability pre-approved, so the inner deep ``trust_gate`` gates the actual
  tool call — which has a replayable payload — instead of the DAG pre-approving
  it away.

The invariant these pin, in one line: **an autonomous run never parks waiting
for a human who is not there.**
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.contracts import PolicyDecision
from src.models.task_graph import TaskRun, TaskStep
from src.services.dag_runner import DagRunner
from src.services.risk_assessor import RiskAssessment


def _make_run() -> TaskRun:
    run = TaskRun()
    run.run_id = "run_liveness"
    run.user_id = "usr_test"
    run.workspace_id = "ws_test"
    run.status = "running"
    run.checkpoint = {}
    run.input_tokens = 0
    run.output_tokens = 0
    run.cost_usd = 0.0
    return run


def _make_step(capability: str) -> TaskStep:
    step = TaskStep()
    step.step_id = f"step_{capability.replace('.', '_')}"
    step.status = "ready"
    step.name = f"Step {capability}"
    step.input_data = {"capability": capability}
    step.retry_count = 0
    step.max_retries = 3
    return step


def _make_runner(*, decision: str = "approval_required", presence: str = "absent"):
    """A DagRunner with every collaborator mocked except the code under test.

    Returns ``(dag_runner, spies)`` where ``spies`` exposes the calls the
    assertions care about.
    """
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    store = MagicMock()
    store.checkpoint = AsyncMock()
    store.resolve_step_references = AsyncMock(side_effect=lambda step, _rid: step.input_data or {})

    trust_gate = MagicMock()
    trust_gate.assess_step_risk = AsyncMock(
        return_value=RiskAssessment(risk_level="none", reasoning="read-only", reversible=True)
    )
    trust_gate.create_approval_and_pause = AsyncMock()
    trust_gate.notify_auto_executed = AsyncMock()
    trust_gate.record_auto_execution_outcome = AsyncMock()

    trust_engine = MagicMock()
    trust_engine.evaluate = AsyncMock(
        return_value=PolicyDecision(decision=decision, reason="test", risk_level="none")
    )

    runner = MagicMock()
    runner.run_step_action = AsyncMock(return_value={"status": "completed", "result": "ok"})

    learner = MagicMock()
    learner.verification_enabled = False

    emitter = MagicMock()
    emitter.emit_event = AsyncMock()
    emitter.emit_surface_update = AsyncMock()

    dag = DagRunner(
        db=db,
        store=store,
        trust_gate=trust_gate,
        runner=runner,
        learner=learner,
        emitter=emitter,
        trust_engine_provider=lambda: trust_engine,
        presence=presence,
    )
    # _finalize_with_verification pulls in the verification stack; the gate
    # decision is what these tests exercise, so stub the tail.
    dag._finalize_with_verification = AsyncMock(return_value=_CONFIRMED)
    dag._defer_for_reauth = AsyncMock(return_value=False)

    spies = MagicMock()
    spies.trust_engine = trust_engine
    spies.trust_gate = trust_gate
    spies.runner = runner
    return dag, spies


class _Confirmed:
    """Stand-in for VerifyVerdict.CONFIRMED (the real enum imports the verifier)."""

    value = "confirmed"


_CONFIRMED = _Confirmed()


# ── B: the gate must not ask about reads or internal system actions ──────────


async def test_read_capability_is_not_gated():
    """A pure read (email.list) never reaches the TrustEngine — it just runs.

    This is the exact capability that deadlocked every live perception run.
    """
    dag, spies = _make_runner()
    run, step = _make_run(), _make_step("email.list")

    await dag.execute_step(run, step)

    spies.trust_engine.evaluate.assert_not_awaited()
    spies.trust_gate.create_approval_and_pause.assert_not_awaited()
    spies.runner.run_step_action.assert_awaited_once()
    assert run.status == "running"


async def test_system_action_capability_is_not_gated():
    """system.add_to_brief writes into Muldro's own data layer — never gated.

    Mirrors permission_gate's SYSTEM_ACTION_CAPABILITIES pass-through on chat.
    """
    dag, spies = _make_runner()
    run, step = _make_run(), _make_step("system.add_to_brief")

    await dag.execute_step(run, step)

    spies.trust_engine.evaluate.assert_not_awaited()
    assert run.status == "running"


async def test_external_write_is_still_gated():
    """The exemption must not leak: a real outbound write still hits the gate."""
    dag, spies = _make_runner(decision="auto_execute_silent")
    run, step = _make_run(), _make_step("email.send")

    await dag.execute_step(run, step)

    spies.trust_engine.evaluate.assert_awaited_once()


async def test_unknown_capability_is_still_gated():
    """A capability absent from the catalog is NOT read-only — fail closed."""
    dag, spies = _make_runner(decision="auto_execute_silent")
    run, step = _make_run(), _make_step("system.respond")

    await dag.execute_step(run, step)

    spies.trust_engine.evaluate.assert_awaited_once()


# ── C: a CONFIRM verdict with nobody present PREPARES, never freezes ─────────


async def test_approval_required_with_nobody_present_does_not_pause_the_run():
    """The core liveness invariant: an absent run is never parked on a human."""
    dag, spies = _make_runner(decision="approval_required", presence="absent")
    run, step = _make_run(), _make_step("email.send")

    await dag.execute_step(run, step)

    spies.trust_gate.create_approval_and_pause.assert_not_awaited()
    assert run.status == "running", "an absent autonomous run must not park on approval"
    spies.runner.run_step_action.assert_awaited_once()


async def test_prepared_step_runs_without_pre_approving_its_capability():
    """PREPARE hands the decision to the INNER gate, which sees the real tool call.

    If the DAG pre-approved the capability here, the inner deep ``trust_gate``
    would short-circuit and the write would execute UNGATED — the exact gap
    CLAUDE.md flags under "GraphExecutor DAG steps".
    """
    dag, spies = _make_runner(decision="approval_required", presence="absent")
    run, step = _make_run(), _make_step("email.send")

    await dag.execute_step(run, step)

    kwargs = spies.runner.run_step_action.await_args.kwargs
    assert kwargs["pre_approve_capability"] is False


async def test_auto_executed_step_does_pre_approve_its_capability():
    """An auto-execute verdict keeps today's behaviour: no double-prompting."""
    dag, spies = _make_runner(decision="auto_execute_silent")
    run, step = _make_run(), _make_step("email.send")

    await dag.execute_step(run, step)

    kwargs = spies.runner.run_step_action.await_args.kwargs
    assert kwargs["pre_approve_capability"] is True


async def test_prepared_step_does_not_reinforce_trust():
    """Nothing was approved and nothing may have executed — trust must not graduate.

    Otherwise a run of prepared (i.e. NOT executed) writes would graduate the
    capability to ``autonomous`` and start executing them silently.
    """
    dag, spies = _make_runner(decision="approval_required", presence="absent")
    run, step = _make_run(), _make_step("email.send")

    await dag.execute_step(run, step)

    spies.trust_gate.record_auto_execution_outcome.assert_not_awaited()


async def test_approval_required_with_a_human_present_still_pauses():
    """The interrupt branch is preserved for any caller that has a human on the run."""
    dag, spies = _make_runner(decision="approval_required", presence="present")
    run, step = _make_run(), _make_step("email.send")

    await dag.execute_step(run, step)

    spies.trust_gate.create_approval_and_pause.assert_awaited_once()
    spies.runner.run_step_action.assert_not_awaited()


async def test_dag_runner_defaults_to_absent():
    """A DagRunner built without stating presence must fail SAFE (never interrupt)."""
    dag, spies = _make_runner(decision="approval_required")
    run, step = _make_run(), _make_step("email.send")
    dag._presence = "absent"

    await dag.execute_step(run, step)

    assert run.status == "running"


async def test_cancel_event_still_honoured_on_the_prepared_path():
    """PREPARE must not smuggle past cancellation — the run can still be stopped."""
    dag, spies = _make_runner(decision="approval_required", presence="absent")
    run, step = _make_run(), _make_step("email.send")
    cancel = asyncio.Event()

    await dag.execute_step(run, step, cancel_event=cancel)

    assert spies.runner.run_step_action.await_args.kwargs["cancel_event"] is cancel


# ── B, inner gate: the exemption must hold wherever a capability is gated ────


def test_both_write_gates_exempt_the_same_capabilities():
    """``trust_gate`` and ``permission_gate`` must agree on what is always-allowed.

    They are separate gates asking different questions (per-capability trust vs
    per-action risk), but "is this an internal system action or a read?" is not
    one of those questions — it is the same fact, and a capability exempt on the
    chat path but gated on the autonomous one is drift, not policy. Asserted
    against the modules' own imports so adding an exemption to one and not the
    other fails here rather than in production.
    """
    from src.deep_runtime.middleware import permission_gate, trust_gate

    assert trust_gate.SYSTEM_ACTION_CAPABILITIES is permission_gate.SYSTEM_ACTION_CAPABILITIES, (
        "both write gates must exempt internal system.* actions from the same set"
    )
    assert trust_gate.is_read_only_capability is permission_gate.is_read_only_capability
