# On-Demand, Docker-Free MCP Servers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make external MCP servers run on demand (spawn on first use within an agent turn, reuse within the turn, tear down at turn end) with no Docker dependency, resolving auth tokens at request time.

**Architecture:** GitHub becomes a remote HTTP MCP (`api.githubcopilot.com/mcp/`, Bearer). Google Workspace stops being a Docker container and runs as an on-demand `uvx workspace-mcp` host process managed by a reference-counted `LocalMCPProcessManager`. A `TurnScope` (ContextVar + session refcounting) tears down sessions/processes at turn end, wired at the two chokepoints `_process_core` (chat) and `GraphExecutor.execute_run` (autonomous). Tool exposure is decoupled from live discovery by persisting `input_schema` in the DB with lazy "discover-once".

**Tech Stack:** Python 3.12, FastMCP `Client`, asyncio, `contextvars`, SQLAlchemy (async), pytest + pytest-asyncio, `uvx`/`npx` host processes.

Spec: [docs/superpowers/specs/2026-06-18-on-demand-dockerless-mcp-design.md](../specs/2026-06-18-on-demand-dockerless-mcp-design.md)

**Conventions for every task:** run from `backend/`, venv active. Lint after edits: `ruff check src/ tests/ --fix && ruff format src/ tests/`. Tests use `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed). MCP bridge auto-skips when `PYTEST_CURRENT_TEST` is set.

---

## Slice 1 — GitHub remote + schema decoupling *(review checkpoint 1)*

### Task 1: Switch GitHub to the remote hosted MCP

**Files:**
- Modify: `src/integrations/seed_installations.py` (the `github` entry in `_DEFAULT_INSTALLATIONS`, ~lines 81-109)
- Test: `tests/integrations/test_seed_installations.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_seed_installations.py
from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS


def _by_name(name: str) -> dict:
    return next(i for i in _DEFAULT_INSTALLATIONS if i["server_name"] == name)


def test_github_is_remote_http_not_docker():
    gh = _by_name("github")
    assert gh["transport"] == "streamable-http"
    assert gh["remote_url"] == "https://api.githubcopilot.com/mcp/"
    assert gh["command"] is None
    assert gh["args"] is None
    # Auth still flows through the github OAuth/PAT provider.
    assert gh["auth_provider"] == "github"
    # No more docker-run leakage.
    assert "docker" not in str(gh.get("args"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_seed_installations.py::test_github_is_remote_http_not_docker -v`
Expected: FAIL (`transport` is `"stdio"`).

- [ ] **Step 3: Edit the github installation**

Replace the entire `github` dict in `_DEFAULT_INSTALLATIONS` with:

```python
    {
        "server_name": "github",
        "display_name": "GitHub",
        "transport": "streamable-http",
        "remote_url": "https://api.githubcopilot.com/mcp/",
        "command": None,
        "args": None,
        "env_template": {},
        "auth_provider": "github",
        "scopes_granted": [
            "issue.create",
            "issue.list",
            "issue.search",
            "issue.comment",
            "repo.create_pr",
            "repo.merge_pr",
            "repo.search_code",
            "repo.search_repos",
            "repo.list_prs",
        ],
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integrations/test_seed_installations.py::test_github_is_remote_http_not_docker -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/seed_installations.py tests/integrations/test_seed_installations.py
git commit -m "feat: switch GitHub MCP to remote hosted HTTP transport"
```

---

### Task 2: Stop clearing HTTP-server tool schemas on restart

**Why:** `seed_installations` clears DB `input_schema` for HTTP servers on every restart so live discovery repopulates. Once discovery is no longer eager, that would hide tools. Make the DB schema durable.

**Files:**
- Modify: `src/integrations/seed_installations.py` (`_clear_stale_tool_schemas` call site, ~lines 260-264; the function ~lines 32-50)
- Test: `tests/integrations/test_seed_installations.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/integrations/test_seed_installations.py
import inspect

import src.integrations.seed_installations as seed_mod


def test_http_schemas_not_cleared_on_seed():
    # The transport-change-only clear is allowed; the unconditional
    # "always clear HTTP schemas" branch must be gone.
    src = inspect.getsource(seed_mod.seed_installations)
    assert "_clear_stale_tool_schemas(db, server_name, workspace_id)" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_seed_installations.py::test_http_schemas_not_cleared_on_seed -v`
Expected: FAIL (the unconditional clear call is present).

- [ ] **Step 3: Remove the unconditional clear branch**

In `seed_installations`, delete these lines (currently ~262-264):

```python
        # HTTP servers get schemas from live discovery — always clear stale
        # DB schemas so they don't override live ones.  This handles both
        # transport changes and schema drift (e.g., OAuth 2.1 mode changes).
        if inst_data.get("transport", "stdio") in ("sse", "streamable-http"):
            await _clear_stale_tool_schemas(db, server_name, workspace_id)
```

Then, so schemas are still refreshed when transport actually *changes*, move a clear into the existing transport-change branch. Replace the transport-change block (currently ~256-258):

```python
        if inst.transport != inst_data.get("transport", "stdio"):
            inst.transport = inst_data.get("transport", "stdio")
            needs_update = True
```

with:

```python
        if inst.transport != inst_data.get("transport", "stdio"):
            # Transport changed — tool schemas may differ between modes, so
            # clear them once. Steady-state restarts no longer clear schemas,
            # making the DB the durable source of truth for tool exposure.
            await _clear_stale_tool_schemas(db, server_name, workspace_id)
            inst.transport = inst_data.get("transport", "stdio")
            needs_update = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integrations/test_seed_installations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/seed_installations.py tests/integrations/test_seed_installations.py
git commit -m "feat: make DB tool schemas durable across restarts (clear only on transport change)"
```

---

### Task 3: Lazy "discover-once-and-persist" for servers missing schemas

**Why:** With no eager discovery, the first agent build for a server whose tools lack a DB schema must discover + persist before exposing tools. This self-heals exposure without startup cost.

**Files:**
- Create: `src/integrations/lazy_discovery.py`
- Modify: `src/orchestrator/jarvis.py` (`_get_tools_for_agent`, ~lines 613-691)
- Test: `tests/integrations/test_lazy_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_lazy_discovery.py
from unittest.mock import AsyncMock, patch

from src.integrations.lazy_discovery import discover_missing_schemas


async def test_discovers_servers_with_no_persisted_schema():
    # Two DB tool defs for "github", neither has a schema yet.
    class _Tool:
        def __init__(self, name, server, schema):
            self.name = name
            self.server = server
            self.input_schema = schema

    tools = [_Tool("create_pr", "github", None), _Tool("list_prs", "github", None)]

    pool = AsyncMock()
    with patch(
        "src.integrations.lazy_discovery.get_workspace_pool", return_value=pool
    ):
        servers = await discover_missing_schemas(tools, workspace_id="ws_1")

    assert servers == {"github"}
    pool.discover_and_persist.assert_awaited_once_with("github", workspace_id="ws_1")


async def test_skips_servers_with_persisted_schema():
    class _Tool:
        def __init__(self, name, server, schema):
            self.name = name
            self.server = server
            self.input_schema = schema

    tools = [_Tool("create_pr", "github", {"type": "object"})]
    pool = AsyncMock()
    with patch(
        "src.integrations.lazy_discovery.get_workspace_pool", return_value=pool
    ):
        servers = await discover_missing_schemas(tools, workspace_id="ws_1")

    assert servers == set()
    pool.discover_and_persist.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_lazy_discovery.py -v`
Expected: FAIL (`ModuleNotFoundError: src.integrations.lazy_discovery`).

- [ ] **Step 3: Create the module**

```python
# src/integrations/lazy_discovery.py
"""Lazy, one-shot MCP tool-schema discovery.

When tool exposure is decoupled from eager startup discovery, the first agent
build for a server whose DB tool records lack ``input_schema`` triggers a
single discovery pass for that server. The discovered schemas are persisted to
the DB by the pool, so subsequent builds read them straight from the registry.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from src.integrations.mcp_pool import get_workspace_pool

logger = logging.getLogger(__name__)


async def discover_missing_schemas(
    tool_defs: Iterable[Any],
    *,
    workspace_id: str,
) -> set[str]:
    """Discover + persist schemas for external servers whose tools lack one.

    Returns the set of server names a discovery pass was run for. Failures are
    swallowed (logged) so a flaky server never blocks agent construction.
    """
    pool = get_workspace_pool()
    if pool is None:
        return set()

    servers_missing: set[str] = set()
    for t in tool_defs:
        server = getattr(t, "server", None)
        if server and not getattr(t, "input_schema", None):
            servers_missing.add(server)

    discovered: set[str] = set()
    for server in servers_missing:
        try:
            await pool.discover_and_persist(server, workspace_id=workspace_id)
            discovered.add(server)
        except Exception:
            logger.debug("Lazy discovery failed for %s", server, exc_info=True)
    return discovered
```

- [ ] **Step 4: Add `discover_and_persist` to `WorkspaceMCPPool`**

In `src/integrations/mcp_pool.py`, add this method to `WorkspaceMCPPool` (after `reload_server`):

```python
    async def discover_and_persist(
        self,
        server_name: str,
        *,
        workspace_id: str,
    ) -> int:
        """Spawn one short-lived discovery session, persist schemas, tear down.

        Used by lazy discovery. Ensures the server config is registered, finds
        a user for auth keying, opens a session (which calls list_tools and
        persists discovered schemas via _register_discovered_tools), then closes
        it immediately so nothing is left running. Returns tool count.
        """
        if not self._session_pool.has_server_config(server_name, workspace_id):
            if not await self.reload_server(workspace_id, server_name):
                return 0

        user_id = await self._resolve_workspace_user(workspace_id)
        if not user_id:
            return 0

        try:
            session = await self._session_pool.get_or_create_session(
                server_name, user_id=user_id, workspace_id=workspace_id
            )
            count = len(session.tools)
        except Exception:
            logger.debug("discover_and_persist failed for %s", server_name, exc_info=True)
            return 0
        finally:
            try:
                await self._session_pool.refresh_session(
                    server_name, user_id, workspace_id=workspace_id
                )
            except Exception:
                logger.debug("teardown after discovery failed for %s", server_name)
        return count
```

- [ ] **Step 5: Run the new-module tests**

Run: `pytest tests/integrations/test_lazy_discovery.py -v`
Expected: PASS.

- [ ] **Step 6: Wire lazy discovery into `_get_tools_for_agent`**

In `src/orchestrator/jarvis.py`, inside `_get_tools_for_agent`, immediately after `all_db_tools = await registry.list_tools(enabled_only=True)` (~line 650), insert:

```python
            # Lazy "discover-once": if any in-scope external tool lacks a
            # persisted schema and has no live session schema yet, run a single
            # discovery pass for its server, then re-read the registry so the
            # freshly persisted schemas are visible this same build.
            in_scope_missing = [
                td
                for td in all_db_tools
                if td.name not in internal_names
                and td.capability
                and td.capability in scope
                and not td.input_schema
                and td.name not in mcp_schemas
            ]
            if in_scope_missing:
                from src.integrations.lazy_discovery import discover_missing_schemas

                discovered = await discover_missing_schemas(
                    in_scope_missing, workspace_id=workspace_id
                )
                if discovered:
                    all_db_tools = await registry.list_tools(enabled_only=True)
                    for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
                        mcp_schemas[mcp_tool["name"]] = {
                            "description": mcp_tool.get("description", ""),
                            "input_schema": mcp_tool.get("input_schema", {}),
                        }
```

- [ ] **Step 7: Run targeted orchestrator tests + commit**

Run: `pytest tests/integrations/test_lazy_discovery.py tests/ -k "tools_for_agent or get_tools" -v`
Expected: PASS (or no-collected if none match — then run `pytest tests/test_jarvis*.py -v` as a smoke check).

```bash
git add src/integrations/lazy_discovery.py src/integrations/mcp_pool.py src/orchestrator/jarvis.py tests/integrations/test_lazy_discovery.py
git commit -m "feat: lazy discover-once-and-persist for MCP tool schemas"
```

> **REVIEW CHECKPOINT 1** — dispatch `code-reviewer` on Slice 1 (Tasks 1-3). Address CRITICAL/HIGH before continuing.

---

## Slice 2 — LocalMCPProcessManager *(review checkpoint 2)*

### Task 4: Create `LocalMCPProcessManager`

**Files:**
- Create: `src/integrations/local_process_manager.py`
- Test: `tests/integrations/test_local_process_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_local_process_manager.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.local_process_manager import (
    LocalMCPProcessManager,
    LocalServerSpec,
)


def _spec() -> LocalServerSpec:
    return LocalServerSpec(
        server_name="google-workspace",
        argv=["uvx", "workspace-mcp", "--transport", "streamable-http"],
        env={"MCP_ENABLE_OAUTH21": "true"},
        path="/mcp",
    )


async def test_ensure_running_starts_once_and_refcounts():
    mgr = LocalMCPProcessManager({"google-workspace": _spec()})
    fake_proc = MagicMock()
    fake_proc.returncode = None

    with patch.object(mgr, "_spawn", AsyncMock(return_value=(fake_proc, 51234))) as spawn, \
         patch.object(mgr, "_wait_ready", AsyncMock(return_value=None)):
        url1 = await mgr.ensure_running("google-workspace")
        url2 = await mgr.ensure_running("google-workspace")

    assert url1 == "http://127.0.0.1:51234/mcp"
    assert url2 == url1
    spawn.assert_awaited_once()  # started once, reused on second call
    assert mgr.refcount("google-workspace") == 2


async def test_release_stops_when_refcount_hits_zero():
    mgr = LocalMCPProcessManager({"google-workspace": _spec()})
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.terminate = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)

    with patch.object(mgr, "_spawn", AsyncMock(return_value=(fake_proc, 51234))), \
         patch.object(mgr, "_wait_ready", AsyncMock(return_value=None)):
        await mgr.ensure_running("google-workspace")
        await mgr.ensure_running("google-workspace")

    await mgr.release("google-workspace")
    assert mgr.refcount("google-workspace") == 1
    fake_proc.terminate.assert_not_called()

    await mgr.release("google-workspace")
    assert mgr.refcount("google-workspace") == 0
    fake_proc.terminate.assert_called_once()


async def test_unknown_server_raises():
    mgr = LocalMCPProcessManager({})
    with pytest.raises(KeyError):
        await mgr.ensure_running("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_local_process_manager.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Create the module**

```python
# src/integrations/local_process_manager.py
"""Manage locally-spawned HTTP MCP server processes, on demand.

Some MCP servers have no hosted remote endpoint and cannot run over stdio in a
multi-user, server-side context (e.g. workspace-mcp, whose stdio mode needs
interactive browser OAuth + on-disk creds). We run them as local host
subprocesses in their stateless OAuth21 HTTP mode, spawned on first use and
torn down when the last in-flight session releases them — reference-counted so
overlapping turns don't restart the process.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 30.0
READY_POLL_INTERVAL = 0.5
TERMINATE_GRACE_SECONDS = 5.0


@dataclass
class LocalServerSpec:
    """How to launch a local HTTP MCP server."""

    server_name: str
    argv: list[str]  # base command; "--port"/env port is appended/injected
    env: dict[str, str]
    path: str = "/mcp"  # URL path the MCP endpoint is served at
    port_env_var: str = "WORKSPACE_MCP_PORT"


@dataclass
class _Running:
    proc: Any
    port: int
    refcount: int = 0


@dataclass
class LocalMCPProcessManager:
    """Reference-counted lifecycle manager for local HTTP MCP processes."""

    specs: dict[str, LocalServerSpec]
    _running: dict[str, _Running] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def refcount(self, server_name: str) -> int:
        r = self._running.get(server_name)
        return r.refcount if r else 0

    async def ensure_running(self, server_name: str) -> str:
        """Start the server if needed, bump refcount, return its base MCP URL."""
        spec = self.specs[server_name]  # KeyError if unknown — caller's bug
        async with self._lock:
            running = self._running.get(server_name)
            if running is None or running.proc.returncode is not None:
                proc, port = await self._spawn(spec)
                running = _Running(proc=proc, port=port)
                self._running[server_name] = running
                try:
                    await self._wait_ready(port, spec.path)
                except Exception:
                    await self._stop(server_name)
                    raise
            running.refcount += 1
            return f"http://127.0.0.1:{running.port}{spec.path}"

    async def release(self, server_name: str) -> None:
        """Drop one reference; stop the process when the last one is released."""
        async with self._lock:
            running = self._running.get(server_name)
            if not running:
                return
            running.refcount = max(0, running.refcount - 1)
            if running.refcount == 0:
                await self._stop(server_name)

    async def shutdown(self) -> None:
        """Force-stop all managed processes (called on app shutdown)."""
        async with self._lock:
            for server_name in list(self._running):
                await self._stop(server_name)

    # --- internals (patched in tests) ---

    async def _spawn(self, spec: LocalServerSpec) -> tuple[Any, int]:
        port = _free_port()
        env = {**spec.env, spec.port_env_var: str(port)}
        import os

        full_env = {**os.environ, **env}
        proc = await asyncio.create_subprocess_exec(
            *spec.argv,
            env=full_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info(
            "[mcp:local] spawned %s pid=%s port=%d", spec.server_name, proc.pid, port
        )
        return proc, port

    async def _wait_ready(self, port: int, path: str) -> None:
        deadline = asyncio.get_event_loop().time() + READY_TIMEOUT_SECONDS
        url = f"http://127.0.0.1:{port}{path}"
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    # Any HTTP response (even 401/406) means the port is serving.
                    await client.get(url)
                    return
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    await asyncio.sleep(READY_POLL_INTERVAL)
        raise TimeoutError(f"MCP server on port {port} not ready in {READY_TIMEOUT_SECONDS}s")

    async def _stop(self, server_name: str) -> None:
        running = self._running.pop(server_name, None)
        if not running:
            return
        proc = running.proc
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
        logger.info("[mcp:local] stopped %s", server_name)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integrations/test_local_process_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/local_process_manager.py tests/integrations/test_local_process_manager.py
git commit -m "feat: add reference-counted LocalMCPProcessManager for on-demand HTTP MCP servers"
```

---

### Task 5: Build the Google Workspace launch spec from settings

**Files:**
- Create: `src/integrations/local_servers.py`
- Modify: `src/config/settings.py` (~line 96, replace the static URL with timeout knobs)
- Test: `tests/integrations/test_local_servers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_local_servers.py
from src.integrations.local_servers import build_local_server_specs


class _Settings:
    google_oauth_client_id = "cid"
    google_oauth_client_secret = "secret"


def test_google_workspace_spec_uses_uvx_and_oauth21_env():
    specs = build_local_server_specs(_Settings())
    gw = specs["google-workspace"]
    assert gw.argv[:2] == ["uvx", "workspace-mcp"]
    assert "--transport" in gw.argv and "streamable-http" in gw.argv
    assert gw.env["MCP_ENABLE_OAUTH21"] == "true"
    assert gw.env["EXTERNAL_OAUTH21_PROVIDER"] == "true"
    assert gw.env["WORKSPACE_MCP_STATELESS_MODE"] == "true"
    assert gw.env["GOOGLE_OAUTH_CLIENT_ID"] == "cid"
    assert gw.env["GOOGLE_OAUTH_CLIENT_SECRET"] == "secret"
    assert gw.path == "/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_local_servers.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Create the spec builder**

```python
# src/integrations/local_servers.py
"""Launch specs for locally-managed HTTP MCP servers, derived from settings.

Replicates the old infra/docker/google-workspace-mcp/entrypoint.sh env mapping
(JARVIS_-prefixed settings -> the names workspace-mcp expects) but spawns the
server as a host process via uvx instead of a Docker container.
"""

from __future__ import annotations

from typing import Any

from src.integrations.local_process_manager import LocalServerSpec

# Pin the workspace-mcp version for reproducibility (see deploy pre-warm).
# Resolve the concrete version in Task 12; "workspace-mcp" alone tracks latest.
WORKSPACE_MCP_PACKAGE = "workspace-mcp"


def build_local_server_specs(settings: Any) -> dict[str, LocalServerSpec]:
    """Return {server_name: LocalServerSpec} for all locally-managed servers."""
    google = LocalServerSpec(
        server_name="google-workspace",
        argv=[
            "uvx",
            WORKSPACE_MCP_PACKAGE,
            "--transport",
            "streamable-http",
            "--tool-tier",
            "complete",
            "--tools",
            "gmail",
            "calendar",
        ],
        env={
            "MCP_ENABLE_OAUTH21": "true",
            "EXTERNAL_OAUTH21_PROVIDER": "true",
            "WORKSPACE_MCP_STATELESS_MODE": "true",
            "GOOGLE_OAUTH_CLIENT_ID": settings.google_oauth_client_id or "",
            "GOOGLE_OAUTH_CLIENT_SECRET": settings.google_oauth_client_secret or "",
        },
        path="/mcp",
        port_env_var="WORKSPACE_MCP_PORT",
    )
    return {"google-workspace": google}
```

- [ ] **Step 4: Update settings**

In `src/config/settings.py`, replace line 96:

```python
    google_workspace_mcp_url: str = "http://localhost:8001/mcp"
```

with:

```python
    # Google Workspace MCP now runs as an on-demand local uvx process
    # (LocalMCPProcessManager), so there is no static URL. These knobs tune
    # the local-process lifecycle and the idle-session reaper.
    mcp_local_ready_timeout_s: float = 30.0
    mcp_session_idle_ttl_s: float = 120.0
```

> Confirm `google_oauth_client_id` / `google_oauth_client_secret` fields exist in settings (they back the old entrypoint mapping). If named differently, adjust `build_local_server_specs` accordingly. Verify with: `grep -n "google_oauth_client" src/config/settings.py`.

- [ ] **Step 5: Run test + commit**

Run: `pytest tests/integrations/test_local_servers.py -v`
Expected: PASS.

```bash
git add src/integrations/local_servers.py src/config/settings.py tests/integrations/test_local_servers.py
git commit -m "feat: derive Google Workspace MCP launch spec from settings"
```

---

### Task 6: Wire the manager into session creation + teardown

**Files:**
- Modify: `src/integrations/session_pool.py` (`SessionEntry`, `get_or_create_session` HTTP branch, all three teardown sites)
- Modify: `src/integrations/seed_installations.py` (`google-workspace` entry: drop static `remote_url`, add `managed_local`)
- Modify: `src/integrations/mcp_pool.py` (`_installation_to_config`: carry `managed_local`)
- Modify: `src/connectors/mcp_bridge.py` (`get_mcp_config`: carry `managed_local`)
- Test: `tests/integrations/test_session_pool_managed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_session_pool_managed.py
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.session_pool import UserMCPSessionPool


async def test_managed_local_server_resolves_url_and_releases_on_teardown():
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "google-workspace",
        {"transport": "streamable-http", "auth_provider": "none", "managed_local": True},
        workspace_id="ws_1",
    )

    mgr = AsyncMock()
    mgr.ensure_running = AsyncMock(return_value="http://127.0.0.1:5/mcp")
    mgr.release = AsyncMock()

    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("src.integrations.session_pool.get_local_process_manager", return_value=mgr), \
         patch("src.integrations.session_pool.Client", MagicMock(return_value=fake_ctx)), \
         patch.object(pool, "_register_discovered_tools", AsyncMock()):
        entry = await pool.get_or_create_session(
            "google-workspace", user_id="u1", workspace_id="ws_1"
        )
        assert entry.managed_server == "google-workspace"
        mgr.ensure_running.assert_awaited_once_with("google-workspace")

        await pool.refresh_session("google-workspace", "__shared__", workspace_id="ws_1")
        mgr.release.assert_awaited_once_with("google-workspace")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_session_pool_managed.py -v`
Expected: FAIL (`get_local_process_manager` does not exist; `managed_server` not on `SessionEntry`).

- [ ] **Step 3: Add a manager accessor**

In `src/integrations/local_process_manager.py`, append a module-level singleton:

```python
_manager: LocalMCPProcessManager | None = None


def get_local_process_manager() -> LocalMCPProcessManager | None:
    return _manager


def set_local_process_manager(manager: LocalMCPProcessManager | None) -> None:
    global _manager
    _manager = manager
```

- [ ] **Step 4: Add `managed_server` to `SessionEntry` and resolve the URL**

In `src/integrations/session_pool.py`:

(a) Add the import near the top:

```python
from src.integrations.local_process_manager import get_local_process_manager
```

(b) Add a field to `SessionEntry` (after `bound_token`):

```python
    # Name of the locally-managed MCP process this session uses (if any), so
    # every teardown path releases the process refcount exactly once.
    managed_server: str | None = None
```

(c) In `get_or_create_session`, replace the HTTP branch (currently):

```python
            transport = config.get("transport", "stdio")
            if transport in ("sse", "streamable-http"):
                url = config["url"]
                client_ctx = Client(url, auth=auth) if auth else Client(url)
```

with:

```python
            transport = config.get("transport", "stdio")
            managed_server: str | None = None
            if transport in ("sse", "streamable-http"):
                if config.get("managed_local"):
                    mgr = get_local_process_manager()
                    if mgr is None:
                        raise RuntimeError(
                            f"'{server_name}' is managed_local but no "
                            "LocalMCPProcessManager is configured"
                        )
                    url = await mgr.ensure_running(server_name)
                    managed_server = server_name
                else:
                    url = config["url"]
                client_ctx = Client(url, auth=auth) if auth else Client(url)
```

(d) When constructing `entry = SessionEntry(...)`, add `managed_server=managed_server,`. (The stdio branch leaves `managed_server` as its default `None`; initialize `managed_server = None` before the `if transport` so it's always bound.)

- [ ] **Step 5: Release the process on every teardown path**

Add a tiny helper to `UserMCPSessionPool`:

```python
    async def _release_managed(self, entry: SessionEntry) -> None:
        if not entry.managed_server:
            return
        mgr = get_local_process_manager()
        if mgr is not None:
            try:
                await mgr.release(entry.managed_server)
            except Exception:
                logger.debug("release of %s failed", entry.managed_server, exc_info=True)
```

Then call `await self._release_managed(entry)` right after each `await entry.client_ctx.__aexit__(...)` in `refresh_session`, `cleanup_idle`, and `shutdown`.

- [ ] **Step 6: Carry `managed_local` through config conversion**

In `src/integrations/seed_installations.py`, change the `google-workspace` entry: remove the `remote_url` line and add `"managed_local": True`:

```python
    {
        "server_name": "google-workspace",
        "display_name": "Google Workspace",
        "transport": "streamable-http",
        "remote_url": None,
        "managed_local": True,
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

> Note: `IntegrationInstallation` has no `managed_local` column. Persist it inside the JSONB `config`. In `seed_installations`, when creating/updating the google-workspace installation, set `installation.config = {**(inst.config or {}), "managed_local": True}`. Add a one-line sync in the update branch too. (Add a focused test asserting the seeded google-workspace installation's `config["managed_local"] is True`.)

In `src/integrations/mcp_pool.py` `_installation_to_config`, after building the HTTP branch, carry the flag:

```python
    inst_cfg = getattr(inst, "config", None) or {}
    if isinstance(inst_cfg, dict) and inst_cfg.get("managed_local"):
        config["managed_local"] = True
```

In `src/connectors/mcp_bridge.py` `get_mcp_config`, in the HTTP branch add:

```python
                elif inst.transport in ("sse", "streamable-http"):
                    if inst.remote_url:
                        server_cfg["url"] = inst.remote_url
                    inst_cfg = getattr(inst, "config", None) or {}
                    if isinstance(inst_cfg, dict) and inst_cfg.get("managed_local"):
                        server_cfg["managed_local"] = True
```

- [ ] **Step 7: Run tests + commit**

Run: `pytest tests/integrations/test_session_pool_managed.py tests/integrations/test_seed_installations.py -v`
Expected: PASS.

```bash
git add src/integrations/session_pool.py src/integrations/local_process_manager.py src/integrations/seed_installations.py src/integrations/mcp_pool.py src/connectors/mcp_bridge.py tests/integrations/test_session_pool_managed.py
git commit -m "feat: resolve managed-local MCP URLs via LocalMCPProcessManager with refcounted teardown"
```

---

### Task 7: Initialize + shut down the manager at app startup

**Files:**
- Modify: `src/connectors/mcp_bridge.py` (`initialize_mcp_bridge`, `shutdown_mcp_bridge`)
- Test: `tests/connectors/test_mcp_bridge_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/connectors/test_mcp_bridge_manager.py
from src.integrations.local_process_manager import get_local_process_manager
from src.integrations.local_servers import build_local_server_specs


class _Settings:
    google_oauth_client_id = "cid"
    google_oauth_client_secret = "secret"


def test_specs_registered_for_google_workspace():
    specs = build_local_server_specs(_Settings())
    assert "google-workspace" in specs
```

- [ ] **Step 2: Run test to verify it fails / passes**

Run: `pytest tests/connectors/test_mcp_bridge_manager.py -v`
Expected: PASS for the spec assertion (this guards the wiring contract). If it errors on import, fix imports.

- [ ] **Step 3: Construct + register the manager in `initialize_mcp_bridge`**

In `src/connectors/mcp_bridge.py`, change `initialize_mcp_bridge` to also accept settings and set the manager. After `_session_pool = UserMCPSessionPool(...)`, add:

```python
    # Wire the local-process manager for managed_local servers (Google Workspace).
    try:
        from src.config.settings import get_settings
        from src.integrations.local_process_manager import (
            LocalMCPProcessManager,
            set_local_process_manager,
        )
        from src.integrations.local_servers import build_local_server_specs

        specs = build_local_server_specs(get_settings())
        set_local_process_manager(LocalMCPProcessManager(specs=specs))
    except Exception:
        logger.exception("Failed to wire LocalMCPProcessManager")
```

> Confirm the settings accessor name with `grep -n "def get_settings\|settings =" src/config/settings.py`; use the project's existing accessor.

- [ ] **Step 4: Tear it down in `shutdown_mcp_bridge`**

At the end of `shutdown_mcp_bridge`, add:

```python
    from src.integrations.local_process_manager import (
        get_local_process_manager,
        set_local_process_manager,
    )

    mgr = get_local_process_manager()
    if mgr is not None:
        await mgr.shutdown()
        set_local_process_manager(None)
```

- [ ] **Step 5: Run test + commit**

Run: `pytest tests/connectors/test_mcp_bridge_manager.py -v`
Expected: PASS.

```bash
git add src/connectors/mcp_bridge.py tests/connectors/test_mcp_bridge_manager.py
git commit -m "feat: construct and shut down LocalMCPProcessManager with the MCP bridge"
```

---

### Task 8: Remove the Google Workspace Docker service + image

**Files:**
- Modify: `docker-compose.yml` (remove the `google-workspace-mcp` service, ~lines 53-64)
- Delete: `infra/docker/google-workspace-mcp/` (Dockerfile + entrypoint.sh)
- Test: `tests/infra/test_no_docker_mcp.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/infra/test_no_docker_mcp.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_compose_has_no_google_workspace_mcp_service():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "google-workspace-mcp" not in compose


def test_google_workspace_docker_dir_removed():
    assert not (ROOT / "infra/docker/google-workspace-mcp").exists()
```

> Verify `parents[3]` resolves to the repo root from `backend/tests/infra/`; adjust the index if the test lives elsewhere. Print `ROOT` once while writing the test to confirm.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/infra/test_no_docker_mcp.py -v`
Expected: FAIL (service + dir still present).

- [ ] **Step 3: Remove the service + directory**

Delete the `google-workspace-mcp:` service block from `docker-compose.yml`. Then:

```bash
git rm -r infra/docker/google-workspace-mcp
```

- [ ] **Step 4: Run test + commit**

Run: `pytest tests/infra/test_no_docker_mcp.py -v`
Expected: PASS.

```bash
git add docker-compose.yml tests/infra/test_no_docker_mcp.py
git commit -m "chore: remove Dockerized Google Workspace MCP service and image"
```

> **REVIEW CHECKPOINT 2** — dispatch `code-reviewer` on Slice 2 (Tasks 4-8). Focus: process/resource leaks, refcount races, readiness-timeout cleanup.

---

## Slice 3 — TurnScope lifecycle *(review checkpoint 3)*

### Task 9: Create `TurnScope` + ContextVar

**Files:**
- Create: `src/integrations/turn_scope.py`
- Test: `tests/integrations/test_turn_scope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_turn_scope.py
from src.integrations.turn_scope import (
    TurnScope,
    current_turn_scope,
    turn_scope,
)


def test_register_and_acquire_refcounts():
    scope = TurnScope()
    scope.register(("ws", "github", "u1"))
    scope.acquire(("ws", "github", "u1"))  # reused within the same turn
    assert scope.refcount(("ws", "github", "u1")) == 2


async def test_turn_scope_sets_and_clears_contextvar():
    assert current_turn_scope() is None
    closed = []
    async with turn_scope(on_close=lambda keys: closed.append(keys)) as scope:
        assert current_turn_scope() is scope
        scope.register(("ws", "slack", "u1"))
    assert current_turn_scope() is None
    assert closed == [[("ws", "slack", "u1")]]


async def test_close_only_returns_keys_at_zero_refcount():
    captured = []
    async with turn_scope(on_close=lambda keys: captured.append(list(keys))):
        scope = current_turn_scope()
        scope.register(("ws", "a", "u"))
        scope.register(("ws", "b", "u"))
        scope.acquire(("ws", "a", "u"))
        scope.release_one(("ws", "a", "u"))  # a back to refcount 1
    # both still owed teardown (each opened once net) at turn end
    assert sorted(captured[0]) == [("ws", "a", "u"), ("ws", "b", "u")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_turn_scope.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Create the module**

```python
# src/integrations/turn_scope.py
"""Per-turn MCP session lifecycle scope.

A TurnScope tracks which MCP session keys were opened (or reused) during a
single agent turn so they can be torn down when the turn ends. Sessions are
reference-counted: opening or reusing a key increments its count, and at turn
end every key the scope still holds is handed to ``on_close`` for teardown.
Reference counting lets overlapping turns share a session without one turn
killing another's live connection.

The active scope is stored in a ContextVar, which asyncio copies into child
tasks at creation — so a session opened inside a spawned sub-task is still
attributed to the turn that spawned it.
"""

from __future__ import annotations

import contextvars
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

logger = logging.getLogger(__name__)

SessionKey = tuple[str, str, str]  # (workspace_id, server_name, user_id)

_current: contextvars.ContextVar["TurnScope | None"] = contextvars.ContextVar(
    "mcp_turn_scope", default=None
)


class TurnScope:
    """Tracks reference-counted MCP session keys opened during one turn."""

    def __init__(self) -> None:
        self._refs: dict[SessionKey, int] = defaultdict(int)

    def register(self, key: SessionKey) -> None:
        """Record a newly opened session key."""
        self._refs[key] += 1

    def acquire(self, key: SessionKey) -> None:
        """Record reuse of an already-open session key within this turn."""
        self._refs[key] += 1

    def release_one(self, key: SessionKey) -> None:
        """Manually drop one reference (rarely needed; mid-turn refresh)."""
        if self._refs.get(key, 0) > 0:
            self._refs[key] -= 1

    def refcount(self, key: SessionKey) -> int:
        return self._refs.get(key, 0)

    def keys(self) -> list[SessionKey]:
        return [k for k, n in self._refs.items() if n > 0]


def current_turn_scope() -> TurnScope | None:
    """Return the TurnScope active for the current task, if any."""
    return _current.get()


@asynccontextmanager
async def turn_scope(
    *,
    on_close: Callable[[list[SessionKey]], object] | None = None,
) -> AsyncIterator[TurnScope]:
    """Activate a TurnScope for the duration of an agent turn.

    On exit, hands every still-held session key to ``on_close`` for teardown.
    ``on_close`` may be sync or async; both are awaited if awaitable.
    """
    scope = TurnScope()
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)
        keys = scope.keys()
        if on_close is not None:
            try:
                result = on_close(keys)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                logger.debug("TurnScope on_close failed", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integrations/test_turn_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/turn_scope.py tests/integrations/test_turn_scope.py
git commit -m "feat: add reference-counted per-turn MCP session scope"
```

---

### Task 10: Register/acquire sessions into the active TurnScope

**Files:**
- Modify: `src/integrations/session_pool.py` (`get_or_create_session`: register on create, acquire on reuse)
- Test: `tests/integrations/test_session_pool_turnscope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_session_pool_turnscope.py
from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.session_pool import UserMCPSessionPool
from src.integrations.turn_scope import turn_scope


async def test_session_create_and_reuse_tracked_in_turn_scope():
    pool = UserMCPSessionPool()
    pool.register_server_config(
        "filesystem",
        {"transport": "stdio", "auth_provider": "none", "command": "x"},
        workspace_id="ws",
    )

    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    captured = []
    with patch("src.integrations.session_pool.Client", MagicMock(return_value=fake_ctx)), \
         patch.object(pool, "_register_discovered_tools", AsyncMock()):
        async with turn_scope(on_close=lambda keys: captured.append(keys)):
            await pool.get_or_create_session("filesystem", user_id="u", workspace_id="ws")
            await pool.get_or_create_session("filesystem", user_id="u", workspace_id="ws")
            scope_keys = captured  # not yet closed
    key = ("ws", "filesystem", "__shared__")
    assert captured == [[key]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_session_pool_turnscope.py -v`
Expected: FAIL (no registration into the scope yet).

- [ ] **Step 3: Hook the scope into `get_or_create_session`**

In `src/integrations/session_pool.py`, add the import:

```python
from src.integrations.turn_scope import current_turn_scope
```

In the cache-hit path (where a cached `entry` is returned), record reuse:

```python
        async with self._lock:
            entry = self._sessions.get(key)
            if entry:
                entry.last_used = time.monotonic()
                scope = current_turn_scope()
                if scope is not None:
                    scope.acquire(key)
                return entry
```

After the new entry is stored (`self._sessions[key] = entry`), record creation:

```python
            self._sessions[key] = entry
            scope = current_turn_scope()
            if scope is not None:
                scope.register(key)
```

- [ ] **Step 4: Run test + commit**

Run: `pytest tests/integrations/test_session_pool_turnscope.py -v`
Expected: PASS.

```bash
git add src/integrations/session_pool.py tests/integrations/test_session_pool_turnscope.py
git commit -m "feat: track MCP session create/reuse in the active TurnScope"
```

---

### Task 11: Add a pool method to close a turn's sessions

**Files:**
- Modify: `src/integrations/session_pool.py` (new `close_keys`)
- Modify: `src/connectors/mcp_bridge.py` (new `close_turn_sessions` helper)
- Test: `tests/integrations/test_session_pool_close_keys.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_session_pool_close_keys.py
from unittest.mock import AsyncMock

from src.integrations.session_pool import SessionEntry, UserMCPSessionPool


async def test_close_keys_exits_context_and_releases_managed():
    pool = UserMCPSessionPool()
    ctx = AsyncMock()
    ctx.__aexit__ = AsyncMock(return_value=None)
    key = ("ws", "google-workspace", "u")
    pool._sessions[key] = SessionEntry(
        client=AsyncMock(),
        client_ctx=ctx,
        server_name="google-workspace",
        user_id="u",
        tools={},
        managed_server="google-workspace",
    )
    released = []
    pool._release_managed = AsyncMock(side_effect=lambda e: released.append(e.managed_server))

    await pool.close_keys([key])
    ctx.__aexit__.assert_awaited_once()
    assert released == ["google-workspace"]
    assert key not in pool._sessions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_session_pool_close_keys.py -v`
Expected: FAIL (`close_keys` does not exist).

- [ ] **Step 3: Add `close_keys`**

In `src/integrations/session_pool.py`:

```python
    async def close_keys(self, keys: list[tuple[str, str, str]]) -> int:
        """Close specific sessions by key (used for per-turn teardown).

        Idempotent: keys with no live session are skipped. Releases any
        managed-local process refcount the session held.
        """
        closed = 0
        async with self._lock:
            for key in keys:
                entry = self._sessions.pop(key, None)
                if not entry:
                    continue
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    logger.debug("close_keys: error closing %s", key, exc_info=True)
                await self._release_managed(entry)
                closed += 1
        if closed:
            logger.info("[mcp:session] closed %d session(s) at turn end", closed)
        return closed
```

- [ ] **Step 4: Add the bridge helper**

In `src/connectors/mcp_bridge.py`:

```python
async def close_turn_sessions(keys: list[tuple[str, str, str]]) -> None:
    """Tear down the MCP sessions opened during a turn (called by TurnScope)."""
    if _session_pool and keys:
        await _session_pool.close_keys(keys)
```

- [ ] **Step 5: Run test + commit**

Run: `pytest tests/integrations/test_session_pool_close_keys.py -v`
Expected: PASS.

```bash
git add src/integrations/session_pool.py src/connectors/mcp_bridge.py tests/integrations/test_session_pool_close_keys.py
git commit -m "feat: add per-key MCP session teardown for turn-scoped cleanup"
```

---

### Task 12: Wire TurnScope into the chat path (`_process_core`)

**Files:**
- Modify: `src/orchestrator/jarvis.py` (`_process_core`, wrap the body, ~line 857+)
- Test: `tests/test_process_core_turnscope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_process_core_turnscope.py
import inspect

from src.orchestrator import jarvis as jarvis_mod


def test_process_core_uses_turn_scope():
    src = inspect.getsource(jarvis_mod.JarvisOrchestrator._process_core)
    assert "turn_scope(" in src
    assert "close_turn_sessions" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_process_core_turnscope.py -v`
Expected: FAIL.

- [ ] **Step 3: Wrap `_process_core` in a TurnScope**

In `src/orchestrator/jarvis.py`, add imports at top of file:

```python
from src.connectors.mcp_bridge import close_turn_sessions
from src.integrations.turn_scope import turn_scope
```

Wrap the generator body. `_process_core` begins `trace = self._trace_manager.start_trace(...)` then `try:`. Change the structure so the existing `try/except/finally` runs *inside* an active turn scope:

```python
        trace = self._trace_manager.start_trace("user_message")

        async with turn_scope(on_close=close_turn_sessions):
            def _fire_event(event_type: str, **kwargs: Any) -> None:
                self._spawn_background(self._emit_runtime_event(event_type, **kwargs))

            try:
                yield TraceStarted(trace_id=trace.trace_id)
                # ... existing body unchanged ...
```

> Indent the existing `try:` block one level to sit inside `async with turn_scope(...)`. The `async with` wrapping an async generator's body is valid; the scope closes when the generator is exhausted or aborted (the skill-noted "finally drain" already protects this — keep that `finally`).

- [ ] **Step 4: Run test + a chat smoke test**

Run: `pytest tests/test_process_core_turnscope.py -v`
Then a broad smoke check: `pytest tests/ -k "process_message or process_core" -v`
Expected: PASS / green.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/jarvis.py tests/test_process_core_turnscope.py
git commit -m "feat: scope chat-path MCP sessions to the agent turn"
```

---

### Task 13: Wire TurnScope into the autonomous path (`execute_run`)

**Files:**
- Modify: `src/services/graph_executor.py` (`execute_run`, ~line 391)
- Test: `tests/services/test_graph_executor_turnscope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_graph_executor_turnscope.py
import inspect

from src.services import graph_executor as ge


def test_execute_run_uses_turn_scope():
    src = inspect.getsource(ge.GraphExecutor.execute_run)
    assert "turn_scope(" in src
    assert "close_turn_sessions" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_graph_executor_turnscope.py -v`
Expected: FAIL.

- [ ] **Step 3: Wrap `execute_run` in a TurnScope**

In `src/services/graph_executor.py`, add imports:

```python
from src.connectors.mcp_bridge import close_turn_sessions
from src.integrations.turn_scope import turn_scope
```

Wrap the method body of `execute_run` so the whole run executes inside one scope:

```python
    async def execute_run(self, ...):  # keep existing signature
        async with turn_scope(on_close=close_turn_sessions):
            # ... existing body unchanged, indented one level ...
```

> If `execute_run` already opens a `JarvisTrace` (it does, ~line 407), keep that *inside* the scope. Every step the run executes shares one turn scope, so a stdio server spawned for step 1 is reused by step 3 and killed once when the run ends.

- [ ] **Step 4: Run test + executor smoke test**

Run: `pytest tests/services/test_graph_executor_turnscope.py -v`
Then: `pytest tests/ -k "graph_executor or execute_run" -v`
Expected: PASS / green.

- [ ] **Step 5: Commit**

```bash
git add src/services/graph_executor.py tests/services/test_graph_executor_turnscope.py
git commit -m "feat: scope autonomous-run MCP sessions to the run"
```

---

### Task 14: Idle reaper safety net for orphaned sessions

**Why:** Crashes, cancellations, or background tasks that escape a scope could strand a session/process. A short idle reaper (using the existing `cleanup_idle`) catches them.

**Files:**
- Modify: `src/integrations/session_pool.py` (use `mcp_session_idle_ttl_s`; default TTL constant lowered or driven by settings)
- Modify: `src/services/scheduler/run_health_tick.py` (call `cleanup_idle` each tick) — confirm the tick module; otherwise add to `lifecycle_tick.py`
- Test: `tests/integrations/test_idle_reaper.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_idle_reaper.py
import time
from unittest.mock import AsyncMock

from src.integrations.session_pool import SessionEntry, UserMCPSessionPool


async def test_cleanup_idle_releases_managed_process():
    pool = UserMCPSessionPool(ttl_seconds=0.0)
    ctx = AsyncMock()
    ctx.__aexit__ = AsyncMock(return_value=None)
    key = ("ws", "google-workspace", "u")
    entry = SessionEntry(
        client=AsyncMock(),
        client_ctx=ctx,
        server_name="google-workspace",
        user_id="u",
        tools={},
        managed_server="google-workspace",
    )
    entry.last_used = time.monotonic() - 10
    pool._sessions[key] = entry
    released = []
    pool._release_managed = AsyncMock(side_effect=lambda e: released.append(e.managed_server))

    removed = await pool.cleanup_idle()
    assert removed == 1
    assert released == ["google-workspace"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_idle_reaper.py -v`
Expected: FAIL (`cleanup_idle` does not yet call `_release_managed`).

- [ ] **Step 3: Release managed processes in `cleanup_idle`**

In `cleanup_idle`, after `entry = self._sessions.pop(key)` and the `__aexit__`, add `await self._release_managed(entry)`.

- [ ] **Step 4: Call the reaper from a scheduler tick**

Identify the lightweight periodic tick (confirm with `grep -rn "cleanup_idle\|async def tick\|async def run" src/services/scheduler/`). In the chosen tick (e.g. `run_health_tick.py`), add:

```python
from src.connectors.mcp_bridge import get_session_pool

pool = get_session_pool()
if pool is not None:
    await pool.cleanup_idle()
```

- [ ] **Step 5: Run test + commit**

Run: `pytest tests/integrations/test_idle_reaper.py -v`
Expected: PASS.

```bash
git add src/integrations/session_pool.py src/services/scheduler/ tests/integrations/test_idle_reaper.py
git commit -m "feat: idle reaper releases managed MCP processes for orphaned sessions"
```

> **REVIEW CHECKPOINT 3** — dispatch `code-reviewer` on Slice 3 (Tasks 9-14). Focus: ContextVar propagation to spawned tasks, refcount races under overlapping turns, teardown-on-exception/cancellation.

---

## Slice 4 — Drop eager discovery + infra *(review checkpoint 4)*

### Task 15: Make startup register configs only (no eager discovery)

**Files:**
- Modify: `src/integrations/mcp_pool.py` (`initialize_from_db`: drop the HTTP + stdio discovery passes)
- Modify: `src/connectors/mcp_bridge.py` (`initialize_mcp_bridge`: no background `_discover` task)
- Test: `tests/integrations/test_no_eager_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_no_eager_discovery.py
import inspect

import src.connectors.mcp_bridge as bridge
import src.integrations.mcp_pool as pool_mod


def test_initialize_from_db_does_not_discover():
    src = inspect.getsource(pool_mod.WorkspaceMCPPool.initialize_from_db)
    assert "discover_tools" not in src
    assert "_discover_stdio_schemas" not in src


def test_bridge_init_has_no_discovery_task():
    src = inspect.getsource(bridge.initialize_mcp_bridge)
    assert "_discover(" not in src
    assert "create_task" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_no_eager_discovery.py -v`
Expected: FAIL.

- [ ] **Step 3: Strip discovery from `initialize_from_db`**

Replace the body of `WorkspaceMCPPool.initialize_from_db` (keep registration, drop both discovery passes):

```python
    async def initialize_from_db(self) -> int:
        """Register all active installations from DB. No network/process I/O.

        Tool schemas are no longer discovered eagerly — they come from the DB
        registry (durable) and are lazily (re)discovered on first agent build
        via discover_and_persist. Returns count of servers registered.
        """
        from src.models.database import get_session_factory

        try:
            from sqlalchemy import select

            from src.models.integration_installation import IntegrationInstallation

            async with get_session_factory()() as db:
                result = await db.execute(
                    select(IntegrationInstallation).where(
                        IntegrationInstallation.status == "active",
                        IntegrationInstallation.enabled.is_(True),
                        IntegrationInstallation.transport.in_(["stdio", "sse", "streamable-http"]),
                    )
                )
                installations = result.scalars().all()

                count = 0
                for inst in installations:
                    config = _installation_to_config(inst)
                    await self.add_server(inst.workspace_id, inst.server_name, config)
                    count += 1

                logger.info("Registered %d MCP server configs from DB (no discovery)", count)
                return count
        except Exception as e:
            logger.debug("Failed to register MCP servers from DB: %s", e)
            return 0
```

Then delete the now-unused `_discover_stdio_schemas` method and `_resolve_workspace_user`'s discovery-only callers — **but keep `_resolve_workspace_user`** (it is still used by `discover_and_persist`). Also `session_pool.discover_tools` becomes unused by startup but is still used by `discover_and_persist` for HTTP servers — keep it.

- [ ] **Step 4: Remove the background discovery task from the bridge**

Replace `initialize_mcp_bridge` so it registers configs synchronously (bounded) and never spawns a discovery task. Replace the `_discover`/`defer_discovery` machinery with a single bounded registration call:

```python
async def initialize_mcp_bridge(
    oauth_manager: Any | None = None,
    *,
    timeout_seconds: float = 30,
) -> None:
    """Wire the session pool + local-process manager and register server configs.

    No eager tool discovery and no background tasks: sessions and schemas are
    created lazily on first use. Registration is a few cheap DB reads, bounded
    by ``timeout_seconds``. Skipped in test environments.
    """
    global _session_pool

    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("JARVIS_SKIP_MCP_BRIDGE"):
        logger.debug("MCP bridge skipped (test environment)")
        return None

    _session_pool = UserMCPSessionPool(
        oauth_manager=oauth_manager,
        circuit_breaker=_circuit_breaker,
    )

    # Wire the local-process manager (managed_local servers).
    try:
        from src.config.settings import get_settings
        from src.integrations.local_process_manager import (
            LocalMCPProcessManager,
            set_local_process_manager,
        )
        from src.integrations.local_servers import build_local_server_specs

        specs = build_local_server_specs(get_settings())
        set_local_process_manager(LocalMCPProcessManager(specs=specs))
    except Exception:
        logger.exception("Failed to wire LocalMCPProcessManager")

    from src.integrations.mcp_pool import WorkspaceMCPPool, set_workspace_pool

    workspace_pool = WorkspaceMCPPool(session_pool=_session_pool)
    set_workspace_pool(workspace_pool)

    try:
        count = await asyncio.wait_for(
            workspace_pool.initialize_from_db(), timeout=timeout_seconds
        )
        logger.info("MCP bridge ready: %d server configs registered", count)
    except asyncio.TimeoutError:
        logger.warning("MCP config registration exceeded %.0fs — lazy on first use", timeout_seconds)
    except Exception:
        logger.exception("MCP config registration failed")
    return None
```

Then delete the `_discovery_task` global and its drain logic in `shutdown_mcp_bridge` (the `create_task` no longer exists). Update the `app.py` lifespan call site: it passes `defer_discovery=True` / captures the returned task — change it to `await initialize_mcp_bridge(oauth_manager)` and drop the returned-task handling. Find it with `grep -n "initialize_mcp_bridge" src/api/app.py`.

- [ ] **Step 5: Run tests + targeted suite**

Run: `pytest tests/integrations/test_no_eager_discovery.py tests/connectors/ tests/integrations/ -v`
Expected: PASS. Also `grep -rn "defer_discovery\|_discovery_task" src/` should return nothing.

- [ ] **Step 6: Commit**

```bash
git add src/integrations/mcp_pool.py src/connectors/mcp_bridge.py src/api/app.py tests/integrations/test_no_eager_discovery.py
git commit -m "feat: register MCP configs at startup without eager discovery"
```

---

### Task 16: Startup preflight for `uvx`/`npx`

**Files:**
- Create: `src/integrations/runtime_preflight.py`
- Modify: `src/connectors/mcp_bridge.py` (call preflight in `initialize_mcp_bridge`, warn-only)
- Test: `tests/integrations/test_runtime_preflight.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integrations/test_runtime_preflight.py
from unittest.mock import patch

from src.integrations.runtime_preflight import check_mcp_runtimes


def test_reports_missing_runtimes():
    with patch("src.integrations.runtime_preflight.shutil.which", return_value=None):
        missing = check_mcp_runtimes(["uvx", "npx"])
    assert missing == ["uvx", "npx"]


def test_reports_present_runtimes():
    with patch("src.integrations.runtime_preflight.shutil.which", return_value="/usr/bin/x"):
        missing = check_mcp_runtimes(["uvx", "npx"])
    assert missing == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integrations/test_runtime_preflight.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Create the preflight module**

```python
# src/integrations/runtime_preflight.py
"""Check that host runtimes needed to spawn MCP servers are present.

We no longer run MCP servers in Docker — stdio servers run via ``npx`` and the
Google Workspace server runs via ``uvx``. Missing runtimes are not fatal at
startup (an MCP call will surface a structured error), but we log a loud
warning so operators notice before a user hits it.
"""

from __future__ import annotations

import logging
import shutil

logger = logging.getLogger(__name__)


def check_mcp_runtimes(required: list[str]) -> list[str]:
    """Return the subset of required runtimes not found on PATH."""
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        logger.warning(
            "[mcp:preflight] missing host runtime(s): %s — MCP servers needing "
            "them will fail until installed (npx=Node, uvx=uv)",
            ", ".join(missing),
        )
    return missing
```

- [ ] **Step 4: Call it from the bridge**

In `initialize_mcp_bridge`, after wiring the session pool, add:

```python
    from src.integrations.runtime_preflight import check_mcp_runtimes

    check_mcp_runtimes(["uvx", "npx"])
```

- [ ] **Step 5: Run test + commit**

Run: `pytest tests/integrations/test_runtime_preflight.py -v`
Expected: PASS.

```bash
git add src/integrations/runtime_preflight.py src/connectors/mcp_bridge.py tests/integrations/test_runtime_preflight.py
git commit -m "feat: warn at startup when uvx/npx host runtimes are missing"
```

---

### Task 17: Deploy pre-warm + drop Google Workspace Docker build; pin versions

**Files:**
- Modify: `infra/scripts/deploy.sh` (pre-warm `uvx`/`npx` package caches)
- Modify: `infra/user-data.sh` (no Google Workspace docker build; ensure `uvx`/`npx` on the `ubuntu` PATH)
- Modify: `src/integrations/local_servers.py` and `src/integrations/seed_installations.py` (pin package versions)
- Test: manual (infra scripts); plus a lint pass.

- [ ] **Step 1: Look up and pin concrete versions**

Run these and record the versions:

```bash
uv pip index versions workspace-mcp 2>/dev/null | head -3
npm view slack-mcp-server version
npm view @playwright/mcp version
npm view @modelcontextprotocol/server-filesystem version
npm view @notionhq/notion-mcp-server version
```

- [ ] **Step 2: Pin in `local_servers.py`**

Set `WORKSPACE_MCP_PACKAGE = "workspace-mcp==<version-from-step-1>"`.

- [ ] **Step 3: Pin npx servers in `seed_installations.py`**

Change each `npx` server's args to a pinned spec, e.g. `["-y", "slack-mcp-server@<version>"]`, and the same for playwright, filesystem, notion. Update the existing `test_seed_installations.py` to assert the pinned `@` specs are present.

- [ ] **Step 4: Pre-warm caches in `deploy.sh`**

Add, after the `uv pip install` block, a best-effort pre-warm (non-fatal):

```bash
# Pre-warm MCP package caches so first real tool call isn't a cold download.
sudo -u ubuntu bash -c '
  export PATH="/home/ubuntu/.local/bin:$PATH"
  uvx workspace-mcp --help >/dev/null 2>&1 || true
  npx -y slack-mcp-server --help >/dev/null 2>&1 || true
' || true
```

- [ ] **Step 5: Remove Docker build from provisioning**

In `infra/user-data.sh`, remove any reference to building/running the `google-workspace-mcp` Docker service (it no longer exists in compose). Ensure `uv`/`uvx` is on the `ubuntu` user's PATH (the deploy script already exports `/home/ubuntu/.local/bin`). Node 22 already provides `npx`.

- [ ] **Step 6: Lint + commit**

Run: `ruff check src/ tests/ && pytest tests/integrations/test_seed_installations.py -v`
Expected: PASS.

```bash
git add infra/scripts/deploy.sh infra/user-data.sh src/integrations/local_servers.py src/integrations/seed_installations.py tests/integrations/test_seed_installations.py
git commit -m "chore: pin MCP package versions and pre-warm host caches; drop Google MCP docker build"
```

> **REVIEW CHECKPOINT 4** — dispatch `code-reviewer` on Slice 4 (Tasks 15-17). Focus: nothing left calling removed discovery code; fail-closed on missing runtimes; app.py call-site correctness.

---

## Slice 5 — Docs + full verification *(review checkpoint 5)*

### Task 18: Update architecture docs

**Files:**
- Modify: `CLAUDE.md` (MCP sections), `backend/CLAUDE.md` if present, `docs/` architecture notes mentioning the Docker Google Workspace MCP and eager discovery.

- [ ] **Step 1: Find stale references**

Run:

```bash
grep -rn "google-workspace-mcp\|docker run\|8001/mcp\|eager discover\|initialize_from_db" CLAUDE.md docs/ backend/ --include=*.md
```

- [ ] **Step 2: Update the Unified Tool Registry / Dispatch sections**

In `CLAUDE.md`, update the MCP description to state: external MCP servers run on demand with no Docker — GitHub and Atlassian are remote HTTP, Google Workspace runs as an on-demand local `uvx workspace-mcp` process via `LocalMCPProcessManager`, stdio servers run via `npx`; sessions are scoped to a turn via `TurnScope` and torn down at turn end; tool schemas are durable in the DB with lazy discover-once (no eager startup discovery).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/
git commit -m "docs: describe on-demand Docker-free MCP architecture"
```

---

### Task 19: Full suite + manual verification

- [ ] **Step 1: Run the full backend suite**

Run: `pytest tests/ -q`
Expected: green (baseline was 2373; new tests added).

- [ ] **Step 2: Lint/format gate**

Run: `ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: clean.

- [ ] **Step 3: Grep for dead references**

Run:

```bash
grep -rn "defer_discovery\|_discovery_task\|_discover_stdio_schemas\|google_workspace_mcp_url\|8001" src/ && echo "FOUND STALE — clean up" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 4: Manual smoke (local, optional but recommended)**

With `docker compose up -d` (datastores only) and real Google/GitHub tokens linked:
- A chat turn that calls a GitHub tool → succeeds via the remote endpoint; no `docker run` spawned (`docker ps` shows only datastores).
- A chat turn that calls a Gmail/Calendar tool → `ps aux | grep workspace-mcp` shows a process during the turn, gone shortly after (released at turn end / reaper).
- A turn calling a `filesystem`/`slack` tool → `ps aux | grep npx` present during the turn, gone after.

- [ ] **Step 5: Final commit (if any doc/cleanup tweaks)**

```bash
git add -A && git commit -m "chore: finalize on-demand Docker-free MCP migration"
```

> **REVIEW CHECKPOINT 5** — final `code-reviewer` + `security-reviewer` pass across the whole change (token handling, no secrets in logs, process leak audit). Then update memory per project convention.

---

## Self-Review (author)

**Spec coverage:** GitHub-remote (T1), durable DB schemas (T2), lazy discover-once (T3), LocalMCPProcessManager (T4-T7), Docker removal (T8, T17), TurnScope + per-turn teardown across both chokepoints (T9-T13), idle reaper safety net (T14), drop eager discovery (T15), host-runtime preflight + pinned versions + pre-warm (T16-T17), docs (T18), verification + code-review checkpoints (T19, all slices). The spec's "Code review" section maps to the five REVIEW CHECKPOINTs.

**Placeholders:** versions in T17 are resolved by an explicit lookup step with exact commands (not left as TBD). `grep`/path-confirmation notes flag the two spots that need a one-line verification before editing (settings accessor name, scheduler tick file, repo-root index in the infra test).

**Type consistency:** `LocalServerSpec`/`LocalMCPProcessManager` (`ensure_running`/`release`/`refcount`/`shutdown`), `get/set_local_process_manager`, `SessionEntry.managed_server`, `TurnScope` (`register`/`acquire`/`release_one`/`refcount`/`keys`), `current_turn_scope`/`turn_scope`, `close_keys`/`close_turn_sessions`, `discover_and_persist`/`discover_missing_schemas` are used consistently across tasks.
