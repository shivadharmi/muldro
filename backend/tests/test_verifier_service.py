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
    return Verifier(mock_settings, mock_db)


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


class TestVerifyRunUntypedProseCriteria:
    """An untyped condition carrying a free-text ``criteria`` key (as stored by
    plan_store for Planner prose) must be routed to the LLM judge — not silently
    treated as ``status_equals`` (which never reads the prose)."""

    @pytest.mark.asyncio
    async def test_untyped_criteria_routes_to_llm_judge(self, verifier, mock_db):
        run = _make_run(status="partially_completed")
        steps = [_make_step(status="completed")]

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

        # success_conditions as plan_store stores prose: {"criteria": "<prose>"}.
        conditions = {"criteria": "The user was emailed a summary of Q3 metrics."}

        with patch.object(verifier, "_llm_judge", new=AsyncMock(return_value=True)) as judge:
            result = await verifier.verify_run("run_001", success_conditions=conditions)

        judge.assert_awaited_once()
        assert result.verdict == Verdict.passed

    @pytest.mark.asyncio
    async def test_untyped_criteria_judge_fail_marks_failed(self, verifier, mock_db):
        run = _make_run(status="partially_completed")
        steps = [_make_step(status="completed")]

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
        conditions = {"criteria": "Did the run book a flight?"}

        with patch.object(verifier, "_llm_judge", new=AsyncMock(return_value=False)):
            result = await verifier.verify_run("run_001", success_conditions=conditions)

        assert result.verdict == Verdict.failed


class TestLLMJudgeNonJSONResponse:
    """The judge is *advisory* (informational, not failing). A non-JSON or empty
    judge response must degrade quietly to a not-passed verdict — NOT raise a
    JSONDecodeError that spews a full traceback on every run (see log2.log)."""

    @pytest.mark.asyncio
    async def test_non_json_judge_response_returns_false_without_raising(self, verifier):
        condition = {"type": "llm_judge", "criteria": "Did the run succeed?"}
        # Must not raise; advisory failure is a clean False. parse_llm_json finds no
        # JSON value in prose and degrades to the not-passed default.
        with patch(
            "src.services.verifier.complete_text",
            AsyncMock(return_value="The run looks fine to me."),
        ):
            result = await verifier._llm_judge(condition, _make_run(), [_make_step()])
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_judge_response_returns_false_without_raising(self, verifier):
        condition = {"type": "llm_judge", "criteria": "Did the run succeed?"}
        with patch("src.services.verifier.complete_text", AsyncMock(return_value="")):
            result = await verifier._llm_judge(condition, _make_run(), [_make_step()])
        assert result is False

    @pytest.mark.asyncio
    async def test_valid_json_judge_response_still_parsed(self, verifier):
        # No prefill: the judge returns a full JSON object, parsed directly.
        condition = {"type": "llm_judge", "criteria": "Did the run succeed?"}
        with patch(
            "src.services.verifier.complete_text",
            AsyncMock(return_value='{"passed": true, "reason": "all good"}'),
        ):
            result = await verifier._llm_judge(condition, _make_run(), [_make_step()])
        assert result is True


class TestLLMJudgeAdaptiveThinkingContract:
    """Regression: adaptive-thinking models (Sonnet 4.6, Opus 4.7/4.8 — every model
    Jarvis runs) reject a conversation that ends with an assistant message with a
    400 ``does not support assistant message prefill``. The old ``prefill='{'`` path
    produced exactly that, so the judge silently caught the 400 and always returned
    False (verification was 100% broken). Drive the real chain through a model that
    enforces the contract and assert we send a user-terminal conversation."""

    @pytest.mark.asyncio
    async def test_llm_judge_sends_user_terminal_conversation(self, verifier):
        from contextlib import asynccontextmanager

        from langchain_core.messages import AIMessage

        from src.services.model_resolver import ResolvedModel

        captured: dict = {}

        class _ContractModel:
            async def ainvoke(self, messages):
                captured["messages"] = messages
                # Mimic the real adaptive-thinking-model API contract.
                if isinstance(messages[-1], AIMessage):
                    raise RuntimeError(
                        "This model does not support assistant message prefill. "
                        "The conversation must end with a user message."
                    )
                return AIMessage(content='{"passed": true, "reason": "all good"}')

        @asynccontextmanager
        async def _fake_session():
            yield object()

        async def _fake_resolve(self, **kwargs):
            return ResolvedModel(
                "anthropic", "claude-haiku-4-5-20251001", "sk", None, {"max_tokens": 256}
            )

        condition = {"type": "llm_judge", "criteria": "Did the run succeed?"}
        # complete_text now resolves via ModelResolver + build_langchain_model inside a
        # short-lived DB session; patch that seam (mirrors tests/llm/test_utility.py) so the
        # judge drives our contract-enforcing model with no DB.
        with (
            patch("src.llm.utility.build_langchain_model", return_value=_ContractModel()),
            patch("src.llm.utility.get_session_factory", lambda: lambda: _fake_session()),
            patch("src.llm.utility.ModelResolver.resolve", _fake_resolve),
        ):
            result = await verifier._llm_judge(condition, _make_run(), [_make_step()])

        # The judge must produce a real verdict, not swallow a 400 into False.
        assert result is True
        # And the conversation we sent must not end with an assistant turn.
        assert not isinstance(captured["messages"][-1], AIMessage)


class TestStatusEqualsDefaultValue:
    """A ``status_equals`` condition with no explicit ``value`` must accept the
    post-completion states (``completed``/``partially_completed``) — dag_runner
    sets the run to ``partially_completed`` *before* verification, so a default
    check hardcoded to ``completed`` would always fail."""

    @pytest.mark.asyncio
    async def test_status_equals_default_passes_for_partially_completed(self, verifier, mock_db):
        run = _make_run(status="partially_completed")
        steps = [_make_step(status="completed")]

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

        # No "value" key → default must accept partially_completed.
        conditions = {"type": "status_equals", "label": "status_ok"}
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.passed
        assert "status_ok" in result.checks_passed

    @pytest.mark.asyncio
    async def test_status_equals_default_passes_for_completed(self, verifier, mock_db):
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
            result.scalars.return_value.all.return_value = steps
            return result

        mock_db.execute = AsyncMock(side_effect=mock_execute)
        conditions = {"type": "status_equals", "label": "status_ok"}
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.passed

    @pytest.mark.asyncio
    async def test_status_equals_explicit_value_still_enforced(self, verifier, mock_db):
        run = _make_run(status="partially_completed")
        steps = [_make_step(status="completed")]

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
        # Explicit value that does not match → still fails (no loosening).
        conditions = {"type": "status_equals", "value": "cancelled", "label": "status_ok"}
        result = await verifier.verify_run("run_001", success_conditions=conditions)
        assert result.verdict == Verdict.failed


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
