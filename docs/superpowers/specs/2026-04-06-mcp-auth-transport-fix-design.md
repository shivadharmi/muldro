# MCP Authentication & Transport Fix

**Date:** 2026-04-06
**Status:** Approved
**Scope:** Fix broken auth for Google Workspace MCP, correct auth_provider labels for 3 servers, fix cross-cutting workspace_id injection, wire OAuthManager into MCP bridge

## Problem Statement

External MCP tool calls fail due to 5 interconnected issues:

1. **Google Workspace auth is completely broken** — `user_google_email` missing, `workspace_id` rejected, OAuth tokens never reach the MCP server
2. **OAuthManager is created but never wired** — `app.state.oauth_manager` is never set, so the MCP bridge has no access to OAuth tokens from the DB
3. **`workspace_id` injected into all tool inputs** — external MCP servers reject the unknown parameter via Pydantic validation
4. **GitHub, Linear, Notion mislabeled** — `auth_provider: "token"` causes `_resolve_auth()` to skip OAuthManager even though these servers use OAuth
5. **Slack env var mismatch** — `_STDIO_TOKEN_ENV_VARS` maps to `SLACK_BOT_TOKEN` but the actual npm package expects `SLACK_MCP_XOXB_TOKEN`

## Design

### 1. Google Workspace — HTTP Service Migration

Move from per-session stdio subprocess to a shared Docker Compose HTTP service with External OAuth 2.1 provider mode.

**Why streamable-http:** The `EXTERNAL_OAUTH21_PROVIDER` mode validates incoming Bearer tokens (Google `ya29.*` access tokens) via Google's userinfo API. This requires HTTP transport — Bearer tokens are sent in `Authorization` headers, which stdio transport doesn't support. In OAuth 2.1 mode, `user_google_email` is automatically removed from all tool schemas; user identity comes from the validated token.

**Docker Compose service:**

```yaml
google-workspace-mcp:
  build:
    context: ./infra/docker/google-workspace-mcp
  ports:
    - "8001:8000"
  environment:
    GOOGLE_OAUTH_CLIENT_ID: ${JARVIS_GOOGLE_OAUTH_CLIENT_ID}
    GOOGLE_OAUTH_CLIENT_SECRET: ${JARVIS_GOOGLE_OAUTH_CLIENT_SECRET}
    MCP_ENABLE_OAUTH21: "true"
    EXTERNAL_OAUTH21_PROVIDER: "true"
    WORKSPACE_MCP_STATELESS_MODE: "true"
  restart: unless-stopped
```

**Dockerfile (`infra/docker/google-workspace-mcp/Dockerfile`):**

```dockerfile
FROM python:3.13-slim
RUN pip install --no-cache-dir workspace-mcp
EXPOSE 8000
CMD ["workspace-mcp", "--transport", "streamable-http", "--tool-tier", "complete", "--tools", "gmail", "calendar"]
```

Key env vars:
- `MCP_ENABLE_OAUTH21=true` — removes `user_google_email` from tool schemas, enables Bearer token auth
- `EXTERNAL_OAUTH21_PROVIDER=true` — server skips its own OAuth flow, validates incoming Bearer tokens only
- `WORKSPACE_MCP_STATELESS_MODE=true` — no filesystem writes (container-friendly)
- Client credentials bridged via Docker Compose `${JARVIS_*}` variable substitution

**Seed installation update (`seed_installations.py`):**

```python
{
    "server_name": "google-workspace",
    "display_name": "Google Workspace",
    "transport": "streamable-http",
    "remote_url": os.environ.get("JARVIS_GOOGLE_WORKSPACE_MCP_URL", "http://localhost:8001/mcp"),
    "command": None,
    "args": None,
    "env_template": {},
    "auth_provider": "google",
    "scopes_granted": [  # unchanged
        "email.send", "email.list", "email.read", "email.search", "email.draft",
        "calendar.list", "calendar.get", "calendar.create", "calendar.update",
        "doc.drive_list", "doc.drive_search", "doc.drive_create",
    ],
}
```

Note: `remote_url` is resolved from env at seed time via `os.environ.get()`, consistent with how `_resolve_env()` works for other servers. The setting in `settings.py` is for documentation and future use.

**Auth flow:**

```
session_pool._resolve_auth("google-workspace", user_id, config)
  → auth_provider == "google" (matches OAuth path at line 502)
  → OAuthManager.get_valid_token(user_id, "google")
  → returns ya29.* access token from Jarvis DB
  → BearerAuth(token="ya29.xxx")

session_pool.get_or_create_session()
  → transport == "streamable-http"
  → Client(url="http://localhost:8001/mcp", auth=BearerAuth)
  → HTTP request with Authorization: Bearer ya29.xxx

MCP Server (EXTERNAL_OAUTH21_PROVIDER mode)
  → ExternalOAuthProvider.verify_token("ya29.xxx")
  → calls Google userinfo API → extracts email
  → AuthInfoMiddleware sets authenticated_user_email
  → @require_google_service uses authenticated email
  → Gmail/Calendar API call succeeds
```

**New setting (`settings.py`):**

```python
google_workspace_mcp_url: str = "http://localhost:8001/mcp"
```

Configurable via `JARVIS_GOOGLE_WORKSPACE_MCP_URL` for production deployments.

### 2. OAuthManager Wiring Fix

**Problem:** `app.py:147` reads `getattr(app.state, "oauth_manager", None)` — always `None`. `runtime.py:210` creates OAuthManager on ServiceContainer, but that's a separate object used by the orchestrator.

**Fix:** Create OAuthManager directly in the app lifespan, before MCP bridge initialization:

```python
# In lifespan, before initialize_mcp_bridge():
try:
    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    oauth_manager = OAuthManager(
        db_factory=get_session_factory(),
        settings=settings,
        encryption_key=settings.oauth_encryption_key,
    )
except Exception:
    logger.debug("OAuthManager unavailable", exc_info=True)
    oauth_manager = None

await initialize_mcp_bridge(oauth_manager=oauth_manager)
```

OAuthManager is lightweight (just a db_factory + encryption key, no state). Creating a second instance for the MCP bridge is correct — the bridge is a singleton that outlives request scope.

### 3. `workspace_id` Injection Scoping

**Problem:** `jarvis.py:2636-2638` injects `workspace_id` into ALL tool inputs. External MCP servers reject it.

**Fix:** Move injection inside the `internal_mcp` and `composite` branches only:

```python
match tool.backend:
    case "internal_mcp":
        if workspace_id and "workspace_id" not in tool_input:
            tool_input = {**tool_input, "workspace_id": workspace_id}
        result = await self._call_internal_tool(
            tool_name,
            {**tool_input, "user_id": user_id},
            server_prefix=tool.server,
        )
    case "external_mcp":
        from src.connectors.mcp_bridge import call_mcp_tool
        result = await call_mcp_tool(
            tool_name, tool_input,
            user_id=user_id, workspace_id=workspace_id,
        )
    case "composite":
        if workspace_id and "workspace_id" not in tool_input:
            tool_input = {**tool_input, "workspace_id": workspace_id}
        result = await self._call_composite_tool(
            tool_name, tool_input,
            user_id=user_id, workspace_id=workspace_id,
        )
```

`workspace_id` is still passed as a keyword arg to `call_mcp_tool()` for session routing — it's just not injected into the tool_input dict that gets sent to the external MCP server.

### 4. `auth_provider` Label Corrections

**Problem:** GitHub, Linear, Notion have OAuth callback routes (`routes_auth.py`) that store tokens via OAuthManager, but `seed_installations.py` labels them `auth_provider: "token"`. This causes `_resolve_auth()` to take the static-token path instead of calling OAuthManager.

**Fix:**

| Server | Before | After |
|--------|--------|-------|
| github | `"token"` | `"github"` |
| linear | `"token"` | `"linear"` |
| notion | `"token"` | `"notion"` |

Using the specific provider name matches the OAuth path in `_resolve_auth()` at line 502:

```python
if auth_provider in ("oauth", "google", "github", "slack", "linear", "notion", "jira"):
```

These servers still work with stdio + env var injection. When OAuthManager has a valid token, it takes priority. When it doesn't (user hasn't authed yet), the env_template fallback provides the static token.

### 5. Slack Env Var Fix

**Problem:** `_STDIO_TOKEN_ENV_VARS` maps `"slack"` to `"SLACK_BOT_TOKEN"` but the `slack-mcp-server` npm package expects `SLACK_MCP_XOXB_TOKEN` (matching the seed template).

**Fix in `session_pool.py`:**

```python
_STDIO_TOKEN_ENV_VARS: dict[str, str] = {
    "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
    "slack": "SLACK_MCP_XOXB_TOKEN",
    "linear": "LINEAR_ACCESS_TOKEN",
    "notion": "NOTION_TOKEN",
}
```

## Files Changed

| File | Change |
|------|--------|
| `docker-compose.yml` | Add `google-workspace-mcp` service |
| `backend/src/config/settings.py` | Add `google_workspace_mcp_url` setting |
| `backend/src/integrations/seed_installations.py` | Google: stdio→streamable-http. GitHub/Linear/Notion: fix auth_provider |
| `backend/src/api/app.py` | Create OAuthManager before MCP bridge init |
| `backend/src/orchestrator/jarvis.py` | Scope workspace_id injection to internal_mcp + composite |
| `backend/src/integrations/session_pool.py` | Fix Slack env var name |

## What's NOT Changing

- **Atlassian** — already uses `mcp-remote` for HTTP transport with interactive OAuth. No change needed.
- **Playwright, Filesystem, Twilio** — no auth issues, stay on stdio.
- **Internal MCP servers** — stay in-process, they're part of the intelligence layer.
- **Other servers migrating to HTTP** — future work. The Google Workspace pattern (Docker + streamable-http + Bearer auth) is reusable.

## Testing Strategy

1. **Unit tests:** Mock OAuthManager in app.py lifespan, verify it's passed to `initialize_mcp_bridge`
2. **Integration test:** Verify `_resolve_auth()` returns `BearerAuth` for google-workspace when OAuthManager has a token
3. **Workspace_id test:** Verify external_mcp tools don't receive workspace_id in tool_input
4. **Auth_provider test:** Verify _resolve_auth takes OAuth path for github/linear/notion
5. **E2E (manual):** Docker compose up with google-workspace-mcp, authenticate via OAuth, call search_gmail_messages
