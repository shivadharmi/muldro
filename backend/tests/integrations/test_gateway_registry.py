"""Registry invariants: the single source of truth must stay internally consistent.

Scope: this file owns the CROSS-PROVIDER invariants -- action-id uniqueness,
capability-catalog membership, legal agent tool names, capability disjointness
across installations, seeded-installation binding, and that each provider is
registered under the installation it belongs to. Each provider's own curated
action set, policy table, and schemas are owned by its per-provider test file,
which does not repeat these.
"""

from src.integrations.capabilities import CAPABILITY_CATALOG
from src.integrations.gateway_actions import (
    ACTION_BY_ID,
    PROVIDER_REGISTRY,
    capabilities_for_server,
    provider_of_action,
    providers_for_server,
)
from src.integrations.gateway_naming import action_id_to_tool_name


def test_gmail_provider_is_registered_under_google_workspace():
    provider = PROVIDER_REGISTRY["gmail"]
    assert provider.server_name == "google-workspace"
    assert len(provider.actions) == 7


def test_googlecalendar_is_registered_under_google_workspace():
    provider = PROVIDER_REGISTRY["googlecalendar"]
    assert provider.server_name == "google-workspace"
    assert provider.actions


def test_github_is_its_own_installation():
    provider = PROVIDER_REGISTRY["github"]
    assert provider.server_name == "github"
    assert providers_for_server("github") == ("github",)


def test_every_provider_binds_to_a_seeded_installation():
    """A typo in server_name would silently drop a provider from its installation."""
    from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS

    seeded = {i["server_name"] for i in _DEFAULT_INSTALLATIONS}
    for provider in PROVIDER_REGISTRY.values():
        assert provider.server_name in seeded, (
            f"{provider.provider_id} binds to unseeded server {provider.server_name!r}"
        )


def test_every_action_capability_exists_in_the_catalog():
    for provider in PROVIDER_REGISTRY.values():
        for action in provider.actions:
            assert action.capability in CAPABILITY_CATALOG, (
                f"{action.action_id} declares unknown capability {action.capability}"
            )


def test_action_ids_are_unique_across_providers():
    total = sum(len(p.actions) for p in PROVIDER_REGISTRY.values())
    assert len(ACTION_BY_ID) == total


def test_every_action_maps_to_a_legal_tool_name():
    for action_id in ACTION_BY_ID:
        action_id_to_tool_name(action_id)  # raises ValueError if illegal


def test_provider_of_action_resolves_by_membership_not_prefix():
    assert provider_of_action("gmail.get_profile") == "gmail"
    # A plausible-looking but unregistered action must NOT resolve to "gmail".
    assert provider_of_action("gmail.attacker_action") is None


def test_providers_for_server_groups_by_installation():
    # One installation, two OpenConnector services -- the cardinality mismatch
    # this increment exists to resolve. Order follows the registry.
    assert providers_for_server("google-workspace") == ("gmail", "googlecalendar")
    assert providers_for_server("nonexistent") == ()


def test_capabilities_for_server_is_the_union_across_its_providers():
    caps = set(capabilities_for_server("google-workspace"))
    assert {"email.send", "calendar.list"} <= caps
    assert caps == {
        c
        for p in PROVIDER_REGISTRY.values()
        if p.server_name == "google-workspace"
        for c in (a.capability for a in p.actions)
    }


def test_a_gateway_token_never_spans_installations():
    """github and google-workspace capability sets must not intersect.

    session_pool mints the union for ONE installation, so an overlap would hand a
    GitHub session a capability that unlocks a Google action at the adapter gate.
    """
    github = set(capabilities_for_server("github"))
    google = set(capabilities_for_server("google-workspace"))
    assert github and google
    assert not (github & google)


def test_unknown_server_mints_no_capabilities():
    assert capabilities_for_server("nonexistent") == ()
