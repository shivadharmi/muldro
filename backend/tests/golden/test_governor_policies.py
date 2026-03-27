"""Golden tests for Governor policy enforcement.

Verifies the Governor correctly classifies tools as
auto_execute, approval_required, or blocked.
"""

import pytest

from tests.conftest import TEST_USER_ID

_W = {"expected_allowed": False, "expected_approval": True}  # Write tool
_R = {"expected_allowed": True}  # Read-only tool
_B = {"expected_allowed": False, "expected_approval": False}  # Blocked tool

POLICY_CASES = [
    # Write tools -> approval required
    {"tool": "gmail_send", "agent": "operator", **_W},
    {"tool": "gmail_send_email", "agent": "operator", **_W},
    {"tool": "gmail_draft", "agent": "operator", **_W},
    {"tool": "calendar_create_event", "agent": "operator", **_W},
    {"tool": "slack_post_message", "agent": "operator", **_W},
    {"tool": "github_comment", "agent": "operator", **_W},
    {"tool": "send_telegram", "agent": "presenter", **_W},
    # Read-only tools -> auto execute
    {"tool": "search_memory", "agent": "planner", **_R},
    {"tool": "get_entities", "agent": "librarian", **_R},
    {"tool": "gmail_list", "agent": "observer", **_R},
    {"tool": "gmail_read", "agent": "observer", **_R},
    {"tool": "calendar_list", "agent": "observer", **_R},
    {"tool": "get_observation_cursor", "agent": "observer", **_R},
    {"tool": "report_observation", "agent": "observer", **_R},
    {"tool": "slack_search", "agent": "researcher", **_R},
    # Blocked tools -> always blocked
    {"tool": "gmail_delete", "agent": "operator", **_B},
    {"tool": "drive_delete", "agent": "operator", **_B},
    # Internal tools -> auto execute
    {"tool": "ingest_event", "agent": "observer", **_R},
    {"tool": "update_execution", "agent": "operator", **_R},
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
