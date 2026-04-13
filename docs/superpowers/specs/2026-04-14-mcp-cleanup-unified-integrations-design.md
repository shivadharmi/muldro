# MCP Cleanup & Unified Integrations Page

**Date:** 2026-04-14
**Branch:** `improve-surface-design-v1`
**Status:** Approved

## Problem

1. Five MCP/OAuth providers (discord, linkedin, twitter, linear, twilio) are configured but unused — dead code adding maintenance burden and confusion.
2. The Integrations page has two overlapping sections: an OAuth providers grid and a collapsible "Advanced: MCP Servers" list. Six integrations appear in both, with inconsistent naming (google vs google-workspace, jira vs atlassian).

## Goals

- Remove all code traces of discord, linkedin, twitter, linear, and twilio.
- Merge the two frontend sections into a single unified view with one card per integration.
- Replace emoji icons with proper SVG brand logos.
- Ensure the remaining 7 MCPs (google-workspace, github, slack, notion, atlassian, playwright, filesystem) work correctly end-to-end.

## Non-Goals

- Adding new MCP servers.
- Changing the OAuth flow mechanics (authorize, callback, token storage).
- Modifying old design spec documents (they are historical records).

---

## Section 1: Removal Scope

### Files to Delete

| File | Description |
|------|-------------|
| `backend/src/connectors/linear_connector.py` | Linear GraphQL connector |
| `backend/src/connectors/linkedin_connector.py` | LinkedIn REST connector |
| `backend/src/connectors/twitter_connector.py` | Twitter/X REST v2 connector |
| `backend/src/connectors/twilio_connector.py` | Twilio SMS connector |

### Entries to Remove from Shared Files

| File | Removals |
|------|----------|
| `backend/src/connectors/__init__.py` | 4 import lines: linear, linkedin, twitter, twilio connectors |
| `backend/src/integrations/auth_providers.py` | Remove `discord`, `linkedin`, `twitter` from `SUPPORTED_PROVIDERS`. Remove Discord builder from `_build_builtin_provider()`. Update docstring to remove Discord mention. |
| `backend/src/integrations/seed_installations.py` | Remove `linear` and `twilio` entries from `_DEFAULT_INSTALLATIONS` |
| `backend/src/config/settings.py` | Remove fields: `linear_oauth_client_id`, `linear_oauth_client_secret`, `linear_oauth_redirect_uri`, `linear_access_token`, `linkedin_oauth_client_id`, `linkedin_oauth_client_secret`, `linkedin_oauth_redirect_uri`, `twitter_oauth_client_id`, `twitter_oauth_client_secret`, `twitter_oauth_redirect_uri`, `observation_stale_linear_minutes` |
| `backend/src/tools/catalog.py` | Remove all 24 Linear `ExternalToolSeed` entries |
| `backend/src/services/oauth_manager.py` | Remove `linear`, `linkedin`, `twitter` from client credentials map and token URL map |
| `backend/src/services/integration_manager.py` | Remove `linkedin`, `twitter` from provider mapping |
| `backend/src/api/routes_auth.py` | Remove linear, linkedin, twitter OAuth authorize + callback handlers. Remove from `_provider_servers` map. |
| `backend/src/api/routes_webhooks.py` | Remove Twilio SMS webhook endpoint (`POST /v1/webhooks/twilio/sms`) and related imports/helpers |
| `backend/src/integrations/session_pool.py` | Remove `"linear"` from `_TOKEN_ENV_VARS`, OAuth provider tuple, and `_infer_provider()` |
| `backend/src/integrations/mcp_pool.py` | Remove `"linear"` from `oauth_providers` set |
| `frontend/src/app/integrations/page.tsx` | Remove discord, linear, linkedin, twitter from `PROVIDER_ICONS` (file will be rewritten in Section 3) |

### Tests to Update

| File | Changes |
|------|---------|
| `backend/tests/test_seed_installations.py` | Remove Linear auth_provider test case |
| `backend/tests/test_catalog.py` | Remove Linear seed prefix validation, update seed count assertions |
| `backend/tests/test_foundation_hardening.py` | Remove twilio field assertions, remove linkedin/twitter stale field assertions |
| `backend/tests/test_perception.py` | Remove twitter assertion from `VALID_PERCEPTION_SOURCES` test |

### Environment Files

Remove any `JARVIS_LINEAR_*`, `JARVIS_LINKEDIN_*`, `JARVIS_TWITTER_*`, `LINEAR_ACCESS_TOKEN`, `TWILIO_*` entries from `.env` and `.env.example` if present.

### CLAUDE.md

Update to reflect reduced provider count (9 OAuth providers to 5, remove linear/twilio from MCP installation examples).

---

## Section 2: Backend Unified Endpoint

### New Endpoint

`GET /v1/integrations/unified`

Added to the existing `routes_integrations.py` router.

### Response Schema

```python
class UnifiedIntegrationResponse(BaseModel):
    server_name: str          # MCP installation name (e.g., "google-workspace")
    display_name: str         # Human-readable (e.g., "Google Workspace")
    provider: str | None      # OAuth provider name (e.g., "google"), None for auth-free
    category: str             # "oauth", "token", "local"
    configured: bool          # True if OAuth client_id is set (always True for local/token)
    connected: bool           # True if valid OAuth token exists (always True for local)
    health_status: str        # "healthy", "degraded", "unhealthy", "unknown"
    enabled: bool             # MCP installation enabled flag
    install_id: str | None    # For disconnect/pause/resume actions
    scopes: list[str]         # OAuth scopes or capability scopes
```

### Server-Side Join Logic

Constant mapping from OAuth provider to MCP server name:

```python
_PROVIDER_TO_SERVER = {
    "google": "google-workspace",
    "github": "github",
    "slack": "slack",
    "notion": "notion",
    "jira": "atlassian",
}
```

For each MCP installation in the workspace:
1. Determine category from `auth_provider` field: `None` -> `"local"`, `"token"` -> `"token"`, else `"oauth"`.
2. For OAuth category: look up matching provider, check if credentials configured (settings has `client_id`), check if valid token exists (via `OAuthManager.get_valid_token()`).
3. For token category: `configured=True`, `connected=True` (token presence checked at runtime by the MCP subprocess).
4. For local category: `configured=True`, `connected=True`.
5. Return combined record with health from the installation model.

### Dependencies

Reuses existing:
- `IntegrationControlPlane` for installation listing
- `OAuthManager` for token checks
- `Settings` for credential configuration checks

No new services or models required.

---

## Section 3: Frontend Unified Integrations Page

### Layout

Single page with one card grid. Two visual groupings:

**"Connected Services"** — `category` is `"oauth"` or `"token"`:
- Google Workspace, GitHub, Slack, Notion, Atlassian (Jira)

**"Local Tools"** — `category` is `"local"`:
- Playwright Browser, Filesystem

### Card Structure

Each card displays:
- **Top row:** SVG brand logo + display name + health status dot (green/yellow/red)
- **Middle:** Scope badges (first 2 visible, "+N more" for rest)
- **Bottom:** Action buttons based on state:
  - Connected: `[Reauthorize]` + `[Disconnect]`
  - Not connected but configured: `[Connect]`
  - Not configured: `[Not configured]` (disabled)
  - Local tools: No auth buttons, health dot only

### SVG Brand Logos

New file: `frontend/src/components/integrations/logos.tsx`

Named exports for each integration logo as React components accepting `className`:

| Server Name | Component | Source |
|------------|-----------|--------|
| `google-workspace` | `GoogleLogo` | Google "G" multicolor mark |
| `github` | `GitHubLogo` | GitHub Octocat silhouette |
| `slack` | `SlackLogo` | Slack hash mark |
| `notion` | `NotionLogo` | Notion "N" block mark |
| `atlassian` | `JiraLogo` | Jira blue gradient mark |
| `playwright` | `PlaywrightLogo` | Playwright theatrical mask |
| `filesystem` | `FolderIcon` | Simple folder geometric icon |

Mapping in the integrations page:

```tsx
const LOGOS: Record<string, React.FC<{ className?: string }>> = {
  "google-workspace": GoogleLogo,
  "github": GitHubLogo,
  "slack": SlackLogo,
  "notion": NotionLogo,
  "atlassian": JiraLogo,
  "playwright": PlaywrightLogo,
  "filesystem": FolderIcon,
};
```

### Data Flow

```
fetchUnifiedIntegrations()  ->  GET /v1/integrations/unified
  -> group by category (oauth/token vs local)
  -> render card grid
```

### Files Changed

| File | Change |
|------|--------|
| `frontend/src/lib/api.ts` | Add `UnifiedIntegration` type + `fetchUnifiedIntegrations()` function |
| `frontend/src/app/integrations/page.tsx` | Rewrite: unified endpoint, single card grid, SVG logos, remove `AdvancedMCPSection` |
| `frontend/src/components/integrations/logos.tsx` | New file: SVG brand logo components |

### Removed

- `AdvancedMCPSection` component
- `PROVIDER_ICONS` emoji record
- Separate `fetchAuthProviders` / `fetchInstallations` calls on this page
- `handleConnect()` sub-provider mapping (gmail/calendar/drive to google) — unified endpoint handles this server-side

---

## Section 4: Cleanup

### session_pool.py

1. Remove `"linear": "LINEAR_ACCESS_TOKEN"` from `_TOKEN_ENV_VARS`.
2. Remove `"linear"` from the OAuth provider check tuple.
3. Remove `if "linear" in name_lower: return "linear"` from `_infer_provider()`.

### mcp_pool.py

Remove `"linear"` from `oauth_providers` set:
```python
# Before: {"github", "slack", "linear", "notion"}
# After:  {"github", "slack", "notion"}
```

### routes_auth.py — _provider_servers

After removal, the remaining map:
```python
_provider_servers = {
    "google": ["google-workspace"],
    "github": ["github"],
    "slack": ["slack"],
    "notion": ["notion"],
    "jira": ["atlassian"],
}
```

### .env / .env.example

Remove entries for: `JARVIS_LINEAR_*`, `JARVIS_LINKEDIN_*`, `JARVIS_TWITTER_*`, `LINEAR_ACCESS_TOKEN`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.

### CLAUDE.md

- Update OAuth provider count (9 -> 5: google, github, slack, notion, jira).
- Update MCP installation count (9 -> 7: google-workspace, github, slack, notion, atlassian, playwright, filesystem).
- Remove linear/twilio from MCP server examples.
- Update external tool seed count (remove 24 Linear tools).

---

## Remaining MCP Servers (Post-Cleanup)

| Server | Transport | Auth | Category |
|--------|-----------|------|----------|
| google-workspace | streamable-http | OAuth (google) | oauth |
| github | stdio (docker) | OAuth (github) | oauth |
| slack | stdio (npx) | Token (xoxp/xoxb) | token |
| notion | stdio (npx) | OAuth (notion) | oauth |
| atlassian | stdio (npx/mcp-remote) | OAuth (jira) | oauth |
| playwright | stdio (npx) | None | local |
| filesystem | stdio (npx) | None | local |

Plus `web_search` as an internal composite tool (not an MCP installation).

---

## Section 5: Final Verification — Playwright & Filesystem MCP

After all cleanup and frontend work is complete, verify that the two auth-free MCPs actually work:

1. **Check npm packages are installed / accessible:**
   - `npx -y @playwright/mcp --help` (or equivalent) — confirm the package resolves
   - `npx -y @modelcontextprotocol/server-filesystem --help` — confirm the package resolves

2. **Check seed installations create DB rows:** Run the backend, confirm both appear in `GET /v1/integrations/unified` with `health_status` not `"unhealthy"`.

3. **If packages are missing:** Add them to the project's npm dependencies or document the npx-on-demand requirement. Fix any PATH or Docker issues preventing subprocess launch.

4. **If health checks fail:** Debug via `GET /v1/integrations/{id}/health` and fix the underlying transport/spawn issue.

This step is a gate — do not mark the work complete until both local MCPs respond to a health check.

---

## Risk Assessment

- **Low risk:** Removing dead code (discord, linkedin, twitter have no MCP servers; linear/twilio are unused).
- **Medium risk:** Frontend rewrite of integrations page — mitigated by keeping the same connect/disconnect/reauthorize flows, just unified.
- **Low risk:** New unified endpoint — read-only, reuses existing services, no new writes.
- **Test coverage:** Update existing tests to remove assertions for deleted providers. Add test for unified endpoint.
