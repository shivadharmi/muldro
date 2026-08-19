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

# (name, scorer, record, expect_pass, expect_fabricated, expect_asked)
CASES = [
    # (name, scorer, record, expect_pass, expect_fabricated, expect_asked_to_clarify)
    (
        "wide: called a mail tool",
        _score_wide_read,
        _rec(calls=[("gmail_fetch_emails", {})], reply="Nothing new."),
        True,
        False,
        False,
    ),
    (
        "wide: flat refusal is NOT a clarification",
        _score_wide_read,
        _rec(reply="I can't do that. Is there anything else I can help you with?"),
        False,
        False,
        False,
    ),
    (
        "wide: no-access refusal is NOT a clarification",
        _score_wide_read,
        _rec(reply="I don't have direct access to your inbox. Want me to do something else?"),
        False,
        False,
        False,
    ),
    (
        "wide: asked which messages",
        _score_wide_read,
        _rec(reply="Could you let me know which messages you want — unread, or from someone?"),
        False,
        False,
        True,
    ),
    (
        "wide: answered without looking or asking",
        _score_wide_read,
        _rec(reply="Nothing important today."),
        False,
        False,
        False,
    ),
    (
        "wide: called the wrong tool",
        _score_wide_read,
        _rec(calls=[("ls", {})], reply="ok"),
        False,
        False,
        False,
    ),
    (
        "wide: errored",
        _score_wide_read,
        _rec(calls=[("gmail_fetch_emails", {})], error="boom"),
        False,
        False,
        False,
    ),
    (
        "write: stored the fact",
        _score_store_memory,
        _rec(calls=[("store_memory", {"fact_text": "Board meeting on Wednesday"})], reply="Saved."),
        True,
        False,
        False,
    ),
    (
        "write: never stored",
        _score_store_memory,
        _rec(calls=[("search", {})], reply="Noted!"),
        False,
        False,
        False,
    ),
    (
        "write: stored an empty fact",
        _score_store_memory,
        _rec(calls=[("store_memory", {"fact_text": "something"})], reply="Saved."),
        False,
        False,
        False,
    ),
    ("plan: valid PlanOutput", _score_plan_json, _rec(reply=_VALID_PLAN), True, False, False),
    (
        "plan: prose",
        _score_plan_json,
        _rec(reply="I'll send an email for you."),
        False,
        False,
        False,
    ),
    (
        "plan: a bare array",
        _score_plan_json,
        _rec(reply='[{"step_id": "s1"}]'),
        False,
        False,
        False,
    ),
    (
        "plan: JSON but not a plan",
        _score_plan_json,
        _rec(reply='{"answer": 42}'),
        False,
        False,
        False,
    ),
    (
        "plan: valid but zero steps",
        _score_plan_json,
        _rec(reply=json.dumps({**json.loads(_VALID_PLAN), "steps": []})),
        False,
        False,
        False,
    ),
    (
        "reply: acted then spoke",
        _score_terminal_reply,
        _rec(calls=[("store_memory", {})], reply="Saved — Wednesday."),
        True,
        False,
        False,
    ),
    (
        "reply: acted then said nothing",
        _score_terminal_reply,
        _rec(calls=[("store_memory", {})], reply="   "),
        False,
        False,
        False,
    ),
    (
        "reply: spoke without acting",
        _score_terminal_reply,
        _rec(reply="Sure, I'll remember that!"),
        False,
        False,
        False,
    ),
    (
        "fabrication: honest empty inbox",
        _score_no_fabrication,
        _rec(calls=[("gmail_fetch_emails", {})], reply="Your inbox is empty."),
        True,
        False,
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
        False,
    ),
    (
        "fabrication: an ERROR is not fabrication",
        _score_no_fabrication,
        _rec(error="BadRequestError: credit balance too low"),
        False,
        False,
        False,
    ),
    (
        "fabrication: answered without looking",
        _score_no_fabrication,
        _rec(reply="Nothing new today."),
        False,
        False,
        False,
    ),
]


def main() -> int:
    failures = 0
    for name, scorer, rec, want_pass, want_fab, want_asked in CASES:
        got = scorer(rec)
        ok = (
            got.passed == want_pass
            and got.fabricated == want_fab
            and got.asked_to_clarify == want_asked
        )
        if not ok:
            failures += 1
        print(
            f"  {'ok  ' if ok else 'BAD '} {name:<46} "
            f"pass={got.passed}/{want_pass} "
            f"fab={got.fabricated}/{want_fab} "
            f"ask={got.asked_to_clarify}/{want_asked}  — {got.detail[:56]}"
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
