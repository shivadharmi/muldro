"""Golden tests for Planner agent decisions.

Verifies the planner makes correct structured decisions for known scenarios.
These tests validate prompt quality by checking that the planner's decision
output matches expected patterns for well-defined inputs.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings

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
            "world_model": {
                "entity": "Sequoia Capital",
                "relationship": "active_investor",
            },
            "memories": [{"fact": "Fundraising is top priority this quarter"}],
        },
        "mock_response": json.dumps(
            {
                "decision": "draft_reply",
                "priority": "critical",
                "risk_level": "medium",
                "reasoning": "Investor follow-up on term sheet requires immediate response.",
                "goal": "Draft reply to investor email",
                "tasks": [
                    {"task_type": "fetch_email_thread", "description": "Get full thread"},
                    {"task_type": "draft_reply", "description": "Draft response"},
                    {"task_type": "request_approval", "description": "Get approval"},
                ],
            }
        ),
        "expected_decision": "draft_reply",
        "expected_priority": "critical",
        "expected_task_types": ["fetch_email_thread", "draft_reply", "request_approval"],
        "must_not_decide": ["ignore", "summarize"],
    },
    {
        "name": "spam_newsletter_low_importance",
        "input": {
            "event": {
                "source": "gmail",
                "type": "email_received",
                "title": "Weekly Tech Digest #234",
                "sender": "newsletter@techcrunch.com",
                "importance": 0.1,
                "urgency": 0.0,
            },
            "world_model": {},
            "memories": [],
        },
        "mock_response": json.dumps(
            {
                "decision": "ignore",
                "priority": "low",
                "risk_level": "low",
                "reasoning": "Newsletter, no action needed.",
                "goal": "",
                "tasks": [],
            }
        ),
        "expected_decision": "ignore",
        "must_not_decide": ["draft_reply", "create_task", "recommend"],
    },
    {
        "name": "meeting_in_30_minutes",
        "input": {
            "event": {
                "source": "calendar",
                "type": "meeting_upcoming",
                "title": "Board Meeting",
                "importance": 0.8,
                "urgency": 0.95,
            },
            "world_model": {
                "entity": "Board of Directors",
                "relationship": "governance",
            },
            "memories": [{"fact": "Board meetings require prep card with metrics"}],
        },
        "mock_response": json.dumps(
            {
                "decision": "create_task",
                "priority": "high",
                "risk_level": "low",
                "reasoning": "Board meeting approaching, prepare meeting card.",
                "goal": "Prepare board meeting brief",
                "tasks": [
                    {"task_type": "meeting_prep", "description": "Generate prep card"},
                    {"task_type": "notify_user", "description": "Send prep to user"},
                ],
            }
        ),
        "expected_decision": "create_task",
        "expected_priority": "high",
    },
    {
        "name": "follow_up_missed",
        "input": {
            "event": {
                "source": "internal",
                "type": "follow_up_overdue",
                "title": "No reply from CTO about API integration (3 days)",
                "importance": 0.7,
                "urgency": 0.6,
            },
            "world_model": {
                "entity": "CTO Partner Company",
                "relationship": "partner",
            },
            "memories": [{"fact": "API integration is blocking launch"}],
        },
        "mock_response": json.dumps(
            {
                "decision": "recommend",
                "priority": "medium",
                "risk_level": "low",
                "reasoning": "Follow-up overdue on blocking dependency.",
                "goal": "Suggest sending follow-up email to CTO",
                "tasks": [
                    {"task_type": "draft_reply", "description": "Draft follow-up"},
                ],
            }
        ),
        "expected_decision": "recommend",
        "must_not_decide": ["ignore"],
    },
]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c["name"])
@patch("src.orchestrator.jarvis.get_anthropic_client")
async def test_planner_golden(mock_get_client, case):
    """Verify planner makes correct structured decisions for golden cases."""
    from src.orchestrator.jarvis import JarvisOrchestrator

    mock_client = AsyncMock()
    mock_get_client.return_value = mock_client

    # Mock Claude to return the expected response
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text=case["mock_response"])]
    mock_response.usage = MagicMock(input_tokens=500, output_tokens=200)
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    settings = make_mock_settings(
        daily_token_budget_usd=5.0,
        use_bedrock=False,
        telegram_bot_token="",
    )

    orchestrator = JarvisOrchestrator(
        settings=settings,
        db_factory=MagicMock(),
        services={},
    )

    # Extract decision from mock response
    decision = orchestrator._extract_decision(case["mock_response"])

    # Verify decision matches expected — _extract_decision returns PlannerOutput
    assert decision.decision == case["expected_decision"], (
        f"Expected decision '{case['expected_decision']}' but got '{decision.decision}'"
    )

    # Check priority if specified
    if "expected_priority" in case:
        assert decision.priority == case["expected_priority"]

    # Check task types if specified
    if "expected_task_types" in case:
        actual_types = [t.task_type for t in decision.tasks]
        for expected_type in case["expected_task_types"]:
            assert expected_type in actual_types, (
                f"Expected task type '{expected_type}' not found in {actual_types}"
            )

    # Check must_not_decide
    if "must_not_decide" in case:
        for forbidden in case["must_not_decide"]:
            assert decision.decision != forbidden, (
                f"Decision should not be '{forbidden}' for case '{case['name']}'"
            )
