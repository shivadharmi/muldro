"""Pytest wrapper for eval suites — runs all eval datasets as tests."""

import pytest

from tests.eval.eval_runner import AVAILABLE_SUITES, run_suite


@pytest.mark.parametrize("suite", AVAILABLE_SUITES)
def test_eval_suite(suite: str):
    """Run an eval suite and assert all cases pass."""
    result = run_suite(suite)
    assert result.total > 0, f"No cases found for suite '{suite}'"
    failed_cases = [r for r in result.results if not r.passed]
    if failed_cases:
        details = "\n".join(f"  - {r.case_id}: {r.details} {r.checks}" for r in failed_cases)
        pytest.fail(f"Suite '{suite}' failed {len(failed_cases)}/{result.total}:\n{details}")
