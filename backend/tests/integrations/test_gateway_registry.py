"""Registry invariants: the single source of truth must stay internally consistent."""

from src.integrations.capabilities import CAPABILITY_CATALOG
from src.integrations.gateway_actions import (
    ACTION_BY_ID,
    PROVIDER_REGISTRY,
    provider_of_action,
    providers_for_server,
)
from src.integrations.gateway_naming import action_id_to_tool_name


def test_gmail_provider_is_registered_under_google_workspace():
    provider = PROVIDER_REGISTRY["gmail"]
    assert provider.server_name == "google-workspace"
    assert len(provider.actions) == 7


def test_every_action_capability_exists_in_the_catalog():
    for provider in PROVIDER_REGISTRY.values():
        for action in provider.actions:
            if provider.provider_id == "hackernews":
                continue  # harness-only provider, capability is synthetic
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
    assert providers_for_server("google-workspace") == ("gmail",)
    assert providers_for_server("nonexistent") == ()
