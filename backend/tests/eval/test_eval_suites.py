"""Pytest wrapper for eval suites — runs all eval datasets as tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.eval.eval_runner import (
    AVAILABLE_SUITES,
    EvalCase,
    _parse_score,
    llm_judge_score,
    run_suite,
)


@pytest.mark.parametrize("suite", AVAILABLE_SUITES)
def test_eval_suite(suite: str):
    """Run an eval suite and assert all cases pass."""
    result = run_suite(suite)
    assert result.total > 0, f"No cases found for suite '{suite}'"
    failed_cases = [r for r in result.results if not r.passed]
    if failed_cases:
        details = "\n".join(f"  - {r.case_id}: {r.details} {r.checks}" for r in failed_cases)
        pytest.fail(f"Suite '{suite}' failed {len(failed_cases)}/{result.total}:\n{details}")


# ── LLM Judge Tests ──────────────────────────────────────────────────────────


class TestParseScore:
    def test_parse_clean_float(self):
        assert _parse_score("0.85") == 0.85

    def test_parse_integer(self):
        assert _parse_score("1") == 1.0

    def test_parse_with_text(self):
        assert _parse_score("Score: 0.72 out of 1.0") == 0.72

    def test_parse_clamps_high(self):
        assert _parse_score("1.5") == 1.0

    def test_parse_no_number(self):
        assert _parse_score("excellent") == 0.0

    def test_parse_zero(self):
        assert _parse_score("0.0") == 0.0


class TestLLMJudge:
    async def test_llm_judge_returns_score(self):
        case = EvalCase(
            case_id="test_001",
            suite="test",
            input_data={"command": "send email"},
            expected={"decision": "draft_reply"},
        )
        actual = {"decision": "draft_reply", "goal": "Draft email"}

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="0.95")]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            score = await llm_judge_score(case, actual)

        assert score == 0.95

    async def test_llm_judge_handles_api_error(self):
        case = EvalCase(
            case_id="test_002",
            suite="test",
            input_data={},
            expected={},
        )

        with patch("anthropic.AsyncAnthropic", side_effect=RuntimeError("API down")):
            score = await llm_judge_score(case, {})

        assert score == 0.0


# ── Score History Tests ───────────────────────────────────────────────────────


class TestScoreHistory:
    def test_record_and_get_baseline(self, tmp_path):
        from tests.eval import score_history

        history_file = tmp_path / "history.jsonl"
        score_history.HISTORY_FILE = history_file

        score_history.record_score("test_suite", 0.90, 10, 9)
        score_history.record_score("test_suite", 0.85, 10, 8)
        score_history.record_score("test_suite", 0.88, 10, 9)

        baseline = score_history.get_baseline("test_suite")
        assert baseline is not None
        assert 0.85 <= baseline <= 0.92

    def test_no_history_returns_none(self, tmp_path):
        from tests.eval import score_history

        score_history.HISTORY_FILE = tmp_path / "empty.jsonl"
        assert score_history.get_baseline("nonexistent") is None

    def test_regression_detected(self, tmp_path):
        from tests.eval import score_history

        history_file = tmp_path / "history.jsonl"
        score_history.HISTORY_FILE = history_file

        for _ in range(5):
            score_history.record_score("suite_a", 0.90, 10, 9)

        result = score_history.check_regression("suite_a", 0.75)
        assert result["regressed"] is True
        assert result["delta"] < 0

    def test_no_regression(self, tmp_path):
        from tests.eval import score_history

        history_file = tmp_path / "history.jsonl"
        score_history.HISTORY_FILE = history_file

        for _ in range(5):
            score_history.record_score("suite_b", 0.90, 10, 9)

        result = score_history.check_regression("suite_b", 0.88)
        assert result["regressed"] is False

    def test_no_baseline_no_regression(self, tmp_path):
        from tests.eval import score_history

        score_history.HISTORY_FILE = tmp_path / "empty.jsonl"
        result = score_history.check_regression("new_suite", 0.5)
        assert result["regressed"] is False
        assert result["baseline"] is None
