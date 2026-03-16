"""Tests for Verifier — validates run outcomes against success conditions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.verifier import Verdict, Verifier
from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def mock_settings():
    return make_mock_settings()


@pytest.fixture
def verifier(mock_settings, mock_db):
    with patch("src.services.verifier.get_anthropic_client") as mock_client:
        mock_client.return_value = AsyncMock()
        v = Verifier(mock_settings, mock_db)
    return v


def _make_run(
    run_id="run_001",
    status="completed",
):
    r = MagicMock()
    r.run_id = run_id
    r.status = status
    return r


def _make_step(
    step_id="step_001",
    task_id="task_001",
    run_id="run_001",
    status="completed",
    output_data=None,
    artifact_refs=None,
):
    s = MagicMock()
    s.step_id = step_id
    s.task_id = task_id
    s.run_id = run_id
    s.status = status
    s.output_data = output_data
    s.artifact_refs = artifact_refs
    return s


class TestVerifyRunNoConditions:
    @pytest.mark.asyncio
    async def test_verify_run_no_conditions(self, verifier, mock_db):
        run = _make_run()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = run
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_run("run_001")
        assert result.verdict == Verdict.skipped
        assert "No success conditions" in result.details

    @pytest.mark.asyncio
    async def test_verify_run_not_found(self, verifier, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_run("run_missing")
        assert result.verdict == Verdict.skipped
        assert "not found" in result.details


class TestVerifyRunStatusEquals:
    @pytest.mark.asyncio
    async def test_verify_run_status_equals_pass(self, verifier, mock_db):
        run = _make_run(status="completed")
        steps = [_make_step(status="completed")]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = run
                return result
            # Steps query
            result.scalars.return_value.all.return_value = steps
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        conditions = {
            "type": "status_equals",
            "value": "completed",
            "label": "run_completed",
        }
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.passed
        assert "run_completed" in result.checks_passed

    @pytest.mark.asyncio
    async def test_verify_run_status_equals_fail(self, verifier, mock_db):
        run = _make_run(status="failed")
        steps = []

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = run
                return result
            result.scalars.return_value.all.return_value = steps
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        conditions = {
            "type": "status_equals",
            "value": "completed",
            "label": "run_completed",
        }
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.failed
        assert "run_completed" in result.checks_failed


class TestVerifyRunAllStepsCompleted:
    @pytest.mark.asyncio
    async def test_verify_run_all_steps_completed(self, verifier, mock_db):
        run = _make_run(status="completed")
        steps = [
            _make_step(step_id="s1", status="completed"),
            _make_step(step_id="s2", status="completed"),
        ]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = run
                return result
            result.scalars.return_value.all.return_value = steps
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        conditions = {
            "conditions": [
                {
                    "type": "all_steps_completed",
                    "label": "all_done",
                },
            ]
        }
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.passed
        assert "all_done" in result.checks_passed

    @pytest.mark.asyncio
    async def test_verify_run_not_all_steps_completed(self, verifier, mock_db):
        run = _make_run(status="completed")
        steps = [
            _make_step(step_id="s1", status="completed"),
            _make_step(step_id="s2", status="failed"),
        ]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = run
                return result
            result.scalars.return_value.all.return_value = steps
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        conditions = {
            "conditions": [
                {
                    "type": "all_steps_completed",
                    "label": "all_done",
                },
            ]
        }
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.failed
        assert "all_done" in result.checks_failed


class TestVerifyRunMultipleConditions:
    @pytest.mark.asyncio
    async def test_partial_pass(self, verifier, mock_db):
        run = _make_run(status="completed")
        steps = [
            _make_step(step_id="s1", status="completed"),
            _make_step(step_id="s2", status="failed"),
        ]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = run
                return result
            result.scalars.return_value.all.return_value = steps
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        conditions = {
            "conditions": [
                {
                    "type": "status_equals",
                    "value": "completed",
                    "label": "status_ok",
                },
                {
                    "type": "all_steps_completed",
                    "label": "all_done",
                },
            ]
        }
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.partial
        assert "status_ok" in result.checks_passed
        assert "all_done" in result.checks_failed
        assert result.score == 0.5


class TestVerifyStepCompleted:
    @pytest.mark.asyncio
    async def test_verify_step_completed(self, verifier, mock_db):
        step = _make_step(step_id="step_001", status="completed")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = step
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_step("step_001")
        assert result.verdict == Verdict.passed
        assert result.score == 1.0
        assert "step_completed" in result.checks_passed

    @pytest.mark.asyncio
    async def test_verify_step_not_completed(self, verifier, mock_db):
        step = _make_step(step_id="step_001", status="running")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = step
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_step("step_001")
        assert result.verdict == Verdict.failed
        assert "step_completed" in result.checks_failed

    @pytest.mark.asyncio
    async def test_verify_step_not_found(self, verifier, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_step("step_missing")
        assert result.verdict == Verdict.skipped
        assert "not found" in result.details

    @pytest.mark.asyncio
    async def test_verify_step_with_output_contains(self, verifier, mock_db):
        step = _make_step(
            step_id="step_001",
            status="completed",
            output_data={"message": "Email sent successfully"},
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = step
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_step(
            "step_001",
            expected_output={"output_contains": "sent"},
        )
        assert result.verdict == Verdict.passed
        assert "output_contains" in result.checks_passed

    @pytest.mark.asyncio
    async def test_verify_step_with_schema_check(self, verifier, mock_db):
        step = _make_step(
            step_id="step_001",
            status="completed",
            output_data={"status": "ok", "id": "msg_123"},
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = step
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_step(
            "step_001",
            expected_output={"output_matches_schema": ["status", "id"]},
        )
        assert result.verdict == Verdict.passed
        assert "output_matches_schema" in result.checks_passed

    @pytest.mark.asyncio
    async def test_verify_step_schema_missing_key(self, verifier, mock_db):
        step = _make_step(
            step_id="step_001",
            status="completed",
            output_data={"status": "ok"},
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = step
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await verifier.verify_step(
            "step_001",
            expected_output={"output_matches_schema": ["status", "missing_key"]},
        )
        assert "output_matches_schema" in result.checks_failed
