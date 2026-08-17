"""The harness-only provider must declare real actions with explicit schemas."""

from src.integrations.gateway_actions import GatewayAction, GatewayProvider
from src.integrations.gateway_actions.hackernews import HACKERNEWS, HACKERNEWS_ACTIONS


def test_provider_is_bound_to_a_server_no_installation_seeds():
    assert isinstance(HACKERNEWS, GatewayProvider)
    assert HACKERNEWS.provider_id == "hackernews"
    assert HACKERNEWS.server_name == "_harness"
    assert HACKERNEWS.actions == HACKERNEWS_ACTIONS


def test_hackernews_actions_carry_explicit_empty_schemas():
    assert {a.action_id for a in HACKERNEWS_ACTIONS} == {
        "hackernews.get_ask_stories",
        "hackernews.get_top_stories",
    }
    for action in HACKERNEWS_ACTIONS:
        assert isinstance(action, GatewayAction)
        assert action.input_schema == {"type": "object", "properties": {}}
        assert action.requires_approval is False
        assert action.risk == "low"
        assert action.capability == "hackernews.read"


def test_each_action_owns_its_schema_dict():
    """Two actions must not share one mutable dict instance."""
    schemas = [a.input_schema for a in HACKERNEWS_ACTIONS]
    assert schemas[0] is not schemas[1]
