"""The perception path and the tool path must agree on which providers exist.

They were built as independent lists, which is how `drive` ended up with a
connector and no installation while `atlassian` got an installation and no
connector — both compiling, both passing every test. These invariants make that
class of half-feature fail loudly.
"""

from src.connectors.base import CONNECTOR_REGISTRY
from src.integrations.gateway_actions import gateway_provider_for_source
from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


def _seeded_server_names() -> set[str]:
    return {i["server_name"] for i in _DEFAULT_INSTALLATIONS}


def _oauth_provider_names() -> set[str]:
    """Server names a native source could plausibly be served by."""
    return {name.replace("-", "_") for name in _seeded_server_names()}


def test_every_registered_connector_has_a_home():
    """A connector with no installation and no gateway provider is half a feature."""
    servers = _oauth_provider_names()
    orphans = []
    for source in CONNECTOR_REGISTRY:
        if gateway_provider_for_source(source) is not None:
            continue
        if source.replace("-", "_") in servers:
            continue
        orphans.append(source)
    assert not orphans, (
        f"connectors with neither a gateway provider nor a seeded installation: {orphans}"
    )


def test_every_gateway_perception_source_has_a_registered_connector():
    """A provider declaring a perception source it cannot poll is the inverse gap."""
    from src.integrations.gateway_actions import PROVIDER_REGISTRY

    missing = []
    for provider in PROVIDER_REGISTRY.values():
        for source in provider.perception_sources:
            if source not in CONNECTOR_REGISTRY:
                missing.append((provider.provider_id, source))
    assert not missing, f"declared perception sources with no connector: {missing}"
