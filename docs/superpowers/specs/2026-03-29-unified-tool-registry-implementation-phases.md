# Unified Tool Registry — Implementation Phases

**Date:** 2026-03-29
**Status:** Pending review
**Parent spec:** [unified-tool-registry-design.md](./2026-03-29-unified-tool-registry-design.md)
**Approach:** Logical Layers — 17 phases, each independently deployable and reversible

---

## Overview

This document decomposes the Unified Tool Registry design spec into 17 incremental phases grouped into 7 layers. Each phase is a standalone commit/PR that builds on the previous layer without breaking existing behavior.

**Guiding principles:**
- No big-bang cutover — every phase is independently deployable
- Feature flag guards behavioral changes (Phases 11–12)
- Old code paths remain functional until explicitly deleted (Phases 15–17)
- Each phase has clear entry/exit criteria and a rollback strategy

**Layer map:**

| Layer | Phases | Theme | Risk |
|-------|--------|-------|------|
| **Foundation** | 1–5 | Bug fixes to existing code | Minimal — fixes known bugs |
| **Catalog** | 6–7 | New data structures (parallel path) | None — additive only |
| **Database** | 8–9 | Schema migration + seeding | Low — additive columns |
| **Validation** | 10 | Startup cross-checks | Low — can disable on failure |
| **Dispatch** | 11–12 | Feature-flagged behavioral switch | Medium — behind flag |
| **Prompts** | 13 | Agent prompt cleanup | Low — no code logic change |
| **Native Elimination** | 14 | Native connectors → MCP | Medium — blocked on GWS verification |
| **Cleanup** | 15–17 | Delete old code, remove flag | Low — only after all above stable |

**Dependency graph:**

```
Phase 1 ─┐
Phase 2 ─┤
Phase 3 ─┼─► Phase 6 ─► Phase 7 ─► Phase 8 ─► Phase 9 ─► Phase 10 ─► Phase 11 ─► Phase 12 ─► Phase 15 ─► Phase 16 ─► Phase 17
Phase 4 ─┤                                                                │
Phase 5 ─┘                                                                ├─► Phase 13 (independent after 11)
                                                                          │
                                                              Phase 14 (blocked on GWS verification, independent)
                                                                          │
                                                                          └─► Phase 17 (Phase 14 completes native cleanup)

Note: Phase 14 is NOT a blocker for Phases 15-17. The feature flag can be made permanent
even with native connectors still present — they become unreachable dead code that Phase 14
cleans up whenever GWS verification completes. Phase 14 can run before OR after Phase 15.
```

**Estimated totals:**
- ~1000+ lines deleted
- ~350 lines added
- ~650+ net lines removed
- 8 files eliminated, 6 files significantly reduced, 1 new file created

---

## Layer 1: FOUNDATION (Phases 1–5)

*Bug fixes to existing code. No new architecture. Each phase fixes real bugs identified in the design spec Section 9.7.*

**Goal:** Make the existing system correct before building the new one on top.

---

### Phase 1: Remove Orphan Tool Schemas

**Problem:** 3 tools (`create_task`, `get_task`, `get_goals`) are presented to Claude but have no MCP implementation — calling them fails with an MCP error. They waste Claude's tool budget (3 of 18 slots).

**Spec reference:** Section 9.3 (Orphan Schemas), Section 9.7 bug #3

**Changes:**

| File | Change |
|------|--------|
| `orchestrator/tool_schemas.py` | Remove `CreateTaskInput`, `GetTaskInput`, `GetGoalsInput` Pydantic models. Remove their entries from `TOOL_INPUT_MODELS` dict. |
| `orchestrator/jarvis.py` | Remove `create_task`, `get_task`, `get_goals` from `internal_tools` set (~line 2516) |
| `integrations/capabilities.py` | Remove `create_task`, `get_task`, `get_goals` entries from `TOOL_TO_CAPABILITY` |
| `services/tool_registry.py` | Remove their entries from `_DEFAULT_TOOLS` |

**Tests:**
- Verify `build_tool_definitions()` returns 15 tools (was 18)
- Verify `internal_tools` set has 14 entries (was 17)
- Existing tests pass unchanged

**Exit criterion:** Claude sees 15 internal tools. No tool call failures from orphan dispatch.
**Rollback:** Revert single commit.
**~Lines:** -40

---

### Phase 2: Add Missing get_goal_memories Tool

**Problem:** `get_goal_memories` exists in the intelligence MCP server (live-verified) but is invisible to Claude — no Pydantic schema, no `internal_tools` entry, no capability mapping.

**Spec reference:** Section 9.1 (tool #13), Section 9.7 bug #1

**Changes:**

| File | Change |
|------|--------|
| `orchestrator/tool_schemas.py` | Add `GetGoalMemoriesInput` Pydantic model (fields: `user_id`, `workspace_id` — both excluded from Claude schema). Add entry in `TOOL_INPUT_MODELS`: `"get_goal_memories": GetGoalMemoriesInput` |
| `orchestrator/jarvis.py` | Add `get_goal_memories` to `internal_tools` set |
| `integrations/capabilities.py` | Add to `TOOL_TO_CAPABILITY`: `"get_goal_memories": "internal.get_goals"` |

**Design note:** The `internal.get_goals` capability already exists in `CAPABILITY_CATALOG` and is in the Planner's scope. No capability catalog or agent scope changes needed.

**Tests:**
- Verify `get_goal_memories` appears in `build_tool_definitions()` output
- Verify dispatching `get_goal_memories` calls `intelligence_get_goal_memories` on the MCP server
- Existing tests pass unchanged

**Exit criterion:** Claude can call `get_goal_memories` and receive goal memories.
**Rollback:** Revert single commit.
**~Lines:** +25

---

### Phase 3: Fix Communication Tool Dispatch

**Problem:** 3 communication tools (`send_telegram`, `send_approval_prompt`, `push_ui_update`) exist in the MCP server but can't be dispatched. `_call_internal_tool()` hardcodes `intelligence_` prefix (line ~2651 of jarvis.py), but these tools live under the `communication` namespace and need `communication_` prefix.

**Spec reference:** Section 9.2, Section 9.6, Section 9.7 bug #2

**Changes:**

| File | Change |
|------|--------|
| `orchestrator/jarvis.py` | (1) Add a temporary server-prefix mapping dict: `_INTERNAL_TOOL_SERVER = {"send_telegram": "communication", "send_approval_prompt": "communication", "push_ui_update": "communication"}` (default: `"intelligence"`). This is a stopgap — Phase 11 replaces it with a registry lookup using the `server` field from `INTERNAL_TOOLS`. (2) Fix `_call_internal_tool()`: `prefix = _INTERNAL_TOOL_SERVER.get(tool_name, "intelligence")` → `namespaced = f"{prefix}_{tool_name}"`. (3) Add all 3 tools to `internal_tools` set. |
| `orchestrator/tool_schemas.py` | Add `SendTelegramInput`, `SendApprovalPromptInput`, `PushUiUpdateInput` Pydantic models. Add entries in `TOOL_INPUT_MODELS`. Note: `send_telegram` and `send_approval_prompt` do NOT take `user_id` — they use bot tokens directly. `push_ui_update` takes `user_id` for Redis channel routing. |
| `integrations/capabilities.py` | Add to `TOOL_TO_CAPABILITY`: `"send_telegram": "internal.send_telegram"`, `"send_approval_prompt": "internal.send_approval"`, `"push_ui_update": "internal.push_ui"`. These capabilities already exist in `CAPABILITY_CATALOG`. |

**Tests:**
- Verify `_call_internal_tool("send_telegram", ...)` calls `communication_send_telegram` (not `intelligence_send_telegram`)
- Verify all 3 tools appear in `build_tool_definitions()` output
- Verify Presenter agent `can_use_tool("send_telegram")` returns True (it has `internal.send_telegram` in scope)
- Existing tests pass unchanged

**Exit criterion:** Claude can call all 3 communication tools. `send_telegram` sends a real Telegram message.
**Rollback:** Revert single commit.
**~Lines:** +60

---

### Phase 4: Fix External MCP Seed Bugs

**Problem:** 3 MCP servers have broken seeds (won't start), and 2 servers have wrong tool names in the registry. These bugs prevent tool dispatch for Notion and Linear, and block Google Workspace/Slack servers entirely.

**Spec reference:** Section 10.5 (Seed Installation Bugs), Section 9.7 bugs #8–#12

**Changes:**

| File | Change |
|------|--------|
| `integrations/seed_installations.py` | (1) Fix Google Workspace: `args: ["google-workspace-mcp"]` → `args: ["google-workspace-worker"]`. (2) Fix Slack: `SLACK_BOT_TOKEN` → `SLACK_MCP_XOXP_TOKEN` (and `SLACK_MCP_XOXB_TOKEN` as fallback). |
| `services/tool_registry.py` | (1) Fix Notion names in `_DEFAULT_TOOLS`: `create-a-page` → `API-post-page`, `update-a-page` → `API-patch-page`, `retrieve-a-page` → `API-retrieve-a-page`, `create-a-comment` → `API-create-a-comment`, `append-block-children` → `API-patch-block-children`, `query-data-source` → `API-query-data-source`. (2) Fix Linear aliases: `linear_comment` → `linear_create_comment`, `linear_list_issues` → `linear_search_issues`. |
| `integrations/capabilities.py` | Update `TOOL_TO_CAPABILITY` entries to match corrected names: replace old Notion names with `API-*` prefixed versions. Replace old Linear names with correct names. |

**Tests:**
- Verify seed_installations builds valid configs for Google Workspace and Slack
- Verify `_DEFAULT_TOOLS` Notion entries use `API-*` prefix
- Verify `TOOL_TO_CAPABILITY` resolves `API-post-page` → `doc.create`
- Existing tests pass unchanged

**Exit criterion:** All seed installations startable (where credentials exist). Notion and Linear tool dispatch uses correct real MCP names.
**Rollback:** Revert single commit.
**~Lines:** ±30

---

### Phase 5: Add Missing Capabilities + Fix Agent Scopes

**Problem:** 14 Filesystem tools completely unmapped (no capability family exists). 17 Linear tools, 7 Playwright tools unmapped. Observer/Operator/Researcher missing `filesystem.*` scopes. Operator missing `calendar.delete` and `workflow.delete`.

**Spec reference:** Section 3.11, Section 9.8, Section 9.7 bugs #11–#15

**Changes:**

| File | Change |
|------|--------|
| `integrations/capabilities.py` | (1) Add `FILESYSTEM = "filesystem"` to `CapabilityFamily` enum. (2) Add ~30 new capabilities to `CAPABILITY_CATALOG`: filesystem.read, filesystem.write, filesystem.move, filesystem.list, filesystem.search, filesystem.read_media, browser.execute, browser.install, browser.navigate_back, browser.wait, workflow.create_issues, workflow.bulk_update, workflow.search_by_id, workflow.update_comment, workflow.delete_comment, workflow.resolve_comment, workflow.unresolve_comment, workflow.get_user, workflow.get_project, workflow.list_projects, workflow.create_project, workflow.create_milestone, workflow.get_milestones, workflow.update_milestone, workflow.delete_milestone, workflow.create_customer_need, workflow.auth, doc.get_property, doc.get_comment, doc.get_children, doc.get_block, doc.update_block, doc.delete_block, doc.move, doc.get_database, doc.create_datasource, doc.get_datasource, doc.update_datasource, doc.list_templates, doc.get_self, doc.get_user, doc.get_users. (3) Add ~50 entries to `TOOL_TO_CAPABILITY` for all live-verified Filesystem (14), Playwright (7 new), Linear (17 new), Notion (16 new) tools. |
| `orchestrator/agents.py` | (1) Add `filesystem.read`, `filesystem.list`, `filesystem.search` to Observer scope. (2) Add `filesystem.*`, `calendar.delete`, `workflow.delete`, `workflow.delete_comment`, `workflow.delete_milestone` to Operator scope. (3) Add `filesystem.read`, `filesystem.list`, `filesystem.search` to Researcher scope. (4) Add new Linear/Notion read capabilities to Observer and Researcher scopes where appropriate. |

**Tests:**
- Verify `CapabilityFamily.FILESYSTEM` exists
- Verify `get_capability_for_tool("read_text_file")` returns `"filesystem.read"`
- Verify `get_capability_for_tool("browser_take_screenshot")` returns `"browser.screenshot"`
- Verify Observer `can_use_tool("read_text_file")` returns True
- Verify Operator `can_use_tool("linear_delete_issue")` returns True
- Existing tests pass unchanged

**Exit criterion:** All 82 live-verified external tools have capability mappings. Agent scopes cover all tool families.
**Rollback:** Revert single commit.
**~Lines:** +120

---

### Foundation Layer Exit Criteria (after Phase 5)

- [ ] Claude sees 19 internal tools (15 intelligence + 3 communication + 1 report_governor_verdict)
- [ ] All 3 communication tools dispatchable with correct `communication_` prefix
- [ ] `get_goal_memories` callable by Claude
- [ ] 0 orphan schemas in `TOOL_INPUT_MODELS`
- [ ] All MCP seed installations have correct executables and env vars
- [ ] All 82 live-verified external tools have capability mappings
- [ ] All agents have complete capability scopes for their role
- [ ] All existing tests pass

---

## Layer 2: CATALOG (Phases 6–7)

*New data structures that exist alongside old code. No behavior changes — the catalog is a parallel source of truth that will later replace the scattered definitions.*

**Goal:** Create the single source of truth (`catalog.py`) without touching any existing behavior.

---

### Phase 6: Create catalog.py — Internal Tool Definitions

**Problem:** Internal tool identity is scattered across 8 files. We need a single source of truth.

**Spec reference:** Section 3.3 (Population 1), Section 4.3

**Changes:**

| File | Change |
|------|--------|
| `src/tools/catalog.py` (**NEW**) | (1) Define `InternalToolDef` frozen dataclass with fields: `name`, `input_model` (type[BaseModel]), `capability`, `risk_level` (default "low"), `requires_approval` (default False), `server` (default "intelligence"), `description` (default ""), `read_only` (default False). (2) Define `INTERNAL_TOOLS: list[InternalToolDef]` with 19 entries (15 intelligence + 3 communication + 1 special). Each entry references the existing Pydantic model from `tool_schemas.py`. (3) Add helper functions: `get_internal_tool_names() -> set[str]`, `get_internal_tool_by_name(name) -> InternalToolDef | None`, `get_internal_tools_for_server(server) -> list[InternalToolDef]`. |

**Design decisions:**
- Pydantic models stay in `tool_schemas.py` for now — they move in Phase 16. This avoids import changes across the codebase during the catalog phase.
- `catalog.py` imports from `tool_schemas.py`, not the other way around. One-directional dependency.
- `report_governor_verdict` gets `server="_special"` to mark it as inline-dispatched (not a real MCP tool).
- The `read_only` field mirrors MCP `readOnlyHint` annotations from the live probe.

**Tests:**
- Verify `len(INTERNAL_TOOLS) == 19`
- Verify `get_internal_tool_names()` matches the `internal_tools` set in jarvis.py
- Verify every `input_model` reference is a valid Pydantic BaseModel subclass
- Verify server field: 15 tools with "intelligence", 3 with "communication", 1 with "_special"

**Exit criterion:** `catalog.py` importable. `INTERNAL_TOOLS` exactly matches the 19 tools across intelligence + communication servers.
**Rollback:** Delete `catalog.py`.
**~Lines:** +120

---

### Phase 7: Add External Tool Seeds to catalog.py

**Problem:** External tool registration is scattered across `_DEFAULT_TOOLS` (150 entries with wrong names), `TOOL_TO_CAPABILITY` (361 entries with aliases), and `seed_installations.py`. We need a single seed list with correct real MCP names.

**Spec reference:** Section 3.3 (Population 2), Section 10.1–10.4

**Changes:**

| File | Change |
|------|--------|
| `src/tools/catalog.py` | (1) Define `ExternalToolSeed` frozen dataclass with fields: `name` (real MCP name), `capability`, `risk_level` (default "medium"), `requires_approval` (default True), `server` (MCP server name), `verified` (default False — True only for live-probed tools). (2) Define `EXTERNAL_TOOL_SEEDS: list[ExternalToolSeed]` with ~116 entries organized by server. (3) Add helper: `get_seeds_for_server(server) -> list[ExternalToolSeed]`, `get_verified_seeds() -> list[ExternalToolSeed]`. |

**Seed entries by server:**

| Server | Count | Verified? | Naming convention |
|--------|-------|-----------|-------------------|
| Google Workspace | 11 | No (broken seed) | camelCase |
| GitHub | 8 | No (no PAT) | snake_case |
| Slack | 8 | No (wrong env var) | prefixed snake |
| Notion | 22 | **Yes** | `API-` prefixed kebab |
| Linear | 24 | **Yes** | `linear_` prefixed snake |
| Playwright | 22 | **Yes** | `browser_` prefixed snake |
| Filesystem | 14 | **Yes** | snake_case |
| Atlassian (Jira) | 6 | No (needs OAuth) | camelCase |
| Composite | 1 (`web_search`) | N/A | snake_case |
| **Total** | **116** | **82 verified** | |

**Tests:**
- Verify `len(EXTERNAL_TOOL_SEEDS) == 116`
- Verify `len(get_verified_seeds()) == 82`
- Verify no duplicate names within same server
- Verify every seed has a non-empty capability string
- Verify Notion seeds all start with `API-`
- Verify Linear seeds all start with `linear_`

**Exit criterion:** `EXTERNAL_TOOL_SEEDS` has 116 entries. 82 marked as verified. Zero duplicate names per server.
**Rollback:** Revert additions to `catalog.py`.
**~Lines:** +200

---

### Catalog Layer Exit Criteria (after Phase 7)

- [ ] `catalog.py` exists at `src/tools/catalog.py`
- [ ] `INTERNAL_TOOLS` has exactly 19 entries
- [ ] `EXTERNAL_TOOL_SEEDS` has exactly 116 entries (82 verified, 34 unverified)
- [ ] All helpers (`get_internal_tool_names()`, `get_seeds_for_server()`, etc.) work
- [ ] No existing behavior changed — old code paths untouched
- [ ] All existing tests pass

---

## Layer 3: DATABASE (Phases 8–9)

*Schema migration and catalog-driven seeding. Existing data preserved. New columns are additive — no dropped columns.*

**Goal:** Enhance `tool_definitions` table and populate it from the catalog.

---

### Phase 8: DB Migration — Enhance tool_definitions Table

**Problem:** The `tool_definitions` table lacks fields needed for unified dispatch: `capability`, `server`, `backend`, `source`, `verified`.

**Spec reference:** Section 3.4

**Changes:**

| File | Change |
|------|--------|
| `alembic/versions/xxx_add_unified_registry_columns.py` (**NEW**) | Add columns to `tool_definitions`: `capability VARCHAR`, `server VARCHAR`, `backend VARCHAR DEFAULT 'external_mcp'`, `source VARCHAR DEFAULT 'seed'`, `verified BOOLEAN DEFAULT FALSE`. Drop `canonical_name` column if it exists. Add UNIQUE constraint on `(workspace_id, name)`. |
| `models/tool_definitions.py` | Add corresponding SQLAlchemy mapped columns: `capability`, `server`, `backend`, `source`, `verified`. |

**Design decisions:**
- All new columns are nullable or have defaults — existing rows remain valid.
- `backend` defaults to `'external_mcp'` (most tools are external).
- `source` defaults to `'seed'` (most tools come from seeds).
- `canonical_name` dropped — normalization concept eliminated.
- UNIQUE constraint on `(workspace_id, name)` prevents duplicate tool names per workspace.

**Tests:**
- Verify migration applies cleanly on existing database
- Verify existing `tool_definitions` rows still queryable
- Verify reverse migration drops new columns cleanly
- Verify UNIQUE constraint prevents duplicate `(workspace_id, name)` pairs

**Exit criterion:** Migration applies. Existing data preserved. New columns populated with defaults.
**Rollback:** Run reverse migration (drops new columns).
**~Lines:** +50

---

### Phase 9: Catalog-Driven Seed Function

**Problem:** `ToolRegistry.seed_defaults()` reads from `_DEFAULT_TOOLS` (150 entries with wrong names). It should read from the catalog instead.

**Spec reference:** Section 5 (Seed-Sync Flow)

**Changes:**

| File | Change |
|------|--------|
| `services/tool_registry.py` | Modify `seed_defaults()` to: (1) Read `INTERNAL_TOOLS` from `catalog.py` → upsert with `backend="internal_mcp"`, `source="internal"`, `server` from tool def, `capability` from tool def, `input_schema` from `model.model_json_schema()`. (2) Read `EXTERNAL_TOOL_SEEDS` from `catalog.py` → upsert with `backend="external_mcp"`, `source="seed"`, `server` from seed, `capability` from seed, `verified` from seed. (3) Keep `_DEFAULT_TOOLS` as a fallback for tools not in catalog (gradually sunset). |

**Design decisions:**
- Catalog-sourced entries take precedence over `_DEFAULT_TOOLS`.
- `_DEFAULT_TOOLS` remains as a fallback — deleted in Phase 16.
- The `input_schema` for internal tools is generated from `input_model.model_json_schema()` with `user_id`/`workspace_id` fields excluded (same exclusion pattern as `build_tool_definitions()`).
- Upsert uses `(workspace_id, name)` as the match key.

**Tests:**
- Verify `seed_defaults()` creates 19 internal tool records with `backend="internal_mcp"`
- Verify `seed_defaults()` creates 116 external seed records with `backend="external_mcp"`
- Verify existing per-workspace overrides are NOT overwritten by seeds
- Verify `capability` column populated for all seeded tools

**Exit criterion:** `tool_definitions` table has all 135 seed entries after startup. Capability column populated.
**Rollback:** Revert seed function changes (old `_DEFAULT_TOOLS` path still works).
**~Lines:** +60, -0 (old code kept as fallback)

---

### Database Layer Exit Criteria (after Phase 9)

- [ ] `tool_definitions` table has new columns: `capability`, `server`, `backend`, `source`, `verified`
- [ ] All 135 catalog entries seeded into DB
- [ ] 19 internal tools with `backend="internal_mcp"`, `source="internal"`
- [ ] 116 external seeds with `backend="external_mcp"`, `source="seed"`
- [ ] Existing per-workspace overrides preserved
- [ ] All existing tests pass

---

## Layer 4: VALIDATION (Phase 10)

*Startup cross-checks that catch inconsistencies early. Can be disabled on failure without affecting dispatch.*

**Goal:** Fail loud on inconsistencies instead of failing silently at runtime.

---

### Phase 10: Startup Validation

**Problem:** No cross-validation exists between tool registrations, capability mappings, and agent scopes. Inconsistencies are only discovered at runtime (silent failures).

**Spec reference:** Section 3.8

**Changes:**

| File | Change |
|------|--------|
| `src/tools/catalog.py` (or `src/tools/validation.py` if catalog.py is getting large) | Add `async def validate_registry(registry, agent_scopes, capability_catalog)` function with 6 checks: (1) Every tool with a capability references a known capability in `CAPABILITY_CATALOG`. (2) Every capability in agent scopes exists in `CAPABILITY_CATALOG`. (3) Every internal tool has a non-null capability. (4) Critical-risk tools require approval. (5) Internal tools exist in their MCP server (verify via `Client(jarvis_tools).list_tools()`). (6) readOnly tools have `risk_level="low"`. |
| `src/tools/__init__.py` or startup path | Wire `validate_registry()` into application startup (after seeding, before accepting requests). Add `JARVIS_SKIP_REGISTRY_VALIDATION` escape hatch setting for emergencies. |

**Design decisions:**
- Validation runs after `seed_defaults()` completes.
- All errors are collected (not fail-on-first) and reported together.
- Validation can be skipped via environment variable for emergencies — but logs a loud warning.
- The MCP server check (item 5) uses the in-process `Client(jarvis_tools)` — zero network overhead.

**Tests:**
- Verify validation passes with correct catalog
- Verify validation catches: unknown capability on tool, unknown capability in agent scope, internal tool without capability, critical tool without approval, missing MCP tool
- Verify `JARVIS_SKIP_REGISTRY_VALIDATION=true` skips validation with warning

**Exit criterion:** Startup validation runs and passes. Intentional inconsistencies are caught.
**Rollback:** Set `JARVIS_SKIP_REGISTRY_VALIDATION=true` or revert commit.
**~Lines:** +80

---

## Layer 5: DISPATCH (Phases 11–12)

*The core behavioral switch, protected by a feature flag. Both old and new dispatch paths coexist. The flag defaults to off — no behavior change until explicitly enabled.*

**Goal:** Replace the 6-step dispatch cascade with a 3-backend match dispatch.

---

### Phase 11: Feature Flag + Registry-Driven Dispatch

**Problem:** `_execute_tool()` in jarvis.py uses a 6-step dispatch cascade (~130 lines) that requires tools to be registered in multiple places. The new dispatch does one registry lookup and one match on `backend`.

**Spec reference:** Section 3.5, Section 6.7 (Phase 2)

**Changes:**

| File | Change |
|------|--------|
| `src/config/settings.py` | Add `JARVIS_USE_UNIFIED_DISPATCH: bool = False` setting |
| `orchestrator/jarvis.py` | (1) Add new `_execute_tool_unified()` method implementing 3-backend match dispatch (Section 3.5 of spec): `internal_mcp` → `_call_internal_tool()` with server prefix from registry, `external_mcp` → `call_mcp_tool()` with real name, `composite` → `_call_composite_tool()`, `_special` → return input as-is. (2) Modify `_execute_tool()` to check flag: if unified dispatch enabled, call `_execute_tool_unified()`; else, existing 6-step cascade. (3) Add `_call_composite_tool()` for `web_search` (extracted from current special-case handling). |
| `orchestrator/agents.py` | Add new `can_use_tool_unified()` method on SubAgent: one registry lookup for capability, no normalizer fallback. Modify `can_use_tool()` to delegate based on flag. |
| `services/governor.py` | Add `async def is_auto_execute_tool(tool_name)` method: derives from registry `risk_level` + `requires_approval` instead of hardcoded set. Keep `AUTO_EXECUTE_ACTIONS` for decision-level policy (unchanged). |

**Design decisions:**
- The feature flag is a `pydantic-settings` field — set via `JARVIS_USE_UNIFIED_DISPATCH=true` env var.
- Both paths coexist. Flag defaults to `False`. Zero behavior change on deploy.
- `_execute_tool_unified()` reads `backend` and `server` from the registry (DB, cached).
- `_call_composite_tool()` is extracted from the current `web_search` special case in `_execute_tool()`.
- `report_governor_verdict` handled via `server="_special"` → returns input as-is (same behavior, registry-driven).

**Tests:**
- Test with flag OFF: all existing tests pass unchanged
- Test with flag ON: internal tools dispatch correctly, external tools dispatch via real names, `web_search` dispatches via composite handler, `report_governor_verdict` returns input
- Test `can_use_tool_unified()`: same results as `can_use_tool()` for all agents
- Test `is_auto_execute_tool()`: low-risk tools auto-execute, high-risk tools don't

**Exit criterion:** All tests pass with flag both ON and OFF. Manual smoke test of internal + external tool calls with flag ON.
**Rollback:** Set `JARVIS_USE_UNIFIED_DISPATCH=false`. Zero code changes needed.
**~Lines:** +150, -0 (old code preserved)

---

### Phase 12: Session Pool De-Normalization

**Problem:** `session_pool.py` normalizes MCP tool names (camelCase → snake_case) and maintains bidirectional mappings. With unified dispatch, real MCP names flow through end-to-end — no normalization needed.

**Spec reference:** Section 3.10

**Changes:**

| File | Change |
|------|--------|
| `integrations/session_pool.py` | (1) Add flag check: when unified dispatch enabled, skip normalization in `get_or_create_session()`. Store real tool names directly in `_server_tools` and `_tool_metadata`. (2) In `call_tool()`: when unified dispatch enabled, skip `canonical → raw` translation — `tool_name` is already the real name. (3) Register discovered unknown tools in DB via `registry.register_discovered()` with `capability=None` (invisible to agents until admin maps capability). |
| `connectors/mcp_bridge.py` | When unified dispatch enabled, skip normalizer calls in `call_mcp_tool()` and `is_mcp_tool()`. |

**Design decisions:**
- Same feature flag (`JARVIS_USE_UNIFIED_DISPATCH`) controls both dispatch and session pool behavior.
- When flag is OFF, normalization continues unchanged.
- Unknown discovered tools default to invisible (`capability=None`) — safe by design.
- The normalizer module is still imported but unused when flag is ON — deleted in Phase 16.

**Tests:**
- Test with flag OFF: normalization still works as before
- Test with flag ON: Notion tool `API-post-page` stored and dispatched as `API-post-page` (no conversion to `api_post_page`)
- Test discovered unknown tool: registered in DB with `source="discovered"`, `capability=None`
- Test unknown tool invisible to agents: `can_use_tool("unknown_tool")` returns False

**Exit criterion:** MCP tools dispatched using real names end-to-end (no normalization). Unknown tools auto-registered safely.
**Rollback:** Set `JARVIS_USE_UNIFIED_DISPATCH=false`.
**~Lines:** +40, -0 (old code preserved behind flag)

---

### Dispatch Layer Exit Criteria (after Phase 12)

- [ ] Feature flag `JARVIS_USE_UNIFIED_DISPATCH` exists, defaults to `false`
- [ ] With flag ON: 3-backend match dispatch works for all tool types
- [ ] With flag ON: session pool stores and dispatches real MCP names
- [ ] With flag ON: unknown MCP tools auto-registered with safe defaults
- [ ] With flag OFF: all existing behavior unchanged
- [ ] All tests pass with flag both ON and OFF

---

## Layer 6: PROMPTS (Phase 13)

*Remove hardcoded tool names from agent prompts. Agents discover tools via the MCP tool list in the Claude API request.*

**Goal:** Eliminate the "tool name ripple" problem where renaming a tool requires updating prompts.py.

---

### Phase 13: Remove Hardcoded Tool Names from Agent Prompts

**Problem:** Agent prompts in `prompts.py` hardcode specific tool names (`gmail_*`, `browser_*`, `search`, `slack_*`). Renaming a tool requires updating prompts — the "tool name ripple" problem.

**Spec reference:** Section 2.4

**Changes:**

| File | Change |
|------|--------|
| `orchestrator/prompts.py` | (1) **Observer prompt:** Replace `gmail_list_unread(max_results=20)`, `slack_get_channel_history`, `calendar_list`, etc. with capability-based descriptions: "check email for unread messages", "review recent Slack channel activity", "scan calendar for upcoming events". (2) **Researcher prompt:** Remove explicit `<tools>` section listing `search`, `web_search`, `browser_navigate`, `browser_snapshot`, `browser_screenshot`. Replace tool call examples with capability-based descriptions: "search internal knowledge", "search the web", "open URLs in browser, then snapshot page content". (3) **Governor prompt:** Remove `report_governor_verdict` reference — describe the behavior instead. (4) **Decision Framework:** Remove tool-specific examples like `Gmail, Calendar, GitHub, Slack` tied to `read_source`. Use generic "connected data sources" language. |

**Rewrite principles (from spec Section 2.4):**
- Prompts describe **capabilities and intent**, not tool names
- Prompts describe **workflow patterns**: "search internal first, then web, then deep-read URLs"
- Prompts use **behavioral examples**: describe expected behavior and output format
- Prompts do NOT contain: specific tool names, tool call syntax, `<tools>` sections, examples showing tool invocations

**Tests:**
- Verify `grep -rE "gmail_|calendar_|browser_|web_search|slack_|search\(" src/orchestrator/prompts.py` returns zero hits
- Manual smoke test: Researcher agent still uses correct tools for search-then-browse workflow
- Manual smoke test: Observer agent still reads from correct data sources
- Existing automated tests pass unchanged

**Exit criterion:** `prompts.py` contains zero hardcoded tool names. Agents discover tools via API tool list.
**Rollback:** Revert single commit.
**~Lines:** ±40 (roughly same line count, different content)

---

## Layer 7a: NATIVE ELIMINATION (Phase 14)

*Remove native connector code paths. Blocked on Google Workspace MCP verification.*

**Goal:** All tools served through MCP — no native connector code.

---

### Phase 14: Eliminate Native Connectors + Capability Resolver

**BLOCKED:** This phase requires Google Workspace MCP server to be verified (correct executable + `list_tools()` confirms feature parity for all 6 Gmail operations). Cannot proceed until Phase 4's seed fix is deployed and Google Workspace MCP is live-tested.

**Problem:** 6 Gmail tools have duplicate native connector handlers (`_NATIVE_TOOL_MAP`) that bypass MCP. The `CapabilityResolver` adds a redundant backend-selection layer. Both are eliminated by the unified registry.

**Spec reference:** Section 6.7 (Phase 3), Open Question #1 and #3

**Preconditions:**
1. Phase 4 deployed (Google Workspace seed fixed)
2. Google Workspace MCP server starts successfully
3. `list_tools()` returns tools with confirmed feature parity:
   - `listGmailMessages` ↔ `gmail_list_unread`
   - `readGmailMessage` ↔ `gmail_get_message`
   - `sendGmailDraft` ↔ `gmail_send_email`
   - `createGmailDraft` ↔ `gmail_create_draft`
   - `deleteGmailMessage` ↔ `gmail_archive`
   - `gmail_mark_read` — verify equivalent exists or accept gap

**Changes:**

| File | Change |
|------|--------|
| `orchestrator/jarvis.py` | Delete `_NATIVE_TOOL_MAP` dict (~15 lines). Delete `_try_native_connector()` method (~60 lines). Delete `_build_native_connector_tools()` method (~80 lines). Remove step 3 (native connector) from old `_execute_tool()` cascade. |
| `integrations/capability_resolver.py` | **DELETE entire file** (~295 lines). |
| Migration (**NEW**) | Drop `capability_bindings` table. |
| `orchestrator/jarvis.py` | Remove step 4 (capability resolver) from old `_execute_tool()` cascade. |

**Tests:**
- Verify Gmail send/draft/read/list operations work via Google Workspace MCP
- Verify no `_NATIVE_TOOL_MAP` references in codebase
- Verify no `capability_resolver` imports in codebase
- All existing tests pass (tests that mock native connectors need updating)

**Exit criterion:** Gmail operations work via MCP. No native connector code. No capability resolver.
**Rollback:** Restore native connector code from git. Re-run seed for `capability_bindings`.
**~Lines:** -450

---

## Layer 7b: CLEANUP (Phases 15–17)

*Delete old files and code blocks. Only after all above phases are stable and the feature flag has been battle-tested.*

**Goal:** Remove all redundant code. Single source of truth is `catalog.py` + `intelligence_server.py`.

---

### Phase 15: Make Feature Flag Permanent

**Problem:** With both dispatch paths proven and stable, the old path is dead code protected by a flag that's always ON.

**Precondition:** Phase 12 complete. Feature flag has been ON in production for sufficient time. Phase 14 is NOT required — native connector code becomes dead code (unreachable via unified dispatch) and can be cleaned up independently.

**Changes:**

| File | Change |
|------|--------|
| `orchestrator/jarvis.py` | Remove old `_execute_tool()` 6-step cascade code (~130 lines). `_execute_tool_unified()` renamed to `_execute_tool()`. Remove flag check. |
| `orchestrator/agents.py` | Remove old `can_use_tool()` normalizer chain. `can_use_tool_unified()` renamed to `can_use_tool()`. Remove flag check. |
| `integrations/session_pool.py` | Remove old normalization code path. Remove flag check. |
| `connectors/mcp_bridge.py` | Remove old normalizer calls. Remove flag check. |
| `src/config/settings.py` | Remove `JARVIS_USE_UNIFIED_DISPATCH` setting. |
| `services/governor.py` | Rename `AUTO_EXECUTE_ACTIONS` → `AUTO_EXECUTE_DECISIONS` for clarity (decision-level policy, not tool-level). |

**Tests:**
- All tests pass (no more flag-dependent branching)
- Verify `JARVIS_USE_UNIFIED_DISPATCH` no longer in settings

**Exit criterion:** Single dispatch path. No feature flag. `AUTO_EXECUTE_DECISIONS` renamed.
**Rollback:** Restore flag + old code paths from git.
**~Lines:** -200

---

### Phase 16: Delete Redundant Files

**Problem:** Several files are now fully superseded by `catalog.py` and the registry-driven dispatch.

**Precondition:** Phase 15 complete.

**Changes:**

| File | Action | Lines removed | Replaced by |
|------|--------|--------------|-------------|
| `orchestrator/tool_schemas.py` | **DELETE** | ~206 | Pydantic models moved to `catalog.py` (or `src/tools/schemas.py`) |
| `integrations/tool_normalizer.py` | **DELETE** | ~184 | No normalization — real names everywhere |
| `orchestrator/tool_policy.py` | **DELETE** | ~231 | Registry `risk_level` + `requires_approval` fields |
| `integrations/capability_resolver.py` | **DELETE** (if not already in Phase 14) | ~295 | Registry `backend` field |

**Pre-deletion steps:**
1. Move Pydantic models from `tool_schemas.py` into `catalog.py` (or a new `src/tools/schemas.py` if catalog gets too large). Update all imports.
2. Verify no remaining imports of deleted modules: `grep -r "tool_schemas\|tool_normalizer\|tool_policy\|capability_resolver" src/`
3. Update `__init__.py` files if they re-export anything from deleted modules.

**Tests:**
- All tests pass after deletion
- `grep -r "tool_schemas\|tool_normalizer\|tool_policy" src/` returns zero hits (excluding tests that may need updating)

**Exit criterion:** 4 files deleted. All imports updated. Zero broken references.
**Rollback:** Restore files from git.
**~Lines:** -620 (after moving Pydantic models)

---

### Phase 17: Remove Redundant Code Blocks + Final Cleanup

**Problem:** Several code blocks within remaining files are now superseded by the registry.

**Precondition:** Phase 16 complete.

**Changes:**

| File | Code block to remove | ~Lines |
|------|---------------------|--------|
| `integrations/capabilities.py` | `TOOL_TO_CAPABILITY` dict (~193 lines) + `get_capability_for_tool()` function | -200 |
| `services/tool_registry.py` | `_DEFAULT_TOOLS` list (~230 lines) + `CANONICAL_ALIASES` dict + `resolve_canonical()` function | -250 |
| `orchestrator/jarvis.py` | `internal_tools` set (~17 lines — now derived from `INTERNAL_TOOLS`) + `_INTERNAL_TOOL_SERVER` mapping (added in Phase 3, now in registry) | -20 |

**What stays in each file:**

| File | Keeps |
|------|-------|
| `integrations/capabilities.py` | `CapabilityFamily` enum, `CapabilityMeta` dataclass, `CAPABILITY_CATALOG` dict, `get_family_for_capability()`, `is_read_only_capability()` |
| `services/tool_registry.py` | `ToolRegistry` class (simplified to DB CRUD + seed from catalog) |
| `orchestrator/jarvis.py` | `_execute_tool()` (3-backend match), `_get_tools_for_agent()` (reads catalog + MCP bridge) |

**Final verification:**

```bash
# All tool identity concentrated in 2 files
grep -r "TOOL_TO_CAPABILITY\|_DEFAULT_TOOLS\|tool_normalizer\|internal_tools.*set\|_NATIVE_TOOL_MAP" src/
# Expected: zero hits

# No hardcoded tool names in prompts
grep -rE "gmail_|calendar_|browser_|web_search|slack_|search\(" src/orchestrator/prompts.py
# Expected: zero hits

# Only 2 files define tool identity
# catalog.py (definitions) + intelligence_server.py (implementations)
```

**Tests:**
- All tests pass
- `build_tool_definitions()` still returns correct tool list (now reads from catalog)
- Dispatch works for all tool types

**Exit criterion:** All 15 success criteria from the design spec (Section 8) met.
**Rollback:** Restore removed code blocks from git.
**~Lines:** -470

---

## Final Success Criteria (all 15 from design spec)

After all 17 phases complete:

- [ ] 1. Adding a new internal tool requires editing exactly 2 files: `catalog.py` + `intelligence_server.py`
- [ ] 2. Adding a new known external tool requires editing exactly 1 file: `catalog.py`
- [ ] 3. Unknown MCP tools auto-register in DB on discovery with safe defaults
- [ ] 4. Startup validation catches all cross-reference inconsistencies
- [ ] 5. Zero name normalization — real MCP names used everywhere
- [ ] 6. All tools served through MCP — no native connector code paths
- [ ] 7. Native Gmail tools eliminated — replaced by Google Workspace MCP
- [ ] 8. Only 3 backend types: `internal_mcp`, `external_mcp`, `composite`
- [ ] 9. All live-verified tool names match registry seeds
- [ ] 10. All tests pass after each migration phase
- [ ] 11. Net reduction of ~800+ lines
- [ ] 12. Unverified seeds flagged — auto-reconcile on first MCP connect
- [ ] 13. CapabilityResolver eliminated
- [ ] 14. `AUTO_EXECUTE_ACTIONS` renamed to `AUTO_EXECUTE_DECISIONS`
- [ ] 15. Agent prompts contain zero hardcoded tool names

---

## Quick Reference: Phase → Files Changed

| Phase | Files touched | Type |
|-------|--------------|------|
| 1 | tool_schemas, jarvis, capabilities, tool_registry | Remove |
| 2 | tool_schemas, jarvis, capabilities | Add |
| 3 | jarvis, tool_schemas, capabilities | Add + Fix |
| 4 | seed_installations, tool_registry, capabilities | Fix |
| 5 | capabilities, agents | Add |
| 6 | **catalog.py (NEW)** | Create |
| 7 | catalog.py | Add |
| 8 | **migration (NEW)**, tool_definitions model | Add |
| 9 | tool_registry | Modify |
| 10 | catalog.py or **validation.py (NEW)**, startup | Add |
| 11 | settings, jarvis, agents, governor | Add (flag-gated) |
| 12 | session_pool, mcp_bridge | Modify (flag-gated) |
| 13 | prompts | Rewrite |
| 14 | jarvis, **capability_resolver (DELETE)**, migration | Remove |
| 15 | jarvis, agents, session_pool, mcp_bridge, settings, governor | Remove flag |
| 16 | **tool_schemas (DELETE)**, **tool_normalizer (DELETE)**, **tool_policy (DELETE)** | Delete |
| 17 | capabilities, tool_registry, jarvis | Remove blocks |

---

## Estimated Effort Per Phase

| Phase | Complexity | Est. size | Notes |
|-------|-----------|-----------|-------|
| 1 | Trivial | -40 lines | Pure deletion |
| 2 | Simple | +25 lines | One model + two registrations |
| 3 | Medium | +60 lines | Dispatch fix + 3 new models |
| 4 | Simple | ±30 lines | Name corrections across files |
| 5 | Medium | +120 lines | Many new capabilities + scope fixes |
| 6 | Medium | +120 lines | New file with 19 structured entries |
| 7 | Medium | +200 lines | 116 seed entries (data-heavy) |
| 8 | Simple | +50 lines | Alembic migration + model update |
| 9 | Medium | +60 lines | Seed function rewrite |
| 10 | Medium | +80 lines | 6 validation checks |
| 11 | **Complex** | +150 lines | Core dispatch rewrite (flag-gated) |
| 12 | Medium | +40 lines | Session pool changes (flag-gated) |
| 13 | Medium | ±40 lines | Prompt rewriting (content change) |
| 14 | Medium | -450 lines | Deletion (blocked on GWS) |
| 15 | Medium | -200 lines | Flag removal + rename |
| 16 | Medium | -620 lines | File deletion + import updates |
| 17 | Medium | -470 lines | Code block removal |

**Total: ~+905 added, ~-1850 removed = ~945 net lines removed**
