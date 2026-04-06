# MCP Authentication & Transport Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix broken external MCP tool authentication by migrating Google Workspace to HTTP transport with External OAuth 2.1, wiring OAuthManager into the MCP bridge, scoping workspace_id injection, correcting auth_provider labels, and fixing the Slack env var.

**Architecture:** 6 files changed, 1 Dockerfile + 1 docker-compose service added. No new abstractions — fixes are surgical edits to existing code paths. Google Workspace moves from stdio subprocess to shared Docker HTTP service with Bearer token auth.

**Tech Stack:** Python 3.12, FastAPI, Docker Compose, workspace-mcp (External OAuth 2.1), fastmcp Client

**Spec:** `docs/superpowers/specs/2026-04-06-mcp-auth-transport-fix-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `infra/docker/google-workspace-mcp/Dockerfile` | CREATE | Docker image for Google Workspace MCP HTTP service |
| `docker-compose.yml` | MODIFY | Add google-workspace-mcp service |
| `backend/src/config/settings.py` | MODIFY | Add `google_workspace_mcp_url` setting |
| `backend/src/integrations/seed_installations.py` | MODIFY | Google: stdio→streamable-http. GitHub/Linear/Notion: fix auth_provider |
| `backend/src/api/app.py` | MODIFY | Wire OAuthManager into MCP bridge init |
| `backend/src/orchestrator/jarvis.py` | MODIFY | Scope workspace_id injection to internal + composite only |
| `backend/src/integrations/session_pool.py` | MODIFY | Fix Slack env var in `_STDIO_TOKEN_ENV_VARS` |
| `backend/tests/test_seed_installations.py` | MODIFY | Update Google seed tests, add auth_provider tests |
| `backend/tests/test_mcp_auth.py` | CREATE | Tests for OAuthManager wiring, workspace_id scoping, _resolve_auth |
| `backend/tests/test_execute_tool.py` | CREATE | Tests for workspace_id injection scoping in _execute_tool |

---

### Task 1: Fix Slack env var in session_pool.py

**Files:**
- Modify: `backend/src/integrations/session_pool.py:33-38`

- [ ] **Step 1: Fix the env var name**

In `backend/src/integrations/session_pool.py`, replace the `_STDIO_TOKEN_ENV_VARS` dict:

```python
_STDIO_TOKEN_ENV_VARS: dict[str, str] = {
    "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
    "slack": "SLACK_MCP_XOXB_TOKEN",
    "linear": "LINEAR_ACCESS_TOKEN",
    "notion": "NOTION_TOKEN",
}
```

Change only line 35: `"slack": "SLACK_BOT_TOKEN",` → `"slack": "SLACK_MCP_XOXB_TOKEN",`

- [ ] **Step 2: Run existing tests**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_seed_installations.py -v`

Expected: All existing tests pass (the Slack seed test already validates the env_template keys, not `_STDIO_TOKEN_ENV_VARS`).

- [ ] **Step 3: Commit**

```bash
git add backend/src/integrations/session_pool.py
git commit -m "fix: correct Slack env var in _STDIO_TOKEN_ENV_VARS to SLACK_MCP_XOXB_TOKEN"
```

---

### Task 2: Fix auth_provider labels for GitHub, Linear, Notion

**Files:**
- Modify: `backend/src/integrations/seed_installations.py:62,128,150`
- Test: `backend/tests/test_seed_installations.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/test_seed_installations.py`:

```python
class TestAuthProviderLabels:
    """Servers with OAuth callback routes must have OAuth-aware auth_provider."""

    def _get_seed(self, server_name: str) -> dict:
        return next(s for s in _DEFAULT_INSTALLATIONS if s["server_name"] == server_name)

    def test_github_auth_provider_is_oauth(self):
        seed = self._get_seed("github")
        assert seed["auth_provider"] == "github", (
            f"GitHub uses OAuth flow — auth_provider must be 'github', got '{seed['auth_provider']}'"
        )

    def test_linear_auth_provider_is_oauth(self):
        seed = self._get_seed("linear")
        assert seed["auth_provider"] == "linear", (
            f"Linear uses OAuth flow — auth_provider must be 'linear', got '{seed['auth_provider']}'"
        )

    def test_notion_auth_provider_is_oauth(self):
        seed = self._get_seed("notion")
        assert seed["auth_provider"] == "notion", (
            f"Notion uses OAuth flow — auth_provider must be 'notion', got '{seed['auth_provider']}'"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_seed_installations.py::TestAuthProviderLabels -v`

Expected: FAIL — all three assert `"token"` != `"github"` (etc.)

- [ ] **Step 3: Fix the auth_provider labels**

In `backend/src/integrations/seed_installations.py`:

Line 62 — GitHub: change `"auth_provider": "token"` to `"auth_provider": "github"`

Line 128 — Linear: change `"auth_provider": "token"` to `"auth_provider": "linear"`

Line 150 — Notion: change `"auth_provider": "token"` to `"auth_provider": "notion"`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_seed_installations.py -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/integrations/seed_installations.py backend/tests/test_seed_installations.py
git commit -m "fix: correct auth_provider labels for GitHub, Linear, Notion to use OAuth path"
```

---

### Task 3: Wire OAuthManager into MCP bridge

**Files:**
- Modify: `backend/src/api/app.py:142-150`
- Test: `backend/tests/test_mcp_auth.py` (create)

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_mcp_auth.py`:

```python
"""Tests for MCP authentication wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


class TestOAuthManagerWiring:
    """OAuthManager must be passed to initialize_mcp_bridge."""

    @pytest.mark.asyncio
    async def test_oauth_manager_passed_to_bridge(self):
        """Verify OAuthManager is created and passed to initialize_mcp_bridge."""
        mock_settings = make_mock_settings()
        mock_settings.oauth_encryption_key = "test-key"
        mock_settings.redis_url = "redis://localhost:6379/0"

        mock_init_bridge = AsyncMock()
        mock_oauth_cls = MagicMock()
        mock_oauth_instance = MagicMock()
        mock_oauth_cls.return_value = mock_oauth_instance

        with (
            patch("src.api.app.get_settings", return_value=mock_settings),
            patch("src.connectors.mcp_bridge.initialize_mcp_bridge", mock_init_bridge),
            patch("src.services.oauth_manager.OAuthManager", mock_oauth_cls),
        ):
            from src.api.app import create_app

            app = create_app()

            # Trigger lifespan startup
            async with app.router.lifespan_context(app):
                pass

            # Verify initialize_mcp_bridge was called with an oauth_manager
            mock_init_bridge.assert_called_once()
            call_kwargs = mock_init_bridge.call_args
            oauth_arg = call_kwargs.kwargs.get("oauth_manager") or call_kwargs.args[0] if call_kwargs.args else None
            assert oauth_arg is not None, (
                "initialize_mcp_bridge must receive a non-None oauth_manager"
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_mcp_auth.py::TestOAuthManagerWiring -v`

Expected: FAIL — `oauth_manager` is None because `app.state.oauth_manager` is never set.

- [ ] **Step 3: Fix app.py — create OAuthManager before bridge init**

In `backend/src/api/app.py`, replace lines 142-150:

```python
        # Initialize MCP bridge with session pool (replaces old gateway layer).
        # Auth is resolved per-user from OAuthManager at call time.
        try:
            from src.connectors.mcp_bridge import initialize_mcp_bridge

            oauth_manager = getattr(app.state, "oauth_manager", None)
            await initialize_mcp_bridge(oauth_manager=oauth_manager)
        except Exception:
            logger.debug("MCP bridge init skipped", exc_info=True)
```

Replace with:

```python
        # Create OAuthManager for MCP bridge token resolution.
        # Lightweight instance (db_factory + encryption key, no state).
        oauth_manager = None
        try:
            from src.models.database import get_session_factory as _get_sf
            from src.services.oauth_manager import OAuthManager

            oauth_manager = OAuthManager(
                db_factory=_get_sf(),
                settings=settings,
                encryption_key=settings.oauth_encryption_key,
            )
        except Exception:
            logger.debug("OAuthManager unavailable for MCP bridge", exc_info=True)

        # Initialize MCP bridge with session pool.
        # Auth is resolved per-user from OAuthManager at call time.
        try:
            from src.connectors.mcp_bridge import initialize_mcp_bridge

            await initialize_mcp_bridge(oauth_manager=oauth_manager)
        except Exception:
            logger.debug("MCP bridge init skipped", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_mcp_auth.py::TestOAuthManagerWiring -v`

Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -5`

Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/app.py backend/tests/test_mcp_auth.py
git commit -m "fix: wire OAuthManager into MCP bridge initialization"
```

---

### Task 4: Scope workspace_id injection to internal + composite only

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:2636-2671`
- Test: `backend/tests/test_execute_tool.py` (create)

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_execute_tool.py`:

```python
"""Tests for _execute_tool workspace_id injection scoping."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_tool_record(backend: str, server: str = "default"):
    """Create a mock tool registry record."""
    tool = MagicMock()
    tool.backend = backend
    tool.server = server
    tool.enabled = True
    return tool


class TestWorkspaceIdInjection:
    """workspace_id must NOT be injected into external_mcp tool inputs."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_external_mcp_no_workspace_id_in_input(self, mock_client):
        """External MCP tool calls must not have workspace_id in tool_input."""
        mock_settings = make_mock_settings()
        mock_call_mcp = AsyncMock(return_value={"status": "ok", "result": "done"})

        orchestrator = MagicMock()
        orchestrator._db_factory = MagicMock()
        orchestrator._settings = mock_settings
        orchestrator._publish_event = AsyncMock()

        tool = _make_tool_record("external_mcp")

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg
            orchestrator._db_factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            orchestrator._db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("src.connectors.mcp_bridge.call_mcp_tool", mock_call_mcp):
                from src.orchestrator.jarvis import JarvisOrchestrator

                real_method = JarvisOrchestrator._execute_tool
                result = await real_method(
                    orchestrator,
                    tool_name="search_gmail_messages",
                    tool_input={"query": "test"},
                    user_id=TEST_USER_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                )

                # Verify call_mcp_tool received tool_input WITHOUT workspace_id
                call_args = mock_call_mcp.call_args
                tool_input_sent = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("arguments", {})
                assert "workspace_id" not in tool_input_sent, (
                    f"External MCP tool received workspace_id in input: {tool_input_sent}"
                )

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.get_anthropic_client")
    async def test_internal_mcp_gets_workspace_id(self, mock_client):
        """Internal MCP tool calls must have workspace_id in tool_input."""
        mock_settings = make_mock_settings()

        orchestrator = MagicMock()
        orchestrator._db_factory = MagicMock()
        orchestrator._settings = mock_settings
        orchestrator._publish_event = AsyncMock()
        orchestrator._call_internal_tool = AsyncMock(return_value={"status": "ok"})

        tool = _make_tool_record("internal_mcp", server="intelligence")

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = AsyncMock()
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg
            orchestrator._db_factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            orchestrator._db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from src.orchestrator.jarvis import JarvisOrchestrator

            result = await JarvisOrchestrator._execute_tool(
                orchestrator,
                tool_name="search",
                tool_input={"query": "test"},
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            # Verify _call_internal_tool received tool_input WITH workspace_id
            call_args = orchestrator._call_internal_tool.call_args
            tool_input_sent = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("tool_input", {})
            assert "workspace_id" in tool_input_sent, (
                f"Internal MCP tool must receive workspace_id: {tool_input_sent}"
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_execute_tool.py -v`

Expected: `test_external_mcp_no_workspace_id_in_input` FAILS because workspace_id is injected for all backends.

- [ ] **Step 3: Fix jarvis.py — scope workspace_id injection**

In `backend/src/orchestrator/jarvis.py`, replace lines 2636-2671:

```python
        # Inject workspace_id so tools always have it
        if workspace_id and "workspace_id" not in tool_input:
            tool_input = {**tool_input, "workspace_id": workspace_id}

        logger.info(
            "[mcp] dispatch %s via %s/%s",
            tool_name,
            tool.backend,
            tool.server or "default",
        )
        await self._publish_event("tool.started", user_id, {"tool": tool_name})

        try:
            match tool.backend:
                case "internal_mcp":
                    result = await self._call_internal_tool(
                        tool_name,
                        {**tool_input, "user_id": user_id},
                        server_prefix=tool.server,
                    )
                case "external_mcp":
                    from src.connectors.mcp_bridge import call_mcp_tool

                    result = await call_mcp_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                case "composite":
                    result = await self._call_composite_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
```

Replace with:

```python
        logger.info(
            "[mcp] dispatch %s via %s/%s",
            tool_name,
            tool.backend,
            tool.server or "default",
        )
        await self._publish_event("tool.started", user_id, {"tool": tool_name})

        try:
            match tool.backend:
                case "internal_mcp":
                    # Internal tools receive workspace_id in input
                    if workspace_id and "workspace_id" not in tool_input:
                        tool_input = {**tool_input, "workspace_id": workspace_id}
                    result = await self._call_internal_tool(
                        tool_name,
                        {**tool_input, "user_id": user_id},
                        server_prefix=tool.server,
                    )
                case "external_mcp":
                    # External MCP servers do not accept workspace_id in tool input —
                    # it is passed as a keyword arg for session routing only.
                    from src.connectors.mcp_bridge import call_mcp_tool

                    result = await call_mcp_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                case "composite":
                    # Composite tools are Jarvis-internal, receive workspace_id
                    if workspace_id and "workspace_id" not in tool_input:
                        tool_input = {**tool_input, "workspace_id": workspace_id}
                    result = await self._call_composite_tool(
                        tool_name,
                        tool_input,
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_execute_tool.py -v`

Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/orchestrator/jarvis.py backend/tests/test_execute_tool.py
git commit -m "fix: scope workspace_id injection to internal_mcp and composite tools only"
```

---

### Task 5: Add google_workspace_mcp_url setting

**Files:**
- Modify: `backend/src/config/settings.py:100`

- [ ] **Step 1: Add the setting**

In `backend/src/config/settings.py`, after line 101 (`google_oauth_redirect_uri`), add:

```python
    google_workspace_mcp_url: str = "http://localhost:8001/mcp"
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/config/settings.py
git commit -m "feat: add google_workspace_mcp_url setting for HTTP MCP service"
```

---

### Task 6: Migrate Google Workspace seed to streamable-http

**Files:**
- Modify: `backend/src/integrations/seed_installations.py:19-45`
- Modify: `backend/tests/test_seed_installations.py`

- [ ] **Step 1: Write failing test**

Update `TestSeedInstallations` in `backend/tests/test_seed_installations.py`. Replace `test_google_workspace_executable`:

```python
    def test_google_workspace_http_transport(self):
        """Google Workspace seed must use streamable-http with remote_url."""
        seed = self._get_seed("google-workspace")
        assert seed["transport"] == "streamable-http", (
            f"Expected streamable-http transport, got '{seed['transport']}'"
        )
        assert seed.get("remote_url"), "Google Workspace must have a remote_url for HTTP transport"
        assert seed.get("command") is None, "HTTP transport should not have a command"
        assert seed.get("args") is None, "HTTP transport should not have args"
        assert seed["auth_provider"] == "google", (
            f"auth_provider must be 'google' for OAuth, got '{seed['auth_provider']}'"
        )
        assert seed["env_template"] == {}, "HTTP service env vars live on Docker, not in seed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_seed_installations.py::TestSeedInstallations::test_google_workspace_http_transport -v`

Expected: FAIL — seed still has `transport: "stdio"`.

- [ ] **Step 3: Update the Google Workspace seed**

In `backend/src/integrations/seed_installations.py`, add `import os` at the top (after existing imports), then replace lines 19-45 (the google-workspace dict):

```python
    {
        "server_name": "google-workspace",
        "display_name": "Google Workspace",
        "transport": "streamable-http",
        "remote_url": os.environ.get(
            "JARVIS_GOOGLE_WORKSPACE_MCP_URL", "http://localhost:8001/mcp"
        ),
        "command": None,
        "args": None,
        "env_template": {},
        "auth_provider": "google",
        "scopes_granted": [
            "email.send",
            "email.list",
            "email.read",
            "email.search",
            "email.draft",
            "calendar.list",
            "calendar.get",
            "calendar.create",
            "calendar.update",
            "doc.drive_list",
            "doc.drive_search",
            "doc.drive_create",
        ],
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/test_seed_installations.py -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/integrations/seed_installations.py backend/tests/test_seed_installations.py
git commit -m "feat: migrate Google Workspace MCP to streamable-http with External OAuth 2.1"
```

---

### Task 7: Create Dockerfile and Docker Compose service

**Files:**
- Create: `infra/docker/google-workspace-mcp/Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Create the Dockerfile**

Create directory and file `infra/docker/google-workspace-mcp/Dockerfile`:

```dockerfile
FROM python:3.13-slim

RUN pip install --no-cache-dir workspace-mcp

EXPOSE 8000

CMD ["workspace-mcp", "--transport", "streamable-http", "--tool-tier", "complete", "--tools", "gmail", "calendar"]
```

- [ ] **Step 2: Add service to docker-compose.yml**

In `docker-compose.yml`, add the service before the `volumes:` section:

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

- [ ] **Step 3: Verify Docker build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis && docker compose build google-workspace-mcp`

Expected: Build succeeds (downloads python:3.13-slim, installs workspace-mcp).

- [ ] **Step 4: Commit**

```bash
git add infra/docker/google-workspace-mcp/Dockerfile docker-compose.yml
git commit -m "feat: add Google Workspace MCP as Docker Compose HTTP service"
```

---

### Task 8: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run full backend tests**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -10`

Expected: All tests pass, zero failures.

- [ ] **Step 2: Run linter**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && ruff check src/ tests/`

Expected: No errors.

- [ ] **Step 3: Verify Docker service starts**

Run: `cd /Users/sivasankarreddybogala/work/jarvis && docker compose up google-workspace-mcp -d && sleep 5 && docker compose logs google-workspace-mcp 2>&1 | tail -20`

Expected: Logs show `MCP_ENABLE_OAUTH21: true`, `EXTERNAL_OAUTH21_PROVIDER: true`, server listening on port 8000.

- [ ] **Step 4: Stop Docker service**

Run: `cd /Users/sivasankarreddybogala/work/jarvis && docker compose stop google-workspace-mcp`
