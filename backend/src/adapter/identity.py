"""Adapter identity verification for the Connection Context Adapter.

Turns a platform JWT (minted by `src.orchestrator.platform_jwt`) into a
verified `AdapterPrincipal`. This is the adapter-side counterpart to the
platform's mint step: the gateway call arrives carrying a short-lived
platform JWT, and this module confirms it was signed by Jarvis, scoped to
the `toolhive-vmcp` audience, and unexpired before trusting any of its
claims.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.orchestrator.platform_jwt import verify_platform_jwt


class IdentityError(Exception):
    """Raised when the platform JWT is missing, malformed, or wrong-audience."""


@dataclass(frozen=True)
class AdapterPrincipal:
    principal_id: str
    tenant_id: str
    workspace_id: str
    capabilities: tuple[str, ...]


def verify_principal(token: str) -> AdapterPrincipal:
    """Verify a platform JWT and return the principal it authorizes.

    Raises `IdentityError` if the token is missing, malformed, expired, or
    scoped to a different audience.
    """
    try:
        claims = verify_platform_jwt(token, audience="toolhive-vmcp")
    except Exception as exc:
        raise IdentityError(str(exc)) from exc
    return AdapterPrincipal(
        principal_id=claims["sub"],
        tenant_id=claims["tenant_id"],
        workspace_id=claims["workspace_id"],
        capabilities=tuple(claims.get("capabilities", [])),
    )
