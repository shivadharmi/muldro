"""Authentication routes — magic link, OAuth, sessions."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
    get_current_user,
    get_current_user_id,
    get_current_workspace_id,
    get_session,
)
from src.config.settings import Settings, get_settings
from src.middleware.security import per_endpoint_rate_limit
from src.models.users import User
from src.services.auth_service import AuthService

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request / Response Schemas ───────────────────────────────


class MagicLinkRequest(BaseModel):
    email: str


class MagicLinkResponse(BaseModel):
    status: str
    message: str
    token: str | None = None  # Only returned in dev mode (no backend_token set)


class VerifyRequest(BaseModel):
    token: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: dict


class UserProfileResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    avatar_url: str | None
    status: str
    onboarding_completed: bool
    settings: dict | None


class OAuthUrlResponse(BaseModel):
    url: str
    provider: str


# ── Magic Link ───────────────────────────────────────────────


@router.post(
    "/v1/auth/magic-link",
    response_model=MagicLinkResponse,
    dependencies=[Depends(per_endpoint_rate_limit(5))],
)
async def send_magic_link(
    req: MagicLinkRequest,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Send a magic link to the provided email."""
    auth = AuthService(settings, db)
    token = await auth.send_magic_link(req.email)
    logger.info("Magic link generated for %s (token length=%d)", req.email, len(token))

    # In dev mode (no backend_token set), return the token so the user can verify
    if not settings.backend_token:
        return MagicLinkResponse(
            status="sent",
            message="Dev mode: use the token below to verify.",
            token=token,
        )

    # Production: send magic link email via SES
    from src.services.email_sender import EmailSender
    from src.services.email_templates import magic_link_email

    verify_url = f"{settings.frontend_url.rstrip('/')}/login?token={quote(token, safe='')}"
    body_html, body_text = magic_link_email(verify_url, settings.magic_link_ttl_minutes)

    try:
        sender = EmailSender(settings)
        await sender.send(
            to=req.email,
            subject="Sign in to Jarvis",
            body_html=body_html,
            body_text=body_text,
        )
    except Exception as e:
        logger.error("Failed to send magic link email to %s: %s", req.email, e)
        raise HTTPException(status_code=500, detail="Failed to send verification email") from e

    return MagicLinkResponse(
        status="sent",
        message="Magic link sent to your email. Check your inbox.",
    )


@router.post("/v1/auth/verify", response_model=AuthTokenResponse)
async def verify_magic_link(
    req: VerifyRequest,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Verify a magic link token and return a session."""
    auth = AuthService(settings, db)
    try:
        session = await auth.verify_magic_link(req.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    user = await auth.get_user(session.user_id)
    raw_token = session._raw_token  # type: ignore[attr-defined]

    return AuthTokenResponse(
        access_token=raw_token,
        expires_at=session.expires_at.isoformat(),
        user={
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
        },
    )


# ── OAuth ────────────────────────────────────────────────────


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


@router.get("/v1/auth/{provider}/authorize", response_model=OAuthUrlResponse)
@router.get("/v1/auth/oauth/{provider}/authorize", response_model=OAuthUrlResponse)
async def oauth_authorize(
    provider: str,
    scopes: str = Query("", description="Space-separated OAuth scopes"),
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """Generate OAuth authorization URL for a provider."""
    if provider == "google":
        client_id = settings.google_oauth_client_id
        if not client_id:
            raise HTTPException(status_code=400, detail="Google OAuth not configured")

        default_scopes = (
            "openid email profile "
            "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/calendar"
        )
        params = {
            "client_id": client_id,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": scopes or default_scopes,
            "access_type": "offline",
            "prompt": "consent",
            "state": user_id,
        }
        url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        return OAuthUrlResponse(url=url, provider="google")

    elif provider == "github":
        client_id = settings.github_oauth_client_id
        if not client_id:
            raise HTTPException(status_code=400, detail="GitHub OAuth not configured")

        params = {
            "client_id": client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "scope": scopes or "read:user user:email repo",
            "state": user_id,
        }
        url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
        return OAuthUrlResponse(url=url, provider="github")

    elif provider == "notion":
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

    if provider == "google":
        client_id = settings.google_oauth_client_id
        client_secret = settings.google_oauth_client_secret
        if not client_id or not client_secret:
            raise HTTPException(status_code=500, detail="Google OAuth not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": settings.google_oauth_redirect_uri,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("Google token exchange failed: %s", resp.text)
                return _error_redirect(settings, "Failed to exchange authorization code")
            token_data = resp.json()

            # Get user info to confirm the account
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                timeout=10,
            )
            userinfo = userinfo_resp.json() if userinfo_resp.status_code == 200 else {}

        expires_at = None
        if token_data.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

        scopes = token_data.get("scope", "").split() if token_data.get("scope") else None

        # Resolve workspace_id for this user
        db_factory = get_session_factory()
        from src.api.deps import resolve_workspace_id

        async with db_factory() as _db:
            workspace_id = await resolve_workspace_id(_db, user_id)

        # Store tokens via OAuthManager (encrypted at rest)
        oauth_mgr = OAuthManager(
            db_factory, encryption_key=settings.oauth_encryption_key, settings=settings
        )
        await oauth_mgr.store_token(
            user_id=user_id,
            provider="google",
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            scopes=scopes,
            workspace_id=workspace_id,
        )

        # Register integrations for the Google services
        await _ensure_integration(
            db_factory, user_id, "gmail", userinfo.get("email"), workspace_id=workspace_id
        )
        await _ensure_integration(
            db_factory, user_id, "calendar", userinfo.get("email"), workspace_id=workspace_id
        )

        logger.info(
            "Google integration linked for %s (%s)",
            user_id,
            userinfo.get("email", "unknown"),
        )
        background_tasks.add_task(
            _trigger_initial_observation, user_id, ["gmail", "calendar"], workspace_id
        )

    elif provider == "github":
        client_id = settings.github_oauth_client_id
        client_secret = settings.github_oauth_client_secret
        if not client_id or not client_secret:
            raise HTTPException(status_code=500, detail="GitHub OAuth not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("GitHub token exchange failed: %s", resp.text)
                return _error_redirect(settings, "Failed to exchange authorization code")
            token_data = resp.json()

        scopes = token_data.get("scope", "").split(",") if token_data.get("scope") else None

        # Fetch GitHub user profile and organizations
        github_config: dict = {}
        async with httpx.AsyncClient() as gh_client:
            user_resp = await gh_client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if user_resp.status_code == 200:
                gh_user = user_resp.json()
                github_config["username"] = gh_user.get("login", "")
                github_config["name"] = gh_user.get("name", "")
                github_config["avatar_url"] = gh_user.get("avatar_url", "")

            orgs_resp = await gh_client.get(
                "https://api.github.com/user/orgs",
                headers={
                    "Authorization": f"Bearer {token_data['access_token']}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if orgs_resp.status_code == 200:
                github_config["organizations"] = [
                    org.get("login", "") for org in orgs_resp.json()[:20]
                ]

        db_factory = get_session_factory()
        from src.api.deps import resolve_workspace_id

        async with db_factory() as _db:
            workspace_id = await resolve_workspace_id(_db, user_id)

        oauth_mgr = OAuthManager(
            db_factory, encryption_key=settings.oauth_encryption_key, settings=settings
        )
        await oauth_mgr.store_token(
            user_id=user_id,
            provider="github",
            access_token=token_data["access_token"],
            refresh_token=None,
            expires_at=None,
            scopes=scopes,
            workspace_id=workspace_id,
        )

        await _ensure_integration(
            db_factory,
            user_id,
            "github",
            workspace_id=workspace_id,
            extra_config=github_config,
        )

        logger.info(
            "GitHub integration linked for %s (%s)",
            user_id,
            github_config.get("username", "unknown"),
        )
        background_tasks.add_task(_trigger_initial_observation, user_id, ["github"], workspace_id)

    elif provider == "notion":
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

        # Map provider to MCP server names that use it
        _provider_servers = {
            "google": ["google-workspace"],
            "github": ["github"],
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

    # Redirect to frontend integrations page with success status
    frontend_url = settings.frontend_url.rstrip("/")
    params = urlencode({"provider": provider, "status": "connected"})
    return RedirectResponse(url=f"{frontend_url}/integrations?{params}")


async def _trigger_initial_observation(user_id: str, sources: list[str], workspace_id: str) -> None:
    """Run initial perception cycle and MCP schema discovery for newly connected sources."""
    try:
        from src.config.settings import get_settings
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.models.database import get_session_factory
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.runtime import build as build_runtime
        from src.tools import intelligence_server

        settings = get_settings()
        db_factory = get_session_factory()

        # Build a short-lived ServiceContainer for this background task
        svc_db = db_factory()
        try:
            svc = build_runtime(settings, svc_db)
            intelligence_server.configure(db_factory, settings, svc)
            orchestrator = JarvisOrchestrator(
                settings=settings,
                db_factory=db_factory,
                services=svc,
            )
            for source in sources:
                # MCP-only integrations (e.g., Atlassian) don't have a native
                # CONNECTOR_REGISTRY entry — their data flows entirely through
                # external MCP servers. Running a perception cycle against such
                # sources just emits "No connector registered" warnings and a
                # misleading perception_poll_failed log. Skip perception for
                # them; MCP schema discovery below still runs.
                if source not in CONNECTOR_REGISTRY:
                    logger.info(
                        "Skipping perception cycle for MCP-only source %s/%s",
                        user_id,
                        source,
                    )
                else:
                    try:
                        await orchestrator.run_perception_cycle(
                            source, user_id=user_id, workspace_id=workspace_id
                        )
                        logger.info("Initial observation completed for %s/%s", user_id, source)
                    except Exception:
                        logger.warning(
                            "Initial observation failed for %s/%s",
                            user_id,
                            source,
                            exc_info=True,
                        )

                # Eagerly create MCP session to discover tool schemas.
                # Stdio servers are lazy (per-user), so schemas aren't
                # available until the first session. Creating it here
                # populates the DB so tools appear in agent tool lists
                # immediately after OAuth connection.
                try:
                    from src.integrations.mcp_pool import get_workspace_pool

                    pool = get_workspace_pool()
                    if pool:
                        # Ensure config is registered (may have been activated
                        # after startup via this OAuth callback)
                        if not pool.session_pool.has_server_config(source, workspace_id):
                            await pool.reload_server(workspace_id, source)

                        session = await pool.session_pool.get_or_create_session(
                            source, user_id=user_id, workspace_id=workspace_id
                        )
                        logger.info(
                            "MCP schema discovery for %s: %d tools",
                            source,
                            len(session.tools),
                        )
                except Exception:
                    logger.debug("MCP schema discovery skipped for %s", source, exc_info=True)
        finally:
            await svc_db.close()
    except Exception:
        logger.warning("Initial observation dispatch failed", exc_info=True)


async def _ensure_integration(
    db_factory,
    user_id: str,
    provider: str,
    account_email: str | None = None,
    workspace_id: str = "",
    extra_config: dict | None = None,
) -> None:
    """Create or reactivate an IntegrationInstallation after OAuth."""
    from sqlalchemy import select as sa_select

    from src.models.ids import generate_id
    from src.models.integration_installation import IntegrationInstallation

    # Build config dict from account_email and any extra provider-specific data
    config: dict = {}
    if account_email:
        config["account_email"] = account_email
    if extra_config:
        config.update(extra_config)

    try:
        async with db_factory() as db:
            result = await db.execute(
                sa_select(IntegrationInstallation).where(
                    IntegrationInstallation.user_id == user_id,
                    IntegrationInstallation.server_name == provider,
                    IntegrationInstallation.workspace_id == workspace_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.status = "active"
                existing.enabled = True
                # Merge new config into existing (preserves other fields)
                merged = dict(existing.config or {})
                merged.update(config)
                existing.config = merged
            else:
                db.add(
                    IntegrationInstallation(
                        install_id=generate_id("inst"),
                        user_id=user_id,
                        workspace_id=workspace_id,
                        server_name=provider,
                        display_name=provider.replace("_", " ").title(),
                        transport="native",
                        auth_provider="oauth",
                        status="active",
                        health_status="unknown",
                        config=config,
                        enabled=True,
                    )
                )
            await db.commit()

        # Enable schedules tied to this integration (observation + globals on first)
        await _enable_integration_schedules(db_factory, provider, workspace_id=workspace_id)
    except Exception:
        logger.warning("Failed to ensure integration %s for %s", provider, user_id, exc_info=True)


async def _enable_integration_schedules(
    db_factory,
    provider: str,
    workspace_id: str = "",
) -> None:
    """Enable seeded schedules when an integration is authorized."""
    try:
        from src.services.schedule_seeder import enable_schedules_for_connector

        async with db_factory() as db:
            enabled = await enable_schedules_for_connector(
                db,
                provider,
                workspace_id=workspace_id,
            )
            if enabled:
                await db.commit()
    except Exception:
        logger.debug("Failed to enable schedules for %s", provider, exc_info=True)


def _error_redirect(settings: Settings, message: str) -> RedirectResponse:
    """Redirect to frontend with an error message."""
    frontend_url = settings.frontend_url.rstrip("/")
    params = urlencode({"error": message})
    return RedirectResponse(url=f"{frontend_url}/integrations?{params}")


# ── Session Management ───────────────────────────────────────


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/v1/auth/refresh", response_model=AuthTokenResponse)
async def refresh_token(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Refresh an expired session token."""
    auth = AuthService(settings, db)
    try:
        session = await auth.refresh_session(req.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    user = await auth.get_user(session.user_id)
    raw_token = session._raw_token  # type: ignore[attr-defined]

    return AuthTokenResponse(
        access_token=raw_token,
        expires_at=session.expires_at.isoformat(),
        user={
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
        },
    )


@router.post("/v1/auth/logout")
async def logout(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Revoke the current session by looking up the token hash."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"status": "logged_out"}

    raw_token = authorization.removeprefix("Bearer ")

    # Don't try to revoke the legacy backend_token
    if settings.backend_token and raw_token == settings.backend_token:
        return {"status": "logged_out"}

    auth = AuthService(settings, db)
    import hashlib

    from sqlalchemy import select as sa_select

    from src.models.users import Session as UserSession

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    result = await db.execute(
        sa_select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.revoked_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if session:
        await auth.revoke_session(session.session_id)

    return {"status": "logged_out"}


@router.get("/v1/auth/me", response_model=UserProfileResponse)
async def get_current_profile(user: User = Depends(get_current_user)):
    """Get the current user's profile."""
    return UserProfileResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        status=user.status,
        onboarding_completed=user.onboarding_completed,
        settings=user.settings,
    )
