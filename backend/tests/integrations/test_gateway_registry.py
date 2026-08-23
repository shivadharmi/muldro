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
    # Non-gateway sources stay on the OAuth path -- github among them: its MCP
    # ACTIONS are gateway-served, but the notifications poll the "github" source
    # names runs on a native OAuth token, so the source must not resolve here.
    assert gateway_provider_for_source("github") is None
    assert gateway_provider_for_source("slack") is None


def test_every_retired_provider_declares_its_perception_source():
    """Retired native OAuth means no token can ever be minted for these sources.

    A gateway provider whose perception source is NOT declared here resolves
    through the OAuth branch, gets ``no_token`` (a PERMANENT reauth reason), and
    is paused unrecoverably -- ``_tick_reauth_recovery`` can only re-ask
    OAuthManager, which will never answer ok. Declared sources are merely
    SKIPPED while unconnected, so they self-heal.

    That reasoning binds a provider whose OAuth really is retired. github is not
    one: ``/v1/auth/github/authorize`` mints a token OAuthManager can answer with,
    so its source is deliberately undeclared and polled natively.
    """
    assert perception_sources_for_provider("gmail") == ("gmail",)
    assert perception_sources_for_provider("googlecalendar") == ("calendar",)
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


def test_pagination_keys_are_pinned_to_recorded_output_schemas():
    """The items_key/next_token_key constants Wave C passes must match reality.

    Wave B had no output ground truth at all, which is why an envelope bug that
    emptied every successful read was invisible to the whole suite. These keys
    address the UNWRAPPED provider payload (what GatewayConnector._call returns
    after stripping OpenConnector's {ok, data} envelope), so a change in either
    key name here breaks the walk silently rather than loudly.
    """
    from tests.gateway_ground_truth import CURATED_ACTIONS

    expected = {
        "gmail.fetch_emails": ("messages", "nextPageToken"),
        "googlecalendar.list_events": ("items", "nextPageToken"),
    }
    for action_id, (items_key, next_token_key) in expected.items():
        properties = CURATED_ACTIONS[action_id]["outputSchema"]["properties"]
        assert items_key in properties, f"{action_id} no longer returns {items_key!r}"
        assert next_token_key in properties, f"{action_id} no longer returns {next_token_key!r}"


# The OpenConnector services that accept an OAuth2 client, read from a live
# v1.3.5 catalog: `GET /api/oauth/configs` lists exactly github, gmail,
# googlecalendar, jira, notion and slack. A service outside this set cannot be
# registered — `confluence` answers
# `400 unsupported_auth_type: confluence does not support oauth2`, because its
# `execution.requiredAuthTypes` is `["api_key"]`.
#
# This is pinned because `register_gateway_oauth_configs` RAISES on a provider
# it cannot register, and it runs at STARTUP: adding such a provider does not
# degrade, it stops the backend from booting. Confluence was added and caught
# only by running the registrar by hand against the live gateway.
#
# Re-verify against a running container before extending, rather than trusting
# this list:
#   curl -H "Authorization: Bearer $ADMIN" localhost:3001/api/oauth/configs \
#     | jq -r '.[].service'
_OC_OAUTH_CAPABLE_SERVICES = frozenset(
    {"github", "gmail", "googlecalendar", "jira", "notion", "slack"}
)


def test_every_registered_provider_can_hold_an_oauth_client():
    """A provider OpenConnector cannot authenticate must not be in the registry.

    The failure is not a broken integration — it is a backend that will not
    start, because the startup registrar raises rather than skipping.
    """
    for provider_id in PROVIDER_REGISTRY:
        assert provider_id in _OC_OAUTH_CAPABLE_SERVICES, (
            f"{provider_id!r} is registered as a gateway provider, but OpenConnector "
            "exposes no OAuth config for it — startup will abort. Confirm with "
            "GET /api/oauth/configs before adding it."
        )
