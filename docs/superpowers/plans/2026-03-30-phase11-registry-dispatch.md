# Phase 11: Feature Flag + Registry-Driven Dispatch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 6-step dispatch cascade with a 3-backend match dispatch, gated behind `JARVIS_USE_UNIFIED_DISPATCH` feature flag.

**Architecture:** One registry lookup via `ToolRegistry.get_tool()`, one `match` on the `backend` column (`internal_mcp`, `external_mcp`, `composite`). Flag OFF = zero change to existing behavior. Flag ON = registry-driven dispatch with no normalizer, no native connectors, no capability resolver.

**Tech Stack:** Python 3.12, SQLAlchemy async, pydantic-settings, pytest + pytest-asyncio

**Design spec:** `docs/superpowers/specs/2026-03-30-phase11-feature-flag-registry-dispatch-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/src/config/settings.py` | Modify | Add `use_unified_dispatch` flag |
| `backend/src/services/tool_registry.py` | Modify | Fix seed_defaults: `backend="composite"` for `_composite` server |
| `backend/src/orchestrator/jarvis.py` | Modify | Add `_execute_tool_unified()`, `_call_composite_tool()`, `_get_tools_for_agent_unified()`. Modify `_call_internal_tool()`, `_execute_tool()`, and two call sites. |
| `backend/src/orchestrator/agents.py` | Modify | Add `can_use_tool_unified()` async method on SubAgent |
| `backend/src/services/governor.py` | Modify | Add `is_auto_execute_tool()` async method |
| `backend/tests/conftest.py` | Modify | Add `use_unified_dispatch` to `make_mock_settings` |
| `backend/tests/test_unified_dispatch.py` | Create | 14 tests for the unified dispatch path |

---

### Task 1: Seed Fix — `web_search` Backend

Phase 9 hardcodes `backend = "external_mcp"` for ALL external seeds. `web_search` has `server="_composite"` in the catalog and needs `backend="composite"` in the DB for the match dispatch to route correctly.

**Files:**
- Modify: `backend/src/services/tool_registry.py:346`
- Test: `backend/tests/test_tool_registry.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_tool_registry.py` at the end of the `TestSeedDefaults` class:

```python
@pytest.mark.asyncio
async def test_seed_defaults_composite_backend(self, registry, mock_db):
    """web_search tool should get backend='composite', not 'external_mcp'."""
    result_mock = MagicMock()
    result_mock.scalars.return_value = result_mock
    result_mock.all.return_value = []
    mock_db.execute = AsyncMock(return_value=result_mock)

    await registry.seed_defaults()

    # Find the web_search add call
    for call in mock_db.add.call_args_list:
        tool_def = call[0][0]
        if tool_def.name == "web_search":
            assert tool_def.backend == "composite", (
                f"web_search should have backend='composite', got '{tool_def.backend}'"
            )
            return

    pytest.fail("web_search tool was not seeded")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tool_registry.py::TestSeedDefaults::test_seed_defaults_composite_backend -v`

Expected: FAIL — `web_search` has `backend='external_mcp'`

- [ ] **Step 3: Fix seed_defaults Pass 2**

In `backend/src/services/tool_registry.py`, change line 346 from:

```python
            backend = "external_mcp"
```

to:

```python
            backend = "composite" if seed.server == "_composite" else "external_mcp"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_tool_registry.py::TestSeedDefaults::test_seed_defaults_composite_backend -v`

Expected: PASS

- [ ] **Step 5: Run full test_tool_registry to check no regressions**

Run: `cd backend && python -m pytest tests/test_tool_registry.py -v`

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/services/tool_registry.py tests/test_tool_registry.py
git commit -m "fix: seed web_search with backend='composite' instead of 'external_mcp'"
```

---

### Task 2: Feature Flag + Mock Settings

**Files:**
- Modify: `backend/src/config/settings.py:164`
- Modify: `backend/tests/conftest.py:66`

- [ ] **Step 1: Add the feature flag to Settings**

In `backend/src/config/settings.py`, after line 164 (`skip_registry_validation: bool = False`), add:

```python
    # Unified tool registry dispatch (Phase 11)
    use_unified_dispatch: bool = False  # JARVIS_USE_UNIFIED_DISPATCH
```

- [ ] **Step 2: Add flag to make_mock_settings**

In `backend/tests/conftest.py`, in the `make_mock_settings` function defaults dict (around line 66), add:

```python
        use_unified_dispatch=False,
```

- [ ] **Step 3: Write test to verify the flag exists and defaults to False**

Add to a new file `backend/tests/test_unified_dispatch.py`:

```python
"""Tests for Phase 11: Feature Flag + Registry-Driven Dispatch.

Covers: unified dispatch, can_use_tool_unified, is_auto_execute_tool,
flag gating, and composite/internal/external backend routing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


class TestFeatureFlag:
    def test_flag_defaults_to_false(self):
        """JARVIS_USE_UNIFIED_DISPATCH defaults to False."""
        settings = make_mock_settings()
        assert settings.use_unified_dispatch is False

    def test_flag_can_be_enabled(self):
        """Flag can be set to True via make_mock_settings override."""
        settings = make_mock_settings(use_unified_dispatch=True)
        assert settings.use_unified_dispatch is True
```

- [ ] **Step 4: Run the test**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestFeatureFlag -v`

Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/config/settings.py tests/conftest.py tests/test_unified_dispatch.py
git commit -m "feat: add JARVIS_USE_UNIFIED_DISPATCH feature flag (default False)"
```

---

### Task 3: `_call_composite_tool()` + `_call_internal_tool()` Server Prefix

Extract the `web_search` handler into a new method. Add `server_prefix` parameter to `_call_internal_tool()` so the unified dispatch can pass the registry's `server` field.

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:2643` (`_call_internal_tool`) and add `_call_composite_tool` before it
- Test: `backend/tests/test_unified_dispatch.py`

- [ ] **Step 1: Write tests for `_call_composite_tool`**

Add to `backend/tests/test_unified_dispatch.py`:

```python
class TestCallCompositeTool:
    """Tests for _call_composite_tool() extracted handler."""

    @pytest.fixture
    def orchestrator(self):
        """Create a minimal JarvisOrchestrator with mocked dependencies."""
        settings = make_mock_settings(use_unified_dispatch=True)
        db_factory = MagicMock()
        services = MagicMock()
        with patch("src.orchestrator.jarvis.create_sub_agents"):
            with patch("src.orchestrator.jarvis.build_tool_definitions", return_value=[]):
                from src.orchestrator.jarvis import JarvisOrchestrator
                orch = JarvisOrchestrator(settings, db_factory, services)
        return orch

    @pytest.mark.asyncio
    async def test_composite_web_search(self, orchestrator):
        """web_search dispatches to the web_search module."""
        with patch("src.browser.web_search.web_search", new_callable=AsyncMock) as mock_ws:
            mock_ws.return_value = {"results": [{"title": "test", "url": "http://example.com"}]}
            result = await orchestrator._call_composite_tool(
                "web_search", {"query": "test"}, user_id="usr_1", workspace_id="ws_1"
            )
        mock_ws.assert_called_once_with(
            query="test", num_results=10, user_id="usr_1", workspace_id="ws_1"
        )
        assert "results" in result

    @pytest.mark.asyncio
    async def test_composite_unknown_tool(self, orchestrator):
        """Unknown composite tool returns error."""
        result = await orchestrator._call_composite_tool(
            "unknown_composite", {}, user_id="usr_1", workspace_id="ws_1"
        )
        assert "error" in result
        assert "Unknown composite tool" in result["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestCallCompositeTool -v`

Expected: FAIL — `_call_composite_tool` not defined

- [ ] **Step 3: Add `_call_composite_tool()` to jarvis.py**

In `backend/src/orchestrator/jarvis.py`, add the new method before `_call_internal_tool` (before line 2643). Place it after the `_NATIVE_TOOL_MAP` class variable (after line 2637):

```python
    async def _call_composite_tool(
        self, tool_name: str, tool_input: dict, user_id: str = "", workspace_id: str = ""
    ) -> dict:
        """Dispatch composite tools (multi-MCP orchestration)."""
        if tool_name == "web_search":
            from src.browser.web_search import web_search

            return await web_search(
                query=tool_input.get("query", ""),
                num_results=tool_input.get("num_results", 10),
                user_id=user_id,
                workspace_id=workspace_id,
            )
        return {"error": f"Unknown composite tool: {tool_name}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestCallCompositeTool -v`

Expected: PASS

- [ ] **Step 5: Write tests for `_call_internal_tool` server_prefix**

Add to `backend/tests/test_unified_dispatch.py`:

```python
class TestCallInternalToolServerPrefix:
    """Tests for _call_internal_tool() server_prefix parameter."""

    @pytest.fixture
    def orchestrator(self):
        settings = make_mock_settings(use_unified_dispatch=True)
        db_factory = MagicMock()
        services = MagicMock()
        with patch("src.orchestrator.jarvis.create_sub_agents"):
            with patch("src.orchestrator.jarvis.build_tool_definitions", return_value=[]):
                from src.orchestrator.jarvis import JarvisOrchestrator
                orch = JarvisOrchestrator(settings, db_factory, services)
        return orch

    @pytest.mark.asyncio
    async def test_server_prefix_from_registry(self, orchestrator):
        """When server_prefix is passed, it overrides _INTERNAL_TOOL_SERVER."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "send_telegram", {"text": "hi"}, server_prefix="communication"
        )
        mock_client.call_tool.assert_called_once_with(
            "communication_send_telegram", {"text": "hi"}
        )

    @pytest.mark.asyncio
    async def test_no_prefix_uses_internal_tool_server(self, orchestrator):
        """Without server_prefix, falls back to _INTERNAL_TOOL_SERVER dict."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool("search", {"query": "test"})
        mock_client.call_tool.assert_called_once_with(
            "intelligence_search", {"query": "test"}
        )

    @pytest.mark.asyncio
    async def test_server_prefix_intelligence(self, orchestrator):
        """server_prefix='intelligence' builds intelligence_search."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "search", {"query": "test"}, server_prefix="intelligence"
        )
        mock_client.call_tool.assert_called_once_with(
            "intelligence_search", {"query": "test"}
        )
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestCallInternalToolServerPrefix -v`

Expected: FAIL — `_call_internal_tool() got an unexpected keyword argument 'server_prefix'`

- [ ] **Step 7: Modify `_call_internal_tool()` to accept `server_prefix`**

In `backend/src/orchestrator/jarvis.py`, change the method signature at line 2643 from:

```python
    async def _call_internal_tool(self, tool_name: str, tool_input: dict) -> dict:
```

to:

```python
    async def _call_internal_tool(
        self, tool_name: str, tool_input: dict, server_prefix: str | None = None
    ) -> dict:
```

Then change lines 2663-2665 from:

```python
        # Map flat name to namespaced name (server-specific prefix)
        prefix = _INTERNAL_TOOL_SERVER.get(tool_name, "intelligence")
        namespaced = f"{prefix}_{tool_name}"
```

to:

```python
        # Map flat name to namespaced name (server-specific prefix)
        if server_prefix is not None:
            namespaced = f"{server_prefix}_{tool_name}"
        else:
            prefix = _INTERNAL_TOOL_SERVER.get(tool_name, "intelligence")
            namespaced = f"{prefix}_{tool_name}"
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestCallInternalToolServerPrefix -v`

Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py tests/test_unified_dispatch.py
git commit -m "feat: add _call_composite_tool() and server_prefix param to _call_internal_tool()"
```

---

### Task 4: `_execute_tool_unified()` + Flag Gate

The core dispatch method: one DB lookup, one match on `backend`. Plus the 2-line flag gate in `_execute_tool()`.

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:2480` (add flag gate + new method)
- Test: `backend/tests/test_unified_dispatch.py`

- [ ] **Step 1: Write tests for unified dispatch**

Add to `backend/tests/test_unified_dispatch.py`:

```python
def _make_tool_record(
    name="search",
    backend="internal_mcp",
    server="intelligence",
    enabled=True,
    capability="internal.search",
    risk_level="low",
    requires_approval=False,
):
    """Create a mock ToolDefinition record."""
    tool = MagicMock()
    tool.name = name
    tool.backend = backend
    tool.server = server
    tool.enabled = enabled
    tool.capability = capability
    tool.risk_level = risk_level
    tool.requires_approval = requires_approval
    return tool


class TestExecuteToolUnified:
    """Tests for _execute_tool_unified() 3-backend match dispatch."""

    @pytest.fixture
    def orchestrator(self):
        settings = make_mock_settings(use_unified_dispatch=True)
        db_factory = MagicMock()
        services = MagicMock()
        with patch("src.orchestrator.jarvis.create_sub_agents"):
            with patch("src.orchestrator.jarvis.build_tool_definitions", return_value=[]):
                from src.orchestrator.jarvis import JarvisOrchestrator
                orch = JarvisOrchestrator(settings, db_factory, services)
        orch._publish_event = AsyncMock()
        return orch

    @pytest.mark.asyncio
    async def test_internal_mcp_dispatch(self, orchestrator):
        """internal_mcp backend dispatches via _call_internal_tool with server_prefix."""
        tool = _make_tool_record(name="search", backend="internal_mcp", server="intelligence")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        orchestrator._call_internal_tool = AsyncMock(return_value={"status": "ok"})

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "search", {"query": "test"}, "usr_1", "ws_1"
            )

        assert result == {"status": "ok"}
        orchestrator._call_internal_tool.assert_called_once()
        call_kwargs = orchestrator._call_internal_tool.call_args
        assert call_kwargs[1].get("server_prefix") == "intelligence" or \
            call_kwargs[0][2] == "intelligence"  # positional or keyword

    @pytest.mark.asyncio
    async def test_special_backend_returns_input(self, orchestrator):
        """_special server returns tool_input as-is (report_governor_verdict)."""
        tool = _make_tool_record(
            name="report_governor_verdict", backend="internal_mcp", server="_special"
        )

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            input_data = {"verdict": "approved", "reasoning": "low risk"}
            result = await orchestrator._execute_tool_unified(
                "report_governor_verdict", input_data, "usr_1", "ws_1"
            )

        assert result == input_data

    @pytest.mark.asyncio
    async def test_external_mcp_dispatch(self, orchestrator):
        """external_mcp backend dispatches via call_mcp_tool with real name."""
        tool = _make_tool_record(
            name="API-post-page", backend="external_mcp", server="notion"
        )

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            with patch("src.connectors.mcp_bridge.call_mcp_tool", new_callable=AsyncMock) as mock_mcp:
                mock_mcp.return_value = {"status": "ok", "page_id": "pg_123"}
                result = await orchestrator._execute_tool_unified(
                    "API-post-page", {"title": "Test"}, "usr_1", "ws_1"
                )

        mock_mcp.assert_called_once_with(
            "API-post-page", {"title": "Test", "workspace_id": "ws_1"},
            user_id="usr_1", workspace_id="ws_1",
        )
        assert result["page_id"] == "pg_123"

    @pytest.mark.asyncio
    async def test_composite_dispatch(self, orchestrator):
        """composite backend dispatches via _call_composite_tool."""
        tool = _make_tool_record(
            name="web_search", backend="composite", server="_composite"
        )

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        orchestrator._call_composite_tool = AsyncMock(
            return_value={"results": [{"title": "test"}]}
        )

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "web_search", {"query": "test"}, "usr_1", "ws_1"
            )

        assert "results" in result
        orchestrator._call_composite_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, orchestrator):
        """Unknown tool returns error dict."""
        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "nonexistent_tool", {}, "usr_1", "ws_1"
            )

        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_disabled_tool_returns_blocked(self, orchestrator):
        """Disabled tool returns blocked error."""
        tool = _make_tool_record(name="search", enabled=False)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            result = await orchestrator._execute_tool_unified(
                "search", {"query": "test"}, "usr_1", "ws_1"
            )

        assert "error" in result
        assert result.get("blocked") is True


class TestFlagGating:
    """Tests for flag-based routing in _execute_tool()."""

    @pytest.fixture
    def orchestrator(self):
        settings = make_mock_settings(use_unified_dispatch=False)
        db_factory = MagicMock()
        services = MagicMock()
        with patch("src.orchestrator.jarvis.create_sub_agents"):
            with patch("src.orchestrator.jarvis.build_tool_definitions", return_value=[]):
                from src.orchestrator.jarvis import JarvisOrchestrator
                orch = JarvisOrchestrator(settings, db_factory, services)
        return orch

    @pytest.mark.asyncio
    async def test_flag_off_uses_old_dispatch(self, orchestrator):
        """Flag OFF: _execute_tool does NOT call _execute_tool_unified."""
        orchestrator._execute_tool_unified = AsyncMock()
        orchestrator._publish_event = AsyncMock()

        # The old path will try ToolRegistry pre-check and then the cascade.
        # We just verify _execute_tool_unified is NOT called.
        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            mock_reg.is_blocked_tool = AsyncMock(return_value=False)
            mock_reg_cls.return_value = mock_reg

            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            orchestrator._db_factory = MagicMock(return_value=db_ctx)

            # report_governor_verdict is the simplest path in old dispatch
            result = await orchestrator._execute_tool(
                "report_governor_verdict", {"v": "ok"}, "usr_1", "ws_1"
            )

        orchestrator._execute_tool_unified.assert_not_called()
        assert result == {"v": "ok"}

    @pytest.mark.asyncio
    async def test_flag_on_uses_unified_dispatch(self, orchestrator):
        """Flag ON: _execute_tool delegates to _execute_tool_unified."""
        orchestrator._settings.use_unified_dispatch = True
        orchestrator._execute_tool_unified = AsyncMock(return_value={"status": "ok"})

        result = await orchestrator._execute_tool("search", {"q": "test"}, "usr_1", "ws_1")

        orchestrator._execute_tool_unified.assert_called_once_with(
            "search", {"q": "test"}, "usr_1", "ws_1"
        )
        assert result == {"status": "ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestExecuteToolUnified -v`

Expected: FAIL — `_execute_tool_unified` not defined

- [ ] **Step 3: Add `_execute_tool_unified()` to jarvis.py**

In `backend/src/orchestrator/jarvis.py`, add the new method after `_execute_tool` (after line 2627, before `_NATIVE_TOOL_MAP`):

```python
    async def _execute_tool_unified(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        """Registry-driven dispatch: one lookup, one match on backend.

        Replaces the 6-step cascade when JARVIS_USE_UNIFIED_DISPATCH is enabled.
        """
        from src.services.tool_registry import ToolRegistry

        async with self._db_factory() as db:
            registry = ToolRegistry(db)
            tool = await registry.get_tool(tool_name)

        if not tool:
            return {"error": f"Unknown tool: {tool_name}"}
        if not tool.enabled:
            return {"error": f"Tool '{tool_name}' is disabled", "blocked": True}

        # Inject workspace_id so tools always have it
        if workspace_id and "workspace_id" not in tool_input:
            tool_input = {**tool_input, "workspace_id": workspace_id}

        await self._publish_event("tool.started", user_id, {"tool": tool_name})

        try:
            match tool.backend:
                case "internal_mcp":
                    if tool.server == "_special":
                        result = tool_input
                    else:
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
                case _:
                    result = {
                        "error": f"Unknown backend '{tool.backend}' for tool '{tool_name}'"
                    }

            await self._publish_event("tool.completed", user_id, {"tool": tool_name})
            return result
        except Exception as e:
            logger.warning("Tool %s failed: %s", tool_name, e)
            await self._publish_event(
                "tool.failed",
                user_id,
                {"tool": tool_name, "error": str(e)[:200]},
            )
            return {"error": f"Tool execution failed for {tool_name}: {e}"}
```

- [ ] **Step 4: Add flag gate to `_execute_tool()`**

In `backend/src/orchestrator/jarvis.py`, at line 2484 (after the docstring of `_execute_tool`, before the `# 0. Pre-dispatch` comment), add:

```python
        # Phase 11: unified dispatch (flag-gated)
        if self._settings.use_unified_dispatch:
            return await self._execute_tool_unified(
                tool_name, tool_input, user_id, workspace_id
            )

```

- [ ] **Step 5: Run all unified dispatch tests**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestExecuteToolUnified tests/test_unified_dispatch.py::TestFlagGating -v`

Expected: PASS (all 8 tests)

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py tests/test_unified_dispatch.py
git commit -m "feat: add _execute_tool_unified() with 3-backend match dispatch"
```

---

### Task 5: `can_use_tool_unified()` + `_get_tools_for_agent_unified()`

Add async capability check on SubAgent and async tool filtering on orchestrator. Wire up call sites with flag check.

**Files:**
- Modify: `backend/src/orchestrator/agents.py:193` (add method after `can_use_tool`)
- Modify: `backend/src/orchestrator/jarvis.py:610,1220,2436` (add method + wire call sites)
- Test: `backend/tests/test_unified_dispatch.py`

- [ ] **Step 1: Write tests for `can_use_tool_unified()`**

Add to `backend/tests/test_unified_dispatch.py`:

```python
class TestCanUseToolUnified:
    """Tests for SubAgent.can_use_tool_unified() — registry-driven."""

    @pytest.mark.asyncio
    async def test_matching_capability_returns_true(self):
        """Tool with matching capability in agent's scope returns True."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="researcher",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.search", "search.web"},
        )
        tool = _make_tool_record(name="search", capability="internal.search")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool_unified("search", mock_db)

        assert result is True

    @pytest.mark.asyncio
    async def test_non_matching_capability_returns_false(self):
        """Tool with capability NOT in agent's scope returns False."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="librarian",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.update_entity"},
        )
        tool = _make_tool_record(name="search", capability="internal.search")

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool_unified("search", mock_db)

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_false(self):
        """Unknown tool returns False."""
        from src.orchestrator.agents import SubAgent

        agent = SubAgent(
            name="researcher",
            prompt="test",
            model_tier="sonnet",
            capability_scope={"internal.search"},
        )

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)

        mock_db = AsyncMock()
        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await agent.can_use_tool_unified("nonexistent", mock_db)

        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestCanUseToolUnified -v`

Expected: FAIL — `can_use_tool_unified` not defined

- [ ] **Step 3: Add `can_use_tool_unified()` to SubAgent**

In `backend/src/orchestrator/agents.py`, after the existing `can_use_tool` method (after line 220), add:

```python
    async def can_use_tool_unified(self, tool_name: str, db) -> bool:
        """Registry-driven capability check. One lookup, no normalizer.

        Used when JARVIS_USE_UNIFIED_DISPATCH is enabled.
        """
        if not self.capability_scope:
            return False
        from src.services.tool_registry import ToolRegistry

        registry = ToolRegistry(db)
        tool = await registry.get_tool(tool_name)
        if tool and tool.capability:
            return tool.capability in self.capability_scope
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestCanUseToolUnified -v`

Expected: PASS

- [ ] **Step 5: Add `_get_tools_for_agent_unified()` to jarvis.py**

In `backend/src/orchestrator/jarvis.py`, after `_get_tools_for_agent` (after line 627), add:

```python
    async def _get_tools_for_agent_unified(
        self, agent: SubAgent, workspace_id: str = ""
    ) -> list[dict]:
        """Filter tool definitions using registry-driven capability check.

        Used when JARVIS_USE_UNIFIED_DISPATCH is enabled.
        """
        from src.connectors.mcp_bridge import list_mcp_tools

        tools = list(self._tools)
        for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
            schema = mcp_tool.get("input_schema", {})
            tools.append(
                {
                    "name": mcp_tool["name"],
                    "description": mcp_tool.get("description", "External MCP tool"),
                    "input_schema": schema if schema else {"type": "object", "properties": {}},
                }
            )

        async with self._db_factory() as db:
            return [t for t in tools if await agent.can_use_tool_unified(t["name"], db)]
```

- [ ] **Step 6: Wire up call site 1 (streaming path, ~line 1220)**

In `backend/src/orchestrator/jarvis.py`, change the tool assignment around line 1220 from:

```python
        tools = self._apply_cache_control_to_tools(
            self._get_tools_for_agent(agent, workspace_id=workspace_id)
        )
```

to:

```python
        if self._settings.use_unified_dispatch:
            tools = self._apply_cache_control_to_tools(
                await self._get_tools_for_agent_unified(agent, workspace_id=workspace_id)
            )
        else:
            tools = self._apply_cache_control_to_tools(
                self._get_tools_for_agent(agent, workspace_id=workspace_id)
            )
```

- [ ] **Step 7: Wire up call site 2 (non-streaming path, ~line 2436)**

In `backend/src/orchestrator/jarvis.py`, change the tool assignment around line 2436 from:

```python
        tools = self._apply_cache_control_to_tools(
            self._get_tools_for_agent(agent, workspace_id=workspace_id)
        )
```

to:

```python
        if self._settings.use_unified_dispatch:
            tools = self._apply_cache_control_to_tools(
                await self._get_tools_for_agent_unified(agent, workspace_id=workspace_id)
            )
        else:
            tools = self._apply_cache_control_to_tools(
                self._get_tools_for_agent(agent, workspace_id=workspace_id)
            )
```

- [ ] **Step 8: Run all tests in test file**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py -v`

Expected: PASS (all tests so far)

- [ ] **Step 9: Commit**

```bash
cd backend && git add src/orchestrator/agents.py src/orchestrator/jarvis.py tests/test_unified_dispatch.py
git commit -m "feat: add can_use_tool_unified() and registry-driven tool filtering"
```

---

### Task 6: `is_auto_execute_tool()` on Governor

Add tool-level auto-execute check that derives from registry `risk_level` + `requires_approval`.

**Files:**
- Modify: `backend/src/services/governor.py` (add method after `_apply_policy`)
- Test: `backend/tests/test_unified_dispatch.py`

- [ ] **Step 1: Write tests for `is_auto_execute_tool()`**

Add to `backend/tests/test_unified_dispatch.py`:

```python
class TestIsAutoExecuteTool:
    """Tests for Governor.is_auto_execute_tool() — registry-derived."""

    @pytest.fixture
    def governor(self):
        from src.services.governor import Governor

        db = AsyncMock()
        return Governor(db=db)

    @pytest.mark.asyncio
    async def test_low_risk_no_approval_returns_true(self, governor):
        """Low risk + no approval required = auto-execute."""
        tool = _make_tool_record(risk_level="low", requires_approval=False)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("search")

        assert result is True

    @pytest.mark.asyncio
    async def test_high_risk_returns_false(self, governor):
        """High risk = not auto-execute."""
        tool = _make_tool_record(risk_level="high", requires_approval=True)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("sendGmailDraft")

        assert result is False

    @pytest.mark.asyncio
    async def test_low_risk_with_approval_returns_false(self, governor):
        """Low risk but requires approval = not auto-execute."""
        tool = _make_tool_record(risk_level="low", requires_approval=True)

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=tool)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("approve_action")

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_false(self, governor):
        """Unknown tool = not auto-execute (safe default)."""
        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)

        with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
            result = await governor.is_auto_execute_tool("nonexistent")

        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestIsAutoExecuteTool -v`

Expected: FAIL — `is_auto_execute_tool` not defined

- [ ] **Step 3: Add `is_auto_execute_tool()` to Governor**

In `backend/src/services/governor.py`, after the `_apply_policy` method (after line 388), add:

```python
    async def is_auto_execute_tool(self, tool_name: str) -> bool:
        """Check if a tool can auto-execute based on registry risk metadata.

        Tool-level policy: derives from risk_level + requires_approval.
        Decision-level policy (AUTO_EXECUTE_ACTIONS) is separate and unchanged.
        """
        from src.services.tool_registry import ToolRegistry

        registry = ToolRegistry(self._db)
        tool = await registry.get_tool(tool_name)
        if not tool:
            return False
        return tool.risk_level == "low" and not tool.requires_approval
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py::TestIsAutoExecuteTool -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/services/governor.py tests/test_unified_dispatch.py
git commit -m "feat: add is_auto_execute_tool() — registry-derived tool-level policy"
```

---

### Task 7: Full Regression + Final Commit

Run the entire test suite with flag OFF (default) to confirm zero regressions. Then run the new tests one more time.

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short 2>&1 | tail -20`

Expected: All 1174+ existing tests pass. 14+ new tests pass. Zero failures.

- [ ] **Step 2: Verify new test count**

Run: `cd backend && python -m pytest tests/test_unified_dispatch.py -v --tb=short`

Expected: 14+ tests pass (2 flag + 2 composite + 3 server_prefix + 6 unified dispatch + 2 flag gating + 3 can_use_tool_unified + 4 is_auto_execute_tool = ~22 tests)

- [ ] **Step 3: Quick smoke check — verify flag-off path unchanged**

Run: `cd backend && python -m pytest tests/test_agent_loop.py -v --tb=short`

Expected: All 11 existing agent loop tests pass unchanged.

- [ ] **Step 4: Final commit (if any remaining unstaged files)**

```bash
cd backend && git status
# If any unstaged changes:
# git add <files>
# git commit -m "feat: Phase 11 unified dispatch — feature flag + registry-driven dispatch"
```

---

## Exit Criteria Checklist

- [ ] `JARVIS_USE_UNIFIED_DISPATCH` flag in settings.py, defaults False
- [ ] `_execute_tool_unified()` implements 3-backend match dispatch
- [ ] `_call_composite_tool()` extracted from web_search handler
- [ ] `_call_internal_tool()` accepts `server_prefix` parameter
- [ ] `_execute_tool()` delegates to unified when flag ON
- [ ] `can_use_tool_unified()` on SubAgent — one registry lookup
- [ ] `_get_tools_for_agent_unified()` — async tool filtering
- [ ] Call sites (streaming + non-streaming) check flag
- [ ] `is_auto_execute_tool()` on Governor — registry-derived
- [ ] `web_search` seed has `backend="composite"` in DB
- [ ] All existing tests pass with flag OFF
- [ ] All new tests pass with flag ON
- [ ] Zero regressions
