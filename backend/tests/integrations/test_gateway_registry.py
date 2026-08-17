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
    gateway_provider_for_source,
    perception_sources_for_provider,
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


def test_every_platform_jwt_installation_is_known_to_the_registry():
    """The converse invariant: no platform_jwt seed without registry providers.

    The two gateway-ness signals must stay pinned together. An installation that
    declares `auth_provider="platform_jwt"` routes to the vMCP and mints its JWT
    capabilities from `capabilities_for_server` — so if the registry knows no
    providers for its server_name it mints an EMPTY capability set and every
    call is denied at the adapter, while integration_status reports it
    unconfigured. A useless installation, silent but for one logger.error.
    """
    from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS

    for entry in _DEFAULT_INSTALLATIONS:
        if entry.get("auth_provider") != "platform_jwt":
            continue
        assert providers_for_server(entry["server_name"]), (
            f"{entry['server_name']!r} declares platform_jwt but the registry "
            "knows no OC providers for it"
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
    """No two installations' capability sets may intersect.

    session_pool mints the union for ONE installation, so an overlap would hand a
    GitHub session a capability that unlocks a Google action at the adapter gate.
    Enumerated over ALL pairs rather than a hardcoded github x google-workspace,
    so adding a third installation is covered the moment it is registered.
    """
    servers = sorted({p.server_name for p in PROVIDER_REGISTRY.values()})
    assert len(servers) >= 2, "disjointness is vacuous with fewer than two installations"
    caps = {s: set(capabilities_for_server(s)) for s in servers}
    for server, server_caps in caps.items():
        assert server_caps, f"{server} mints no capabilities"
    for i, a in enumerate(servers):
        for b in servers[i + 1 :]:
            assert not (caps[a] & caps[b]), f"{a} and {b} share capabilities {caps[a] & caps[b]}"


def test_unknown_server_mints_no_capabilities():
    assert capabilities_for_server("nonexistent") == ()


def test_no_perception_source_is_claimed_by_two_providers():
    """Which credential backs a source must have exactly one answer.

    Two providers claiming one source would make the perception gate's decision
    depend on registry ORDER — a silent, order-sensitive authorization bug.
    """
    seen: dict[str, str] = {}
    for provider_id, provider in PROVIDER_REGISTRY.items():
        for source in provider.perception_sources:
            assert source not in seen, (
                f"perception source {source!r} claimed by both {seen[source]!r} and {provider_id!r}"
            )
            seen[source] = provider_id


def test_perception_sources_resolve_by_membership_across_the_vocabulary_gap():
    # The source name and the provider id deliberately differ for calendar.
    assert gateway_provider_for_source("gmail") == "gmail"
    assert gateway_provider_for_source("calendar") == "googlecalendar"
    # Not a source name — the provider id itself must not resolve as one.
    assert gateway_provider_for_source("googlecalendar") is None
    # Non-gateway sources stay on the OAuth path.
    assert gateway_provider_for_source("slack") is None
    assert gateway_provider_for_source("github") is None


def test_github_declares_no_perception_source():
    assert perception_sources_for_provider("github") == ()


def test_perception_sources_for_provider_round_trips():
    for provider_id, provider in PROVIDER_REGISTRY.items():
        assert perception_sources_for_provider(provider_id) == provider.perception_sources
        for source in provider.perception_sources:
            assert gateway_provider_for_source(source) == provider_id
    assert perception_sources_for_provider("nonexistent") == ()


def test_declared_perception_sources_are_real_scheduler_sources():
    """A typo here would silently strand a source on the dead OAuth branch."""
    from src.orchestrator.intent_classifier import VALID_PERCEPTION_SOURCES

    for provider in PROVIDER_REGISTRY.values():
        for source in provider.perception_sources:
            assert source in VALID_PERCEPTION_SOURCES, (
                f"{provider.provider_id} declares unknown perception source {source!r}"
            )


def test_every_provider_declares_a_display_name():
    """The registry owns the label the LLM reads; no downstream label table restates it."""
    for provider_id, provider in PROVIDER_REGISTRY.items():
        assert provider.display_name.strip(), f"{provider_id} has no display_name"
