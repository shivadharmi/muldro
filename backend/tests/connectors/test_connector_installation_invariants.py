"""The perception path and the tool path must agree on which providers exist.

They were built as independent lists, which is how `drive` ended up with a
connector and no installation while `atlassian` got an installation and no
connector — both compiling, both passing every test. These invariants make that
class of half-feature fail loudly.

Why ``drive`` was safe to delete (corrected — the original write-up cited two
gates that do not gate, and a restorer acting on them would be misled):

* NOT a gate: absence from ``perception_policy.DEFAULT_INTERVALS``.
  ``get_or_create_state`` reads it as ``.get(source, 300)``, so an unlisted
  source is provisioned at a 300s default, not rejected. "Re-adding drive to
  DEFAULT_INTERVALS" re-enables nothing.
* NOT a gate: "no ``PerceptionState(source='drive')`` can be constructed". Two
  writers build one from a free-form string — ``api/routes_observation.py``
  and ``tools/intelligence_server/observation.py`` — and
  ``PerceptionReportRequest.source`` is a bare ``str`` with no validator.
* WHAT ACTUALLY HOLDS: both of those writers omit ``mode``, so the row takes
  the column's ``server_default="paused"`` with ``next_run_at`` NULL, and both
  due-source queries filter ``mode != "paused"``. Such a row is never due.
  Separately, ``intent_classifier.VALID_PERCEPTION_SOURCES`` does gate the
  intent path.
* ONE PATH REACHES IT, harmlessly: ``scheduler/schedule_dispatch.py`` action
  ``wake_agent`` takes an UNVALIDATED ``config["source"]`` and calls
  ``request_run(signal_source="agent")``; ``"agent"`` is in ``_wake_signals``,
  so it flips a paused row active and due. It then degrades gracefully —
  ``perception_runner`` short-circuits any source absent from
  ``CONNECTOR_REGISTRY`` as ``{"status": "skipped", "reason":
  "mcp_only_source"}``, and ``connector_poller`` returns a
  permanent-classified error. Post-deletion behaviour is strictly better than
  before: that same schedule previously polled DriveConnector with the Google
  OAuth token increment 2 retired.

``drive`` returns as a ``googledrive`` OpenConnector registry entry if wanted;
it does not come back as a native connector.
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
