"""Eval runner — measures quality of Jarvis subsystems against datasets.

Usage:
    python -m tests.eval.eval_runner --suite meeting_prep
    python -m tests.eval.eval_runner --suite all --output results.json

Each suite defines:
- input scenarios (JSON)
- expected outputs or quality criteria
- scoring functions
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DATASETS_DIR = Path(__file__).parent / "datasets"


@dataclass
class EvalCase:
    case_id: str
    suite: str
    input_data: dict
    expected: dict
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case_id: str
    suite: str
    passed: bool
    score: float
    details: str = ""
    checks: dict = field(default_factory=dict)


@dataclass
class SuiteResult:
    suite: str
    total: int
    passed: int
    failed: int
    avg_score: float
    results: list[EvalResult]


def load_dataset(suite: str) -> list[EvalCase]:
    """Load eval cases from a JSON dataset file."""
    path = DATASETS_DIR / f"{suite}.json"
    if not path.exists():
        logger.error("Dataset not found: %s", path)
        return []
    with open(path) as f:
        data = json.load(f)
    return [
        EvalCase(
            case_id=case.get("case_id", f"{suite}_{i}"),
            suite=suite,
            input_data=case.get("input", {}),
            expected=case.get("expected", {}),
            tags=case.get("tags", []),
        )
        for i, case in enumerate(data.get("cases", []))
    ]


def evaluate_case(case: EvalCase) -> EvalResult:
    """Evaluate a single case against its expected output.

    This is a simple structural/keyword check. For LLM-judge
    evals, extend with async Claude calls.
    """
    checks: dict[str, bool] = {}
    expected = case.expected

    # Check required_fields present in input
    if "required_fields" in expected:
        for field_name in expected["required_fields"]:
            checks[f"has_{field_name}"] = field_name in case.input_data

    # Check expected_decision
    if "decision" in expected:
        actual = case.input_data.get("decision", "")
        checks["decision_match"] = actual == expected["decision"]

    # Check expected_priority
    if "priority" in expected:
        actual = case.input_data.get("priority", "")
        checks["priority_match"] = actual == expected["priority"]

    # Check expected_risk_level
    if "risk_level" in expected:
        actual = case.input_data.get("risk_level", "")
        checks["risk_match"] = actual == expected["risk_level"]

    # Check min_items (for list outputs)
    if "min_items" in expected:
        field_name = expected.get("items_field", "items")
        items = case.input_data.get(field_name, [])
        checks["min_items"] = len(items) >= expected["min_items"]

    # Check keyword presence
    if "contains_keywords" in expected:
        text = json.dumps(case.input_data).lower()
        for kw in expected["contains_keywords"]:
            checks[f"keyword_{kw}"] = kw.lower() in text

    total = len(checks)
    passed_count = sum(1 for v in checks.values() if v)
    score = passed_count / total if total > 0 else 1.0
    all_passed = all(checks.values()) if checks else True

    return EvalResult(
        case_id=case.case_id,
        suite=case.suite,
        passed=all_passed,
        score=score,
        details=f"{passed_count}/{total} checks passed",
        checks=checks,
    )


async def llm_judge_score(case: EvalCase, actual: dict) -> float:
    """Score a case using Claude as an LLM judge. Returns 0.0-1.0."""
    try:
        import anthropic

        client = anthropic.AsyncAnthropic()
        prompt = (
            f"Score the following AI output from 0.0 to 1.0 based on how well it matches "
            f"the expected behavior.\n\n"
            f"Input: {json.dumps(case.input_data)}\n"
            f"Expected: {json.dumps(case.expected)}\n"
            f"Actual: {json.dumps(actual)}\n\n"
            f"Respond with ONLY a number between 0.0 and 1.0."
        )
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        return _parse_score(text)
    except Exception as e:
        logger.warning("LLM judge failed: %s", e)
        return 0.0


def _parse_score(text: str) -> float:
    """Extract a float score from LLM judge response."""
    import re

    match = re.search(r"(\d+\.?\d*)", text)
    if match:
        score = float(match.group(1))
        return max(0.0, min(1.0, score))
    return 0.0


def run_suite(suite: str) -> SuiteResult:
    """Run all eval cases in a suite."""
    cases = load_dataset(suite)
    if not cases:
        return SuiteResult(
            suite=suite,
            total=0,
            passed=0,
            failed=0,
            avg_score=0.0,
            results=[],
        )

    results = [evaluate_case(case) for case in cases]
    passed = sum(1 for r in results if r.passed)
    scores = [r.score for r in results]
    avg = sum(scores) / len(scores) if scores else 0.0

    return SuiteResult(
        suite=suite,
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        avg_score=avg,
        results=results,
    )


AVAILABLE_SUITES = [
    "meeting_prep",
    "inbox_triage",
    "research",
    "approval_gating",
    "ui_selection",
]


def main():
    parser = argparse.ArgumentParser(description="Jarvis Eval Runner")
    parser.add_argument(
        "--suite",
        default="all",
        help=f"Suite to run: {', '.join(AVAILABLE_SUITES)} or 'all'",
    )
    parser.add_argument("--output", help="Write results to JSON file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    suites = AVAILABLE_SUITES if args.suite == "all" else [args.suite]
    all_results: dict[str, dict] = {}

    for suite in suites:
        result = run_suite(suite)
        all_results[suite] = {
            "total": result.total,
            "passed": result.passed,
            "failed": result.failed,
            "avg_score": round(result.avg_score, 3),
            "cases": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": round(r.score, 3),
                    "details": r.details,
                    "checks": r.checks,
                }
                for r in result.results
            ],
        }
        status = "PASS" if result.failed == 0 else "FAIL"
        print(
            f"[{status}] {suite}: "
            f"{result.passed}/{result.total} passed, "
            f"avg score {result.avg_score:.1%}"
        )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults written to {args.output}")

    total_failed = sum(r["failed"] for r in all_results.values())
    sys.exit(1 if total_failed > 0 else 0)


if __name__ == "__main__":
    main()
