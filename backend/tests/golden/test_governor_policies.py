"""Golden tests for Governor policy enforcement.

Verifies the Governor correctly classifies tools as
auto_execute, approval_required, or blocked.

Uses catalog tool names (MCP server names and internal tool names)
since the ToolPolicy fallback sets were removed in Phase 16-17.
"""

import pytest

from tests.conftest import TEST_USER_ID

_W = {"expected_allowed": False, "expected_approval": True}  # Write tool
_R = {"expected_allowed": True}  # Read-only tool

POLICY_CASES = [
    # Write tools -> approval required (catalog names)
    {"tool": "send_gmail_message", "agent": "operator", **_W},
    {"tool": "draft_gmail_message", "agent": "operator", **_W},
    {"tool": "manage_event", "agent": "operator", **_W},
    {"tool": "slack_post_message", "agent": "operator", **_W},
    {"tool": "add_issue_comment", "agent": "operator", **_W},
    {"tool": "send_telegram", "agent": "presenter", **_W},
    {"tool": "issue_write", "agent": "operator", **_W},
    {"tool": "create_pull_request", "agent": "operator", **_W},
    # Read-only tools -> auto execute (catalog names)
    {"tool": "search", "agent": "planner", **_R},
    {"tool": "search_gmail_messages", "agent": "observer", **_R},
    {"tool": "get_events", "agent": "observer", **_R},
    {"tool": "get_observation_cursor", "agent": "observer", **_R},
    {"tool": "report_observation", "agent": "observer", **_R},
    {"tool": "slack_get_channel_history", "agent": "researcher", **_R},
    # Internal tools -> auto execute
    {"tool": "ingest_event", "agent": "observer", **_R},
    {"tool": "update_execution", "agent": "operator", **_R},
    {"tool": "build_context", "agent": "planner", **_R},
    # Unknown tools -> default allow (not in any registry)
    {"tool": "totally_unknown_tool_xyz", "agent": "operator", **_R},
]


@pytest.mark.parametrize(
    "case",
    POLICY_CASES,
    ids=lambda c: f"{c['tool']}_{c['agent']}",
)
async def test_governor_policy(case):
    """Verify Governor correctly enforces policies for each tool type."""
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

    if "expected_approval" in case and not case["expected_allowed"]:
        actual = result.get("approval_required", False)
        assert actual == case["expected_approval"], (
            f"Tool '{case['tool']}': expected approval_required={case['expected_approval']}"
        )
