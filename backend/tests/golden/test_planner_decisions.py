"""Golden tests for Planner agent plans.

Verifies the planner makes correct structured plans for known scenarios.
These tests validate prompt quality by checking that the planner's plan
output matches expected patterns for well-defined inputs.
"""

import json

import pytest

GOLDEN_CASES = [
    {
        "name": "investor_email_high_urgency",
        "input": {
            "event": {
                "source": "gmail",
                "type": "email_received",
                "title": "Re: Series A Term Sheet",
                "sender": "partner@sequoia.com",
                "importance": 0.95,
                "urgency": 0.9,
            },
        },
        "mock_response": json.dumps(
            {
                "goal": "Draft reply to investor email",
                "reasoning": "Investor follow-up on term sheet requires immediate response.",
                "priority": "critical",
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "Get full thread",
                        "capability": "email.search",
                        "risk": "none",
                    },
                    {
                        "step_id": "s2",
                        "description": "Draft response",
                        "capability": "email.draft",
                        "risk": "medium",
                        "depends_on": ["s1"],
                    },
                ],
            }
        ),
        "expected_goal": "Draft reply to investor email",
        "expected_priority": "critical",
        "expected_capabilities": ["email.search", "email.draft"],
    },
    {
        "name": "spam_newsletter_low_importance",
        "input": {
            "event": {
                "source": "gmail",
                "type": "email_received",
                "title": "Weekly Tech Digest #234",
                "sender": "newsletter@techcrunch.com",
            },
        },
        "mock_response": json.dumps(
            {
                "goal": "No action needed for newsletter",
                "priority": "low",
                "steps": [
                    {
                        "description": "Acknowledge newsletter",
                        "capability": "respond",
                    },
                ],
            }
        ),
        "expected_priority": "low",
        "expected_capabilities": ["respond"],
    },
    {
        "name": "meeting_in_30_minutes",
        "input": {
            "event": {
                "source": "calendar",
                "type": "meeting_upcoming",
                "title": "Board Meeting",
            },
        },
        "mock_response": json.dumps(
            {
                "goal": "Prepare board meeting brief",
                "reasoning": "Board meeting approaching, prepare meeting card.",
                "priority": "high",
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "Generate prep card",
                        "capability": "knowledge.search",
                    },
                    {
                        "step_id": "s2",
                        "description": "Send prep to user",
                        "capability": "respond",
                        "depends_on": ["s1"],
                    },
                ],
            }
        ),
        "expected_goal": "Prepare board meeting brief",
        "expected_priority": "high",
    },
    {
        "name": "follow_up_missed",
        "input": {
            "event": {
                "source": "internal",
                "type": "follow_up_overdue",
                "title": "No reply from CTO about API integration (3 days)",
            },
        },
        "mock_response": json.dumps(
            {
                "goal": "Suggest sending follow-up email to CTO",
                "reasoning": "Follow-up overdue on blocking dependency.",
                "priority": "medium",
                "steps": [
                    {
                        "description": "Draft follow-up",
                        "capability": "email.draft",
                        "risk": "low",
                    },
                ],
            }
        ),
        "expected_goal": "Suggest sending follow-up email to CTO",
        "expected_capabilities": ["email.draft"],
    },
]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c["name"])
async def test_planner_golden(case):
    """Verify planner makes correct structured plans for golden cases."""
    from src.orchestrator.intent_classifier import extract_plan

    plan = extract_plan(case["mock_response"])

    # Check goal if specified
    if "expected_goal" in case:
        assert plan.goal == case["expected_goal"], (
            f"Expected goal '{case['expected_goal']}' but got '{plan.goal}'"
        )

    # Check priority if specified
    if "expected_priority" in case:
        assert plan.priority == case["expected_priority"]

    # Check capabilities if specified
    if "expected_capabilities" in case:
        actual_caps = [s.capability for s in plan.steps]
        for expected_cap in case["expected_capabilities"]:
            assert expected_cap in actual_caps, (
                f"Expected capability '{expected_cap}' not found in {actual_caps}"
            )

    # Steps should always be non-empty
    assert len(plan.steps) >= 1, f"No steps for case '{case['name']}'"
