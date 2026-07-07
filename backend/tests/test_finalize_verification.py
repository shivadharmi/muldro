"""Characterization test (spec §4.5): no write path emits a terminal step status
without a passing post-condition OR an explicit completed_unverified verdict.

Two layers:
  1. The pure verdict->status mapping (total, and only CONFIRMED -> 'completed').
  2. The call-graph guard: drive DagRunner.execute_step (via GraphExecutor) with an
     irreversible write whose read-back is UNVERIFIED through BOTH terminal paths —
     auto-execute AND approved-resume — and assert the step lands 'completed_unverified'
     (never bare 'completed'). This is what actually forbids a future write call site
     from reaching finalize_step with the default 'completed'.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.risk_assessor import RiskAssessment
from src.services.verification.readback import VerifyVerdict, verdict_to_step_status
from tests.conftest import make_mock_settings


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


# ── Call-graph guard: drive execute_step end-to-end through BOTH terminal paths ──


def _make_executor(settings, mock_db, trust_engine=None):
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        return GraphExecutor(settings, mock_db, trust_engine=trust_engine)


def _make_step(status, capability="email.send"):
    step = MagicMock()
    step.step_id = "step_wr_001"
    step.name = f"Step: {capability}"
    step.status = status  # a REAL string so transition_step validates against STEP_TRANSITIONS
    step.input_data = {"capability": capability}
    step.started_at = None
    step.completed_at = None
    step.output_data = None
    step.depends_on = []
    step.task_id = "task_001"
    step.retry_count = 0
    step.max_retries = 3
    step.timeout_seconds = None
    step.error = None
    return step


def _make_run():
    run = MagicMock()
    run.run_id = "run_001"
    run.user_id = "usr_test"
    run.workspace_id = "ws_test"
    run.status = "running"
    run.checkpoint = {}
    return run


def _wire_common(executor):
    """Stub the collaborators execute_step touches, but keep finalize_step + the real
    verifier so the terminal status is actually driven by the verdict (not mocked)."""
    executor._runner.run_step_action = AsyncMock(return_value={"ok": True})
    executor._surface_emitter.emit_event = AsyncMock()
    executor._store.checkpoint = AsyncMock()
    executor._store.resolve_step_references = AsyncMock(return_value={"capability": "email.send"})
    # run_readback must never be reached for email.send (UNVERIFIABLE -> no read seam).
    executor._runner.run_readback = AsyncMock(
        side_effect=AssertionError("run_readback must not be called for an UNVERIFIABLE cap")
    )
    # escalate must never fire on an UNVERIFIED (only CONTRADICTED escalates).
    executor._dag_runner._escalate_divergence = AsyncMock(
        side_effect=AssertionError("_escalate_divergence must not fire on UNVERIFIED")
    )


@patch("src.services.trust_gate.get_or_assess_risk")
async def test_auto_execute_irreversible_unverified_lands_completed_unverified(mock_risk):
    """Auto-execute path: an irreversible write with no read-back seam must land
    'completed_unverified' — NEVER bare 'completed' — and trust reinforcement must
    NOT fire (verdict != CONFIRMED)."""
    from src.contracts import PolicyDecision

    settings = make_mock_settings()
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    risk = RiskAssessment(risk_level="high", reasoning="irreversible", reversible=False)
    mock_risk.return_value = risk

    trust_engine = AsyncMock()
    trust_engine.evaluate = AsyncMock(
        return_value=PolicyDecision(
            decision="auto_execute_silent", justification="trusted", risk_level="high"
        )
    )

    executor = _make_executor(settings, mock_db, trust_engine=trust_engine)
    _wire_common(executor)
    executor._trust_gate.record_auto_execution_outcome = AsyncMock()

    # 'ready' -> the auto-execute branch transitions ready->running->completed_unverified.
    step = _make_step(status="ready")
    run = _make_run()

    await executor._execute_step(run, step)

    assert step.status == "completed_unverified"
    # Trust reinforcement is gated on CONFIRMED — an UNVERIFIED write must not graduate.
    executor._trust_gate.record_auto_execution_outcome.assert_not_called()


@patch("src.services.trust_gate.get_or_assess_risk")
async def test_approved_resume_irreversible_unverified_lands_completed_unverified(mock_risk):
    """Approved-resume path (the highest-risk write class): a HUMAN-approved
    irreversible write must ALSO be verified — landing 'completed_unverified' when the
    read-back is UNVERIFIED, never bare 'completed'. On UNVERIFIED the positive trust
    increment does NOT fire at finalize (Step 6C relocates it to the CONFIRMED outcome);
    instead the user's decision_type is stamped into the verification meta so the deferred
    tick can increment with it."""
    settings = make_mock_settings()
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    mock_risk.return_value = RiskAssessment(
        risk_level="high", reasoning="irreversible", reversible=False
    )

    executor = _make_executor(settings, mock_db)
    _wire_common(executor)
    executor._trust_gate.record_user_approval_outcome = AsyncMock()
    # The persisted decision_type read is exercised separately (test_trust_increment_
    # relocation); here we stub it so this test stays focused on the terminal status.
    executor._dag_runner._read_approval_decision_type = AsyncMock(return_value="approved")

    step = _make_step(status="running")  # already_approved
    run = _make_run()

    await executor._execute_step(run, step)

    assert step.status == "completed_unverified"
    # UNVERIFIED → the increment is deferred to the tick, NOT recorded at finalize.
    executor._trust_gate.record_user_approval_outcome.assert_not_called()
    # The user's decision_type is stamped into the verification meta for the deferred tick.
    assert step.output_data["verification"]["decision_type"] == "approved"
