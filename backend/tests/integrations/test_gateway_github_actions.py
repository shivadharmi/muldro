"""GitHub gateway actions, checked against the OpenConnector ground truth.

Scope: this file owns GitHub's curated action set, its reviewed policy table,
and its schemas. Registry-wide invariants that hold for EVERY provider --
capability-catalog membership, legal agent tool names, action-id uniqueness,
capability disjointness, seeded-installation binding -- are asserted once in
test_gateway_registry.py and deliberately not repeated here.
"""

from src.integrations.gateway_actions import GatewayAction, GatewayProvider
from src.integrations.gateway_actions.github import GITHUB, GITHUB_ACTIONS
from tests.gateway_ground_truth import input_schema_for

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

# Derived from the policy table above rather than restated, so the write set can
# never drift from the approval column it is meant to describe.
WRITE_CAPABILITIES = {cap for cap, _risk, approval in EXPECTED.values() if approval}

BY_ID = {a.action_id: a for a in GITHUB_ACTIONS}


def test_github_provider_object_wraps_its_curated_actions():
    assert isinstance(GITHUB, GatewayProvider)
    assert GITHUB.provider_id == "github"
    assert GITHUB.server_name == "github"
    assert GITHUB.actions == GITHUB_ACTIONS


def test_github_the_curated_eight_are_declared():
    assert set(BY_ID) == set(EXPECTED)


def test_github_capability_risk_approval_match_the_reviewed_policy():
    for action_id, (cap, risk, approval) in EXPECTED.items():
        a = BY_ID[action_id]
        assert (a.capability, a.risk, a.requires_approval) == (cap, risk, approval)


def test_github_capabilities_do_not_overlap_gmail_or_calendar():
    """A github session token must never carry an email or calendar capability."""
    for a in GITHUB_ACTIONS:
        assert not a.capability.startswith(("email.", "calendar."))


def test_github_writes_require_approval_and_reads_do_not():
    """Approval is decided per CAPABILITY: actions sharing one must not disagree."""
    for a in GITHUB_ACTIONS:
        assert a.requires_approval is (a.capability in WRITE_CAPABILITIES)


def test_github_schemas_are_verbatim_openconnector_ground_truth():
    for a in GITHUB_ACTIONS:
        assert a.input_schema == input_schema_for(a.action_id), (
            f"{a.action_id} schema diverges from the OpenConnector catalog"
        )


def test_github_schemas_are_object_typed_and_not_opaque():
    for a in GITHUB_ACTIONS:
        assert isinstance(a, GatewayAction)
        assert a.input_schema.get("type") == "object"
        assert a.input_schema != {"type": "object", "additionalProperties": True}
