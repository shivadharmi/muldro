# MCP Cleanup & Unified Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove discord, linkedin, twitter, linear, and twilio integrations completely, then unify the Integrations page with a single backend endpoint and proper SVG logos.

**Architecture:** Delete 4 connector files, strip entries from ~15 shared backend files, add one unified API endpoint joining auth + MCP data, rewrite the frontend Integrations page with a single card grid and SVG brand logos.

**Tech Stack:** Python/FastAPI (backend), React/Next.js/TypeScript (frontend), SQLAlchemy, Pydantic, TanStack Query

**Spec:** `docs/superpowers/specs/2026-04-14-mcp-cleanup-unified-integrations-design.md`

---

## File Map

### Files to Delete
- `backend/src/connectors/linear_connector.py`
- `backend/src/connectors/linkedin_connector.py`
- `backend/src/connectors/twitter_connector.py`
- `backend/src/connectors/twilio_connector.py`

### Files to Create
- `frontend/src/components/integrations/logos.tsx` — SVG brand logo components

### Files to Modify
- `backend/src/connectors/__init__.py` — remove 4 imports
- `backend/src/integrations/auth_providers.py` — remove 3 providers + Discord builder
- `backend/src/integrations/seed_installations.py` — remove 2 installations
- `backend/src/config/settings.py` — remove 11 settings fields
- `backend/src/tools/catalog.py` — remove 24 Linear tool seeds
- `backend/src/services/oauth_manager.py` — remove 3 providers from 2 maps
- `backend/src/services/integration_manager.py` — remove 2 providers from map
- `backend/src/api/routes_auth.py` — remove 3 authorize + 3 callback handlers + map entries
- `backend/src/api/routes_webhooks.py` — remove Twilio webhook
- `backend/src/integrations/session_pool.py` — remove linear from 3 places
- `backend/src/integrations/mcp_pool.py` — remove linear from oauth set
- `backend/src/api/routes_integrations.py` — add unified endpoint
- `frontend/src/lib/api.ts` — add unified integration type + fetch function
- `frontend/src/app/integrations/page.tsx` — rewrite with unified cards
- `backend/tests/test_seed_installations.py` — remove Linear test
- `backend/tests/test_catalog.py` — remove Linear tests, update counts
- `backend/tests/test_foundation_hardening.py` — update assertions
- `backend/tests/test_perception.py` — remove twitter assertion
- `backend/tests/test_orchestrator.py` — update linear tool reference
- `backend/tests/test_phase5_capabilities.py` — remove Linear capability test
- `CLAUDE.md` — update provider/server counts

---

## Task 1: Delete Connector Files

**Files:**
- Delete: `backend/src/connectors/linear_connector.py`
- Delete: `backend/src/connectors/linkedin_connector.py`
- Delete: `backend/src/connectors/twitter_connector.py`
- Delete: `backend/src/connectors/twilio_connector.py`
- Modify: `backend/src/connectors/__init__.py`

- [ ] **Step 1: Delete the 4 connector files**

```bash
cd /Users/sivasankarreddybogala/work/jarvis
rm backend/src/connectors/linear_connector.py
rm backend/src/connectors/linkedin_connector.py
rm backend/src/connectors/twitter_connector.py
rm backend/src/connectors/twilio_connector.py
```

- [ ] **Step 2: Remove imports from `backend/src/connectors/__init__.py`**

Remove lines 8, 9, 12, 13. The file should become:

```python
"""Connector package — import all modules so @register_connector decorators fire."""

import src.connectors.calendar  # noqa: F401
import src.connectors.drive_connector  # noqa: F401
import src.connectors.github_connector  # noqa: F401
import src.connectors.gmail  # noqa: F401
import src.connectors.jira_connector  # noqa: F401
import src.connectors.notion_connector  # noqa: F401
import src.connectors.slack_connector  # noqa: F401
import src.connectors.web_search_connector  # noqa: F401
import src.connectors.whatsapp_connector  # noqa: F401
```

- [ ] **Step 3: Verify imports work**

Run: `cd backend && python -c "import src.connectors"`
Expected: No ImportError

- [ ] **Step 4: Commit**

```bash
git add -A backend/src/connectors/
git commit -m "refactor: remove linear, linkedin, twitter, twilio connectors"
```

---

## Task 2: Remove OAuth Provider Entries

**Files:**
- Modify: `backend/src/integrations/auth_providers.py`

- [ ] **Step 1: Remove discord, linkedin, twitter from `SUPPORTED_PROVIDERS`**

In `backend/src/integrations/auth_providers.py`, remove these 3 entries from the `SUPPORTED_PROVIDERS` dict (lines 54-59 for discord, 97-104 for linkedin, 105-112 for twitter):

```python
# DELETE this block (discord):
    "discord": ProviderMeta(
        name="discord",
        display_name="Discord",
        provider_type="builtin",
        default_scopes=["identify", "guilds"],
    ),

# DELETE this block (linkedin):
    "linkedin": ProviderMeta(
        name="linkedin",
        display_name="LinkedIn",
        provider_type="oauth_proxy",
        default_scopes=["openid", "profile", "w_member_social"],
        authorize_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
    ),

# DELETE this block (twitter):
    "twitter": ProviderMeta(
        name="twitter",
        display_name="Twitter / X",
        provider_type="oauth_proxy",
        default_scopes=["tweet.read", "tweet.write", "users.read"],
        authorize_url="https://twitter.com/i/oauth2/authorize",
        token_url="https://api.twitter.com/2/oauth2/token",
    ),
```

- [ ] **Step 2: Remove Discord builder from `_build_builtin_provider()`**

Remove lines 186-199 (the `if provider_name == "discord":` block) and the `DiscordProvider` import.

- [ ] **Step 3: Update docstring**

Change line 3 from:
```python
Built-in providers: Google, GitHub, Discord (native FastMCP support).
```
to:
```python
Built-in providers: Google, GitHub (native FastMCP support).
```

- [ ] **Step 4: Verify module loads**

Run: `cd backend && python -c "from src.integrations.auth_providers import SUPPORTED_PROVIDERS; print(list(SUPPORTED_PROVIDERS.keys()))"`
Expected: `['google', 'github', 'slack', 'notion', 'jira']`

- [ ] **Step 5: Commit**

```bash
git add backend/src/integrations/auth_providers.py
git commit -m "refactor: remove discord, linkedin, twitter from auth providers"
```

---

## Task 3: Remove Seed Installations and Tool Seeds

**Files:**
- Modify: `backend/src/integrations/seed_installations.py`
- Modify: `backend/src/tools/catalog.py`

- [ ] **Step 1: Remove linear and twilio from `_DEFAULT_INSTALLATIONS`**

In `backend/src/integrations/seed_installations.py`, remove the linear entry (lines 146-163) and the twilio entry (lines 201-213).

The linear block to delete:
```python
    {
        "server_name": "linear",
        ...
    },
```

The twilio block to delete:
```python
    {
        "server_name": "twilio",
        ...
    },
```

After removal, `_DEFAULT_INSTALLATIONS` should have 7 entries: google-workspace, github, slack, playwright, filesystem, notion, atlassian.

- [ ] **Step 2: Remove all 24 Linear tool seeds from catalog.py**

In `backend/src/tools/catalog.py`, delete lines 445-501 (the `# linear (24 tools, verified=True)` comment through the last `linear_auth_callback` entry).

- [ ] **Step 3: Verify seed counts**

Run: `cd backend && python -c "from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS; print(len(_DEFAULT_INSTALLATIONS), [i['server_name'] for i in _DEFAULT_INSTALLATIONS])"`
Expected: `7 ['google-workspace', 'github', 'slack', 'playwright', 'filesystem', 'notion', 'atlassian']`

Run: `cd backend && python -c "from src.tools.catalog import EXTERNAL_TOOL_SEEDS; servers = {s.server for s in EXTERNAL_TOOL_SEEDS}; print(sorted(servers))"`
Expected: `['_composite', 'atlassian', 'filesystem', 'github', 'google-workspace', 'notion', 'playwright', 'slack']` — no `linear`

- [ ] **Step 4: Commit**

```bash
git add backend/src/integrations/seed_installations.py backend/src/tools/catalog.py
git commit -m "refactor: remove linear and twilio seed installations and tool seeds"
```

---

## Task 4: Remove Settings Fields

**Files:**
- Modify: `backend/src/config/settings.py`

- [ ] **Step 1: Remove Linear, LinkedIn, Twitter settings**

Delete these lines from `backend/src/config/settings.py`:

Line 61: `observation_stale_linear_minutes: int = 30`

Lines 104-108 (Linear OAuth block):
```python
    # Linear OAuth
    linear_oauth_client_id: str = ""
    linear_oauth_client_secret: str = ""
    linear_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/linear/callback"
    linear_access_token: str = ""  # For MCP server (mcp-server-linear)
```

Lines 126-129 (LinkedIn OAuth block):
```python
    # LinkedIn OAuth
    linkedin_oauth_client_id: str = ""
    linkedin_oauth_client_secret: str = ""
    linkedin_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/linkedin/callback"
```

Lines 131-134 (Twitter OAuth block):
```python
    # Twitter/X OAuth (PKCE)
    twitter_oauth_client_id: str = ""
    twitter_oauth_client_secret: str = ""
    twitter_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/twitter/callback"
```

- [ ] **Step 2: Verify settings load**

Run: `cd backend && python -c "from src.config.settings import Settings; s = Settings(); print('OK')" 2>/dev/null || echo "Check .env"`
Expected: `OK` (or acceptable env var warning)

- [ ] **Step 3: Commit**

```bash
git add backend/src/config/settings.py
git commit -m "refactor: remove linear, linkedin, twitter settings fields"
```

---

## Task 5: Remove OAuth Manager and Integration Manager References

**Files:**
- Modify: `backend/src/services/oauth_manager.py`
- Modify: `backend/src/services/integration_manager.py`

- [ ] **Step 1: Clean oauth_manager.py**

In `backend/src/services/oauth_manager.py`, remove linear, linkedin, twitter from two dicts:

In `_get_client_credentials()` `settings_map` (lines 172-180), remove:
```python
            "linear": ("linear_oauth_client_id", "linear_oauth_client_secret"),
            "linkedin": ("linkedin_oauth_client_id", "linkedin_oauth_client_secret"),
            "twitter": ("twitter_oauth_client_id", "twitter_oauth_client_secret"),
```

The remaining map should be:
```python
        settings_map: dict[str, tuple[str, str]] = {
            "google": ("google_oauth_client_id", "google_oauth_client_secret"),
            "github": ("github_oauth_client_id", "github_oauth_client_secret"),
            "notion": ("notion_oauth_client_id", "notion_oauth_client_secret"),
            "jira": ("jira_oauth_client_id", "jira_oauth_client_secret"),
        }
```

In `_refresh_token()` `endpoints` (lines 198-206), remove:
```python
            "linear": "https://api.linear.app/oauth/token",
            "linkedin": "https://www.linkedin.com/oauth/v2/accessToken",
            "twitter": "https://api.twitter.com/2/oauth2/token",
```

The remaining map should be:
```python
        endpoints = {
            "google": "https://oauth2.googleapis.com/token",
            "github": "https://github.com/login/oauth/access_token",
            "slack": "https://slack.com/api/oauth.v2.access",
            "jira": "https://auth.atlassian.com/oauth/token",
        }
```

- [ ] **Step 2: Clean integration_manager.py**

In `backend/src/services/integration_manager.py`, remove linkedin and twitter from `_PROVIDER_TO_OAUTH` (lines 29-40):

Remove:
```python
    "linear": "linear",
    "linkedin": "linkedin",
    "twitter": "twitter",
```

The remaining map should be:
```python
_PROVIDER_TO_OAUTH: dict[str, str] = {
    "gmail": "google",
    "calendar": "google",
    "drive": "google",
    "github": "github",
    "slack": "slack",
    "notion": "notion",
    "jira": "jira",
}
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/oauth_manager.py backend/src/services/integration_manager.py
git commit -m "refactor: remove linear, linkedin, twitter from oauth and integration managers"
```

---

## Task 6: Remove OAuth Route Handlers

**Files:**
- Modify: `backend/src/api/routes_auth.py`

- [ ] **Step 1: Remove authorize handlers**

In `backend/src/api/routes_auth.py` function `oauth_authorize()`, remove these `elif` blocks:

Lines 254-267 (linear authorize):
```python
    elif provider == "linear":
        ...
        return OAuthUrlResponse(url=url, provider="linear")
```

Lines 299-311 (linkedin authorize):
```python
    elif provider == "linkedin":
        ...
        return OAuthUrlResponse(url=url, provider="linkedin")
```

Lines 313-352 (twitter authorize — includes PKCE logic):
```python
    elif provider == "twitter":
        ...
        return OAuthUrlResponse(url=url, provider="twitter")
```

- [ ] **Step 2: Remove callback handlers**

In the callback function, remove these `elif` blocks:

Lines 546-596 (linear callback):
```python
    elif provider == "linear":
        ...
        background_tasks.add_task(_trigger_initial_observation, user_id, ["linear"], workspace_id)
```

Lines 741-789 (linkedin callback):
```python
    elif provider == "linkedin":
        ...
        logger.info("LinkedIn integration linked for %s", user_id)
```

Lines 791-866 (twitter callback — includes PKCE verifier retrieval):
```python
    elif provider == "twitter":
        ...
        logger.info("Twitter integration linked for %s", user_id)
```

- [ ] **Step 3: Clean up `_provider_servers` map**

In the `_provider_servers` dict (lines 876-885), remove:
```python
            "linear": ["linear"],
            "linkedin": [],
            "twitter": [],
```

The remaining map should be:
```python
        _provider_servers = {
            "google": ["google-workspace"],
            "github": ["github"],
            "slack": ["slack"],
            "notion": ["notion"],
            "jira": ["atlassian"],
        }
```

- [ ] **Step 4: Verify routes load**

Run: `cd backend && python -c "from src.api.routes_auth import router; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes_auth.py
git commit -m "refactor: remove linear, linkedin, twitter OAuth route handlers"
```

---

## Task 7: Remove Twilio Webhook and Session/MCP Pool References

**Files:**
- Modify: `backend/src/api/routes_webhooks.py`
- Modify: `backend/src/integrations/session_pool.py`
- Modify: `backend/src/integrations/mcp_pool.py`

- [ ] **Step 1: Remove Twilio webhook from routes_webhooks.py**

Delete lines 143-183 in `backend/src/api/routes_webhooks.py`:
```python
# ── Twilio SMS Webhook ───────────────────────────────────────


@router.post("/v1/webhooks/twilio/sms")
async def twilio_sms_webhook(
    ...
):
    ...
```

- [ ] **Step 2: Clean session_pool.py**

In `backend/src/integrations/session_pool.py`:

Remove `"linear": "LINEAR_ACCESS_TOKEN"` from `_STDIO_TOKEN_ENV_VARS` (line 36). Remaining:
```python
_STDIO_TOKEN_ENV_VARS: dict[str, str] = {
    "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
    "slack": "SLACK_MCP_XOXB_TOKEN",
    "notion": "NOTION_TOKEN",
}
```

Remove `"linear"` from the OAuth provider tuple (line 638):
```python
# Before:
if auth_provider in ("oauth", "google", "github", "slack", "linear", "notion", "jira"):
# After:
if auth_provider in ("oauth", "google", "github", "slack", "notion", "jira"):
```

Remove the `if "linear" in name_lower:` branch from `_infer_provider()` (lines 668-669):
```python
# DELETE these 2 lines:
    if "linear" in name_lower:
        return "linear"
```

- [ ] **Step 3: Clean mcp_pool.py**

In `backend/src/integrations/mcp_pool.py` (line 281):
```python
# Before:
oauth_providers = {"github", "slack", "linear", "notion"}
# After:
oauth_providers = {"github", "slack", "notion"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes_webhooks.py backend/src/integrations/session_pool.py backend/src/integrations/mcp_pool.py
git commit -m "refactor: remove twilio webhook, linear from session/mcp pools"
```

---

## Task 8: Update Tests

**Files:**
- Modify: `backend/tests/test_seed_installations.py`
- Modify: `backend/tests/test_catalog.py`
- Modify: `backend/tests/test_foundation_hardening.py`
- Modify: `backend/tests/test_perception.py`
- Modify: `backend/tests/test_orchestrator.py`
- Modify: `backend/tests/test_phase5_capabilities.py`

- [ ] **Step 1: Remove Linear test from test_seed_installations.py**

Delete the `test_linear_auth_provider_is_oauth` method (lines 115-120):
```python
# DELETE:
    def test_linear_auth_provider_is_oauth(self):
        seed = self._get_seed("linear")
        actual = seed["auth_provider"]
        assert actual == "linear", (
            f"Linear uses OAuth flow — auth_provider must be 'linear', got '{actual}'"
        )
```

- [ ] **Step 2: Remove Linear tests from test_catalog.py**

Delete the entire `test_linear_seeds_prefix` function (lines 217-224):
```python
# DELETE:
def test_linear_seeds_prefix():
    """Verify Linear seeds all start with linear_ prefix."""
    linear_seeds = get_seeds_for_server("linear")
    assert len(linear_seeds) == 24
    for seed in linear_seeds:
        assert seed.name.startswith("linear_"), (
            f"Linear tool {seed.name} should start with 'linear_'"
        )
```

In `test_seeds_for_server_counts()` (line 234), remove the `"linear": 24,` entry from `expected_counts`.

In `test_get_verified_seeds_helper()` (line 263), update the verified count:
```python
# Before:
    assert len(verified) == 100
# After:
    assert len(verified) == 76
```

In `test_get_verified_seeds_helper()` (line 271), remove `"linear"` from `expected_verified`:
```python
# Before:
    expected_verified = {"notion", "linear", "playwright", "filesystem", "google-workspace"}
# After:
    expected_verified = {"notion", "playwright", "filesystem", "google-workspace"}
```

In `test_seed_server_names_match_installations()` (line 287), remove `"linear"` from `expected_servers`:
```python
# Before:
    expected_servers = {
        "google-workspace",
        "github",
        "slack",
        "notion",
        "linear",
        "playwright",
        "filesystem",
        "atlassian",
        "_composite",
    }
# After:
    expected_servers = {
        "google-workspace",
        "github",
        "slack",
        "notion",
        "playwright",
        "filesystem",
        "atlassian",
        "_composite",
    }
```

- [ ] **Step 3: Update test_foundation_hardening.py**

Add linear field assertions to `test_unused_stale_observation_fields_removed` (since we just removed `observation_stale_linear_minutes` from settings) — add at line 61:
```python
        assert not hasattr(Settings, "observation_stale_linear_minutes")
```

Add linear OAuth field assertions to a new or existing test in `TestSettingsCleanup`:
```python
    def test_unused_linear_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "linear_oauth_client_id")
        assert not hasattr(Settings, "linear_oauth_client_secret")
        assert not hasattr(Settings, "linear_access_token")

    def test_unused_linkedin_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "linkedin_oauth_client_id")
        assert not hasattr(Settings, "linkedin_oauth_client_secret")

    def test_unused_twitter_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "twitter_oauth_client_id")
        assert not hasattr(Settings, "twitter_oauth_client_secret")
```

- [ ] **Step 4: Update test_perception.py**

Remove the twitter assertion (line 358):
```python
# DELETE:
        assert "twitter" not in VALID_PERCEPTION_SOURCES
```

- [ ] **Step 5: Update test_orchestrator.py**

In `test_write_tools_allowed_by_hook` (line 293), change `"linear_delete_issue"` to another write tool that still exists:
```python
# Before:
        result = await governor_pre_tool_hook(
            "linear_delete_issue", {}, "operator", user_id=TEST_USER_ID
        )
# After:
        result = await governor_pre_tool_hook(
            "API-delete-block", {}, "operator", user_id=TEST_USER_ID
        )
```

- [ ] **Step 6: Remove Linear capability test from test_phase5_capabilities.py**

Delete the entire `test_new_linear_tools_mapped` method (lines 115-137).

- [ ] **Step 7: Run all tests**

Run: `cd backend && pytest tests/test_seed_installations.py tests/test_catalog.py tests/test_foundation_hardening.py tests/test_perception.py tests/test_orchestrator.py tests/test_phase5_capabilities.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add backend/tests/
git commit -m "test: update tests for removed integrations"
```

---

## Task 9: Add Unified Integrations Endpoint

**Files:**
- Modify: `backend/src/api/routes_integrations.py`
- Test: `backend/tests/test_unified_integrations.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_unified_integrations.py`:

```python
"""Tests for the unified integrations endpoint."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.api.routes_integrations import UnifiedIntegrationResponse


class TestUnifiedIntegrationResponse:
    def test_local_category(self):
        resp = UnifiedIntegrationResponse(
            server_name="playwright",
            display_name="Playwright Browser",
            provider=None,
            category="local",
            configured=True,
            connected=True,
            health_status="healthy",
            enabled=True,
            install_id="inst_abc",
            scopes=[],
        )
        assert resp.category == "local"
        assert resp.configured is True
        assert resp.connected is True

    def test_oauth_category(self):
        resp = UnifiedIntegrationResponse(
            server_name="google-workspace",
            display_name="Google Workspace",
            provider="google",
            category="oauth",
            configured=True,
            connected=False,
            health_status="unknown",
            enabled=True,
            install_id="inst_def",
            scopes=["email.send", "calendar.list"],
        )
        assert resp.category == "oauth"
        assert resp.provider == "google"
        assert len(resp.scopes) == 2

    def test_token_category(self):
        resp = UnifiedIntegrationResponse(
            server_name="slack",
            display_name="Slack",
            provider="slack",
            category="token",
            configured=True,
            connected=True,
            health_status="healthy",
            enabled=True,
            install_id="inst_ghi",
            scopes=["messaging.send"],
        )
        assert resp.category == "token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_unified_integrations.py -v`
Expected: FAIL — `ImportError: cannot import name 'UnifiedIntegrationResponse'`

- [ ] **Step 3: Add the unified endpoint to routes_integrations.py**

Add the response model and endpoint to `backend/src/api/routes_integrations.py`. Add after the existing `HealthCheckResponse` model (around line 75):

```python
class UnifiedIntegrationResponse(BaseModel):
    server_name: str
    display_name: str
    provider: str | None = None
    category: str  # "oauth", "token", "local"
    configured: bool
    connected: bool
    health_status: str
    enabled: bool
    install_id: str | None = None
    scopes: list[str] = []
```

Add the endpoint after the existing `list_installations` endpoint:

```python
@router.get("/unified", response_model=list[UnifiedIntegrationResponse])
async def list_unified_integrations(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Unified view: joins MCP installations with OAuth provider status."""
    from src.config.settings import Settings, get_settings
    from src.integrations.control_plane import IntegrationControlPlane
    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    settings: Settings = get_settings()
    cp = IntegrationControlPlane(db, workspace_id)
    installations = await cp.list_installations()

    # Build OAuth manager for token checks
    oauth_mgr: OAuthManager | None = None
    if settings.oauth_encryption_key:
        db_factory = get_session_factory()
        oauth_mgr = OAuthManager(
            db_factory,
            encryption_key=settings.oauth_encryption_key,
            settings=settings,
        )

    # Provider name -> settings attribute for client_id
    _provider_client_id_attr = {
        "google": "google_oauth_client_id",
        "github": "github_oauth_client_id",
        "slack": "slack_oauth_client_id",
        "notion": "notion_oauth_client_id",
        "jira": "jira_oauth_client_id",
    }

    # Server auth_provider -> OAuth provider name for token checks
    _server_to_oauth_provider = {
        "google": "google",
        "github": "github",
        "slack": "slack",
        "notion": "notion",
        "jira": "jira",
        "oauth": "jira",  # atlassian uses generic "oauth"
    }

    results: list[UnifiedIntegrationResponse] = []
    for inst in installations:
        auth_provider = inst.auth_provider

        # Determine category
        if auth_provider is None:
            category = "local"
        elif auth_provider == "token":
            category = "token"
        else:
            category = "oauth"

        # Determine configured + connected
        configured = True
        connected = True

        if category == "oauth":
            oauth_name = _server_to_oauth_provider.get(auth_provider, auth_provider)
            client_id_attr = _provider_client_id_attr.get(oauth_name, "")
            configured = bool(getattr(settings, client_id_attr, "")) if client_id_attr else False
            connected = False
            if configured and oauth_mgr:
                try:
                    token = await oauth_mgr.get_valid_token(user_id, oauth_name)
                    connected = token is not None
                except Exception:
                    connected = False

        results.append(
            UnifiedIntegrationResponse(
                server_name=inst.server_name,
                display_name=inst.display_name,
                provider=_server_to_oauth_provider.get(
                    auth_provider, auth_provider
                ) if auth_provider and auth_provider not in ("token", "none") else auth_provider,
                category=category,
                configured=configured,
                connected=connected,
                health_status=inst.health_status,
                enabled=inst.enabled,
                install_id=inst.install_id,
                scopes=inst.scopes_granted or [],
            )
        )

    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_unified_integrations.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes_integrations.py backend/tests/test_unified_integrations.py
git commit -m "feat: add unified integrations endpoint"
```

---

## Task 10: Create SVG Logo Components

**Files:**
- Create: `frontend/src/components/integrations/logos.tsx`

- [ ] **Step 1: Create the logos file**

Create `frontend/src/components/integrations/logos.tsx` with SVG brand logos:

```tsx
interface LogoProps {
  className?: string;
}

export function GoogleLogo({ className }: LogoProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  );
}

export function GitHubLogo({ className }: LogoProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </svg>
  );
}

export function SlackLogo({ className }: LogoProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <path
        d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z"
        fill="#E01E5A"
      />
      <path
        d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z"
        fill="#36C5F0"
      />
      <path
        d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zm-1.27 0a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.163 0a2.528 2.528 0 0 1 2.523 2.522v6.312z"
        fill="#2EB67D"
      />
      <path
        d="M15.163 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.163 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zm0-1.27a2.527 2.527 0 0 1-2.52-2.523 2.527 2.527 0 0 1 2.52-2.52h6.315A2.528 2.528 0 0 1 24 15.163a2.528 2.528 0 0 1-2.522 2.523h-6.315z"
        fill="#ECB22E"
      />
    </svg>
  );
}

export function NotionLogo({ className }: LogoProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L18.29 2.09c-.467-.373-.746-.186-1.306-.093l-12.656.84c-.466.046-.56.28-.373.466l.505.905zm.793 3.36v13.914c0 .746.373 1.026 1.213.98l14.522-.84c.84-.046.933-.56.933-1.166V6.63c0-.606-.233-.933-.746-.886l-15.176.886c-.56.047-.746.327-.746.933zm14.336.42c.094.42 0 .84-.42.886l-.7.14v10.264c-.607.327-1.166.513-1.633.513-.746 0-.933-.233-1.493-.933l-4.573-7.178v6.952l1.446.327s0 .84-1.166.84l-3.22.187c-.093-.187 0-.653.326-.746l.84-.233V9.854L7.822 9.76c-.093-.42.14-1.026.793-1.073l3.453-.233 4.76 7.272V9.527l-1.213-.14c-.093-.513.28-.886.746-.933l3.22-.187z" />
    </svg>
  );
}

export function JiraLogo({ className }: LogoProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <path
        d="M11.571 11.513H0a5.218 5.218 0 0 0 5.232 5.215h2.13v2.057A5.215 5.215 0 0 0 12.575 24V12.518a1.005 1.005 0 0 0-1.005-1.005z"
        fill="#2684FF"
      />
      <path
        d="M17.09 5.98H5.518a5.218 5.218 0 0 0 5.233 5.214h2.13v2.058A5.216 5.216 0 0 0 18.094 18.5V6.984A1.005 1.005 0 0 0 17.09 5.98z"
        fill="url(#jira-grad-1)"
      />
      <path
        d="M22.6.448H11.028a5.218 5.218 0 0 0 5.233 5.214h2.13v2.058A5.215 5.215 0 0 0 23.604 12.97V1.453A1.005 1.005 0 0 0 22.6.449z"
        fill="url(#jira-grad-2)"
      />
      <defs>
        <linearGradient id="jira-grad-1" x1="12.12" y1="6.1" x2="7.34" y2="11.38" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0052CC" />
          <stop offset="1" stopColor="#2684FF" />
        </linearGradient>
        <linearGradient id="jira-grad-2" x1="17.84" y1="0.56" x2="12.87" y2="5.73" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0052CC" />
          <stop offset="1" stopColor="#2684FF" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function PlaywrightLogo({ className }: LogoProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <circle cx="8" cy="9" r="7" fill="#2EAD33" opacity="0.9" />
      <circle cx="16" cy="9" r="7" fill="#D65348" opacity="0.9" />
      <ellipse cx="8" cy="8.5" rx="1.5" ry="2" fill="white" />
      <ellipse cx="16" cy="8.5" rx="1.5" ry="2" fill="white" />
      <path d="M6 14c1 2 3 3 6 3s5-1 6-3" stroke="#333" strokeWidth="1.2" strokeLinecap="round" fill="none" />
    </svg>
  );
}

export function FolderIcon({ className }: LogoProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit src/components/integrations/logos.tsx 2>&1 | head -5`
Expected: No errors (or only unrelated project-wide errors)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/integrations/logos.tsx
git commit -m "feat: add SVG brand logos for integrations page"
```

---

## Task 11: Add Frontend API Type and Fetch Function

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add the unified integration type and fetch function**

Add to `frontend/src/lib/api.ts` (near the existing `AuthProvider` and `Installation` types):

```typescript
export interface UnifiedIntegration {
  server_name: string;
  display_name: string;
  provider: string | null;
  category: "oauth" | "token" | "local";
  configured: boolean;
  connected: boolean;
  health_status: string;
  enabled: boolean;
  install_id: string | null;
  scopes: string[];
}

export function fetchUnifiedIntegrations(): Promise<UnifiedIntegration[]> {
  return apiFetch("/integrations/unified");
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat: add unified integration type and fetch function"
```

---

## Task 12: Rewrite Integrations Page

**Files:**
- Modify: `frontend/src/app/integrations/page.tsx`

- [ ] **Step 1: Rewrite the integrations page**

Replace the entire content of `frontend/src/app/integrations/page.tsx` with:

```tsx
"use client";

import { Suspense, useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  fetchUnifiedIntegrations,
  getAuthUrl,
  deleteInstallation,
  type UnifiedIntegration,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { SkeletonGrid } from "@/components/ui/skeleton";
import {
  GoogleLogo,
  GitHubLogo,
  SlackLogo,
  NotionLogo,
  JiraLogo,
  PlaywrightLogo,
  FolderIcon,
} from "@/components/integrations/logos";

type LogoComponent = React.FC<{ className?: string }>;

const LOGOS: Record<string, LogoComponent> = {
  "google-workspace": GoogleLogo,
  github: GitHubLogo,
  slack: SlackLogo,
  notion: NotionLogo,
  atlassian: JiraLogo,
  playwright: PlaywrightLogo,
  filesystem: FolderIcon,
};

/** Map MCP server auth_provider to the OAuth provider name used in authorize URL. */
const AUTH_PROVIDER_MAP: Record<string, string> = {
  google: "google",
  github: "github",
  slack: "slack",
  notion: "notion",
  jira: "jira",
  oauth: "jira", // atlassian uses generic "oauth"
};

function HealthDot({ status }: { status: string }) {
  const color =
    status === "healthy"
      ? "bg-j-success"
      : status === "degraded"
        ? "bg-j-warning"
        : "bg-j-error";
  return (
    <span
      className={`w-2 h-2 rounded-full ${color}`}
      title={status}
    />
  );
}

function IntegrationsContent() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const [connecting, setConnecting] = useState<string | null>(null);
  const { addToast } = useToast();

  useEffect(() => {
    const status = searchParams.get("status");
    const provider = searchParams.get("provider");
    const error = searchParams.get("error");
    if (status === "connected" && provider) {
      queryClient.invalidateQueries({ queryKey: ["unified-integrations"] });
      addToast(`${provider} connected successfully`, "success");
      window.history.replaceState({}, "", "/integrations");
    } else if (error) {
      addToast(`Error: ${error}`, "error");
      window.history.replaceState({}, "", "/integrations");
    }
  }, [searchParams, queryClient, addToast]);

  const { data: integrations, isLoading } = useQuery({
    queryKey: ["unified-integrations"],
    queryFn: fetchUnifiedIntegrations,
  });

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => deleteInstallation(id),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["unified-integrations"] });
      const prev = queryClient.getQueryData(["unified-integrations"]);
      queryClient.setQueryData(
        ["unified-integrations"],
        (old: UnifiedIntegration[] | undefined) =>
          old ? old.filter((i) => i.install_id !== id) : old,
      );
      return { prev };
    },
    onError: (err, _id, context) => {
      if (context?.prev)
        queryClient.setQueryData(["unified-integrations"], context.prev);
      addToast(`Failed to disconnect: ${err.message}`, "error");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["unified-integrations"] });
    },
  });

  async function handleConnect(integration: UnifiedIntegration) {
    const provider =
      AUTH_PROVIDER_MAP[integration.provider ?? ""] ?? integration.provider;
    if (!provider) return;
    setConnecting(integration.server_name);
    try {
      const { url } = await getAuthUrl(provider);
      window.location.href = url;
    } catch (err) {
      addToast(
        `Failed to start OAuth: ${err instanceof Error ? err.message : "Unknown error"}`,
        "error",
      );
      setConnecting(null);
    }
  }

  const services = (integrations ?? []).filter(
    (i) => i.category === "oauth" || i.category === "token",
  );
  const localTools = (integrations ?? []).filter(
    (i) => i.category === "local",
  );

  function renderCard(integration: UnifiedIntegration) {
    const Logo = LOGOS[integration.server_name];

    return (
      <Card key={integration.server_name}>
        <div className="p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2.5">
              {Logo ? (
                <Logo className="w-5 h-5 shrink-0" />
              ) : (
                <span className="w-5 h-5 rounded bg-surface-2" />
              )}
              <div>
                <h3 className="text-sm font-medium text-t-primary">
                  {integration.display_name}
                </h3>
                <p className="text-xs text-t-secondary">
                  {integration.category === "local"
                    ? "Local tool"
                    : integration.category === "token"
                      ? "Token auth"
                      : "OAuth connection"}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <HealthDot status={integration.health_status} />
              <Badge
                variant={integration.connected ? "green" : "default"}
              >
                {integration.connected ? "Connected" : "Not connected"}
              </Badge>
            </div>
          </div>

          {integration.scopes.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3">
              {integration.scopes.slice(0, 2).map((scope) => (
                <span
                  key={scope}
                  className="text-[11px] px-1.5 py-0.5 rounded bg-surface-2 text-t-secondary"
                >
                  {scope.split(".").pop() || scope}
                </span>
              ))}
              {integration.scopes.length > 2 && (
                <span
                  className="text-[11px] px-1.5 py-0.5 text-t-secondary"
                  title={integration.scopes.join(", ")}
                >
                  +{integration.scopes.length - 2} more
                </span>
              )}
            </div>
          )}

          {integration.category !== "local" && (
            <div className="flex gap-2">
              {integration.connected && integration.install_id ? (
                <>
                  <button
                    onClick={() => handleConnect(integration)}
                    className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2"
                  >
                    Reauthorize
                  </button>
                  <button
                    onClick={() =>
                      disconnectMutation.mutate(integration.install_id!)
                    }
                    className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-j-error/30 text-j-error hover:bg-j-error-soft"
                  >
                    Disconnect
                  </button>
                </>
              ) : integration.connected ? (
                <button
                  onClick={() => handleConnect(integration)}
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2"
                >
                  Reauthorize
                </button>
              ) : (
                <button
                  onClick={() => handleConnect(integration)}
                  disabled={
                    connecting === integration.server_name ||
                    !integration.configured
                  }
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
                >
                  {connecting === integration.server_name
                    ? "Redirecting..."
                    : !integration.configured
                      ? "Not configured"
                      : "Connect"}
                </button>
              )}
            </div>
          )}
        </div>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-6">
        <PageHeader
          title="Integrations"
          subtitle="Manage connections and data sources"
          variant="config"
        />
        <SkeletonGrid count={6} />
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader
        title="Integrations"
        subtitle="Manage connections and data sources"
        variant="config"
      />

      {services.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-t-secondary mb-3">
            Connected Services
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {services.map(renderCard)}
          </div>
        </div>
      )}

      {localTools.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-t-secondary mb-3">
            Local Tools
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {localTools.map(renderCard)}
          </div>
        </div>
      )}
    </div>
  );
}

export default function IntegrationsPage() {
  return (
    <Suspense>
      <IntegrationsContent />
    </Suspense>
  );
}
```

- [ ] **Step 2: Build check**

Run: `cd frontend && npm run build 2>&1 | tail -20`
Expected: Build succeeds (or only unrelated warnings)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/integrations/page.tsx
git commit -m "feat: rewrite integrations page with unified cards and SVG logos"
```

---

## Task 13: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update provider and server counts**

In `CLAUDE.md`, make these changes:

Update the tool layer description — change external tool seed count from ~140 to ~120 (24 Linear tools removed).

In `SUPPORTED_PROVIDERS` references or OAuth provider lists, ensure only 5 providers are listed: google, github, slack, notion, jira.

In seed installation references, ensure only 7 servers: google-workspace, github, slack, notion, atlassian, playwright, filesystem.

Update any reference to "9 OAuth providers" to "5 OAuth providers" and "9 MCP installations" to "7 MCP installations".

Remove any mentions of discord, linkedin, twitter, linear (as OAuth provider), or twilio from architecture descriptions.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for removed integrations"
```

---

## Task 14: Run Full Test Suite and Lint

**Files:** None (verification only)

- [ ] **Step 1: Run backend tests**

Run: `cd backend && pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: All tests PASS

- [ ] **Step 2: Run linter**

Run: `cd backend && ruff check src/ tests/ 2>&1 | tail -20`
Expected: No errors (warnings OK)

- [ ] **Step 3: Run frontend build**

Run: `cd frontend && npm run build 2>&1 | tail -20`
Expected: Build succeeds

- [ ] **Step 4: Run frontend lint**

Run: `cd frontend && npm run lint 2>&1 | tail -20`
Expected: No errors

- [ ] **Step 5: Fix any failures and commit**

If any test failures or lint errors, fix them and commit:
```bash
git add -A
git commit -m "fix: resolve test and lint issues from MCP cleanup"
```

---

## Task 15: Verify Playwright and Filesystem MCP

**Files:** None (verification only)

- [ ] **Step 1: Check Playwright MCP package resolves**

Run: `npx -y @playwright/mcp --help 2>&1 | head -5`
Expected: Help output or version info (not "package not found")

- [ ] **Step 2: Check Filesystem MCP package resolves**

Run: `npx -y @modelcontextprotocol/server-filesystem --help 2>&1 | head -5`
Expected: Help output or version info (not "package not found")

- [ ] **Step 3: Start backend and check unified endpoint**

Run: `cd backend && source .venv/bin/activate && timeout 15 python -c "
import asyncio
from src.config.settings import Settings
print('Settings OK')
from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS
names = [i['server_name'] for i in _DEFAULT_INSTALLATIONS]
print(f'Installations: {names}')
assert 'playwright' in names
assert 'filesystem' in names
assert 'linear' not in names
assert 'twilio' not in names
print('All assertions passed')
"`
Expected: `All assertions passed`

- [ ] **Step 4: If packages fail to resolve, fix installation**

If either npx command fails, check:
- Is Node.js installed and in PATH?
- Is npx available?
- Network connectivity for npm registry?

Document findings and fix. If packages require local npm install, add them to `frontend/package.json` or create a dedicated `mcp-tools/package.json`.

- [ ] **Step 5: Final commit if fixes were needed**

```bash
git add -A
git commit -m "fix: ensure playwright and filesystem MCP packages resolve"
```
