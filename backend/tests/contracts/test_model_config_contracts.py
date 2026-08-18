"""The model-config response schemas live in the neutral contracts layer, and
the routes module re-exports the very same class objects (no divergent copy)."""

from src.api import routes_model_config
from src.contracts.model_config import ModelConfigResponse, ProviderStatus, TierBinding


def test_routes_reexport_the_neutral_contract_classes():
    assert routes_model_config.ModelConfigResponse is ModelConfigResponse
    assert routes_model_config.ProviderStatus is ProviderStatus
    assert routes_model_config.TierBinding is TierBinding


def test_model_config_response_composes_neutral_members():
    resp = ModelConfigResponse(
        tiers=[TierBinding(tier="fast", provider="anthropic", model_id="x")],
        agent_overrides=[],
        providers=[ProviderStatus(provider="anthropic", configured=True, status="valid")],
    )
    assert resp.tiers[0].tier == "fast"
    assert resp.providers[0].configured is True
