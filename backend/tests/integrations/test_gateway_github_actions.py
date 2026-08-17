"""GitHub gateway actions, checked against the OpenConnector ground truth."""

import json
from pathlib import Path

import pytest

from src.integrations.capabilities import CAPABILITY_CATALOG
from src.integrations.gateway_actions import GatewayAction, GatewayProvider
from src.integrations.gateway_actions.github import GITHUB, GITHUB_ACTIONS
from src.integrations.gateway_naming import action_id_to_tool_name

_GROUND_TRUTH = Path(
    "/private/tmp/claude-501/-Users-sivasankarreddybogala-work-jarvis/"
    "bc22864c-2a5d-49fe-866f-14b01750659e/scratchpad/spike/curated-schemas.json"
)

EXPECTED = {
    "github.list_repository_issues": ("issue.list", "low", False),
    "github.search_issues_and_pull_requests": ("issue.search", "low", False),
    "github.create_issue": ("issue.create", "medium", True),
    "github.create_issue_comment": ("issue.comment", "medium", True),
    "github.search_code": ("repo.search_code", "low", False),
    "github.search_repositories": ("repo.search_repos", "low", False),
    "github.list_pull_requests": ("repo.list_prs", "low", False),
    "github.create_pull_request": ("repo.create_pr", "high", True),
}

BY_ID = {a.action_id: a for a in GITHUB_ACTIONS}


def test_github_is_its_own_installation():
    assert isinstance(GITHUB, GatewayProvider)
    assert GITHUB.provider_id == "github"
    assert GITHUB.server_name == "github"
    assert GITHUB.actions == GITHUB_ACTIONS


def test_the_curated_eight_are_declared():
    assert set(BY_ID) == set(EXPECTED)


def test_capability_risk_approval_match_the_reviewed_policy():
    for action_id, (cap, risk, approval) in EXPECTED.items():
        a = BY_ID[action_id]
        assert (a.capability, a.risk, a.requires_approval) == (cap, risk, approval)


def test_every_capability_exists_in_the_catalog():
    for a in GITHUB_ACTIONS:
        assert a.capability in CAPABILITY_CATALOG


def test_github_capabilities_do_not_overlap_gmail_or_calendar():
    """A github session token must never carry an email or calendar capability."""
    for a in GITHUB_ACTIONS:
        assert not a.capability.startswith(("email.", "calendar."))


def test_every_action_maps_to_a_legal_agent_tool_name():
    for a in GITHUB_ACTIONS:
        action_id_to_tool_name(a.action_id)  # raises ValueError if illegal


def test_writes_require_approval_and_reads_do_not():
    writes = {"issue.create", "issue.comment", "repo.create_pr"}
    for a in GITHUB_ACTIONS:
        assert a.requires_approval is (a.capability in writes)


@pytest.mark.skipif(not _GROUND_TRUTH.exists(), reason="spike capture not present")
def test_schemas_are_verbatim_openconnector_ground_truth():
    truth = json.loads(_GROUND_TRUTH.read_text())
    for a in GITHUB_ACTIONS:
        assert a.input_schema == truth[a.action_id]["inputSchema"], (
            f"{a.action_id} schema diverges from the OpenConnector catalog"
        )


def test_schemas_are_object_typed_and_not_opaque():
    for a in GITHUB_ACTIONS:
        assert isinstance(a, GatewayAction)
        assert a.input_schema.get("type") == "object"
        assert a.input_schema != {"type": "object", "additionalProperties": True}
