"""OAuth routes: provider listing, authorize-URL generation, and the
callback that exchanges codes for tokens and provisions integrations.

Serves the one provider Muldro still authenticates natively: github, and
only for its notifications poll.
``google`` was retired here when it moved behind the OpenConnector gateway: that
gateway owns its OAuth client and stores its credentials, so it hits the
"Unknown provider" 400 and is connected through ``routes_integrations`` instead.
``github`` is a split case — its MCP actions run on the gateway credential,
while the token minted here backs only the native notifications poll.

Extracted from routes_auth.py (decomposition, 2026-06-20)."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from src.api.deps import (
    get_current_user_id,
    get_current_workspace_id,
)
from src.api.routes_auth_oauth_integration import (
    _ensure_integration,
    _error_redirect,
    _trigger_initial_observation,
)
from src.api.routes_auth_schemas import OAuthUrlResponse
from src.config.settings import Settings, get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v1/auth/providers")
async def list_auth_providers(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    settings: Settings = Depends(get_settings),
):
    """List all supported OAuth providers with configuration and connection status."""
    from src.integrations.auth_providers import SUPPORTED_PROVIDERS
    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    db_factory = get_session_factory()

    # Check which providers have active tokens
    connected_providers: dict[str, dict] = {}
    try:
        oauth_mgr = OAuthManager(
            db_factory,
            encryption_key=settings.oauth_encryption_key,
            settings=settings,
        )
        for provider_name in SUPPORTED_PROVIDERS:
            # Map sub-providers to their OAuth parent
            oauth_name = _oauth_provider_name(provider_name)
            try:
                token = await oauth_mgr.get_valid_token(user_id, oauth_name)
                if token:
                    connected_providers[provider_name] = {"connected": True}
            except Exception:
                pass
    except Exception:
        pass  # OAuthManager not available (no encryption key)

    providers = []
    for name, meta in SUPPORTED_PROVIDERS.items():
        client_id = getattr(settings, f"{name}_oauth_client_id", "")
        is_connected = name in connected_providers
        # gmail and calendar share google OAuth
        if name in ("gmail", "calendar", "drive"):
            is_connected = "google" in connected_providers

        providers.append(
            {
                "name": name,
                "display_name": meta.display_name,
                "type": meta.provider_type,
                "configured": bool(client_id),
                "connected": is_connected,
                "scopes": meta.default_scopes,
            }
        )

    return {"providers": providers}


def _oauth_provider_name(provider: str) -> str:
    """Map provider name to OAuth provider name (gmail/calendar/drive share google)."""
    if provider in ("gmail", "calendar", "drive"):
        return "google"
    return provider


async def _resume_after_reauth(db_factory, user_id: str, provider: str, workspace_id: str) -> None:
    """Clear a provider's needs-reauth state after a successful reconnect.

    Builds a ``ReauthService`` (db_factory + Notifier + redis + settings) and
    calls ``clear_reauth`` — which restores the integration to active, resumes
    the provider's paused perception sources, re-queues any runs deferred in
    ``awaiting_reauth`` back to ``pending`` (the background-task scheduler tick
    then picks them up), and clears the notify-dedup key.

    Best-effort: a failure here must never fail the OAuth connect.
    """
    from src.config.settings import get_settings
    from src.services.notifier import Notifier
    from src.services.reauth_service import ReauthService
    from src.services.surface_registry import SurfaceRegistry

    settings = get_settings()

    # H1: own the per-call Redis client and ALWAYS close it (try/finally) so each
    # OAuth callback does not leak a connection. (We cannot reuse a shared app
    # client here — this runs as a background task without request/app access.)
    redis = None
    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.debug("Redis unavailable for reauth resume", exc_info=True)

    try:
        # L1: ``clear_reauth`` opens its OWN session via ``db_factory`` for all DB
        # work and does NOT notify, so the Notifier here is only a constructor
        # dependency. Build it WITHOUT a session (db=None) rather than inside a
        # ``async with db_factory()`` block whose session would already be closed
        # by the time anything used it — the previous closed-session landmine.
        notifier: Notifier | None = None
        try:
            notifier = Notifier(
                surface_registry=SurfaceRegistry(redis=redis),
                redis=redis,
                db=None,
            )
        except Exception:
            logger.debug("Notifier unavailable for reauth resume", exc_info=True)

        reauth = ReauthService(
            db_factory=db_factory,
            notifier=notifier,
            redis=redis,
            settings=settings,
        )
        await reauth.clear_reauth(user_id, provider, workspace_id=workspace_id)
        logger.info("Cleared needs-reauth state for %s/%s after reconnect", user_id, provider)
    except Exception:
        logger.warning(
            "Reauth resume failed for %s/%s (connect succeeded regardless)",
            user_id,
            provider,
            exc_info=True,
        )
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                logger.debug("Failed to close reauth-resume Redis client", exc_info=True)


@router.get("/v1/auth/{provider}/authorize", response_model=OAuthUrlResponse)
@router.get("/v1/auth/oauth/{provider}/authorize", response_model=OAuthUrlResponse)
async def oauth_authorize(
    provider: str,
    scopes: str = Query("", description="Space-separated OAuth scopes"),
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """Generate OAuth authorization URL for a provider.

    ``google`` and ``notion`` are deliberately absent: both are served entirely
    by the OpenConnector gateway, which owns their OAuth client. Minting a
    Muldro-side credential for either would produce a token nothing reads, so
    they fall through to the "Unknown provider" 400 below. Their
    ``*_oauth_client_id``/``_secret`` settings are still required — the startup
    registrar hands those to OpenConnector — but the authorization itself now
    runs through ``POST /v1/connections/begin``.

    ``github`` is here for ONE job — the notifications poll. Its MCP actions keep
    running on the gateway credential; the token minted here is read only by
    ``GitHubConnector``, which the gateway catalog cannot replace because it
    exposes no notifications action.
    """
    if provider == "github":
        client_id = settings.github_oauth_client_id
        if not client_id:
            raise HTTPException(status_code=400, detail="GitHub OAuth not configured")
        # ``notifications`` is the scope that grants read access to the
        # /notifications API this connector polls; ``read:user`` identifies the
        # account the notifications belong to. Notifications originating in
        # PRIVATE repositories additionally require the broad ``repo`` scope, and
        # we deliberately do not request it: ``repo`` carries write access to
        # every repository the founder can reach, which is far more authority
        # than a perception source may hold. Missing private-repo notifications
        # is the accepted cost.
        default_scopes = "notifications read:user"
        params = {
            "client_id": client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "scope": scopes or default_scopes,
            "state": user_id,
        }
        url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
        return OAuthUrlResponse(url=url, provider="github")

    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")


@router.get("/v1/auth/{provider}/callback")
@router.get("/v1/auth/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    background_tasks: BackgroundTasks,
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
    settings: Settings = Depends(get_settings),
):
    """Handle OAuth callback — exchange code for tokens, store as integration.

    This is an integration OAuth flow, not a login flow.
    Exchanges the authorization code for access/refresh tokens,
    stores them encrypted via OAuthManager, and redirects to the frontend.
    """
    import httpx

    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    # Handle user-denied or provider-error callbacks (no code param)
    if error:
        return _error_redirect(settings, f"OAuth {provider} error: {error}")

    if not code:
        return _error_redirect(settings, f"OAuth {provider}: no authorization code received")

    # user_id must be passed in state param from the authorize step
    if not state or not state.startswith("usr_"):
        return _error_redirect(settings, "Invalid OAuth state: missing user_id")
    user_id = state

    if provider == "github":
        client_id = settings.github_oauth_client_id
        client_secret = settings.github_oauth_client_secret
        if not client_id or not client_secret:
            return _error_redirect(settings, "GitHub OAuth not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": settings.github_oauth_redirect_uri,
                },
                # Without this header GitHub answers form-encoded
                # (``access_token=gho_...&scope=...``), not JSON, and ``.json()``
                # raises. This is the single most common way this flow breaks.
                headers={"Accept": "application/json"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.error(
                    "GitHub token exchange failed (status=%d): %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return _error_redirect(settings, "Failed to exchange GitHub authorization code")
            token_data = resp.json()

        # GitHub reports a rejected exchange as HTTP 200 with an ``error`` key
        # (bad_verification_code, incorrect_client_credentials, ...). Checking
        # only the status would store ``token_data["access_token"]`` -> KeyError,
        # or worse a None token that looks connected.
        if token_data.get("error"):
            logger.error(
                "GitHub token exchange rejected: %s (%s)",
                token_data.get("error"),
                token_data.get("error_description", ""),
            )
            return _error_redirect(settings, f"GitHub token exchange failed: {token_data['error']}")
        access_token = token_data.get("access_token")
        if not access_token:
            return _error_redirect(settings, "GitHub returned no access token")

        db_factory = get_session_factory()
        from src.api.deps import resolve_workspace_id

        async with db_factory() as _db:
            workspace_id = await resolve_workspace_id(_db, user_id)

        oauth_mgr = OAuthManager(
            db_factory,
            encryption_key=settings.oauth_encryption_key,
            settings=settings,
        )
        # An OAuth App token does not expire and carries no refresh token, so
        # both are None by design — a future reader must not read that as a bug
        # and go looking for the missing half of the response. (GitHub *Apps*
        # issue expiring user-to-server tokens; this is the OAuth *App* flow.)
        await oauth_mgr.store_token(
            user_id=user_id,
            provider="github",
            access_token=access_token,
            refresh_token=None,
            expires_at=None,
            scopes=token_data.get("scope", "").split(",") if token_data.get("scope") else None,
            workspace_id=workspace_id,
        )
        # server_name matches seed_installations.py ("github"). This reactivates
        # the existing installation and enables the observe_github schedule; it
        # does not change the installation's gateway transport or auth provider,
        # which still serve the github.* MCP actions.
        await _ensure_integration(db_factory, user_id, "github", workspace_id=workspace_id)
        logger.info("GitHub integration linked for %s", user_id)
        background_tasks.add_task(_trigger_initial_observation, user_id, ["github"], workspace_id)

    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Refresh MCP session for this provider so new token is used immediately
    try:
        from src.connectors.mcp_bridge import refresh_server_auth

        # Map provider to MCP server names that use it. google and github are
        # absent: their MCP servers authenticate through the OpenConnector
        # gateway's platform JWT. GitHub's native token is for the notifications
        # poll only and its MCP session must not be re-keyed to it.
        # Only slack remains: every other installation authenticates to the
        # OpenConnector adapter with a platform JWT, which no native token
        # re-keys. GitHub's token serves the notifications poll alone and must
        # NOT be pushed into its MCP session.
        _provider_servers = {
            "slack": ["slack"],
        }
        for server_name in _provider_servers.get(provider, []):
            background_tasks.add_task(
                refresh_server_auth,
                server_name,
                user_id,
                workspace_id=workspace_id,
            )
    except Exception:
        logger.debug("MCP session refresh skipped", exc_info=True)

    # Auto-resume: clear any needs-reauth state for this provider — un-pause its
    # perception sources and re-queue runs parked in awaiting_reauth. Runs for
    # every natively-authenticated provider (github/slack),
    # best-effort.
    background_tasks.add_task(
        _resume_after_reauth,
        db_factory,
        user_id,
        provider,
        workspace_id,
    )

    # Redirect to frontend integrations page with success status
    frontend_url = settings.frontend_url.rstrip("/")
    params = urlencode({"provider": provider, "status": "connected"})
    return RedirectResponse(url=f"{frontend_url}/integrations?{params}")
