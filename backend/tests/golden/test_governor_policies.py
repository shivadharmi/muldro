"""Golden tests for Governor hook audit-only behavior.

Post Spec 2B-i: the hook is audit-only — all non-blocked tools pass through.
Approval gating moved to TrustEngine in GraphExecutor._execute_step().

Verifies the hook correctly allows all tools (write and read)
and only blocks tools disabled in the registry.
"""

import pytest

from tests.conftest import TEST_USER_ID

_A = {"expected_allowed": True}  # All non-blocked tools pass through

POLICY_CASES = [
    # Write tools -> allowed (approval gating is now in GraphExecutor)
    {"tool": "send_gmail_message", "agent": "operator", **_A},
    {"tool": "draft_gmail_message", "agent": "operator", **_A},
    {"tool": "manage_event", "agent": "operator", **_A},
    {"tool": "slack_post_message", "agent": "operator", **_A},
    {"tool": "add_issue_comment", "agent": "operator", **_A},
    {"tool": "send_telegram", "agent": "presenter", **_A},
    {"tool": "issue_write", "agent": "operator", **_A},
    {"tool": "create_pull_request", "agent": "operator", **_A},
    # Read-only tools -> allowed
    {"tool": "search", "agent": "planner", **_A},
    {"tool": "search_gmail_messages", "agent": "perceiver", **_A},
    {"tool": "get_events", "agent": "perceiver", **_A},
    {"tool": "get_observation_cursor", "agent": "perceiver", **_A},
    {"tool": "report_observation", "agent": "perceiver", **_A},
    {"tool": "slack_get_channel_history", "agent": "perceiver", **_A},
    # Internal tools -> allowed
    {"tool": "ingest_event", "agent": "perceiver", **_A},
    {"tool": "update_execution", "agent": "operator", **_A},
    {"tool": "build_context", "agent": "planner", **_A},
    # Unknown tools -> allowed (not blocked)
    {"tool": "totally_unknown_tool_xyz", "agent": "operator", **_A},
]


@pytest.mark.parametrize(
    "case",
    POLICY_CASES,
    ids=lambda c: f"{c['tool']}_{c['agent']}",
)
async def test_governor_policy(case):
    """Verify hook allows all non-blocked tools (audit-only post Spec 2B-i)."""
    from src.orchestrator.hooks import governor_pre_tool_hook

    result = await governor_pre_tool_hook(
        tool_name=case["tool"],
        tool_input={},
        agent_name=case["agent"],
        user_id=TEST_USER_ID,
    )

    assert result.get("allowed", True) == case["expected_allowed"], (
        f"Tool '{case['tool']}' by '{case['agent']}': "
        f"expected allowed={case['expected_allowed']}, got {result}"
    )
