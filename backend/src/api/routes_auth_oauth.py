"""OAuth routes: provider listing, authorize-URL generation, and the
callback that exchanges codes for tokens and provisions integrations.

Serves the providers Jarvis still authenticates natively (slack/notion/
atlassian). ``google`` and ``github`` were retired here when they moved behind
the OpenConnector gateway: that gateway owns their OAuth clients and stores
their credentials, so both hit the "Unknown provider" 400 and are connected
through ``routes_integrations`` instead.

Extracted from routes_auth.py (decomposition, 2026-06-20)."""

import logging
from datetime import datetime, timedelta, timezone
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

    ``google`` and ``github`` are deliberately absent: both are served by the
    OpenConnector gateway, which owns their OAuth clients. Minting a Jarvis-side
    credential for them would produce a token nothing reads, so they fall
    through to the "Unknown provider" 400 below.
    """
    if provider == "notion":
        client_id = settings.notion_oauth_client_id
        if not client_id:
            raise HTTPException(status_code=400, detail="Notion OAuth not configured")
        params = {
            "client_id": client_id,
            "redirect_uri": settings.notion_oauth_redirect_uri,
            "response_type": "code",
            "owner": "user",
            "state": user_id,
        }
        url = f"https://api.notion.com/v1/oauth/authorize?{urlencode(params)}"
        return OAuthUrlResponse(url=url, provider="notion")

    elif provider == "atlassian":
        client_id = settings.atlassian_oauth_client_id
        if not client_id:
            raise HTTPException(status_code=400, detail="Atlassian OAuth not configured")
        # Scope list required by Atlassian's hosted Remote MCP
        # (https://mcp.atlassian.com/v1/mcp). Missing `read:me` was the
        # cause of every MCP tool call returning the opaque
        # {"error":true,"message":"We are having trouble completing this action..."}
        # wrapper even though direct REST calls with the same token worked.
        # The RMCP server needs to resolve the calling identity on every
        # tool invocation; without read:me / read:account-scoped claims it
        # fails silently with the generic message instead of a 403.
        default_scopes = (
            "offline_access read:me "
            "read:jira-work write:jira-work read:jira-user manage:jira-project "
            "read:confluence-content.all read:confluence-content.summary "
            "write:confluence-content "
            "read:confluence-space.summary "
            "read:confluence-props write:confluence-props "
            "read:confluence-user read:confluence-groups "
            "search:confluence"
        )
        params = {
            "audience": "api.atlassian.com",
            "client_id": client_id,
            "scope": scopes or default_scopes,
            "redirect_uri": settings.atlassian_oauth_redirect_uri,
            "state": user_id,
            "response_type": "code",
            "prompt": "consent",
        }
        url = f"https://auth.atlassian.com/authorize?{urlencode(params)}"
        return OAuthUrlResponse(url=url, provider="atlassian")

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

    if provider == "notion":
        client_id = settings.notion_oauth_client_id
        client_secret = settings.notion_oauth_client_secret
        if not client_id or not client_secret:
            return _error_redirect(settings, "Notion OAuth not configured")

        import base64

        basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.notion.com/v1/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.notion_oauth_redirect_uri,
                },
                headers={
                    "Authorization": f"Basic {basic_auth}",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.error(
                    "Notion token exchange failed (status=%d): %s",
                    resp.status_code,
                    resp.text,
                )
                return _error_redirect(
                    settings, f"Notion token exchange failed: {resp.json().get('error', resp.text)}"
                )
            token_data = resp.json()

        # Notion tokens don't expire
        db_factory = get_session_factory()
        from src.api.deps import resolve_workspace_id

        async with db_factory() as _db:
            workspace_id = await resolve_workspace_id(_db, user_id)

        oauth_mgr = OAuthManager(
            db_factory,
            encryption_key=settings.oauth_encryption_key,
            settings=settings,
        )
        await oauth_mgr.store_token(
            user_id=user_id,
            provider="notion",
            access_token=token_data["access_token"],
            refresh_token=None,
            expires_at=None,
            workspace_id=workspace_id,
        )
        await _ensure_integration(db_factory, user_id, "notion", workspace_id=workspace_id)
        logger.info("Notion integration linked for %s", user_id)
        background_tasks.add_task(_trigger_initial_observation, user_id, ["notion"], workspace_id)

    elif provider == "atlassian":
        client_id = settings.atlassian_oauth_client_id
        client_secret = settings.atlassian_oauth_client_secret
        if not client_id or not client_secret:
            return _error_redirect(settings, "Atlassian OAuth not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://auth.atlassian.com/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": settings.atlassian_oauth_redirect_uri,
                },
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("Atlassian token exchange failed: %s", resp.text)
                return _error_redirect(settings, "Failed to exchange Atlassian authorization code")
            token_data = resp.json()

            # Fetch accessible resources — Atlassian's MCP tools (and any REST
            # API call we make ourselves) require the cloudId. Agents won't
            # know this value on their own, so we persist it on the
            # installation and later auto-inject it into every MCP tool call.
            cloud_id = ""
            site_url = ""
            sites: list[dict] = []
            res_resp = await client.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                timeout=10,
            )
            if res_resp.status_code == 200:
                resources = res_resp.json()
                sites = [
                    {
                        "id": r.get("id", ""),
                        "name": r.get("name", ""),
                        "url": r.get("url", ""),
                        "scopes": r.get("scopes", []),
                    }
                    for r in resources
                ]
                if resources:
                    cloud_id = resources[0].get("id", "")
                    site_url = resources[0].get("url", "")

            # Fetch accessible projects up-front so the agent has context
            # ("your projects are X, Y, Z") without needing a tool call on
            # every user question. Bounded to 50 to keep the request small;
            # full list is still available via MCP tools.
            projects: list[dict] = []
            if cloud_id:
                try:
                    proj_resp = await client.get(
                        f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project/search",
                        params={"maxResults": 50},
                        headers={
                            "Authorization": f"Bearer {token_data['access_token']}",
                            "Accept": "application/json",
                        },
                        timeout=10,
                    )
                    if proj_resp.status_code == 200:
                        data = proj_resp.json()
                        projects = [
                            {
                                "id": p.get("id", ""),
                                "key": p.get("key", ""),
                                "name": p.get("name", ""),
                                "project_type": p.get("projectTypeKey", ""),
                            }
                            for p in data.get("values", [])
                        ]
                    else:
                        logger.warning(
                            "Atlassian project fetch returned %s: %s",
                            proj_resp.status_code,
                            proj_resp.text[:200],
                        )
                except Exception:
                    logger.warning("Atlassian project fetch failed", exc_info=True)

            # Fetch the current Atlassian user profile so the agent can
            # personalize output without another roundtrip.
            atlassian_user: dict = {}
            if cloud_id:
                try:
                    me_resp = await client.get(
                        f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself",
                        headers={
                            "Authorization": f"Bearer {token_data['access_token']}",
                            "Accept": "application/json",
                        },
                        timeout=10,
                    )
                    if me_resp.status_code == 200:
                        me = me_resp.json()
                        atlassian_user = {
                            "account_id": me.get("accountId", ""),
                            "display_name": me.get("displayName", ""),
                            "email": me.get("emailAddress", ""),
                            "timezone": me.get("timeZone", ""),
                        }
                except Exception:
                    logger.debug("Atlassian user profile fetch failed", exc_info=True)

        expires_at = None
        if token_data.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

        db_factory = get_session_factory()
        from src.api.deps import resolve_workspace_id

        async with db_factory() as _db:
            workspace_id = await resolve_workspace_id(_db, user_id)

        oauth_mgr = OAuthManager(
            db_factory,
            encryption_key=settings.oauth_encryption_key,
            settings=settings,
        )
        await oauth_mgr.store_token(
            user_id=user_id,
            provider="atlassian",
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            scopes=token_data.get("scope", "").split() if token_data.get("scope") else None,
            workspace_id=workspace_id,
        )
        # server_name matches seed_installations.py ("atlassian"), not provider name
        await _ensure_integration(
            db_factory,
            user_id,
            "atlassian",
            workspace_id=workspace_id,
        )
        # Persist everything the agent will need on the installation record:
        # - cloud_id / site_url / sites: so we don't re-fetch from Atlassian
        # - projects / atlassian_user: give the Planner/Presenter real context
        # - tool_defaults: auto-injected by session_pool.call_tool so the
        #   agent never needs to ask "what's your cloudId?" for MCP calls.
        if cloud_id:
            from sqlalchemy import select as sa_select

            from src.models.integration_installation import IntegrationInstallation

            async with db_factory() as _db:
                result = await _db.execute(
                    sa_select(IntegrationInstallation).where(
                        IntegrationInstallation.user_id == user_id,
                        IntegrationInstallation.server_name == "atlassian",
                        IntegrationInstallation.workspace_id == workspace_id,
                    )
                )
                inst = result.scalar_one_or_none()
                if inst:
                    config = inst.config or {}
                    config["cloud_id"] = cloud_id
                    if site_url:
                        config["site_url"] = site_url
                    if sites:
                        config["sites"] = sites
                    if projects:
                        config["projects"] = projects
                    if atlassian_user:
                        config["atlassian_user"] = atlassian_user
                    # Keys merged into every Atlassian MCP tool_input when
                    # absent (agent doesn't need to learn cloudId).
                    config["tool_defaults"] = {"cloudId": cloud_id}
                    inst.config = config
                    await _db.commit()

        logger.info(
            "Atlassian integration linked for %s (cloudId=%s site=%s projects=%d)",
            user_id,
            cloud_id,
            site_url or "?",
            len(projects),
        )
        background_tasks.add_task(
            _trigger_initial_observation, user_id, ["atlassian"], workspace_id
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Refresh MCP session for this provider so new token is used immediately
    try:
        from src.connectors.mcp_bridge import refresh_server_auth

        # Map provider to MCP server names that use it. google/github are absent:
        # they authenticate through the OpenConnector gateway, never here.
        _provider_servers = {
            "slack": ["slack"],
            "notion": ["notion"],
            "atlassian": ["atlassian"],
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
    # every natively-authenticated provider (slack/notion/atlassian), best-effort.
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
