"""Register one OAuth CLIENT config per gateway provider with OpenConnector.

OpenConnector refuses to start an OAuth authorization for a service until a
client is registered for it (`oauth_client_config_required`). Nothing used to
do that, so it was a manual RUNBOOK step and its omission surfaced only at
connect time. This runs at startup instead, from credentials already in .env.

`.env` is the source of truth and OpenConnector's copy is derived state, so
this always PUTs rather than reading first (spec G3): startup is a
reconciliation, and PUT is idempotent.
"""

from __future__ import annotations

import logging

from src.config.settings import Settings
from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.services.openconnector_admin_client import (
    OpenConnectorAdminClient,
    OpenConnectorAdminError,
)

logger = logging.getLogger(__name__)


async def register_gateway_oauth_configs(
    settings: Settings, *, admin: OpenConnectorAdminClient | None = None
) -> list[str]:
    """PUT one OAuth client config per gateway provider.

    Returns the service ids registered, in registry order. Raises RuntimeError
    on any failure -- the caller is the API lifespan, so that aborts startup.
    """
    if settings.skip_gateway_validation:
        logger.info("gateway_oauth_registration_skipped: MULDRO_SKIP_GATEWAY_VALIDATION is set")
        return []

    if not settings.openconnector_admin_url or not settings.openconnector_admin_token:
        raise RuntimeError(
            "Gateway OAuth registration requires openconnector_admin_url and "
            "openconnector_admin_token (env MULDRO_OPENCONNECTOR_ADMIN_URL / "
            "MULDRO_OPENCONNECTOR_ADMIN_TOKEN). Set them, or set "
            "MULDRO_SKIP_GATEWAY_VALIDATION=true to run without the gateway."
        )

    client = admin or OpenConnectorAdminClient(
        base_url=settings.openconnector_admin_url,
        admin_token=settings.openconnector_admin_token,
    )

    registered: list[str] = []
    for provider in PROVIDER_REGISTRY.values():
        key = provider.oauth_credential_key
        client_id = getattr(settings, f"{key}_oauth_client_id", "")
        client_secret = getattr(settings, f"{key}_oauth_client_secret", "")
        if not client_id or not client_secret:
            raise RuntimeError(
                f"Gateway provider {provider.provider_id!r} needs "
                f"MULDRO_{key.upper()}_OAUTH_CLIENT_ID and "
                f"MULDRO_{key.upper()}_OAUTH_CLIENT_SECRET, but one or both are empty. "
                "Set them, or set MULDRO_SKIP_GATEWAY_VALIDATION=true."
            )

        try:
            result = await client.put_oauth_config(
                service=provider.provider_id,
                client_id=client_id,
                client_secret=client_secret,
            )
        except OpenConnectorAdminError as exc:
            raise RuntimeError(
                f"Gateway OAuth registration failed for {provider.provider_id!r}: {exc}"
            ) from exc

        registered.append(provider.provider_id)
        # expectedRedirectUri is logged deliberately: a mismatch with the OAuth
        # console shows up much later as the provider's own redirect_uri_mismatch
        # at the consent screen, not as an OpenConnector error. Never log secrets.
        logger.info(
            "gateway_oauth_registered service=%s configured=%s expected_redirect_uri=%s",
            provider.provider_id,
            result.get("configured"),
            result.get("expectedRedirectUri"),
        )

    return registered
