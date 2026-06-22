"""Regression test for the awaiting_approval → awaiting_approval crash.

Reproduces production log: when a ready batch has two independent steps that both
require approval, the second ``create_approval_and_pause`` ran while the run was
already ``awaiting_approval`` and raised InvalidTransitionError. The run-transition
must be idempotent so a sibling step cannot crash the DAG.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.contracts import PolicyDecision
from src.models.task_graph import TaskRun, TaskStep
from src.services.risk_assessor import RiskAssessment
from src.services.trust_gate import TrustGate


def _make_run() -> TaskRun:
    run = TaskRun()
    run.run_id = "run_idem_test"
    run.user_id = "usr_test"
    run.workspace_id = "ws_test"
    run.status = "running"
    run.checkpoint = {}
    return run


def _make_step(step_id: str) -> TaskStep:
    step = TaskStep()
    step.step_id = step_id
    step.status = "ready"
    step.name = f"Step {step_id}"
    step.input_data = {"capability": "email.send"}
    return step


def _make_gate(db) -> TrustGate:
    store = MagicMock()
    store.checkpoint = AsyncMock()
    emitter = MagicMock()
    emitter.emit_event = AsyncMock()
    emitter.emit_surface_update = AsyncMock()
    return TrustGate(
        db=db,
        client=MagicMock(),
        redis=None,
        notifier_provider=lambda: None,
        store=store,
        emitter=emitter,
    )


@patch("src.services.approval_service.create_approval")
async def test_second_approval_on_same_run_does_not_crash(mock_create_approval):
    """Two ready steps both needing approval → run pauses once, no exception."""
    mock_create_approval.return_value = MagicMock(approval_id="apr_x", expires_at=None)

    db = MagicMock()
    db.flush = AsyncMock()
    gate = _make_gate(db)

    run = _make_run()
    risk = RiskAssessment(risk_level="high", reasoning="r", reversible=False)
    decision = PolicyDecision(decision="approval_required", reason="needs approval")

    step_a = _make_step("step_a")
    await gate.create_approval_and_pause(run, step_a, "email.send", risk, decision)
    assert run.status == "awaiting_approval"
    assert step_a.status == "waiting_approval"

    # Sibling step in the same batch — previously raised InvalidTransitionError.
    step_b = _make_step("step_b")
    await gate.create_approval_and_pause(run, step_b, "email.send", risk, decision)
    assert run.status == "awaiting_approval"
    assert step_b.status == "waiting_approval"


@patch("src.services.approval_service.create_approval")
async def test_create_approval_and_pause_populates_artifact_refs(mock_create_approval):
    """The approval must carry a preview of what will be executed (artifact_refs)."""
    mock_create_approval.return_value = MagicMock(approval_id="apr_y", expires_at=None)

    db = MagicMock()
    db.flush = AsyncMock()
    gate = _make_gate(db)

    run = _make_run()
    risk = RiskAssessment(
        risk_level="high", reasoning="r", reversible=False, blast_radius="external_single"
    )
    decision = PolicyDecision(decision="approval_required", reason="needs approval")

    step = _make_step("step_a")
    step.name = "Send launch email"
    step.input_data = {"capability": "email.send", "description": "Email investors"}

    await gate.create_approval_and_pause(run, step, "email.send", risk, decision)

    _, kwargs = mock_create_approval.call_args
    refs = kwargs["artifact_refs"]
    assert refs["capability"] == "email.send"
    assert refs["step_name"] == "Send launch email"
    assert refs["description"] == "Email investors"
    assert refs["reversible"] is False
    assert refs["blast_radius"] == "external_single"
