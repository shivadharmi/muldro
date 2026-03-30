# Phase 11: Feature Flag + Registry-Driven Dispatch — Design Spec

**Date:** 2026-03-30
**Status:** Approved
**Parent spec:** [unified-tool-registry-design.md](./2026-03-29-unified-tool-registry-design.md)
**Phase breakdown:** [unified-tool-registry-implementation-phases.md](./2026-03-29-unified-tool-registry-implementation-phases.md)
**Precondition:** Phases 1–10 complete (1174 tests passing)

---

## 1. Goal

Replace the 6-step dispatch cascade in `_execute_tool()` with a 3-backend match dispatch, gated behind a feature flag. Flag OFF = zero behavior change. Flag ON = registry-driven dispatch.

---

## 2. Scope

| File | Change |
|------|--------|
| `src/config/settings.py` | Add `use_unified_dispatch: bool = False` |
| `src/orchestrator/jarvis.py` | Add `_execute_tool_unified()`, `_call_composite_tool()`, `_get_tools_for_agent_unified()`. Modify `_call_internal_tool()` (add `server_prefix` param). Flag gate in `_execute_tool()` and tool-filtering call sites. |
| `src/orchestrator/agents.py` | Add `can_use_tool_unified()` async method on SubAgent |
| `src/services/governor.py` | Add `is_auto_execute_tool()` async method on Governor |
| `src/services/tool_registry.py` | Fix seed_defaults: `backend="composite"` for `_composite` server |
| `tests/` | 14 new tests covering both flag states |

**~Lines:** +150 added, -0 removed (old code preserved behind flag)

---

## 3. Feature Flag

**File:** `src/config/settings.py`

```python
# Unified tool registry dispatch (Phase 11)
use_unified_dispatch: bool = False  # JARVIS_USE_UNIFIED_DISPATCH
```

- pydantic-settings field, set via `JARVIS_USE_UNIFIED_DISPATCH=true` env var
- Defaults to `False` — zero behavior change on deploy
- Rollback: unset the env var, no code changes needed
- Removed in Phase 15 when unified dispatch becomes permanent

---

## 4. Flag Gate in `_execute_tool()`

**File:** `src/orchestrator/jarvis.py`

Two-line gate at the top of existing `_execute_tool()`, before any existing logic:

```python
async def _execute_tool(self, tool_name, tool_input, user_id, workspace_id=""):
    if self._settings.use_unified_dispatch:
        return await self._execute_tool_unified(tool_name, tool_input, user_id, workspace_id)
    # ... existing 6-step cascade unchanged ...
```

Flag OFF: entire existing method runs as-is.
Flag ON: delegates immediately to `_execute_tool_unified()`.

---

## 5. `_execute_tool_unified()` — 3-Backend Match Dispatch

**File:** `src/orchestrator/jarvis.py`

Per design spec Section 3.5. One `ToolRegistry.get_tool()` lookup (DB, cached), one `match` on `backend`:

```python
async def _execute_tool_unified(
    self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
) -> dict:
    """Registry-driven dispatch: one lookup, one match on backend."""
    from src.services.tool_registry import ToolRegistry

    async with self._db_factory() as db:
        registry = ToolRegistry(db)
        tool = await registry.get_tool(tool_name)

    if not tool:
        return {"error": f"Unknown tool: {tool_name}"}
    if not tool.enabled:
        return {"error": f"Tool '{tool_name}' is disabled", "blocked": True}

    # Inject workspace_id (same as existing _execute_tool)
    if workspace_id and "workspace_id" not in tool_input:
        tool_input = {**tool_input, "workspace_id": workspace_id}

    await self._publish_event("tool.started", user_id, {"tool": tool_name})

    try:
        match tool.backend:
            case "internal_mcp":
                if tool.server == "_special":
                    result = tool_input  # report_governor_verdict returns as-is
                else:
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
                result = await self._call_composite_tool(
                    tool_name, tool_input,
                    user_id=user_id, workspace_id=workspace_id,
                )
            case _:
                result = {"error": f"Unknown backend '{tool.backend}' for tool '{tool_name}'"}

        await self._publish_event("tool.completed", user_id, {"tool": tool_name})
        return result
    except Exception as e:
        logger.warning("Tool %s failed: %s", tool_name, e)
        await self._publish_event(
            "tool.failed", user_id, {"tool": tool_name, "error": str(e)[:200]},
        )
        return {"error": f"Tool execution failed for {tool_name}: {e}"}
```

### Backend dispatch table

| Backend | Dispatch target | Example tools |
|---------|----------------|---------------|
| `internal_mcp` | `_call_internal_tool()` with `server_prefix` from registry | `search`, `send_telegram`, `evaluate_policy` |
| `internal_mcp` + `_special` | Return input as-is | `report_governor_verdict` |
| `external_mcp` | `call_mcp_tool()` with real MCP name | `sendGmailDraft`, `API-post-page`, `linear_create_issue` |
| `composite` | `_call_composite_tool()` | `web_search` |

---

## 6. `_call_internal_tool()` — Add `server_prefix` Parameter

**File:** `src/orchestrator/jarvis.py`

The existing method uses `_INTERNAL_TOOL_SERVER.get(tool_name, "intelligence")` for namespace resolution. Add an optional `server_prefix` parameter so the unified dispatch path can pass the registry's `server` field directly:

```python
async def _call_internal_tool(
    self, tool_name: str, tool_input: dict, server_prefix: str | None = None
) -> dict:
    # ... existing lazy-init unchanged ...

    # Map flat name to namespaced name
    if server_prefix is not None:
        namespaced = f"{server_prefix}_{tool_name}"
    else:
        prefix = _INTERNAL_TOOL_SERVER.get(tool_name, "intelligence")
        namespaced = f"{prefix}_{tool_name}"

    result = await self._internal_client.call_tool(namespaced, tool_input)
    # ... existing result parsing unchanged ...
```

- Flag OFF: no `server_prefix` passed, uses `_INTERNAL_TOOL_SERVER` (unchanged)
- Flag ON: `server_prefix=tool.server` from registry replaces `_INTERNAL_TOOL_SERVER`
- Phase 15 removes the `_INTERNAL_TOOL_SERVER` fallback and makes `server_prefix` required

### Why not pass a pre-namespaced name?

The spec pseudocode (Section 3.5) shows `self._call_internal_tool(mcp_name, tool_input)` with `mcp_name` already namespaced. However, the current `_call_internal_tool` always prefixes its input. Passing a pre-namespaced name would cause double-prefixing (e.g., `intelligence_intelligence_search`). The `server_prefix` parameter avoids this while keeping the flag-off path untouched.

---

## 7. `_call_composite_tool()` — Extracted Handler

**File:** `src/orchestrator/jarvis.py`

Extract the `web_search` special-case from `_execute_tool()` into its own method:

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

- `web_search` is currently the only composite tool
- The old `web_search` handler in `_execute_tool()` stays intact for the flag-off path
- Extensible for future composite tools

---

## 8. `can_use_tool_unified()` on SubAgent

**File:** `src/orchestrator/agents.py`

Per spec Section 3.6: one registry lookup for capability, no normalizer fallback.

```python
async def can_use_tool_unified(self, tool_name: str, db: AsyncSession) -> bool:
    """Registry-driven capability check. One lookup, no normalizer."""
    if not self.capability_scope:
        return False
    from src.services.tool_registry import ToolRegistry
    registry = ToolRegistry(db)
    tool = await registry.get_tool(tool_name)
    if tool and tool.capability:
        return tool.capability in self.capability_scope
    return False
```

### Async constraint

`ToolRegistry.get_tool()` is async (DB access). The existing `can_use_tool()` is sync. Making it async would change its signature. Since `can_use_tool()` has exactly one call site (jarvis.py line 627 in `_get_tools_for_agent()`), the approach is:

- Keep `can_use_tool()` sync for flag-off path (unchanged)
- Add `can_use_tool_unified()` as async
- Add `_get_tools_for_agent_unified()` as async on the orchestrator (Section 9)
- Call sites check the flag and call the appropriate method

Phase 15 collapses to a single async path.

---

## 9. `_get_tools_for_agent_unified()` — Async Tool Filtering

**File:** `src/orchestrator/jarvis.py`

The existing `_get_tools_for_agent()` is sync and calls `can_use_tool()` synchronously. The unified path needs an async equivalent:

```python
async def _get_tools_for_agent_unified(
    self, agent: SubAgent, workspace_id: str = ""
) -> list[dict]:
    """Filter tool definitions using registry-driven capability check."""
    from src.connectors.mcp_bridge import list_mcp_tools

    tools = list(self._tools)
    for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
        schema = mcp_tool.get("input_schema", {})
        tools.append({
            "name": mcp_tool["name"],
            "description": mcp_tool.get("description", "External MCP tool"),
            "input_schema": schema if schema else {"type": "object", "properties": {}},
        })

    async with self._db_factory() as db:
        return [t for t in tools if await agent.can_use_tool_unified(t["name"], db)]
```

Call sites (lines 1221 and 2437) become:

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

---

## 10. `is_auto_execute_tool()` on Governor

**File:** `src/services/governor.py`

Per spec Section 3.7: derives from registry `risk_level` + `requires_approval`.

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

- `AUTO_EXECUTE_ACTIONS` stays unchanged — it's decision-level policy (Planner routing)
- `is_auto_execute_tool()` is tool-level policy (individual tool calls)
- Rename to `AUTO_EXECUTE_DECISIONS` happens in Phase 15

---

## 11. Seed Fix: `web_search` Backend

**File:** `src/services/tool_registry.py`

Phase 9's seed_defaults hardcodes `backend = "external_mcp"` for ALL external seeds (line 346). But `web_search` has `server="_composite"` in the catalog. Without this fix, Phase 11's match dispatch would route `web_search` to `call_mcp_tool()` instead of `_call_composite_tool()`.

**Fix in seed_defaults Pass 2:**

```python
# Before (Phase 9):
backend = "external_mcp"

# After (Phase 11):
backend = "composite" if seed.server == "_composite" else "external_mcp"
```

This ensures `web_search` gets `backend="composite"` in the DB.

---

## 12. Tests

### Existing tests (flag OFF)
All 1174 existing tests pass unchanged. Flag defaults to `False`.

### New tests (flag ON)

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_execute_tool_unified_internal` | Internal tool dispatches via `_call_internal_tool` with `server_prefix` from registry |
| 2 | `test_execute_tool_unified_special` | `report_governor_verdict` returns input as-is via `_special` backend |
| 3 | `test_execute_tool_unified_external` | External tool dispatches via `call_mcp_tool` with real name |
| 4 | `test_execute_tool_unified_composite` | `web_search` dispatches via `_call_composite_tool` |
| 5 | `test_execute_tool_unified_unknown` | Unknown tool returns error dict |
| 6 | `test_execute_tool_unified_disabled` | Disabled tool returns blocked error |
| 7 | `test_flag_off_uses_old_dispatch` | Flag OFF: existing 6-step cascade runs |
| 8 | `test_flag_on_uses_unified_dispatch` | Flag ON: `_execute_tool_unified` called |
| 9 | `test_can_use_tool_unified_with_capability` | Tool with matching capability returns True |
| 10 | `test_can_use_tool_unified_no_capability` | Tool without matching capability returns False |
| 11 | `test_can_use_tool_unified_unknown_tool` | Unknown tool returns False |
| 12 | `test_is_auto_execute_tool_low_risk` | Low risk + no approval required returns True |
| 13 | `test_is_auto_execute_tool_high_risk` | High risk returns False |
| 14 | `test_is_auto_execute_tool_requires_approval` | Low risk + requires approval returns False |

---

## 13. Exit Criteria

Per spec:
- [ ] All existing tests pass with flag OFF (default)
- [ ] All new tests pass with flag ON
- [ ] Internal tools dispatch correctly via registry server prefix
- [ ] External tools dispatch via real MCP names
- [ ] `web_search` dispatches via composite handler
- [ ] `report_governor_verdict` returns input as-is
- [ ] `can_use_tool_unified()` produces same results as `can_use_tool()` for all agents
- [ ] `is_auto_execute_tool()`: low-risk tools auto-execute, high-risk don't
- [ ] `web_search` seed has `backend="composite"` in DB

## 14. Rollback

Set `JARVIS_USE_UNIFIED_DISPATCH=false`. Zero code changes needed.
