from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.integrations.gateway_naming import action_id_to_tool_name
from src.tools.catalog import EXTERNAL_TOOL_SEEDS


def _seeds_for(server: str) -> list:
    return [s for s in EXTERNAL_TOOL_SEEDS if s.server == server]


def test_every_gateway_action_has_a_seed_with_the_adapter_name():
    by_name = {s.name: s for s in EXTERNAL_TOOL_SEEDS}
    for provider in PROVIDER_REGISTRY.values():
        for action in provider.actions:
            seed = by_name[action_id_to_tool_name(action.action_id)]
            assert seed.server == provider.server_name
            assert seed.capability == action.capability
            assert seed.risk_level == action.risk
            assert seed.requires_approval == action.requires_approval


def test_no_dotted_gateway_seed_names_remain():
    assert not any("." in s.name for s in EXTERNAL_TOOL_SEEDS)


def test_native_seeds_for_migrated_servers_are_gone():
    """google-workspace and github are gateway-only; native tool names must not survive."""
    names = {s.name for s in _seeds_for("google-workspace")} | {
        s.name for s in _seeds_for("github")
    }
    for legacy in (
        "search_gmail_messages",
        "send_gmail_message",
        "get_events",
        "list_calendars",
        "issue_write",
        "merge_pull_request",
        "search_orgs",
    ):
        assert legacy not in names


def test_unmigrated_servers_keep_their_native_seeds():
    assert _seeds_for("slack") and _seeds_for("notion") and _seeds_for("atlassian")
