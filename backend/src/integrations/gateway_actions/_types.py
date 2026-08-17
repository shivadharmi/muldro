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
    capability: str  # Jarvis capability (email.send, calendar.list, issue.create)
    risk: str
    requires_approval: bool
    input_schema: dict  # hand-typed; OC's runtime guide exposes no schema


@dataclass(frozen=True)
class GatewayProvider:
    """One OpenConnector service, and the Jarvis installation that serves it."""

    provider_id: str  # OC service id: "gmail" | "googlecalendar" | "github"
    server_name: str  # IntegrationInstallation.server_name
    actions: tuple[GatewayAction, ...]

    def __post_init__(self) -> None:
        if not self.actions:
            raise ValueError(f"gateway provider {self.provider_id!r} declares no actions")
        ids = [a.action_id for a in self.actions]
        if len(set(ids)) != len(ids):
            raise ValueError(f"gateway provider {self.provider_id!r} has duplicate action ids")
