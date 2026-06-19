"""Tests for trust feedback loop — record_approval_decision integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import record_approval_decision


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


class TestRecordApprovalDecision:
    async def test_approved_increments_count(self, mock_db):
        state = MagicMock()
        state.approved_count = 0
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "approved")
        assert state.approved_count == 1
        assert state.last_decision_at is not None

    async def test_rejected_applies_demotion(self, mock_db):
        state = MagicMock()
        state.approved_count = 10
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "trusted"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "rejected")
        assert state.rejected_count == 1
        assert state.trust_level == "learning"
        assert state.cooldown_until is not None

    async def test_modified_increments_both(self, mock_db):
        state = MagicMock()
        state.approved_count = 5
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "learning"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "modified")
        assert state.modified_count == 1
        assert state.approved_count == 6

    async def test_graduation_after_three_approvals(self, mock_db):
        state = MagicMock()
        state.approved_count = 2
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "approved")
        assert state.trust_level == "learning"


class TestAutoExecutionTrustFeedback:
    """Successful auto-executed steps must reinforce trust (graduate), not only
    explicit user approvals — closes the autonomous outcome→trust loop."""

    async def test_auto_execution_success_records_approved(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        with patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec:
            await executor._record_auto_execution_outcome("email.send", "low", "ws_test")

        rec.assert_awaited_once()
        args = rec.await_args.args
        assert args[2] == "email.send"  # capability
        assert args[3] == "low"  # risk_level
        assert args[4] == "approved"  # decision

    async def test_empty_capability_skips(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        with patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec:
            await executor._record_auto_execution_outcome("", "low", "ws_test")

        rec.assert_not_awaited()

    def test_remember_auto_executed_records_on_checkpoint(self):
        """Auto-executed (capability, risk_level) pairs are stamped on the run
        so a later verification failure can reverse the trust reinforcement."""
        from unittest.mock import AsyncMock, MagicMock

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        run = MagicMock()
        run.checkpoint = None

        executor._remember_auto_executed(run, "email.send", "medium")
        executor._remember_auto_executed(run, "calendar.create", "low")

        assert run.checkpoint["auto_executed"] == [
            {"capability": "email.send", "risk_level": "medium"},
            {"capability": "calendar.create", "risk_level": "low"},
        ]


class TestVerificationTrustFeedback:
    """A failed verification must reverse the premature trust reinforcement
    that auto-executed steps recorded, so capabilities whose outputs fail
    verification stop graduating toward autonomy."""

    def _make_executor_with_verdict(self, verdict_value: str):
        from unittest.mock import AsyncMock, MagicMock

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        verdict = MagicMock()
        verdict.value = verdict_value
        result = MagicMock()
        result.verdict = verdict
        result.score = 0.1
        result.details = "details"
        executor._verifier = MagicMock()
        executor._verifier.verify_run = AsyncMock(return_value=result)
        executor._checkpoint = AsyncMock()

        plan_res = MagicMock()
        plan_res.scalar_one_or_none.return_value = MagicMock(success_conditions=None)
        executor._db.execute = AsyncMock(return_value=plan_res)
        return executor

    def _make_run(self, auto_executed):
        from unittest.mock import MagicMock

        run = MagicMock()
        run.run_id = "run_1"
        run.plan_id = "plan_1"
        run.workspace_id = "ws_1"
        run.status = "partially_completed"
        run.checkpoint = {"auto_executed": auto_executed} if auto_executed is not None else {}
        return run

    async def test_failed_verification_rejects_auto_executed_caps(self):
        from unittest.mock import AsyncMock, patch

        executor = self._make_executor_with_verdict("failed")
        run = self._make_run(
            [
                {"capability": "email.send", "risk_level": "medium"},
                {"capability": "email.send", "risk_level": "medium"},  # dup → one reject
                {"capability": "calendar.create", "risk_level": "low"},
            ]
        )

        with (
            patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec,
            patch("src.services.graph_executor.transition_run"),
        ):
            await executor._run_verification(run)

        # Deduped: one rejection per unique (capability, risk_level).
        assert rec.await_count == 2
        recorded = {(c.args[2], c.args[3], c.args[4]) for c in rec.await_args_list}
        assert ("email.send", "medium", "rejected") in recorded
        assert ("calendar.create", "low", "rejected") in recorded

    async def test_passed_verification_records_no_rejection(self):
        from unittest.mock import AsyncMock, patch

        executor = self._make_executor_with_verdict("passed")
        run = self._make_run([{"capability": "email.send", "risk_level": "medium"}])

        with (
            patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec,
            patch("src.services.graph_executor.transition_run"),
        ):
            await executor._run_verification(run)

        rec.assert_not_awaited()

    async def test_failed_verification_without_auto_executed_is_noop(self):
        from unittest.mock import AsyncMock, patch

        executor = self._make_executor_with_verdict("failed")
        run = self._make_run(None)

        with (
            patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec,
            patch("src.services.graph_executor.transition_run"),
        ):
            await executor._run_verification(run)

        rec.assert_not_awaited()

    async def test_failed_verification_real_checkpoint_preserves_auto_executed(self):
        """Regression: with the REAL _checkpoint (not mocked), the auto_executed
        audit trail must survive the verification checkpoint so the penalty still
        fires. Previously _checkpoint overwrote run.checkpoint wholesale, leaving
        the penalty reading an empty list — so the reversal silently never ran in
        production even though the mocked-_checkpoint tests above passed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        verdict = MagicMock()
        verdict.value = "failed"
        result = MagicMock()
        result.verdict = verdict
        result.score = 0.1
        result.details = "d"
        executor._verifier = MagicMock()
        executor._verifier.verify_run = AsyncMock(return_value=result)
        # Do NOT mock _checkpoint — exercise the real merge-preservation path.
        executor._get_all_steps = AsyncMock(return_value=[])
        plan_res = MagicMock()
        plan_res.scalar_one_or_none.return_value = MagicMock(success_conditions=None)
        executor._db.execute = AsyncMock(return_value=plan_res)

        run = MagicMock()
        run.run_id = "run_1"
        run.plan_id = "plan_1"
        run.workspace_id = "ws_1"
        run.status = "partially_completed"
        run.current_step_ids = []
        run.checkpoint = {"auto_executed": [{"capability": "email.send", "risk_level": "medium"}]}

        with (
            patch("src.services.risk_assessor.record_approval_decision", new=AsyncMock()) as rec,
            patch("src.services.graph_executor.transition_run"),
        ):
            await executor._run_verification(run)

        rec.assert_awaited_once()
        assert rec.await_args.args[2:5] == ("email.send", "medium", "rejected")
        # The audit trail must still be present after checkpointing.
        assert run.checkpoint.get("auto_executed") == [
            {"capability": "email.send", "risk_level": "medium"}
        ]


class TestCheckpointPreservation:
    """_checkpoint owns only the execution-snapshot keys; it must merge-preserve
    other application-state keys on run.checkpoint instead of clobbering them."""

    async def test_checkpoint_preserves_auto_executed(self):
        from unittest.mock import AsyncMock, MagicMock

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        executor._get_all_steps = AsyncMock(return_value=[])
        run = MagicMock()
        run.run_id = "run_1"
        run.workspace_id = "ws_1"
        run.status = "running"
        run.current_step_ids = ["s1"]
        run.checkpoint = {"auto_executed": [{"capability": "email.send", "risk_level": "low"}]}

        await executor._checkpoint(run, None, "step_completed")

        assert run.checkpoint["auto_executed"] == [
            {"capability": "email.send", "risk_level": "low"}
        ]
        assert run.checkpoint["status"] == "running"
        assert "checkpoint_at" in run.checkpoint
