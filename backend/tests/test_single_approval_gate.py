"""Tests for single TrustEngine approval gate in GraphExecutor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.risk_assessor import RiskAssessment
from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_trust_engine():
    engine = AsyncMock()
    engine.evaluate = AsyncMock()
    return engine


def _make_executor(settings, mock_db, trust_engine=None):
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        return GraphExecutor(settings, mock_db, trust_engine=trust_engine)


def _make_step(step_id="step_001", capability="email.send", status="pending"):
    step = MagicMock()
    step.step_id = step_id
    step.name = f"Step: {capability}"
    step.status = status
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


def _make_run(
    run_id="run_001",
    user_id="usr_test",
    workspace_id="ws_test",
    status="running",
):
    run = MagicMock()
    run.run_id = run_id
    run.user_id = user_id
    run.workspace_id = workspace_id
    run.status = status
    return run


class TestTrustEngineWiring:
    def test_executor_accepts_trust_engine(self, settings, mock_db, mock_trust_engine):
        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        assert executor._trust_engine is mock_trust_engine

    def test_executor_works_without_trust_engine(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        assert executor._trust_engine is None


class TestSingleGateApprovalRequired:
    """TrustEngine returns approval_required -> step pauses."""

    @patch("src.services.trust_gate.get_or_assess_risk")
    async def test_approval_required_pauses_step(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="low", reasoning="test")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="approval_required",
            justification="first_use capability",
            risk_level="low",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._trust_gate.create_approval_and_pause = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()
        executor._store.checkpoint = AsyncMock()

        step = _make_step()
        run = _make_run()

        await executor._execute_step(run, step)

        mock_trust_engine.evaluate.assert_called_once_with(
            "email.send", risk, workspace_id="ws_test"
        )
        executor._trust_gate.create_approval_and_pause.assert_called_once()


class TestSingleGateAutoExecuteNotify:
    """TrustEngine returns auto_execute_notify -> execute then notify."""

    @patch("src.services.trust_gate.get_or_assess_risk")
    async def test_auto_notify_executes_and_notifies(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="low", reasoning="trusted capability")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="auto_execute_notify",
            justification="trusted capability",
            risk_level="low",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._runner.run_step_action = AsyncMock(return_value={"ok": True})
        executor._trust_gate.notify_auto_executed = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()
        executor._store.checkpoint = AsyncMock()
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.send"}
        )
        executor._dag_runner.finalize_step = AsyncMock()

        step = _make_step(status="pending")
        run = _make_run()

        with patch("src.services.dag_runner.transition_step"):
            await executor._execute_step(run, step)

        executor._runner.run_step_action.assert_called_once()
        executor._trust_gate.notify_auto_executed.assert_called_once()


class TestSingleGateAutoExecuteSilent:
    """TrustEngine returns auto_execute_silent -> execute silently."""

    @patch("src.services.trust_gate.get_or_assess_risk")
    async def test_auto_silent_executes_without_notify(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="none", reasoning="no risk")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="auto_execute_silent",
            justification="autonomous + no risk",
            risk_level="none",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._runner.run_step_action = AsyncMock(return_value={"ok": True})
        executor._trust_gate.notify_auto_executed = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()
        executor._store.checkpoint = AsyncMock()
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.send"}
        )
        executor._dag_runner.finalize_step = AsyncMock()

        step = _make_step(status="pending")
        run = _make_run()

        with patch("src.services.dag_runner.transition_step"):
            await executor._execute_step(run, step)

        executor._runner.run_step_action.assert_called_once()
        executor._trust_gate.notify_auto_executed.assert_not_called()


class TestSingleGateResumedStep:
    """Step already running (resumed after approval) -> skip gate."""

    @patch("src.services.trust_gate.get_or_assess_risk")
    async def test_resumed_step_skips_trust_check(
        self, mock_risk, settings, mock_db, mock_trust_engine
    ):
        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._runner.run_step_action = AsyncMock(return_value={"ok": True})
        executor._surface_emitter.emit_event = AsyncMock()
        executor._store.checkpoint = AsyncMock()
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.send"}
        )
        executor._dag_runner.finalize_step = AsyncMock()

        step = _make_step(status="running")
        run = _make_run()

        await executor._execute_step(run, step)

        mock_risk.assert_not_called()
        mock_trust_engine.evaluate.assert_not_called()
        executor._runner.run_step_action.assert_called_once()


class TestNoTrustEngineFailsClosed:
    """No TrustEngine -> fail closed (SVC-P3-1).

    Production always supplies a TrustEngine (its construction cannot fail), so
    an absent engine at the gate is a wiring/misconfiguration. The executor must
    refuse to execute the step ungated rather than fall back to a legacy path.
    """

    async def test_no_trust_engine_fails_step_without_executing(self, settings, mock_db):
        executor = _make_executor(settings, mock_db, trust_engine=None)
        executor._runner.run_step_action = AsyncMock(return_value={"ok": True})
        executor._surface_emitter.emit_event = AsyncMock()
        executor._store.checkpoint = AsyncMock()
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.send"}
        )
        executor._dag_runner.finalize_step = AsyncMock()

        step = _make_step(status="ready")
        run = _make_run()

        await executor._execute_step(run, step)

        # Fail-closed: the step action is NEVER run without a gate.
        executor._runner.run_step_action.assert_not_called()
        executor._dag_runner.finalize_step.assert_not_called()
        assert step.status == "failed"
        assert "contract_violation" in (step.output_data or {}).get("error", "")
        executor._surface_emitter.emit_event.assert_any_await(
            "step.failed",
            run.user_id,
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "error": "contract_violation: missing TrustEngine",
            },
            workspace_id=run.workspace_id,
        )


class TestStepFailureHandling:
    """_handle_step_failure retries or marks permanent failure."""

    async def test_failure_with_retries_remaining(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        executor._surface_emitter.emit_event = AsyncMock()

        step = _make_step()
        step.retry_count = 0
        step.max_retries = 3
        run = _make_run()

        with patch("src.services.dag_runner.transition_step"):
            await executor._handle_step_failure(run, step, RuntimeError("boom"), 100)

        assert step.retry_count == 1
        assert step.error["attempt"] == 1

    async def test_failure_permanent(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        executor._surface_emitter.emit_event = AsyncMock()

        step = _make_step()
        step.retry_count = 2
        step.max_retries = 3
        run = _make_run()

        with patch("src.services.dag_runner.transition_step"):
            await executor._handle_step_failure(run, step, RuntimeError("final"), 200)

        assert step.retry_count == 3
        assert step.error["final"] is True
        executor._surface_emitter.emit_event.assert_called()


class TestGateIntegrationApprovalResume:
    """Full flow: step → approval_required → pause → approval created."""

    @patch("src.services.trust_gate.get_or_assess_risk")
    @patch("src.services.approval_service.create_approval")
    async def test_approval_creates_record_and_pauses(
        self, mock_create_approval, mock_risk, settings, mock_db, mock_trust_engine
    ):
        from src.contracts import PolicyDecision

        mock_approval = MagicMock()
        mock_approval.approval_id = "apr_test_001"
        mock_create_approval.return_value = mock_approval

        risk = RiskAssessment(risk_level="medium", reasoning="external write")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="approval_required",
            justification="first_use capability",
            risk_level="medium",
        )

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._surface_emitter.emit_event = AsyncMock()
        executor._store.checkpoint = AsyncMock()

        step = _make_step(capability="email.send")
        run = _make_run()

        # The running → waiting_approval → awaiting_approval transitions happen
        # inside TrustGate.create_approval_and_pause (extracted), so patch the
        # transition fns in the trust_gate module where they now resolve.
        with (
            patch("src.services.trust_gate.transition_step") as mock_ts,
            patch("src.services.trust_gate.transition_run") as mock_tr,
        ):
            await executor._execute_step(run, step)

            # Verify approval created
            mock_create_approval.assert_called_once()

            # Verify state transitions: running → waiting_approval, run → awaiting_approval
            assert mock_ts.call_count == 2
            assert mock_tr.call_count == 1


class TestGateIntegrationAutoNotifyFlow:
    """Full flow: step → auto_execute_notify → execute → notify."""

    @patch("src.services.trust_gate.get_or_assess_risk")
    async def test_auto_notify_full_flow(self, mock_risk, settings, mock_db, mock_trust_engine):
        from src.contracts import PolicyDecision

        risk = RiskAssessment(risk_level="low", reasoning="trusted calendar op")
        mock_risk.return_value = risk
        mock_trust_engine.evaluate.return_value = PolicyDecision(
            decision="auto_execute_notify",
            justification="trusted + low risk",
            risk_level="low",
        )

        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value={"status": "sent"})

        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        executor._notifier = mock_notifier
        executor._runner.run_step_action = AsyncMock(return_value={"event_id": "evt_123"})
        executor._surface_emitter.emit_event = AsyncMock()
        executor._store.checkpoint = AsyncMock()
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "calendar.create"}
        )
        executor._dag_runner.finalize_step = AsyncMock()

        step = _make_step(capability="calendar.create")
        run = _make_run()

        with patch("src.services.dag_runner.transition_step"):
            await executor._execute_step(run, step)

        # Verify execution happened
        executor._runner.run_step_action.assert_called_once()

        # Verify post-execution notification sent
        mock_notifier.notify.assert_called_once()
        notify_call = mock_notifier.notify.call_args
        # Check notification_type in either positional or keyword args
        if notify_call.kwargs:
            assert notify_call.kwargs.get("notification_type") == "auto_execute_notify"
        else:
            # Could be positional
            assert "auto_execute_notify" in str(notify_call)
