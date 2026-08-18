"""Types the gateway registry is built from.

These live in their own module -- rather than in the package ``__init__`` --
so the per-provider leaf modules (gmail, googlecalendar, github) can import
them without importing the package that imports *them*. Keeping the
dataclasses here is what lets every import in this package sit at the top of
its file with no cycle to work around.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayAction:
    action_id: str  # OC-native, dotted (sent to OpenConnector)
    capability: str  # Muldro capability (email.send, calendar.list, issue.create)
    risk: str
    requires_approval: bool
    input_schema: dict  # hand-typed; OC's runtime guide exposes no schema


@dataclass(frozen=True)
class GatewayProvider:
    """One OpenConnector service, and the Muldro installation that serves it."""

    provider_id: str  # OC service id: "gmail" | "googlecalendar" | "github"
    server_name: str  # IntegrationInstallation.server_name
    # Human-readable label for tool descriptions (the text the LLM reads to pick
    # a tool). Lives HERE, on the registry, so a new provider cannot degrade to
    # its raw provider_id via a hand-maintained label table somewhere downstream.
    display_name: str
    actions: tuple[GatewayAction, ...]
    # The Muldro PERCEPTION SOURCE names this provider's credential backs, i.e.
    # the ``perception_state.source`` values the scheduler polls. These names
    # deliberately DIFFER from ``provider_id`` -- the OC provider
    # "googlecalendar" backs the source "calendar" -- and declaring them HERE is
    # what stops that vocabulary gap from becoming yet another hand-maintained
    # source -> provider table that drifts. A provider that backs no perception
    # source declares the empty tuple.
    perception_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError(f"gateway provider {self.provider_id!r} declares no display_name")
        if not self.actions:
            raise ValueError(f"gateway provider {self.provider_id!r} declares no actions")
        ids = [a.action_id for a in self.actions]
        if len(set(ids)) != len(ids):
            raise ValueError(f"gateway provider {self.provider_id!r} has duplicate action ids")
