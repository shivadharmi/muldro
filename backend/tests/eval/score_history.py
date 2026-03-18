"""Score history — track eval scores over time for regression detection.

Stores scores in a JSONL file. Each line is one suite run result.
Compares current scores against baselines and alerts on >10% regression.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_FILE = Path(__file__).parent / "score_history.jsonl"
REGRESSION_THRESHOLD = 0.10  # 10% drop triggers alert


def record_score(suite: str, avg_score: float, total: int, passed: int) -> None:
    """Append a score record to the history file."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "suite": suite,
        "avg_score": round(avg_score, 4),
        "total": total,
        "passed": passed,
    }
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def get_baseline(suite: str, window: int = 5) -> float | None:
    """Get the average score from the last N runs for a suite."""
    if not HISTORY_FILE.exists():
        return None

    scores = []
    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("suite") == suite:
                scores.append(record["avg_score"])

    if not scores:
        return None

    recent = scores[-window:]
    return sum(recent) / len(recent)


def check_regression(suite: str, current_score: float) -> dict:
    """Check if current score represents a regression vs baseline.

    Returns:
        {"regressed": bool, "baseline": float | None, "delta": float | None}
    """
    baseline = get_baseline(suite)
    if baseline is None:
        return {"regressed": False, "baseline": None, "delta": None}

    delta = current_score - baseline
    regressed = delta < -REGRESSION_THRESHOLD

    if regressed:
        logger.warning(
            "Regression detected in %s: %.1f%% drop (%.3f → %.3f)",
            suite,
            abs(delta) * 100,
            baseline,
            current_score,
        )

    return {
        "regressed": regressed,
        "baseline": round(baseline, 4),
        "delta": round(delta, 4),
    }
