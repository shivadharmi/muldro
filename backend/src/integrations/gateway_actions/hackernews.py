"""A no-auth provider used ONLY by the docker-gated integration harness.

Gmail's OAuth cannot run headless, so the harness drives a real action through
the full adapter over HTTP using public Hacker News reads. Read-only by design.
``server_name`` is deliberately a name no installation seeds, so this provider
can never be routed to from production code.

The declared schemas are deliberately empty-object rather than transcribed from
OpenConnector: OC's real schemas for these two actions carry one optional
``print`` pretty-print flag that the harness never sends. Declaring no
properties is narrower than OC and therefore safe, and it keeps the harness
fixture stable. This is the one provider whose schemas are NOT ground truth.
"""

from __future__ import annotations

from src.integrations.gateway_actions import GatewayAction, GatewayProvider

_NO_INPUT: dict = {"type": "object", "properties": {}}

HACKERNEWS_ACTIONS: tuple[GatewayAction, ...] = (
    GatewayAction("hackernews.get_ask_stories", "hackernews.read", "low", False, dict(_NO_INPUT)),
    GatewayAction("hackernews.get_top_stories", "hackernews.read", "low", False, dict(_NO_INPUT)),
)

HACKERNEWS = GatewayProvider(
    provider_id="hackernews",
    server_name="_harness",
    actions=HACKERNEWS_ACTIONS,
)
