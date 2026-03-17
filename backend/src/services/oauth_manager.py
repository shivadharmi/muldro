"""OAuth token management with Fernet encryption.

Handles secure storage, retrieval, and auto-refresh of OAuth tokens.
Tokens are encrypted at rest using Fernet symmetric encryption.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import select
from ulid import ULID

from src.models.oauth_token import OAuthToken

logger = logging.getLogger(__name__)


def _get_fernet(key: str = "") -> Fernet:
    if not key:
        key = os.environ.get("JARVIS_OAUTH_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            "JARVIS_OAUTH_ENCRYPTION_KEY not set. "
            "Generate with: python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    return Fernet(key.encode())


class OAuthManager:
    """Manage encrypted OAuth tokens with auto-refresh."""

    def __init__(self, db_factory, encryption_key: str = ""):
        self._db_factory = db_factory
        self._encryption_key = encryption_key

    async def store_token(
        self,
        user_id: str,
        provider: str,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: datetime | None = None,
        scopes: list[str] | None = None,
        workspace_id: str = "",
    ) -> str:
        """Store or update an OAuth token (encrypted at rest)."""
        f = _get_fernet(self._encryption_key)
        async with self._db_factory() as db:
            result = await db.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == user_id,
                    OAuthToken.provider == provider,
                )
            )
            token = result.scalar_one_or_none()

            encrypted_access = f.encrypt(access_token.encode()).decode()
            encrypted_refresh = (
                f.encrypt(refresh_token.encode()).decode() if refresh_token else None
            )

            if token:
                token.access_token_encrypted = encrypted_access
                token.refresh_token_encrypted = encrypted_refresh
                token.expires_at = expires_at
                token.scopes = scopes
            else:
                token = OAuthToken(
                    token_id=f"token_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    provider=provider,
                    access_token_encrypted=encrypted_access,
                    refresh_token_encrypted=encrypted_refresh,
                    expires_at=expires_at,
                    scopes=scopes,
                )
                db.add(token)

            await db.commit()
            logger.info(
                "oauth_token_stored",
                extra={"user_id": user_id, "provider": provider},
            )
            return token.token_id

    async def get_valid_token(self, user_id: str, provider: str) -> str | None:
        """Get a valid access token, refreshing if needed.

        Returns the decrypted access token or None if not found.
        """
        f = _get_fernet(self._encryption_key)
        async with self._db_factory() as db:
            result = await db.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == user_id,
                    OAuthToken.provider == provider,
                )
            )
            token = result.scalar_one_or_none()
            if not token:
                return None

            # Check if token needs refresh (5-minute buffer)
            if token.expires_at and token.expires_at < datetime.now(timezone.utc) + timedelta(
                minutes=5
            ):
                if token.refresh_token_encrypted:
                    refreshed = await self._refresh_token(
                        provider,
                        f.decrypt(token.refresh_token_encrypted.encode()).decode(),
                    )
                    if refreshed:
                        token.access_token_encrypted = f.encrypt(
                            refreshed["access_token"].encode()
                        ).decode()
                        if refreshed.get("expires_in"):
                            token.expires_at = datetime.now(timezone.utc) + timedelta(
                                seconds=refreshed["expires_in"]
                            )
                        if refreshed.get("refresh_token"):
                            token.refresh_token_encrypted = f.encrypt(
                                refreshed["refresh_token"].encode()
                            ).decode()
                        await db.commit()
                        logger.info(
                            "oauth_token_refreshed",
                            extra={"user_id": user_id, "provider": provider},
                        )
                        return refreshed["access_token"]
                    else:
                        logger.warning(
                            "oauth_token_refresh_failed",
                            extra={"user_id": user_id, "provider": provider},
                        )
                        return None
                else:
                    logger.warning(
                        "oauth_token_expired_no_refresh",
                        extra={"user_id": user_id, "provider": provider},
                    )
                    return None

            return f.decrypt(token.access_token_encrypted.encode()).decode()

    async def delete_token(self, user_id: str, provider: str) -> bool:
        """Delete a stored OAuth token."""
        async with self._db_factory() as db:
            result = await db.execute(
                select(OAuthToken).where(
                    OAuthToken.user_id == user_id,
                    OAuthToken.provider == provider,
                )
            )
            token = result.scalar_one_or_none()
            if token:
                await db.delete(token)
                await db.commit()
                return True
            return False

    async def _refresh_token(self, provider: str, refresh_token: str) -> dict | None:
        """Attempt to refresh an OAuth token via the provider's token endpoint."""
        import httpx

        endpoints = {
            "google": "https://oauth2.googleapis.com/token",
            "github": "https://github.com/login/oauth/access_token",
            "slack": "https://slack.com/api/oauth.v2.access",
        }
        endpoint = endpoints.get(provider)
        if not endpoint:
            logger.warning("No refresh endpoint for provider %s", provider)
            return None

        client_id = os.environ.get(f"JARVIS_{provider.upper()}_CLIENT_ID", "")
        client_secret = os.environ.get(f"JARVIS_{provider.upper()}_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            logger.warning("Missing client credentials for %s", provider)
            return None

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(
                    "Token refresh failed: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
        except Exception as e:
            logger.error("Token refresh request failed: %s", e)
            return None
