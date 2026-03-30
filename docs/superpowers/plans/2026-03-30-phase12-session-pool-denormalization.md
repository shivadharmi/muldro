# Phase 12: Session Pool De-Normalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `JARVIS_USE_UNIFIED_DISPATCH` is ON, skip tool name normalization in session_pool.py. Store and dispatch real MCP names end-to-end. Auto-register unknown discovered tools with safe defaults.

**Architecture:** Add `use_unified_dispatch` flag to session pool constructor. When ON: store real names directly in `_server_tools` and `_tool_metadata`, skip canonical→raw translation in `call_tool()`, register unknown tools in DB. When OFF: existing normalization unchanged.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest + pytest-asyncio

**Design spec:** `docs/superpowers/specs/2026-03-29-unified-tool-registry-implementation-phases.md` (Phase 12)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/src/integrations/session_pool.py` | Modify | Add flag to constructor, skip normalization when ON, register unknown tools |
| `backend/src/connectors/mcp_bridge.py` | Modify | Pass flag from settings to session pool |
| `backend/tests/test_unified_dispatch.py` | Modify | Add Phase 12 tests |

---

### Task 1: Session Pool — Skip Normalization When Flag ON

**Files:**
- Modify: `backend/src/integrations/session_pool.py`
- Modify: `backend/src/connectors/mcp_bridge.py`
- Test: `backend/tests/test_unified_dispatch.py`

- [ ] **Step 1: Write tests for session pool de-normalization**

Add to `backend/tests/test_unified_dispatch.py`:

```python
class TestSessionPoolDeNormalization:
    """Tests for Phase 12: session pool skips normalization when flag ON."""

    def _make_pool(self, unified=True):
        """Create a UserMCPSessionPool with mocked normalizer."""
        from src.integrations.session_pool import UserMCPSessionPool

        normalizer = MagicMock()
        pool = UserMCPSessionPool(
            normalizer=normalizer,
            use_unified_dispatch=unified,
        )
        return pool, normalizer

    @pytest.mark.asyncio
    async def test_unified_stores_real_names(self):
        """When flag ON, tool names stored as-is (no normalization)."""
        pool, normalizer = self._make_pool(unified=True)

        # Simulate server config
        pool.register_server_config("notion", {"command": "npx", "args": ["notion-mcp"]})

        # Mock the session creation internals
        mock_client = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "API-post-page"
        mock_tool.description = "Create a page"
        mock_tool.inputSchema = {"type": "object", "properties": {"title": {"type": "string"}}}
        mock_client.list_tools = AsyncMock(return_value=[mock_tool])

        with patch("src.integrations.session_pool.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_ctx

            session = await pool.get_or_create_session("notion", "usr_1")

        # Normalizer should NOT have been called
        normalizer.register_server_tools.assert_not_called()
        normalizer.normalize.assert_not_called()

        # Tool stored under real name
        assert "API-post-page" in session.tools
        assert pool._tool_metadata.get("API-post-page") is not None
        assert pool._tool_metadata["API-post-page"]["name"] == "API-post-page"

    @pytest.mark.asyncio
    async def test_legacy_still_normalizes(self):
        """When flag OFF, normalization still happens."""
        pool, normalizer = self._make_pool(unified=False)

        pool.register_server_config("notion", {"command": "npx", "args": ["notion-mcp"]})

        normalizer.register_server_tools.return_value = {"api_post_page": "API-post-page"}
        normalizer.normalize.return_value = "api_post_page"

        mock_client = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "API-post-page"
        mock_tool.description = "Create a page"
        mock_tool.inputSchema = {"type": "object"}
        mock_client.list_tools = AsyncMock(return_value=[mock_tool])

        with patch("src.integrations.session_pool.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_ctx

            await pool.get_or_create_session("notion", "usr_1")

        # Normalizer SHOULD have been called
        normalizer.register_server_tools.assert_called_once()
        normalizer.normalize.assert_called()

    @pytest.mark.asyncio
    async def test_unified_call_tool_skips_translation(self):
        """When flag ON, call_tool uses tool_name directly (no canonical→raw)."""
        pool, _ = self._make_pool(unified=True)

        # Pre-populate a session with a real-name tool mapping
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text='{"status": "ok"}')]
        mock_client.call_tool = AsyncMock(return_value=mock_result)

        from src.integrations.session_pool import SessionEntry

        session = SessionEntry(
            client=mock_client,
            client_ctx=MagicMock(),
            server_name="notion",
            user_id="usr_1",
            tools={"API-post-page": "API-post-page"},
        )
        pool._sessions[("", "notion", "usr_1")] = session

        result = await pool.call_tool(
            "API-post-page", {"title": "Test"},
            user_id="usr_1", server_name="notion",
        )

        # Should call with real name directly
        mock_client.call_tool.assert_called_once_with("API-post-page", {"title": "Test"})
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unified_registers_unknown_tools(self):
        """When flag ON, unknown discovered tools are registered in DB."""
        pool, _ = self._make_pool(unified=True)
        pool.register_server_config("notion", {"command": "npx", "args": ["notion-mcp"]})

        mock_client = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "API-new-unknown-tool"
        mock_tool.description = "A new tool"
        mock_tool.inputSchema = {"type": "object"}
        mock_client.list_tools = AsyncMock(return_value=[mock_tool])

        mock_registry = MagicMock()
        mock_registry.get_tool = AsyncMock(return_value=None)  # Not in registry
        mock_registry.register_tool = AsyncMock()

        with patch("src.integrations.session_pool.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_ctx

            with patch("src.integrations.session_pool.get_session_factory") as mock_sf:
                mock_db_ctx = AsyncMock()
                mock_db = AsyncMock()
                mock_db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
                mock_db_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_sf.return_value = MagicMock(return_value=mock_db_ctx)

                with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
                    await pool.get_or_create_session("notion", "usr_1")

        # Should have tried to register the unknown tool
        mock_registry.register_tool.assert_called_once()
        call_kwargs = mock_registry.register_tool.call_args[1]
        assert call_kwargs["name"] == "API-new-unknown-tool"
        assert call_kwargs["capability"] is None  # Invisible to agents
        assert call_kwargs["source"] == "discovered"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_unified_dispatch.py::TestSessionPoolDeNormalization -v`

Expected: FAIL — `use_unified_dispatch` not accepted by constructor

- [ ] **Step 3: Modify session_pool.py — constructor + get_or_create_session**

In `backend/src/integrations/session_pool.py`:

**Constructor (line 65):** Add `use_unified_dispatch` parameter:

Change:
```python
    def __init__(
        self,
        oauth_manager: Any | None = None,
        circuit_breaker: MCPCircuitBreaker | None = None,
        normalizer: ToolNameNormalizer | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
    ) -> None:
```

To:
```python
    def __init__(
        self,
        oauth_manager: Any | None = None,
        circuit_breaker: MCPCircuitBreaker | None = None,
        normalizer: ToolNameNormalizer | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        use_unified_dispatch: bool = False,
    ) -> None:
```

And add after `self._ttl_seconds = ttl_seconds`:
```python
        self._use_unified_dispatch = use_unified_dispatch
```

**get_or_create_session (lines 139-157):** Replace the normalization block with a flag check:

Change lines 140-157 from:
```python
            raw_tools = await client.list_tools()
            tool_dicts = [{"name": t.name, "description": t.description or ""} for t in raw_tools]
            tool_mapping = self._normalizer.register_server_tools(server_name, tool_dicts)
            self._server_tools[(workspace_id, server_name)] = tool_mapping
            for t in raw_tools:
                canonical = self._normalizer.normalize(t.name, server_name)
                input_schema = (
                    getattr(t, "inputSchema", None)
                    or getattr(t, "input_schema", None)
                    or {"type": "object", "properties": {}}
                )
                self._tool_metadata[canonical] = {
                    "name": canonical,
                    "server": server_name,
                    "description": t.description or "",
                    "input_schema": input_schema,
                    "_workspace_id": workspace_id,
                }
```

To:
```python
            raw_tools = await client.list_tools()

            if self._use_unified_dispatch:
                # Store real MCP names directly — no normalization
                tool_mapping: dict[str, str] = {}
                for t in raw_tools:
                    real_name = t.name
                    tool_mapping[real_name] = real_name
                    input_schema = (
                        getattr(t, "inputSchema", None)
                        or getattr(t, "input_schema", None)
                        or {"type": "object", "properties": {}}
                    )
                    self._tool_metadata[real_name] = {
                        "name": real_name,
                        "server": server_name,
                        "description": t.description or "",
                        "input_schema": input_schema,
                        "_workspace_id": workspace_id,
                    }
                self._server_tools[(workspace_id, server_name)] = tool_mapping

                # Register unknown discovered tools in DB with safe defaults
                await self._register_discovered_tools(
                    raw_tools, server_name, workspace_id
                )
            else:
                tool_dicts = [
                    {"name": t.name, "description": t.description or ""} for t in raw_tools
                ]
                tool_mapping = self._normalizer.register_server_tools(
                    server_name, tool_dicts
                )
                self._server_tools[(workspace_id, server_name)] = tool_mapping
                for t in raw_tools:
                    canonical = self._normalizer.normalize(t.name, server_name)
                    input_schema = (
                        getattr(t, "inputSchema", None)
                        or getattr(t, "input_schema", None)
                        or {"type": "object", "properties": {}}
                    )
                    self._tool_metadata[canonical] = {
                        "name": canonical,
                        "server": server_name,
                        "description": t.description or "",
                        "input_schema": input_schema,
                        "_workspace_id": workspace_id,
                    }
```

**call_tool (line 219):** Add flag check for translation skip:

Change:
```python
        # Resolve canonical → raw MCP tool name
        raw_name = session.tools.get(tool_name) or tool_name
```

To:
```python
        # Resolve tool name: unified dispatch uses real names, legacy uses canonical→raw
        if self._use_unified_dispatch:
            raw_name = tool_name
        else:
            raw_name = session.tools.get(tool_name) or tool_name
```

**Add `_register_discovered_tools` method** after `get_or_create_session`:

```python
    async def _register_discovered_tools(
        self, raw_tools: list, server_name: str, workspace_id: str
    ) -> None:
        """Register unknown discovered tools in DB with safe defaults.

        Unknown tools get capability=None, making them invisible to agents
        until an admin maps their capability. Safe by design.
        """
        try:
            from src.models.database import get_session_factory
            from src.services.tool_registry import ToolRegistry

            async with get_session_factory()() as db:
                registry = ToolRegistry(db)
                for t in raw_tools:
                    existing = await registry.get_tool(t.name)
                    if not existing:
                        await registry.register_tool(
                            name=t.name,
                            server=server_name,
                            backend="external_mcp",
                            source="discovered",
                            capability=None,
                            risk_level="medium",
                            requires_approval=True,
                            description=t.description or "",
                            workspace_id=workspace_id,
                        )
                        logger.info(
                            "Registered discovered tool: %s from %s",
                            t.name,
                            server_name,
                        )
                await db.commit()
        except Exception:
            logger.debug("Failed to register discovered tools", exc_info=True)
```

- [ ] **Step 4: Modify mcp_bridge.py — pass flag to session pool**

In `backend/src/connectors/mcp_bridge.py`, modify `initialize_mcp_bridge()` at line 102:

Change:
```python
    _session_pool = UserMCPSessionPool(
        oauth_manager=oauth_manager,
        circuit_breaker=_circuit_breaker,
        normalizer=get_normalizer(),
    )
```

To:
```python
    from src.config.settings import get_settings

    settings = get_settings()
    _session_pool = UserMCPSessionPool(
        oauth_manager=oauth_manager,
        circuit_breaker=_circuit_breaker,
        normalizer=get_normalizer(),
        use_unified_dispatch=settings.use_unified_dispatch,
    )
```

- [ ] **Step 5: Run tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_unified_dispatch.py -v`

Expected: All 26 tests pass (22 from Phase 11 + 4 new)

- [ ] **Step 6: Run full test suite (excluding E2E)**

Run: `source .venv/bin/activate && python -m pytest tests/ --ignore=tests/e2e -v --tb=short 2>&1 | tail -5`

Expected: All tests pass, zero failures

- [ ] **Step 7: Commit**

```bash
git add backend/src/integrations/session_pool.py backend/src/connectors/mcp_bridge.py backend/tests/test_unified_dispatch.py
git commit -m "feat: session pool de-normalization — real MCP names when unified dispatch ON (Phase 12)"
```

---

## Exit Criteria

- [ ] Flag OFF: normalization continues as before
- [ ] Flag ON: Notion `API-post-page` stored and dispatched as `API-post-page` (no conversion)
- [ ] Flag ON: `call_tool()` uses tool_name directly (no canonical→raw translation)
- [ ] Flag ON: Unknown discovered tools registered in DB with `source="discovered"`, `capability=None`
- [ ] Unknown tools invisible to agents (`can_use_tool_unified` returns False for `capability=None`)
- [ ] All existing tests pass
- [ ] mcp_bridge passes flag from settings to session pool
