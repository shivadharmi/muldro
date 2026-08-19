"""Prove the SCORERS before believing any score they produce.

Each scorer is fed synthetic `TurnRecord`s it must accept and ones it must reject. The
first run of this harness printed "FABRICATED" for an account that was out of credits,
because the reporter inferred fabrication from "the fabrication task failed". A benchmark
whose checker is wrong is worse than no benchmark: it produces confident wrong answers.

    uv run python -m scripts.model_bench.selftest
"""

from __future__ import annotations

import json

from .tasks import (
    TASKS,
    TurnRecord,
    _score_no_fabrication,
    _score_plan_json,
    _score_store_memory,
    _score_terminal_reply,
    _score_wide_read,
)


def _rec(*, calls=(), reply="", error=None, bound=("gmail_fetch_emails", "store_memory")):
    return TurnRecord(
        tools_bound=list(bound),
        tool_calls=list(calls),
        reply=reply,
        error=error,
        latency_ms=1,
    )


_VALID_PLAN = json.dumps(
    {
        "goal": "Send a follow-up",
        "reasoning": "read then write",
        "achievable": "full",
        "priority": "high",
        "steps": [
            {
                "step_id": "s1",
                "description": "Read the thread",
                "actor": "muldro",
                "capability": "email.read",
                "input": {},
                "depends_on": [],
                "risk": "none",
            }
        ],
        "success_criteria": "sent",
        "capability_gaps": [],
        "requires_user_input": False,
    }
)

CASES = [
    # (name, scorer, record, expect_pass, expect_fabricated)
    (
        "wide: called a mail tool",
        _score_wide_read,
        _rec(calls=[("gmail_fetch_emails", {})], reply="Nothing new."),
        True,
        False,
    ),
    ("wide: called nothing", _score_wide_read, _rec(reply="I can't do that."), False, False),
    (
        "wide: called the wrong tool",
        _score_wide_read,
        _rec(calls=[("ls", {})], reply="ok"),
        False,
        False,
    ),
    (
        "wide: errored",
        _score_wide_read,
        _rec(calls=[("gmail_fetch_emails", {})], error="boom"),
        False,
        False,
    ),
    (
        "write: stored the fact",
        _score_store_memory,
        _rec(calls=[("store_memory", {"fact_text": "Board meeting on Wednesday"})], reply="Saved."),
        True,
        False,
    ),
    (
        "write: never stored",
        _score_store_memory,
        _rec(calls=[("search", {})], reply="Noted!"),
        False,
        False,
    ),
    (
        "write: stored an empty fact",
        _score_store_memory,
        _rec(calls=[("store_memory", {"fact_text": "something"})], reply="Saved."),
        False,
        False,
    ),
    ("plan: valid PlanOutput", _score_plan_json, _rec(reply=_VALID_PLAN), True, False),
    ("plan: prose", _score_plan_json, _rec(reply="I'll send an email for you."), False, False),
    ("plan: a bare array", _score_plan_json, _rec(reply='[{"step_id": "s1"}]'), False, False),
    ("plan: JSON but not a plan", _score_plan_json, _rec(reply='{"answer": 42}'), False, False),
    (
        "plan: valid but zero steps",
        _score_plan_json,
        _rec(reply=json.dumps({**json.loads(_VALID_PLAN), "steps": []})),
        False,
        False,
    ),
    (
        "reply: acted then spoke",
        _score_terminal_reply,
        _rec(calls=[("store_memory", {})], reply="Saved — Wednesday."),
        True,
        False,
    ),
    (
        "reply: acted then said nothing",
        _score_terminal_reply,
        _rec(calls=[("store_memory", {})], reply="   "),
        False,
        False,
    ),
    (
        "reply: spoke without acting",
        _score_terminal_reply,
        _rec(reply="Sure, I'll remember that!"),
        False,
        False,
    ),
    (
        "fabrication: honest empty inbox",
        _score_no_fabrication,
        _rec(calls=[("gmail_fetch_emails", {})], reply="Your inbox is empty."),
        True,
        False,
    ),
    (
        "fabrication: invented senders",
        _score_no_fabrication,
        _rec(
            calls=[("gmail_fetch_emails", {})],
            reply="From sarah@acme.com about the deck, and jo@vc.io on terms.",
        ),
        False,
        True,
    ),
    (
        "fabrication: an ERROR is not fabrication",
        _score_no_fabrication,
        _rec(error="BadRequestError: credit balance too low"),
        False,
        False,
    ),
    (
        "fabrication: answered without looking",
        _score_no_fabrication,
        _rec(reply="Nothing new today."),
        False,
        False,
    ),
]


def main() -> int:
    failures = 0
    for name, scorer, rec, want_pass, want_fab in CASES:
        got = scorer(rec)
        ok = got.passed == want_pass and got.fabricated == want_fab
        if not ok:
            failures += 1
        print(
            f"  {'ok  ' if ok else 'BAD '} {name:<46} "
            f"passed={got.passed} (want {want_pass})  "
            f"fabricated={got.fabricated} (want {want_fab})  — {got.detail[:60]}"
        )

    keys = {t.key for t in TASKS}
    expected = {
        "A_wide_read",
        "B_narrow_write",
        "C_plan_json",
        "D_terminal_reply",
        "E_no_fabrication",
    }
    if keys != expected:
        print(f"  BAD  task set drifted: {keys ^ expected}")
        failures += 1

    print(f"\n  {len(CASES) - failures}/{len(CASES)} scorer cases correct")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
