# Unified Tool Registry — Design Spec

**Date:** 2026-03-29
**Status:** Design approved, pending implementation plan
**Scope:** Internal + External MCP tool architecture redesign

---

## 1. Problem Statement

Adding or removing a single tool (like `search`) requires touching 8 files with 3 naming systems. Miss any one and you get a silent failure.

### 1.1 Current File Fragmentation

| File | What to update | Purpose |
|------|---------------|---------|
| `tools/intelligence_server.py` | MCP tool function | Implementation |
| `orchestrator/tool_schemas.py` | `TOOL_INPUT_MODELS` dict + Pydantic model | Claude tool definitions |
| `orchestrator/agents.py` | `CAPABILITY_SCOPES` dict | Which agents can use it |
| `integrations/capabilities.py` | `CAPABILITY_CATALOG` + `TOOL_TO_CAPABILITY` | Capability mapping |
| `services/tool_registry.py` | `_DEFAULT_TOOLS` list | Risk levels, approval rules |
| `services/governor.py` | `AUTO_EXECUTE_ACTIONS` set | Governor bypass |
| `orchestrator/jarvis.py` | `internal_tools` set in `_execute_tool()` | Dispatch routing |
| `orchestrator/tool_policy.py` | `FALLBACK_WRITE_TOOLS` + `FALLBACK_BLOCKED_TOOLS` + `_HIGH_RISK_TOOLS` | Risk classification fallback |

### 1.2 Three Naming Systems Coexist

| System | Example for same tool | Where used |
|--------|----------------------|------------|
| Flat name | `search` | tool_schemas.py, internal_tools set, Claude tool_use |
| MCP namespaced | `intelligence_search` | FastMCP server, `_call_internal_tool()` |
| Capability name | `internal.search` | agents.py scope, CAPABILITY_CATALOG |

### 1.3 Specific Issues

1. **No single source of truth** — tool identity fragmented across 7 files
2. **Three naming systems** — flat, MCP-namespaced, capability. `TOOL_TO_CAPABILITY` must map both flat and namespaced forms
3. **Duplicate dispatch logic** — `internal_tools` set in jarvis.py must exactly match `TOOL_INPUT_MODELS.keys()` in tool_schemas.py; they can silently drift
4. **Governor safe-list separate from risk levels** — `AUTO_EXECUTE_ACTIONS` in governor.py and `risk_level` in tool_registry.py are two independent systems that should be one
5. **Capability mapping is a 169-entry hardcoded dict** — `TOOL_TO_CAPABILITY` maps every possible name variant (camelCase, kebab-case, snake_case, namespaced, bare). Fragile, redundant, un-auditable
6. **No validation at startup** — none of these registrations are cross-validated

### 1.4 Real Bugs Hit

- Capability lookup failed because `TOOL_TO_CAPABILITY` had the namespaced key but `can_use_tool()` looked up the flat key
- `search` (internal tool) vs Notion's `search` — name collision from normalizing different tools to the same canonical form
- Tool in `tool_schemas` but not in `internal_tools` → Claude can call it but dispatch fails

---

## 2. Key Decisions Made During Brainstorming

### 2.1 Scope: Both internal AND external tools

Not just the 18 internal tools — the full registry including ~150 external MCP tool entries.

### 2.2 Keep DB-backed registry

Code catalog is the seed source. DB is the runtime registry. Per-workspace overrides supported.

### 2.3 Use real MCP names everywhere — drop the normalizer

**Current roundtrip (with normalizer):**
```
MCP server reports: "sendGmailDraft"
  -> normalizer converts to: "send_gmail_draft" (canonical)
  -> stored as: "send_gmail_draft"
  -> presented to Claude as: "send_gmail_draft"
  -> Claude calls: "send_gmail_draft"
  -> dispatch resolves back to: "sendGmailDraft" (raw)
  -> MCP server receives: "sendGmailDraft"
```

**New (no normalizer):**
```
MCP server reports: "sendGmailDraft"
  -> stored as: "sendGmailDraft"
  -> presented to Claude as: "sendGmailDraft"
  -> Claude calls: "sendGmailDraft"
  -> MCP server receives: "sendGmailDraft"
```

**Why the normalizer is unnecessary:**
- The capability system (`tool_name -> capability`) already abstracts over naming. Agent scopes are defined in terms of capabilities (`email.draft`), not tool names.
- Claude handles any tool name format (camelCase, kebab-case, snake_case).
- The normalizer was _introducing_ problems (name collisions like `search`) rather than solving them.
- Eliminating the normalizer removes the entire `tool_normalizer.py` module, the bidirectional mapping system in `session_pool.py`, and ~190 redundant name-variant entries in `TOOL_TO_CAPABILITY`.

### 2.4 Remove specific tool names from agent prompts — agents discover tools via MCP

Currently, agent prompts in `prompts.py` hardcode specific tool names:

| Agent | Hardcoded tool names in prompt |
|-------|-------------------------------|
| Observer | `gmail_*`, `calendar_*`, `github_*`, `slack_*`, `gmail_list_unread(max_results=20)`, `slack_get_channel_history`, `calendar_list` |
| Researcher | Explicit `<tools>` section listing `search`, `web_search`, `browser_navigate`, `browser_snapshot`, `browser_screenshot` — plus tool calls in all examples |
| Governor | `report_governor_verdict`, `search_memory`, `gmail_send_email` |
| Decision Framework | `Gmail, Calendar, GitHub, Slack` as examples tied to `read_source` |

This creates a maintenance burden: renaming a tool (e.g., `search_memory` → `search`) requires updating prompts.py in addition to the registry files — the "tool name ripple" problem.

**Resolution:** Remove all specific tool names from agent prompts. Agents already receive their available tools via the Claude API tool list (built by `get_tools_for_agent()` from the registry). Claude sees tool names, descriptions, and input schemas in the request — it does not need prompt text to tell it which tools exist.

**What prompts should contain:**
- **Capabilities and intent** — "search internal knowledge", "fetch email data", "navigate web pages"
- **Workflow patterns** — "search internal knowledge first, then search the web if insufficient, then open URLs for deep reading"
- **Behavioral examples** — describe expected behavior and output format, not specific tool calls

**What prompts should NOT contain:**
- Specific tool names (`gmail_list_unread`, `browser_navigate`, `web_search`)
- Tool call syntax (`gmail_list_unread(max_results=20)`)
- `<tools>` sections listing available tools by name
- Examples that show specific tool invocations

**Example rewrite (Researcher):**

Before:
```
3. If insufficient, search the web using web_search tool for broad discovery
4. For deeper reading, open URLs with browser_navigate, then browser_snapshot to read
```

After:
```
3. If insufficient, search the web for broad discovery
4. For deeper reading, open result URLs in the browser, then snapshot the page content
```

Before (example):
```
→ search("Acme Corp") → find entity + memories
→ web_search("Google A2UI agent-to-user interface proposal") → 8 results
→ browser_navigate(url="https://best-result-url...") → page loads
→ browser_snapshot() → full article text
```

After (example):
```
→ Search internal knowledge for "Acme Corp" → find entity + memories
→ Search the web for "Google A2UI agent-to-user interface proposal" → 8 results
→ Open the most relevant URL → read the full article text
→ Synthesize findings with source URLs and citations
```

### 2.5 External MCP tools don't need Pydantic schema definitions

External MCP servers provide their own schemas via `list_tools()`. The current pipeline already uses these directly:
```python
# session_pool.py: discovers tools WITH schemas from MCP server
raw_tools = await client.list_tools()
for t in raw_tools:
    input_schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
    # ^ schema comes from the MCP server itself

# jarvis.py: presents them to Claude with the MCP-provided schema
for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
    tools.append({
        "name": mcp_tool["name"],
        "description": mcp_tool["description"],
        "input_schema": mcp_tool["input_schema"],  # FROM the MCP server
    })
```

Only the 18 internal tools need Pydantic models (for generating Claude-compatible schemas from code).

### 2.6 Capabilities stay for agent authorization

Capabilities (`email.send`, `internal.search`, etc.) remain the authorization abstraction. They provide useful grouping — adding a new tool with capability `email.send` automatically grants it to all agents that already have `email.send` in scope.

What changes: the `tool -> capability` mapping moves from a hardcoded 340-line dict (`TOOL_TO_CAPABILITY`) into the registry (a `capability` column on the tool record).

---

## 3. Architecture

### 3.1 First Principles

| # | Principle | Implication |
|---|-----------|-------------|
| 1 | **One identity, one place** | Every tool has one canonical entry in one registry. All metadata lives there. |
| 2 | **Use real names** | No normalization. The MCP server's tool name IS the tool's identity. |
| 3 | **Derive, don't duplicate** | Governor auto-execute, agent filtering, dispatch routing, Claude tool schemas — all derived from the registry. |
| 4 | **Capabilities are the authorization abstraction** | Tools have capabilities. Agents have capability scopes. The indirection is valuable. |
| 5 | **Discovery feeds the registry** | MCP server connects -> tools discovered -> registered with defaults. Seeded tools get metadata from code. User overrides persist in DB. |
| 6 | **Validate on startup** | Cross-validate all registrations. Missing capability? Unknown agent scope? Fail loud. |

### 3.2 High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Tool Registry (DB)                     │
│              Single source of truth for ALL tools         │
│                                                           │
│  Per tool:                                                │
│  ┌──────────────────────────────────────────────────┐    │
│  │  name          "sendGmailDraft"  <- real name     │    │
│  │  server        "google-workspace"                 │    │
│  │  capability    "email.draft"                      │    │
│  │  risk_level    "medium"                           │    │
│  │  approval      true                               │    │
│  │  backend       "external_mcp"                     │    │
│  │  enabled       true                               │    │
│  │  workspace_id  "ws_..."                           │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────┘
                          |
          ┌───────────────┼───────────────┐
          |               |               |
     Code Seeds      MCP Discovery    User Override
   (internal +       (list_tools()    (admin changes
    known ext.)       at connect)      risk/enable)
```

### 3.3 The Three Tool Populations

#### Population 1: Internal Tools (19 tools — we own the code, all ✅ live-verified)

> 15 intelligence + 3 communication + 1 special dispatch = 19 entries in catalog.
> 3 orphan tools removed (`create_task`, `get_task`, `get_goals`).
> 1 missing tool added (`get_goal_memories`).
> 3 communication tools added (were invisible to Claude).

Defined in a catalog module (`src/tools/catalog.py`):

```python
from dataclasses import dataclass, field
from pydantic import BaseModel

@dataclass(frozen=True)
class InternalToolDef:
    name: str                        # "search" — what Claude sees
    input_model: type[BaseModel]     # SearchInput — Pydantic schema
    capability: str                  # "internal.search"
    risk_level: str = "low"          # low/medium/high/critical
    requires_approval: bool = False
    server: str = "intelligence"     # which FastMCP server hosts it
    description: str = ""            # auto-derived from docstring if empty
    read_only: bool = False          # from MCP readOnlyHint annotation

INTERNAL_TOOLS: list[InternalToolDef] = [
    # ── Intelligence server (15 tools) — ✅ all live-verified ──
    InternalToolDef(
        name="search",
        input_model=SearchInput,
        capability="internal.search",
        server="intelligence",
        read_only=True,
    ),
    InternalToolDef(
        name="ingest_event",
        input_model=IngestEventInput,
        capability="internal.ingest_event",
        server="intelligence",
    ),
    InternalToolDef(
        name="evaluate_policy",
        input_model=EvaluatePolicyInput,
        capability="internal.evaluate_policy",
        server="intelligence",
        read_only=True,
    ),
    InternalToolDef(
        name="get_briefing",
        input_model=GetBriefingInput,
        capability="internal.get_briefing",
        server="intelligence",
        read_only=True,
    ),
    InternalToolDef(
        name="get_observation_cursor",
        input_model=GetObservationCursorInput,
        capability="internal.get_cursor",
        server="intelligence",
        read_only=True,
    ),
    InternalToolDef(
        name="update_observation_cursor",
        input_model=UpdateObservationCursorInput,
        capability="internal.update_cursor",
        server="intelligence",
    ),
    InternalToolDef(
        name="report_observation",
        input_model=ReportObservationInput,
        capability="internal.report_observation",
        server="intelligence",
    ),
    InternalToolDef(
        name="approve_action",
        input_model=ApproveActionInput,
        capability="internal.approve_action",
        risk_level="medium",
        requires_approval=False,
        server="intelligence",
    ),
    InternalToolDef(
        name="update_execution",
        input_model=UpdateExecutionInput,
        capability="internal.update_execution",
        server="intelligence",
    ),
    InternalToolDef(
        name="update_entity",
        input_model=UpdateEntityInput,
        capability="internal.update_entity",
        server="intelligence",
    ),
    InternalToolDef(
        name="get_active_plans",
        input_model=GetActivePlansInput,
        capability="internal.get_plans",
        server="intelligence",
        read_only=True,
    ),
    InternalToolDef(
        name="extract_preferences",
        input_model=ExtractPreferencesInput,
        capability="internal.extract_preferences",
        server="intelligence",
    ),
    InternalToolDef(
        name="get_goal_memories",   # Was missing schema — now added
        input_model=GetGoalMemoriesInput,
        capability="internal.get_goals",
        server="intelligence",
        read_only=True,
    ),
    InternalToolDef(
        name="build_context",
        input_model=BuildContextInput,
        capability="internal.build_context",
        server="intelligence",
        read_only=True,
    ),
    InternalToolDef(
        name="verify_run",
        input_model=VerifyRunInput,
        capability="internal.verify_run",
        server="intelligence",
        read_only=True,
    ),

    # ── Communication server (3 tools) — ✅ all live-verified ──
    InternalToolDef(
        name="send_telegram",
        input_model=SendTelegramInput,
        capability="internal.send_telegram",
        risk_level="medium",
        server="communication",         # NOTE: different server!
    ),
    InternalToolDef(
        name="send_approval_prompt",
        input_model=SendApprovalPromptInput,
        capability="internal.send_approval",
        risk_level="medium",
        server="communication",
    ),
    InternalToolDef(
        name="push_ui_update",
        input_model=PushUiUpdateInput,
        capability="internal.push_ui",
        server="communication",
    ),

    # ── Special dispatch (not MCP) ──
    InternalToolDef(
        name="report_governor_verdict",
        input_model=ReportGovernorVerdictInput,
        capability="internal.report_governor_verdict",
        server="_special",              # Handled inline, not via MCP
        read_only=False,
    ),
]

# REMOVED — orphan tools with no MCP implementation:
# - create_task (standalone tasks removed in product redesign)
# - get_task (standalone tasks removed)
# - get_goals (replaced by get_goal_memories)
```

**What is derived from this single list:**
- Claude tool schemas: `input_model.model_json_schema()` (excluding `user_id`/`workspace_id` — injected at dispatch)
- Dispatch routing: `server` field drives namespace prefix (`intelligence` → `intelligence_search`, `communication` → `communication_send_telegram`, `_special` → inline handler)
- Governor policy: `risk_level` + `read_only` fields (readOnly tools auto-execute, no approval needed)
- DB seed: auto-seeded into `tool_definitions` table
- Internal tools set: `{t.name for t in INTERNAL_TOOLS}` (replaces hardcoded set in jarvis.py)
- Orphan detection: if a tool is in INTERNAL_TOOLS but not in the MCP server's `list_tools()`, startup validation fails loud

#### Population 2: Known External Tools (seeded, pre-configured connectors — 116 seeds)

> **Live-verified on 2026-03-29**: Notion (22), Linear (24), Playwright (22), Filesystem (14) tool names confirmed via actual `list_tools()` probing. Google Workspace, GitHub, Slack, Atlassian, Twilio names are from docs/assumed — need live verification when credentials are available. See Section 10 for full probing results.

```python
@dataclass(frozen=True)
class ExternalToolSeed:
    name: str                        # "sendGmailDraft" — REAL MCP tool name
    capability: str                  # "email.draft"
    risk_level: str = "medium"
    requires_approval: bool = True
    server: str = "google-workspace" # MCP server name (matches seed_installations)
    verified: bool = False           # True = name confirmed via live list_tools() probe

# Seeding behavior depends on `verified`:
#   verified=True  → seed into DB with source="seed", enabled=True
#   verified=False → seed into DB with source="unverified", enabled=True
#                    but flag for re-verification on first MCP connect.
#                    If list_tools() returns a DIFFERENT name, update the seed.
#                    If list_tools() doesn't return this name at all, disable it.

EXTERNAL_TOOL_SEEDS: list[ExternalToolSeed] = [
    # ══════════════════════════════════════════════════════════════
    # Google Workspace MCP (camelCase — tool names from docs,
    # NOT yet live-verified; seed_installations.py has wrong executable)
    # BUG: command should be "google-workspace-worker" not "google-workspace-mcp"
    # verified=False (all entries) — names assumed from docs
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("sendGmailDraft", "email.send", "high", True, "google-workspace"),
    ExternalToolSeed("createGmailDraft", "email.draft", "medium", True, "google-workspace"),
    ExternalToolSeed("listGmailMessages", "email.list", "low", False, "google-workspace"),
    ExternalToolSeed("readGmailMessage", "email.read", "low", False, "google-workspace"),
    ExternalToolSeed("searchGmail", "email.search", "low", False, "google-workspace"),
    ExternalToolSeed("deleteGmailMessage", "email.delete", "critical", True, "google-workspace"),
    ExternalToolSeed("createCalendarEvent", "calendar.create", "medium", True, "google-workspace"),
    ExternalToolSeed("updateCalendarEvent", "calendar.update", "medium", True, "google-workspace"),
    ExternalToolSeed("deleteCalendarEvent", "calendar.delete", "critical", True, "google-workspace"),
    ExternalToolSeed("listCalendarEvents", "calendar.list", "low", False, "google-workspace"),
    ExternalToolSeed("getCalendarEvent", "calendar.get", "low", False, "google-workspace"),

    # ══════════════════════════════════════════════════════════════
    # GitHub MCP (snake_case — from docs, NOT yet live-verified)
    # verified=False (all entries) — names assumed from docs
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("create_pull_request", "repo.create_pr", "high", True, "github"),
    ExternalToolSeed("merge_pull_request", "repo.merge_pr", "high", True, "github"),
    ExternalToolSeed("update_pull_request", "repo.update_pr", "medium", True, "github"),
    ExternalToolSeed("list_issues", "issue.list", "low", False, "github"),
    ExternalToolSeed("search_issues", "issue.search", "low", False, "github"),
    ExternalToolSeed("search_code", "repo.search_code", "low", False, "github"),
    ExternalToolSeed("issue_write", "issue.create", "medium", True, "github"),
    ExternalToolSeed("add_issue_comment", "issue.comment", "medium", True, "github"),

    # ══════════════════════════════════════════════════════════════
    # Slack MCP (prefixed snake_case — from docs, NOT yet live-verified)
    # BUG: seed_installations.py uses wrong env var SLACK_BOT_TOKEN;
    # actual server needs SLACK_MCP_XOXP_TOKEN / SLACK_MCP_XOXB_TOKEN
    # verified=False (all entries) — names assumed from docs
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("slack_send_message", "messaging.send", "high", True, "slack"),
    ExternalToolSeed("slack_reply_to_thread", "messaging.reply", "high", True, "slack"),
    ExternalToolSeed("slack_add_reaction", "messaging.react", "medium", True, "slack"),
    ExternalToolSeed("slack_list_channels", "messaging.list_channels", "low", False, "slack"),
    ExternalToolSeed("slack_get_channel_history", "messaging.get_history", "low", False, "slack"),
    ExternalToolSeed("slack_get_thread_replies", "messaging.get_thread", "low", False, "slack"),
    ExternalToolSeed("slack_get_users", "messaging.get_users", "low", False, "slack"),
    ExternalToolSeed("slack_get_user_profile", "messaging.get_profile", "low", False, "slack"),

    # ══════════════════════════════════════════════════════════════
    # Notion MCP — ✅ LIVE-VERIFIED (22 tools)
    # REAL naming: "API-" prefixed kebab-case
    # Old seeds had WRONG names (missing API- prefix)
    # verified=True (all entries)
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("API-post-page", "doc.create", "medium", True, "notion"),
    ExternalToolSeed("API-patch-page", "doc.update", "medium", True, "notion"),
    ExternalToolSeed("API-retrieve-a-page", "doc.get", "low", False, "notion"),
    ExternalToolSeed("API-retrieve-a-page-property", "doc.get_property", "low", False, "notion"),
    ExternalToolSeed("API-query-data-source", "doc.query", "low", False, "notion"),
    ExternalToolSeed("API-post-search", "doc.search", "low", False, "notion"),
    ExternalToolSeed("API-create-a-comment", "doc.comment", "medium", True, "notion"),
    ExternalToolSeed("API-retrieve-a-comment", "doc.get_comment", "low", False, "notion"),
    ExternalToolSeed("API-patch-block-children", "doc.append", "medium", True, "notion"),
    ExternalToolSeed("API-get-block-children", "doc.get_children", "low", False, "notion"),
    ExternalToolSeed("API-retrieve-a-block", "doc.get_block", "low", False, "notion"),
    ExternalToolSeed("API-update-a-block", "doc.update_block", "medium", True, "notion"),
    ExternalToolSeed("API-delete-a-block", "doc.delete_block", "high", True, "notion"),
    ExternalToolSeed("API-move-page", "doc.move", "medium", True, "notion"),
    ExternalToolSeed("API-retrieve-a-database", "doc.get_database", "low", False, "notion"),
    ExternalToolSeed("API-create-a-data-source", "doc.create_datasource", "medium", True, "notion"),
    ExternalToolSeed("API-retrieve-a-data-source", "doc.get_datasource", "low", False, "notion"),
    ExternalToolSeed("API-update-a-data-source", "doc.update_datasource", "medium", True, "notion"),
    ExternalToolSeed("API-list-data-source-templates", "doc.list_templates", "low", False, "notion"),
    ExternalToolSeed("API-get-self", "doc.get_self", "low", False, "notion"),
    ExternalToolSeed("API-get-user", "doc.get_user", "low", False, "notion"),
    ExternalToolSeed("API-get-users", "doc.get_users", "low", False, "notion"),

    # ══════════════════════════════════════════════════════════════
    # Linear MCP — ✅ LIVE-VERIFIED (24 tools)
    # REAL naming: "linear_" prefixed snake_case
    # verified=True (all entries)
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("linear_create_issue", "workflow.create_issue", "medium", True, "linear"),
    ExternalToolSeed("linear_create_issues", "workflow.create_issues", "medium", True, "linear"),
    ExternalToolSeed("linear_edit_issue", "workflow.update_issue", "medium", True, "linear"),
    ExternalToolSeed("linear_bulk_update_issues", "workflow.bulk_update", "medium", True, "linear"),
    ExternalToolSeed("linear_get_issue", "workflow.get", "low", False, "linear"),
    ExternalToolSeed("linear_search_issues", "workflow.search", "low", False, "linear"),
    ExternalToolSeed("linear_search_issues_by_identifier", "workflow.search_by_id", "low", False, "linear"),
    ExternalToolSeed("linear_delete_issue", "workflow.delete", "critical", True, "linear"),
    ExternalToolSeed("linear_create_comment", "workflow.comment", "medium", True, "linear"),
    ExternalToolSeed("linear_update_comment", "workflow.update_comment", "medium", True, "linear"),
    ExternalToolSeed("linear_delete_comment", "workflow.delete_comment", "high", True, "linear"),
    ExternalToolSeed("linear_resolve_comment", "workflow.resolve_comment", "medium", True, "linear"),
    ExternalToolSeed("linear_unresolve_comment", "workflow.unresolve_comment", "medium", True, "linear"),
    ExternalToolSeed("linear_get_teams", "workflow.get_teams", "low", False, "linear"),
    ExternalToolSeed("linear_get_user", "workflow.get_user", "low", False, "linear"),
    ExternalToolSeed("linear_get_project", "workflow.get_project", "low", False, "linear"),
    ExternalToolSeed("linear_list_projects", "workflow.list_projects", "low", False, "linear"),
    ExternalToolSeed("linear_create_project_with_issues", "workflow.create_project", "medium", True, "linear"),
    ExternalToolSeed("linear_create_project_milestone", "workflow.create_milestone", "medium", True, "linear"),
    ExternalToolSeed("linear_get_project_milestones", "workflow.get_milestones", "low", False, "linear"),
    ExternalToolSeed("linear_update_project_milestone", "workflow.update_milestone", "medium", True, "linear"),
    ExternalToolSeed("linear_delete_project_milestone", "workflow.delete_milestone", "high", True, "linear"),
    ExternalToolSeed("linear_create_customer_need_from_attachment", "workflow.create_customer_need", "medium", True, "linear"),
    ExternalToolSeed("linear_auth_callback", "workflow.auth", "low", False, "linear"),

    # ══════════════════════════════════════════════════════════════
    # Playwright MCP — ✅ LIVE-VERIFIED (22 tools)
    # REAL naming: "browser_" prefixed snake_case
    # NOTE: browser_screenshot → browser_take_screenshot (renamed)
    #        browser_wait → browser_wait_for (renamed)
    #        browser_pdf_save does NOT exist (phantom)
    # verified=True (all entries)
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("browser_navigate", "browser.open", "medium", False, "playwright"),
    ExternalToolSeed("browser_navigate_back", "browser.navigate_back", "low", False, "playwright"),
    ExternalToolSeed("browser_snapshot", "browser.snapshot", "low", False, "playwright"),
    ExternalToolSeed("browser_take_screenshot", "browser.screenshot", "low", False, "playwright"),
    ExternalToolSeed("browser_click", "browser.click", "medium", False, "playwright"),
    ExternalToolSeed("browser_drag", "browser.click", "medium", False, "playwright"),
    ExternalToolSeed("browser_hover", "browser.click", "medium", False, "playwright"),
    ExternalToolSeed("browser_type", "browser.type", "medium", False, "playwright"),
    ExternalToolSeed("browser_fill_form", "browser.type", "medium", False, "playwright"),
    ExternalToolSeed("browser_select_option", "browser.click", "medium", False, "playwright"),
    ExternalToolSeed("browser_press_key", "browser.type", "medium", False, "playwright"),
    ExternalToolSeed("browser_handle_dialog", "browser.click", "medium", False, "playwright"),
    ExternalToolSeed("browser_file_upload", "browser.submit", "medium", True, "playwright"),
    ExternalToolSeed("browser_close", "browser.open", "low", False, "playwright"),
    ExternalToolSeed("browser_tabs", "browser.open", "low", False, "playwright"),
    ExternalToolSeed("browser_resize", "browser.open", "low", False, "playwright"),
    ExternalToolSeed("browser_console_messages", "browser.snapshot", "low", False, "playwright"),
    ExternalToolSeed("browser_network_requests", "browser.snapshot", "low", False, "playwright"),
    ExternalToolSeed("browser_evaluate", "browser.execute", "high", True, "playwright"),
    ExternalToolSeed("browser_run_code", "browser.execute", "high", True, "playwright"),
    ExternalToolSeed("browser_install", "browser.install", "medium", True, "playwright"),
    ExternalToolSeed("browser_wait_for", "browser.wait", "low", False, "playwright"),

    # ══════════════════════════════════════════════════════════════
    # Filesystem MCP — ✅ LIVE-VERIFIED (14 tools)
    # REAL naming: snake_case (no prefix)
    # ALL tools were previously missing from registry
    # verified=True (all entries)
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("read_text_file", "filesystem.read", "low", False, "filesystem"),
    ExternalToolSeed("read_file", "filesystem.read", "low", False, "filesystem"),
    ExternalToolSeed("read_media_file", "filesystem.read_media", "low", False, "filesystem"),
    ExternalToolSeed("read_multiple_files", "filesystem.read", "low", False, "filesystem"),
    ExternalToolSeed("write_file", "filesystem.write", "high", True, "filesystem"),
    ExternalToolSeed("edit_file", "filesystem.write", "medium", True, "filesystem"),
    ExternalToolSeed("create_directory", "filesystem.write", "medium", True, "filesystem"),
    ExternalToolSeed("move_file", "filesystem.move", "high", True, "filesystem"),
    ExternalToolSeed("list_directory", "filesystem.list", "low", False, "filesystem"),
    ExternalToolSeed("list_directory_with_sizes", "filesystem.list", "low", False, "filesystem"),
    ExternalToolSeed("directory_tree", "filesystem.list", "low", False, "filesystem"),
    ExternalToolSeed("get_file_info", "filesystem.read", "low", False, "filesystem"),
    ExternalToolSeed("search_files", "filesystem.search", "low", False, "filesystem"),
    ExternalToolSeed("list_allowed_directories", "filesystem.list", "low", False, "filesystem"),

    # ══════════════════════════════════════════════════════════════
    # Jira/Atlassian MCP (camelCase — from docs, NOT yet live-verified)
    # verified=False (all entries) — names assumed from docs
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("getJiraIssue", "issue.get", "low", False, "atlassian"),
    ExternalToolSeed("searchJiraIssuesUsingJql", "issue.search", "low", False, "atlassian"),
    ExternalToolSeed("createJiraIssue", "issue.create", "medium", True, "atlassian"),
    ExternalToolSeed("editJiraIssue", "issue.update", "medium", True, "atlassian"),
    ExternalToolSeed("transitionJiraIssue", "issue.transition", "medium", True, "atlassian"),
    ExternalToolSeed("addCommentToJiraIssue", "issue.comment", "medium", True, "atlassian"),

    # ══════════════════════════════════════════════════════════════
    # Composite tools (multi-MCP orchestration)
    # ══════════════════════════════════════════════════════════════
    ExternalToolSeed("web_search", "search.web", "low", False, "composite"),
]
```

**Key difference from current system:** One entry per tool using the REAL MCP name. No `gmail_send` AND `sendGmailDraft` AND `gmail_send_email` mapping to the same thing. Just the name the MCP server actually uses.

**Key change: no native connectors.** The 6 `gmail_*` tools in `_NATIVE_TOOL_MAP` are replaced by Google Workspace MCP equivalents. All email/calendar operations route through the Google Workspace MCP server.

#### Population 3: Discovered Unknown Tools

When a new MCP server connects and reports tools we don't have seeds for:

```python
# In session_pool, after list_tools():
for tool in raw_tools:
    if not await registry.get_tool(tool.name):
        await registry.register_discovered(
            name=tool.name,
            server=server_name,
            capability=None,        # unmapped -> invisible to all agents
            risk_level="medium",    # safe default
            requires_approval=True, # safe default
            input_schema=tool.inputSchema,
            description=tool.description,
        )
```

Unknown tools default to **invisible** (no capability = no agent can use them) until an admin maps their capability. Safe by design.

### 3.4 DB Schema: Enhanced `tool_definitions` Table

The existing `tool_definitions` table is enhanced (not replaced):

```sql
-- Existing columns (keep as-is)
tool_id         VARCHAR PRIMARY KEY,  -- tool_ULID
workspace_id    VARCHAR,              -- FK to workspaces (nullable for global)
name            VARCHAR NOT NULL,     -- REAL MCP tool name (unique per workspace)
risk_level      VARCHAR DEFAULT 'low',
requires_approval BOOLEAN DEFAULT FALSE,
connector_type  VARCHAR,              -- e.g., 'gmail', 'slack', 'internal'
enabled         BOOLEAN DEFAULT TRUE,
description     TEXT,
input_schema    JSONB,
output_schema   JSONB,
timeout_seconds INTEGER DEFAULT 30,
idempotent      BOOLEAN DEFAULT FALSE,

-- Enhanced/new columns
capability      VARCHAR,              -- e.g., 'email.draft', 'internal.search'
server          VARCHAR,              -- MCP server name: 'intelligence', 'google-workspace', etc.
backend         VARCHAR DEFAULT 'external_mcp',  -- 'internal_mcp', 'external_mcp', 'composite'
source          VARCHAR DEFAULT 'seed',           -- 'internal', 'seed', 'discovered', 'override'
verified        BOOLEAN DEFAULT FALSE,            -- True = tool name confirmed via live list_tools() probe

-- Remove (no longer needed)
-- canonical_name  -- normalizer concept, eliminated
```

**UNIQUE constraint:** `(workspace_id, name)` — a tool name is unique within a workspace.

### 3.5 Dispatch: Registry-Driven

**Current dispatch** (8-step cascade in `_execute_tool`):
```
ToolRegistry blocked check -> early reject
report_governor_verdict -> special case (returns input as-is)
web_search -> special case (Playwright MCP internally)
internal_tools set -> _call_internal_tool()
_NATIVE_TOOL_MAP -> _try_native_connector()
CapabilityResolver -> capability_resolver.execute()
MCP bridge -> call_mcp_tool()
ToolRegistry fallback -> _execute_connector_tool()
```

**New dispatch** (one lookup, one match):
```python
async def _execute_tool(self, tool_name, tool_input, user_id, workspace_id):
    tool = await self._registry.get_tool(tool_name)
    if not tool:
        return {"error": f"Unknown tool: {tool_name}"}

    if not tool.enabled:
        return {"error": f"Tool '{tool_name}' is disabled"}

    match tool.backend:
        case "internal_mcp":
            # server field drives the namespace prefix:
            #   server="intelligence" -> "intelligence_search"
            #   server="communication" -> "communication_send_telegram"
            #   server="_special" -> handled inline (report_governor_verdict)
            if tool.server == "_special":
                return tool_input  # e.g., report_governor_verdict returns as-is
            mcp_name = f"{tool.server}_{tool_name}"
            return await self._call_internal_tool(mcp_name, tool_input)
        case "external_mcp":
            # Real name goes directly to MCP bridge — no translation
            return await call_mcp_tool(tool_name, tool_input, user_id=user_id, workspace_id=workspace_id)
        case "composite":
            return await self._call_composite_tool(tool_name, tool_input, user_id=user_id, workspace_id=workspace_id)
```

Three backends, one lookup, zero hardcoded sets. No native connectors — **all tools served through MCP**.

**Backend types:**
| Backend | Description | Example tools |
|---------|-------------|--------------|
| `internal_mcp` | In-process FastMCP server | `search`, `ingest_event`, `evaluate_policy` |
| `external_mcp` | External MCP server via session pool | `sendGmailDraft`, `create_pull_request`, `API-post-page` |
| `composite` | Multi-MCP orchestration handler | `web_search` (Playwright MCP internally) |

### 3.6 Authorization: Capability-Based (Unchanged Pattern)

Agent scopes remain capability-based. The only change is WHERE the tool->capability mapping lives.

```python
# agents.py — AGENT_CAPABILITY_SCOPES unchanged
AGENT_CAPABILITY_SCOPES = {
    "researcher": {"internal.search", "email.list", "email.read", "search.web", ...},
    "operator":   {"email.send", "email.draft", "calendar.create", ...},
    # ... same as today
}

# can_use_tool() simplified — ONE lookup, no normalizer fallback
def can_use_tool(self, tool_name: str) -> bool:
    cap = self._registry.get_capability(tool_name)  # DB lookup (cached)
    return cap in self.capability_scope if cap else False
```

**Current can_use_tool()** (3-step chain with normalizer):
```python
def can_use_tool(self, tool_name):
    cap = get_capability_for_tool(tool_name)       # Step 1: TOOL_TO_CAPABILITY dict
    if cap: return cap in self.capability_scope
    canonical = normalizer.normalize(tool_name)     # Step 2: normalizer
    if canonical != tool_name:
        cap = get_capability_for_tool(canonical)    # Step 3: retry
        if cap: return cap in self.capability_scope
    return False
```

### 3.7 Governor: Derived from Registry

```python
# Current: separate hardcoded set that must be kept in sync
AUTO_EXECUTE_ACTIONS = {"fetch_info", "summarize", "search", "add_to_brief", "acknowledge", "answer_directly"}

# New: derived from registry at decision time
async def is_auto_execute(self, tool_name: str) -> bool:
    tool = await self._registry.get_tool(tool_name)
    return tool is not None and tool.risk_level == "low" and not tool.requires_approval
```

Note: `AUTO_EXECUTE_ACTIONS` in governor.py is actually about **decision types** (Planner outputs like "search", "summarize"), not individual tools. This set operates at a different level than tool risk. During implementation, verify whether this set should remain as-is (decision-level policy) or merge into the registry (tool-level policy). They may serve different purposes.

### 3.8 Startup Validation

```python
async def validate_registry(registry, agent_scopes, capability_catalog):
    """Cross-validate everything at startup. Fail loud on inconsistency."""
    errors = []

    all_tools = await registry.list_all()

    # 1. Every tool with a capability must reference a known capability
    for tool in all_tools:
        if tool.capability and tool.capability not in capability_catalog:
            errors.append(f"Tool '{tool.name}': unknown capability '{tool.capability}'")

    # 2. Every capability in agent scopes must exist in catalog
    for agent_name, scopes in agent_scopes.items():
        for cap in scopes:
            if cap not in capability_catalog:
                errors.append(f"Agent '{agent_name}': unknown capability '{cap}'")

    # 3. Every internal tool must have a corresponding MCP server function
    internal_tools = [t for t in all_tools if t.backend == "internal_mcp"]
    for tool in internal_tools:
        if not tool.capability:
            errors.append(f"Internal tool '{tool.name}': missing capability")

    # 4. Risk/approval consistency: critical tools must require approval
    for tool in all_tools:
        if tool.risk_level == "critical" and not tool.requires_approval:
            errors.append(f"Tool '{tool.name}': critical risk but approval not required")

    # 5. Internal tools must exist in their MCP server
    from fastmcp import Client
    from src.tools.server import jarvis_tools
    async with Client(jarvis_tools) as client:
        mcp_tools = {t.name for t in await client.list_tools()}
    for tool in internal_tools:
        if tool.server == "_special":
            continue  # Not an MCP tool
        expected = f"{tool.server}_{tool.name}"
        if expected not in mcp_tools:
            errors.append(f"Internal tool '{tool.name}': expected '{expected}' in MCP server but not found")

    # 6. MCP annotations consistency check
    # readOnly tools should have risk_level="low"
    for tool in internal_tools:
        if tool.read_only and tool.risk_level not in ("low",):
            errors.append(f"Tool '{tool.name}': marked readOnly but risk_level='{tool.risk_level}'")

    if errors:
        for e in errors:
            logger.error("Registry validation: %s", e)
        raise RuntimeError(f"Tool registry has {len(errors)} validation errors")

    logger.info("Registry validation passed: %d tools, %d capabilities", len(all_tools), len(capability_catalog))
```

### 3.9 Tool Assembly for Claude API

```python
# In jarvis.py — replaces _build_tool_definitions() + _build_native_connector_tools() + _get_tools_for_agent()
def get_tools_for_agent(self, agent: SubAgent, workspace_id: str = "") -> list[dict]:
    """Build filtered tool list for Claude API from registry."""
    tools = []

    # 1. Internal tools — schemas from Pydantic models in catalog
    for tool_def in INTERNAL_TOOLS:
        if agent.can_use_tool(tool_def.name):
            schema = tool_def.input_model.model_json_schema()
            tools.append({
                "name": tool_def.name,
                "description": tool_def.input_model.__doc__.strip() if tool_def.input_model.__doc__ else tool_def.name,
                "input_schema": schema,
            })

    # 2. External MCP tools — schemas from MCP servers (already discovered)
    for mcp_tool in list_mcp_tools(workspace_id=workspace_id):
        if agent.can_use_tool(mcp_tool["name"]):
            tools.append({
                "name": mcp_tool["name"],
                "description": mcp_tool.get("description", ""),
                "input_schema": mcp_tool.get("input_schema", {"type": "object", "properties": {}}),
            })

    return tools
```

### 3.10 MCP Session Pool Changes

The session pool no longer normalizes tool names:

```python
# Current (session_pool.py):
raw_tools = await client.list_tools()
tool_mapping = self._normalizer.register_server_tools(server_name, tool_dicts)  # normalize
self._server_tools[(workspace_id, server_name)] = tool_mapping                  # canonical -> raw
for t in raw_tools:
    canonical = self._normalizer.normalize(t.name, server_name)
    self._tool_metadata[canonical] = {...}

# New:
raw_tools = await client.list_tools()
tool_names = {}
for t in raw_tools:
    real_name = t.name  # USE AS-IS — no normalization
    tool_names[real_name] = real_name  # identity mapping (or just a set)
    self._tool_metadata[real_name] = {
        "name": real_name,
        "server": server_name,
        "description": t.description or "",
        "input_schema": getattr(t, "inputSchema", None) or {"type": "object", "properties": {}},
        "_workspace_id": workspace_id,
    }
    # Register in DB if not already known
    if not await registry.get_tool(real_name):
        await registry.register_discovered(name=real_name, server=server_name, ...)
self._server_tools[(workspace_id, server_name)] = tool_names
```

And `call_tool()` no longer needs canonical->raw translation:

```python
# Current:
raw_name = session.tools.get(tool_name) or tool_name  # canonical -> raw

# New:
raw_name = tool_name  # it's already the real name
```

### 3.11 What CAPABILITY_CATALOG Becomes

`CAPABILITY_CATALOG` stays in `capabilities.py`. It defines the capability taxonomy:

```python
CAPABILITY_CATALOG: dict[str, CapabilityMeta] = {
    "email.send": _cap(CapabilityFamily.EMAIL, False, "high"),
    "email.draft": _cap(CapabilityFamily.EMAIL, False, "medium"),
    "email.list": _cap(CapabilityFamily.EMAIL, True),
    # ... ~80 capabilities
}
```

What gets **deleted** from `capabilities.py`:
- `TOOL_TO_CAPABILITY` dict (340 lines) — replaced by `capability` column in registry
- `get_capability_for_tool()` function — replaced by `registry.get_capability()`

What **stays**:
- `CapabilityFamily` enum
- `CapabilityMeta` dataclass
- `CAPABILITY_CATALOG` dict
- `get_family_for_capability()` — reads from catalog
- `is_read_only_capability()` — reads from catalog

**New capability families to add** (from live-probed inventory):

```python
# CapabilityFamily enum additions:
FILESYSTEM = "filesystem"

# CAPABILITY_CATALOG additions:
"filesystem.read":         _cap(CapabilityFamily.FILESYSTEM, True),
"filesystem.read_media":   _cap(CapabilityFamily.FILESYSTEM, True),
"filesystem.write":        _cap(CapabilityFamily.FILESYSTEM, False, "high"),
"filesystem.move":         _cap(CapabilityFamily.FILESYSTEM, False, "high"),
"filesystem.list":         _cap(CapabilityFamily.FILESYSTEM, True),
"filesystem.search":       _cap(CapabilityFamily.FILESYSTEM, True),
"filesystem.install":      _cap(CapabilityFamily.FILESYSTEM, False, "medium"),

# New browser capabilities:
"browser.execute":         _cap(CapabilityFamily.BROWSER, False, "high"),
"browser.install":         _cap(CapabilityFamily.BROWSER, False, "medium"),
"browser.navigate_back":   _cap(CapabilityFamily.BROWSER, True),
"browser.wait":            _cap(CapabilityFamily.BROWSER, True),

# New workflow/linear capabilities:
"workflow.create_issues":        _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.bulk_update":          _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.search_by_id":         _cap(CapabilityFamily.WORKFLOW, True),
"workflow.update_comment":       _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.delete_comment":       _cap(CapabilityFamily.WORKFLOW, False, "high"),
"workflow.resolve_comment":      _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.unresolve_comment":    _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.get_user":             _cap(CapabilityFamily.WORKFLOW, True),
"workflow.get_project":          _cap(CapabilityFamily.WORKFLOW, True),
"workflow.list_projects":        _cap(CapabilityFamily.WORKFLOW, True),
"workflow.create_project":       _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.create_milestone":     _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.get_milestones":       _cap(CapabilityFamily.WORKFLOW, True),
"workflow.update_milestone":     _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.delete_milestone":     _cap(CapabilityFamily.WORKFLOW, False, "high"),
"workflow.create_customer_need": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
"workflow.auth":                 _cap(CapabilityFamily.WORKFLOW, True),

# New doc/notion capabilities:
"doc.get_property":        _cap(CapabilityFamily.DOC, True),
"doc.get_comment":         _cap(CapabilityFamily.DOC, True),
"doc.get_children":        _cap(CapabilityFamily.DOC, True),
"doc.get_block":           _cap(CapabilityFamily.DOC, True),
"doc.update_block":        _cap(CapabilityFamily.DOC, False, "medium"),
"doc.delete_block":        _cap(CapabilityFamily.DOC, False, "high"),
"doc.move":                _cap(CapabilityFamily.DOC, False, "medium"),
"doc.get_database":        _cap(CapabilityFamily.DOC, True),
"doc.create_datasource":   _cap(CapabilityFamily.DOC, False, "medium"),
"doc.get_datasource":      _cap(CapabilityFamily.DOC, True),
"doc.update_datasource":   _cap(CapabilityFamily.DOC, False, "medium"),
"doc.list_templates":      _cap(CapabilityFamily.DOC, True),
"doc.get_self":            _cap(CapabilityFamily.DOC, True),
"doc.get_user":            _cap(CapabilityFamily.DOC, True),
"doc.get_users":           _cap(CapabilityFamily.DOC, True),
```

---

## 4. What Gets Eliminated vs What Stays

### 4.1 Files Eliminated

| File | Lines | Reason |
|------|-------|--------|
| `orchestrator/tool_schemas.py` | ~207 | Pydantic models move to `catalog.py`, `TOOL_INPUT_MODELS` derived from catalog |
| `integrations/tool_normalizer.py` | ~185 | No normalization — use real names |
| `orchestrator/tool_policy.py` | ~231 | `FALLBACK_WRITE_TOOLS`, `FALLBACK_BLOCKED_TOOLS`, `_HIGH_RISK_TOOLS` replaced by registry `risk_level` |
| `integrations/capability_resolver.py` | ~295 | Backend selection handled by registry `backend` field; trust-tier by `source` field; health by MCP circuit breaker |

### 4.1.1 Code Blocks Eliminated (within files that stay)

| File | Code block | Lines | Reason |
|------|-----------|-------|--------|
| `orchestrator/jarvis.py` | `_NATIVE_TOOL_MAP` dict | ~15 | Native connectors eliminated (all through MCP) |
| `orchestrator/jarvis.py` | `_try_native_connector()` method | ~60 | Native dispatch path removed |
| `orchestrator/jarvis.py` | `_build_native_connector_tools()` method | ~80 | Native schema generation removed |
| `orchestrator/jarvis.py` | `internal_tools` set (hardcoded) | ~17 | Derived from `INTERNAL_TOOLS` catalog |
| `orchestrator/jarvis.py` | 8-step dispatch cascade in `_execute_tool()` | ~130 | Replaced by 3-backend match dispatch |
| `orchestrator/prompts.py` | Hardcoded tool names in Observer, Researcher, Governor, Decision Framework prompts | ~40 | Agents discover tools via MCP tool list (Section 2.4) |

### 4.2 Files Significantly Reduced

| File | What's removed | What stays |
|------|---------------|------------|
| `integrations/capabilities.py` | `TOOL_TO_CAPABILITY` (169 entries, ~193 lines), `get_capability_for_tool()` | `CAPABILITY_CATALOG`, `CapabilityFamily`, helper functions |
| `services/tool_registry.py` | `_DEFAULT_TOOLS` (230 lines), `CANONICAL_ALIASES`, `resolve_canonical()` | `ToolRegistry` class (simplified to DB CRUD + seed from catalog) |
| `services/governor.py` | Nothing removed — `AUTO_EXECUTE_ACTIONS` renamed to `AUTO_EXECUTE_DECISIONS` | Governor class. Decision-level auto-execute stays (Planner routing). Tool-level auto-execute derived from registry. |
| `orchestrator/jarvis.py` | `internal_tools` set (17 entries), `_build_native_connector_tools()`, `_build_tool_definitions()`, 8-step dispatch cascade | `_execute_tool()` (simplified match dispatch), `_get_tools_for_agent()` (reads from catalog + MCP bridge) |
| `orchestrator/agents.py` | `can_use_tool()` normalizer chain (3-step) | `can_use_tool()` (1-step registry lookup) |
| `integrations/session_pool.py` | Normalizer integration, bidirectional mapping | Direct real-name storage and dispatch |
| `orchestrator/prompts.py` | Hardcoded tool names (`gmail_*`, `browser_*`, `search`, etc.) and tool call examples | Capability-based descriptions and behavioral examples (no tool names) |

### 4.3 New Files

| File | Purpose | ~Lines |
|------|---------|--------|
| `src/tools/catalog.py` | Single source of truth: `InternalToolDef`, `ExternalToolSeed`, `INTERNAL_TOOLS`, `EXTERNAL_TOOL_SEEDS` | ~200 |
| Migration | Add `backend`, `source`, `server`, `verified` columns; remove `canonical_name` | ~30 |

### 4.4 Estimated Net Impact

- **~1000+ lines deleted** (tool_schemas.py, TOOL_TO_CAPABILITY, _DEFAULT_TOOLS, normalizer, dispatch chain, AUTO_EXECUTE_ACTIONS, tool_policy.py, native connector code in jarvis.py)
- **~350 lines added** (catalog.py with 134 seed entries, validation, migration, new capabilities)
- **~650+ net lines removed**

---

## 5. Seed-Sync Flow on Startup

```
Application starts
  |
  v
1. Read INTERNAL_TOOLS from catalog.py
   -> For each: upsert into tool_definitions with backend="internal_mcp", source="internal"
   -> Generate input_schema from Pydantic model_json_schema()
  |
  v
2. Read EXTERNAL_TOOL_SEEDS from catalog.py
   -> For each: upsert into tool_definitions with backend="external_mcp", source="seed"
   -> No input_schema (MCP server provides it at connect time)
  |
  v
3. Run startup validation
   -> Cross-validate all capabilities, agent scopes, risk levels
   -> Fail loud on any inconsistency
  |
  v
4. (Later, on MCP server connect)
   -> list_tools() discovers real tools
   -> Match against seeded entries by name
   -> Register any unknown tools as source="discovered", capability=None
```

---

## 6. Open Questions for Implementation

1. **~~Native connectors~~ RESOLVED** — All tools must be served through MCP. The 6 native Gmail connector tools (`gmail_list_unread`, `gmail_get_message`, `gmail_send_email`, `gmail_create_draft`, `gmail_archive`, `gmail_mark_read`) will be eliminated. Their functionality is provided by the Google Workspace MCP server (`sendGmailDraft`, `listGmailMessages`, etc.). The `_NATIVE_TOOL_MAP`, `_try_native_connector()`, and `_build_native_connector_tools()` in jarvis.py will be deleted. The `backend="native"` type is no longer needed.

   **⚠️ Sequencing constraint:** The Google Workspace MCP seed is currently broken (wrong executable: `google-workspace-mcp` → should be `google-workspace-worker`). Native connector elimination MUST be sequenced after: (a) fixing the seed executable, (b) live-verifying Google Workspace tool names via `list_tools()`, (c) confirming feature parity with the 6 native handlers. Until then, keep `_NATIVE_TOOL_MAP` as a fallback. See Migration Strategy (Section 6.7) Phase 3.

2. **~~`AUTO_EXECUTE_ACTIONS` in governor.py~~ RESOLVED** — This set contains decision types (`fetch_info`, `summarize`, `search`) not tool names. It operates at the Planner decision level, not the tool level. **Resolution: keep it separate.** These are two distinct policies at two distinct layers:
   - **Decision-level** (Governor): "Should the Planner's `search` decision skip approval?" → `AUTO_EXECUTE_ACTIONS` stays in `governor.py`, unchanged.
   - **Tool-level** (Registry): "Should `browser_navigate` require approval?" → derived from registry `risk_level` + `requires_approval`.

   Merging them conflates routing policy with execution policy. The Governor decides whether a *plan* can proceed; the registry decides whether a specific *tool call* needs approval. Both are needed. Rename `AUTO_EXECUTE_ACTIONS` to `AUTO_EXECUTE_DECISIONS` for clarity.

3. **~~`CapabilityResolver`~~ RESOLVED — Eliminate.** The capability resolver routes to the "best backend" (native > official MCP > user MCP) via `CapabilityBinding` DB records. With the unified registry, this layer is redundant:
   - **Backend selection**: the registry's `backend` field (`internal_mcp`, `external_mcp`, `composite`) replaces the resolver's priority-based routing.
   - **Trust-tier logic**: move to the registry layer. The `source` field (`internal`, `seed`, `discovered`) combined with seed trust configuration in `seed_installations.py` provides equivalent trust differentiation.
   - **Health checks**: the MCP circuit breaker in `session_pool.py` already handles health at the connection level.

   **Migration**: delete `capability_resolver.py` and the `capability_bindings` table. Remove step 6 from the dispatch cascade. The new 3-backend `match` dispatch (Section 3.5) subsumes all resolver functionality.

4. **Agent scope: code or DB?** — `AGENT_CAPABILITY_SCOPES` currently lives in `agents.py`. Should it stay in code (simple, changes infrequently) or move to DB (dynamic, per-workspace customization)?

5. **MCP server tool name conflicts** — If two MCP servers (e.g., user installs two different Notion MCP servers) report a tool with the same name, how to handle? Options: namespace by server name (defeats real-names principle), or reject the second install with a conflict error.

6. **Pydantic models location** — The Pydantic `BaseModel` classes for internal tools (`SearchInput`, `IngestEventInput`, etc.) move from `tool_schemas.py` to `catalog.py`. If `catalog.py` gets too large, they could live in `src/tools/schemas.py` with `catalog.py` importing them.

### 6.7 Migration Strategy

The migration must be incremental — no big-bang cutover. Each phase is independently deployable and reversible.

#### Phase 0: Fix Preconditions (no architecture changes)

Before touching tool dispatch, fix the bugs that block verification:

1. Fix Google Workspace seed executable (`google-workspace-mcp` → `google-workspace-worker`)
2. Fix Slack seed env var (`SLACK_BOT_TOKEN` → `SLACK_MCP_XOXP_TOKEN` / `SLACK_MCP_XOXB_TOKEN`)
3. Remove 3 orphan schemas (`create_task`, `get_task`, `get_goals`) from `tool_schemas.py` + `internal_tools` set
4. Add `get_goal_memories` schema to `tool_schemas.py` + `internal_tools` set
5. Fix `_call_internal_tool()` to support `communication_` prefix (unblocks communication tools)
6. Add communication tools to `tool_schemas.py` + `internal_tools` set

**Rollback:** Each fix is a standalone commit. Revert individually.

**Exit criterion:** All 19 internal tools callable by Claude. All MCP server seeds startable.

#### Phase 1: Create catalog.py + DB migration (parallel path)

Create `src/tools/catalog.py` with `INTERNAL_TOOLS` and `EXTERNAL_TOOL_SEEDS` (including `verified` field). Run the Alembic migration to add `capability`, `server`, `backend`, `source`, `verified` columns to `tool_definitions`. Seed the DB from catalog.

**Critical:** The old dispatch path continues to work. The catalog exists alongside the old files. No behavior changes yet.

**Rollback:** Drop new columns via reverse migration. Delete `catalog.py`.

**Exit criterion:** `tool_definitions` table has all 135 seed entries (19 internal + 116 external). Startup validation passes.

#### Phase 2: Switch dispatch to registry-driven (feature flag)

Add a `JARVIS_USE_UNIFIED_DISPATCH` feature flag (default `false`). When enabled:
- `_execute_tool()` uses the new 3-backend `match` dispatch (Section 3.5)
- `can_use_tool()` reads capability from registry instead of `TOOL_TO_CAPABILITY`
- Governor `is_auto_execute()` derives from registry instead of `AUTO_EXECUTE_ACTIONS` for tool-level policy
- **Clean up agent prompts** (Section 2.4): remove all hardcoded tool names from `prompts.py`. Rewrite Observer, Researcher, Governor, and Decision Framework prompts to use capability-based descriptions. Agents discover tools via the MCP tool list in the Claude API request.

When disabled: old dispatch path unchanged.

**Rollback:** Set flag to `false`. Zero code changes needed.

**Exit criterion:** All tests pass with flag both on and off. Manual smoke test of internal + external tool calls with flag on.

#### Phase 3: Eliminate native connectors (after Google Workspace MCP verified)

**Precondition:** Google Workspace MCP server starts successfully AND `list_tools()` returns tools with confirmed feature parity for all 6 native Gmail operations.

1. Live-verify Google Workspace tool names → update seeds with `verified=True`
2. Delete `_NATIVE_TOOL_MAP`, `_try_native_connector()`, `_build_native_connector_tools()`
3. Delete `CapabilityResolver` + `capability_bindings` table (per Open Question #3 resolution)

**Rollback:** Restore native connector code from git. Re-run seed for `capability_bindings`.

**Exit criterion:** Gmail send/draft/read/list operations work via Google Workspace MCP. No `_NATIVE_TOOL_MAP` references in codebase.

#### Phase 4: Delete old files (cleanup)

Only after Phase 2 flag is permanently enabled and Phase 3 is complete:

1. Delete `tool_schemas.py` (schemas now in `catalog.py`)
2. Delete `tool_normalizer.py`
3. Delete `tool_policy.py`
4. Remove `TOOL_TO_CAPABILITY` from `capabilities.py`
5. Remove `_DEFAULT_TOOLS` from `tool_registry.py`
6. Remove `internal_tools` set from `jarvis.py`
7. Remove feature flag — new dispatch is the only path
8. Rename `AUTO_EXECUTE_ACTIONS` to `AUTO_EXECUTE_DECISIONS` in `governor.py`
9. Verify agent prompts contain zero tool names (done in Phase 2, verify here)

**Rollback:** Restore files from git + revert feature flag removal.

**Exit criterion:** Success criteria 1-15 (Section 8) all met. `grep -r "TOOL_TO_CAPABILITY\|_DEFAULT_TOOLS\|tool_normalizer\|internal_tools.*set\|_NATIVE_TOOL_MAP" src/` returns zero hits. `grep -rE "gmail_|calendar_|browser_|web_search|slack_|search\(" src/orchestrator/prompts.py` returns zero hits.

#### Rollback principles

- Every phase is a separate PR. Never combine phases.
- Feature flag in Phase 2 is the critical safety valve — it enables instant rollback without code changes.
- DB migration (Phase 1) is additive only — new columns, no dropped columns. Reverse migration drops them cleanly.
- Column drops and file deletions happen last (Phase 4) after the new path is battle-tested.

---

## 7. Current File Locations (for Reference)

All paths relative to `backend/src/`:

| File | Role in current system |
|------|----------------------|
| `orchestrator/tool_schemas.py` | Pydantic models + `TOOL_INPUT_MODELS` dict + `build_tool_definitions()` |
| `orchestrator/agents.py` | `AGENT_CAPABILITY_SCOPES` + `SubAgent.can_use_tool()` |
| `orchestrator/jarvis.py` | `internal_tools` set + `_execute_tool()` (6-step dispatch) + `_get_tools_for_agent()` + `_build_tool_definitions()` + `_build_native_connector_tools()` |
| `integrations/capabilities.py` | `CAPABILITY_CATALOG` + `TOOL_TO_CAPABILITY` (340 lines) + `get_capability_for_tool()` |
| `integrations/tool_normalizer.py` | `ToolNameNormalizer` + `get_normalizer()` singleton |
| `integrations/session_pool.py` | `UserMCPSessionPool` — normalizer integration in `get_or_create_session()` and `call_tool()` |
| `integrations/capability_resolver.py` | `CapabilityResolver` — routes to best backend |
| `services/tool_registry.py` | `_DEFAULT_TOOLS` (230 lines) + `CANONICAL_ALIASES` + `ToolRegistry` class |
| `services/governor.py` | `AUTO_EXECUTE_ACTIONS` + `APPROVAL_REQUIRED_ACTIONS` + `CRITICAL_ACTIONS` + `BLOCKED_ACTIONS` |
| `tools/intelligence_server.py` | FastMCP server with actual tool implementations |
| `connectors/mcp_bridge.py` | `list_mcp_tools()` + `call_mcp_tool()` + `is_mcp_tool()` |
| `models/tool_definitions.py` | SQLAlchemy model for `tool_definitions` table |

---

## 8. Success Criteria

1. Adding a new internal tool requires editing exactly 2 files: `catalog.py` (definition) + `intelligence_server.py` (implementation)
2. Adding a new known external tool requires editing exactly 1 file: `catalog.py` (seed entry)
3. Unknown MCP tools auto-register in DB on discovery with safe defaults
4. Startup validation catches all cross-reference inconsistencies
5. Zero name normalization — real MCP names used everywhere
6. **All tools served through MCP** — no native connector code paths, no hardcoded `_NATIVE_TOOL_MAP` (after Phase 3)
7. **Native Gmail tools eliminated** — 6 hardcoded handlers replaced by Google Workspace MCP equivalents (after Phase 3, blocked on seed fix)
8. **Only 3 backend types**: `internal_mcp`, `external_mcp`, `composite` — no `native` backend
9. All live-verified tool names match registry seeds (Notion `API-*`, Playwright `browser_*`, Linear `linear_*`, Filesystem snake_case)
10. All tests pass after each migration phase
11. Net reduction of ~800+ lines (increased from 600 with native connector elimination)
12. **Unverified seeds flagged** — `verified=false` seeds auto-reconcile against `list_tools()` on first MCP connect
13. **CapabilityResolver eliminated** — no `capability_bindings` table, no priority-based backend routing (after Phase 3)
14. **`AUTO_EXECUTE_ACTIONS` renamed to `AUTO_EXECUTE_DECISIONS`** — clarifies decision-level vs tool-level policy separation
15. **Agent prompts contain zero hardcoded tool names** — prompts describe capabilities and workflows; agents discover available tools via the MCP tool list passed in the Claude API request (`get_tools_for_agent()`). `grep -r "gmail_\|calendar_\|browser_\|web_search\|slack_\|search(" src/orchestrator/prompts.py` returns zero hits.

---

## 9. Verified Tool Inventory (from reading every source file)

This section was added after tracing every tool through every file to find the actual state vs assumed state.

### 9.1 Intelligence Server Tools (`intelligence_server.py`) — ✅ LIVE-VERIFIED

FastMCP server name: `"jarvis-intelligence"`, mounted as namespace `"intelligence"` in `server.py`.
When called via `_call_internal_tool()`, names are prefixed: `search` -> `intelligence_search`.

**Live probed on 2026-03-29** via `backend/scripts/explore_tools.py --internal-only --with-schemas`.

| # | Tool name | MCP annotations | In schemas? | In `internal_tools`? | Capability | Required params |
|---|---|---|---|---|---|---|
| 1 | `ingest_event` | write, non-idempotent | YES | YES | `internal.ingest_event` | user_id, source, event_type, entity_type, entity_id, title |
| 2 | `search` | **readOnly** | YES | YES | `internal.search` | user_id, query |
| 3 | `update_entity` | write, idempotent | YES | YES | `internal.update_entity` | entity_id |
| 4 | `get_active_plans` | **readOnly** | YES | YES | `internal.get_plans` | user_id |
| 5 | `evaluate_policy` | **readOnly** | YES | YES | `internal.evaluate_policy` | user_id, plan_id |
| 6 | `approve_action` | write, **destructive**, idempotent | YES | YES | `internal.approve_action` | user_id, approval_id, decision |
| 7 | `extract_preferences` | write, non-idempotent | YES | YES | `internal.extract_preferences` | user_id, source_text |
| 8 | `get_briefing` | **readOnly** | YES | YES | `internal.get_briefing` | user_id |
| 9 | `get_observation_cursor` | **readOnly** | YES | YES | `internal.get_cursor` | user_id, source |
| 10 | `update_observation_cursor` | write, idempotent | YES | YES | `internal.update_cursor` | user_id, source, cursor_type, cursor_value |
| 11 | `report_observation` | write, idempotent | YES | YES | `internal.report_observation` | user_id, source |
| 12 | `update_execution` | write, idempotent | YES | YES | `internal.update_execution` | execution_id, status |
| 13 | `get_goal_memories` | **readOnly** | **NO** | **NO** | *unmapped* | user_id |
| 14 | `build_context` | **readOnly** | YES | YES | `internal.build_context` | user_id, query |
| 15 | `verify_run` | **readOnly** | YES | YES | `internal.verify_run` | run_id |

**MCP annotations key:** `readOnly` = readOnlyHint:true, `destructive` = destructiveHint:true, `idempotent` = idempotentHint:true, `non-idempotent` = idempotentHint:false, `write` = readOnlyHint:false.

**Schema vs MCP param mismatch (by design):** MCP tools accept `user_id*` and `workspace_id` as params, but Pydantic schemas do NOT expose these to Claude. They are injected by `_call_internal_tool()` at dispatch time. This is correct — Claude should not choose which user to act as. The unified registry must preserve this injection pattern.

### 9.2 Communication Server Tools (`communication_server.py`) — ✅ LIVE-VERIFIED

FastMCP server name: `"jarvis-communication"`, mounted as namespace `"communication"` in `server.py`.
When called via `_call_internal_tool()`, names SHOULD be prefixed `communication_` but the current code hardcodes `intelligence_` prefix — **dispatch is broken**.

| # | Tool name | MCP annotations | In schemas? | In `internal_tools`? | Capability | Required params |
|---|---|---|---|---|---|---|
| 16 | `send_telegram` | **destructive**, non-idempotent | **NO** | **NO** | `internal.send_telegram` | text |
| 17 | `send_approval_prompt` | **destructive**, idempotent | **NO** | **NO** | `internal.send_approval` | approval_id, title, summary |
| 18 | `push_ui_update` | write, idempotent | **NO** | **NO** | `internal.push_ui` | surface_id, payload, user_id |

**Note:** `send_telegram` and `send_approval_prompt` don't take `user_id` as a param — they use the bot token directly. `push_ui_update` takes `user_id` for Redis pub/sub channel routing.

### 9.3 Orphan Schemas in `tool_schemas.py` (no MCP implementation)

| Pydantic model | Key in `TOOL_INPUT_MODELS` | In `internal_tools` set? | Status |
|---|---|---|---|
| `CreateTaskInput` | `create_task` | YES | **Orphan** — standalone tasks removed in product redesign |
| `GetTaskInput` | `get_task` | YES | **Orphan** — standalone tasks removed |
| `GetGoalsInput` | `get_goals` | YES | **Orphan** — goals removed, replaced by `get_goal_memories` |
| `ReportGovernorVerdictInput` | `report_governor_verdict` | **NO** | Special-case dispatch (returns input as-is, not an MCP tool) |

### 9.4 Native Connector Tools — TO BE DELETED

These have manually written JSON schemas in `_build_native_connector_tools()` and dispatch via `_try_native_connector()` using `_NATIVE_TOOL_MAP`. **All 6 will be eliminated** — their functionality is provided by the Google Workspace MCP server. Code to delete: `_NATIVE_TOOL_MAP`, `_try_native_connector()`, `_build_native_connector_tools()` in jarvis.py.

| Tool name (to delete) | Replaced by (Google Workspace MCP) | Capability |
|---|---|---|
| `gmail_list_unread` | `listGmailMessages` | `email.list` |
| `gmail_get_message` | `readGmailMessage` | `email.read` |
| `gmail_send_email` | `sendGmailDraft` | `email.send` |
| `gmail_create_draft` | `createGmailDraft` | `email.draft` |
| `gmail_archive` | `deleteGmailMessage` (or equivalent) | `email.delete` |
| `gmail_mark_read` | *(no direct equivalent — may need custom MCP tool)* | `email.read` |

### 9.5 External MCP Servers (`seed_installations.py`) — VERIFIED via Live Probing

9 servers seeded. **Live `list_tools()` probing was performed on 2026-03-29** using `backend/scripts/explore_tools.py`. Results below are from actually launching each server.

| Server name | Package | Transport | Probe result | Actual tool count | Naming convention |
|---|---|---|---|---|---|
| `google-workspace` | `uvx google-workspace-mcp` | stdio | **BROKEN** — wrong executable | 0 (unknown) | camelCase (assumed) |
| `github` | `ghcr.io/github/github-mcp-server` (Docker) | stdio | **FAILED** — no PAT token | 0 (unknown) | snake_case (from docs) |
| `slack` | `npx slack-mcp-server` | stdio | **BROKEN** — wrong env var name | 0 (unknown) | prefixed snake (assumed) |
| `playwright` | `npx @playwright/mcp --headless` | stdio | **✅ 22 tools** | 22 | snake_case (`browser_*`) |
| `filesystem` | `npx @modelcontextprotocol/server-filesystem` | stdio | **✅ 14 tools** | 14 | snake_case |
| `linear` | `npx mcp-server-linear` | stdio | **✅ 24 tools** | 24 | prefixed snake (`linear_*`) |
| `notion` | `npx @notionhq/notion-mcp-server` | stdio | **✅ 22 tools** | 22 | `API-` prefixed kebab-case |
| `atlassian` | `npx mcp-remote@latest` → Rovo MCP | stdio | **FAILED** — needs OAuth browser flow | 0 (unknown) | camelCase (assumed) |
| `twilio` | `npx @twilio-alpha/mcp` | stdio | **FAILED** — no credentials | 0 (unknown) | unknown |

### 9.6 Composed Server Architecture (`server.py`)

```python
jarvis_tools = FastMCP("jarvis-tools")
jarvis_tools.mount(intelligence, namespace="intelligence")  # 15 tools -> intelligence_*
jarvis_tools.mount(communication, namespace="communication")  # 3 tools -> communication_*
```

The `_call_internal_tool()` method hardcodes `intelligence_` prefix (line 2651 of jarvis.py):
```python
namespaced = f"intelligence_{tool_name}"
```
This means communication server tools CANNOT be called via this path. They would need `communication_` prefix.

### 9.7 Critical Bugs Found — Complete List (Internal + External)

#### Internal Tool Bugs (from live probing)

1. **`get_goal_memories` is invisible to Claude** — exists in intelligence server (live-verified) but has no Pydantic model in `tool_schemas.py`, no entry in `internal_tools` set, and no capability mapping. Claude cannot see or call this tool.

2. **Communication tools have no dispatch path** — `send_telegram`, `send_approval_prompt`, `push_ui_update` are NOT in the `internal_tools` set. Even if they were, `_call_internal_tool()` hardcodes `intelligence_` prefix but these tools need `communication_` prefix. They appear in `agents.py` capability scopes, `tool_registry.py`, and `capabilities.py` but cannot actually be dispatched.

3. **3 orphan schemas waste Claude's tool budget** — `create_task`, `get_task`, `get_goals` are presented to Claude as callable tools (in schemas AND `internal_tools` set), but their MCP implementations don't exist (removed in product redesign). Calling them will fail with an MCP error.

4. **`get_goals` vs `get_goal_memories` incomplete rename** — `get_goals` (schema exists, implementation removed) should have been replaced by `get_goal_memories` (implementation exists, schema missing).

5. **`report_governor_verdict` is a dispatch hack** — In tool schemas (Claude can call it) and handled as a special case in `_execute_tool()` (returns input as-is). NOT an MCP tool — doesn't exist in any server. Works, but is an undocumented backdoor in the dispatch chain.

6. **18 namespaced names have NO capability mapping** — `intelligence_search`, `communication_send_telegram`, etc. are discoverable via the composed `jarvis-tools` server but are absent from `TOOL_TO_CAPABILITY`. If any code path uses the namespaced name for capability lookup, it silently fails.

7. **`tool_policy.py` adds an 8th tool identity file** — Contains `FALLBACK_WRITE_TOOLS` (65 entries), `FALLBACK_BLOCKED_TOOLS` (7 entries), and `_HIGH_RISK_TOOLS` (13 entries) that must be kept in sync with the other 7 files.

#### External Tool Bugs (from live probing)

8. **ALL 6 Notion tool names are wrong** — Registry has `create-a-page`, `update-a-page`, etc. Actual MCP names are `API-post-page`, `API-patch-page`, etc. Tool dispatch will fail for every Notion tool call.

9. **Google Workspace seed has wrong executable** — `command: "uvx", args: ["google-workspace-mcp"]` but actual package exposes `google-workspace-worker`. Server won't start.

10. **Slack seed has wrong env var** — `SLACK_BOT_TOKEN` in seed, actual server needs `SLACK_MCP_XOXP_TOKEN` / `SLACK_MCP_XOXB_TOKEN`.

11. **14 Filesystem tools completely unmapped** — ALL tools missing from tool_registry, TOOL_TO_CAPABILITY, and capability catalog. No `CapabilityFamily.FILESYSTEM` exists.

12. **17 Linear tools missing from registry** — Only 7 of 24 are registered. Plus 2 wrong alias names (`linear_comment` should be `linear_create_comment`, `linear_list_issues` should be `linear_search_issues`).

13. **7 Playwright tools unmapped + 2 wrong names** — `browser_screenshot` should be `browser_take_screenshot`, `browser_wait` should be `browser_wait_for`. `browser_pdf_save` in TOOL_TO_CAPABILITY doesn't exist in server (phantom entry).

#### Agent Scope Bugs (from live probing)

14. **Observer missing `filesystem.*` capabilities** — Can read emails/calendar/docs but not filesystem, even though Filesystem MCP server is seeded.

15. **Operator missing `calendar.delete` and `workflow.delete`** — Can create/update but cannot delete calendar events or Linear issues.

> **Note:** `browser.extract` in Researcher scope was initially suspected to be phantom, but verification confirmed it IS a real tool implemented in `src/browser/tools.py` with a valid TOOL_TO_CAPABILITY mapping and CAPABILITY_CATALOG entry. Not a bug.

#### Summary

| Category | Count |
|---|---|
| Internal tool bugs | 7 |
| External tool bugs | 6 |
| Agent scope bugs | 2 |
| **Total** | **15** |

### 9.8 Agent Capability Scopes — ✅ LIVE-VERIFIED

**Probed via** `backend/scripts/explore_tools.py --internal-only --agents`.

| Agent | # Capabilities | Role | Notable gaps |
|---|---|---|---|
| **Observer** | 27 | Read external sources, detect changes | Missing `filesystem.*` — can't observe filesystem. Missing `workflow.get_teams`, `workflow.get_user` |
| **Operator** | 29 | Execute approved plans via tools | Missing `calendar.delete` — can create/update but not delete events. Missing `workflow.delete` — can't delete Linear issues. Missing `filesystem.*` |
| **Researcher** | 32 | Deep context gathering (broadest scope) | Missing `filesystem.*`. `browser.extract` is valid (maps to `browser_extract` in `src/browser/tools.py`) |
| **Presenter** | 6 | Generate user-facing output | `internal.get_briefing`, `internal.push_ui`, `internal.send_approval`, `internal.send_telegram`, `internal.search`, `messaging.send` — minimal and correct |
| **Planner** | 2 | Produce task graphs | `internal.get_plans`, `internal.search` — correct (Planner just reads context) |
| **Governor** | 2 | Evaluate policies, gate approvals | `internal.approve_action`, `internal.evaluate_policy` — correct |
| **Librarian** | 2 | Extract entities, update world model | `internal.search`, `internal.update_entity` — correct |
| **Persona** | 2 | Learn preferences | `internal.extract_preferences`, `internal.search` — correct |

**Scope fixes needed in unified registry:**

1. Add `filesystem.*` capabilities to Observer, Operator, Researcher scopes
2. Add `calendar.delete` to Operator scope
3. Add `workflow.delete` to Operator scope
4. Add new Linear capabilities (`workflow.get_project`, `workflow.list_projects`, etc.) to appropriate scopes

### 9.9 Count Summary

| Category | Current | After Unification |
|---|---|---|
| Intelligence server tools (implemented) | 15 | 15 |
| Communication server tools (implemented) | 3 (but **invisible** to Claude) | 3 (in catalog, dispatched via `communication_` prefix) |
| Special dispatch (not MCP) | 1 (`report_governor_verdict`) | 1 (in catalog with `server="_special"`) |
| Total internal catalog entries | 18 schemas (3 orphans, 4 missing) | **19** (15 + 3 + 1) |
| Orphan schemas (no implementation) | 3 (`create_task`, `get_task`, `get_goals`) | **0** (deleted) |
| Native connector tools | 6 (all Gmail) | **0** (replaced by Google Workspace MCP) |
| External MCP servers seeded | 9 | 9 |
| External tool seeds (EXTERNAL_TOOL_SEEDS) | 59 (many wrong names) | **116** (live-verified) |
| Entries in `TOOL_TO_CAPABILITY` | 169 | **0** (replaced by registry `capability` column) |
| Entries in `_DEFAULT_TOOLS` (tool_registry) | 150 | **0** (replaced by `catalog.py`) |
| Entries in `FALLBACK_WRITE_TOOLS` (tool_policy) | 65 | **0** (replaced by registry `risk_level`) |
| Files with tool identity data | **8** | **2** (`catalog.py` + `intelligence_server.py`) |

---

## 10. Live-Probed Tool Inventory (2026-03-29)

**Methodology:** Each MCP server was launched as a real subprocess via `fastmcp.Client`, `list_tools()` was called, and the actual tool names, descriptions, and input schemas were captured. Script: `backend/scripts/explore_tools.py`.

### 10.1 Playwright MCP — 22 tools (was assumed 7+14)

**Package:** `@playwright/mcp` (headless mode)
**Naming:** `browser_*` snake_case

| # | Actual tool name | In tool_registry? | In TOOL_TO_CAPABILITY? | Capability |
|---|---|---|---|---|
| 1 | `browser_click` | YES | YES | `browser.click` |
| 2 | `browser_close` | no | YES | `browser.open` |
| 3 | `browser_console_messages` | no | YES | `browser.snapshot` |
| 4 | `browser_drag` | no | YES | `browser.click` |
| 5 | `browser_evaluate` | **no** | **no** | *unmapped* |
| 6 | `browser_file_upload` | no | YES | `browser.submit` |
| 7 | `browser_fill_form` | **no** | **no** | *unmapped* |
| 8 | `browser_handle_dialog` | no | YES | `browser.click` |
| 9 | `browser_hover` | no | YES | `browser.click` |
| 10 | `browser_install` | **no** | **no** | *unmapped* |
| 11 | `browser_navigate` | no | YES | `browser.open` |
| 12 | `browser_navigate_back` | **no** | **no** | *unmapped* |
| 13 | `browser_network_requests` | no | YES | `browser.snapshot` |
| 14 | `browser_press_key` | no | YES | `browser.type` |
| 15 | `browser_resize` | no | YES | `browser.open` |
| 16 | `browser_run_code` | **no** | **no** | *unmapped* |
| 17 | `browser_select_option` | no | YES | `browser.click` |
| 18 | `browser_snapshot` | YES | YES | `browser.snapshot` |
| 19 | `browser_tabs` | no | YES | `browser.open` |
| 20 | `browser_take_screenshot` | **no** | **no** | *unmapped* — NOTE: `browser_screenshot` in registry is wrong name |
| 21 | `browser_type` | YES | YES | `browser.type` |
| 22 | `browser_wait_for` | **no** | **no** | *unmapped* — NOTE: `browser_wait` in TOOL_TO_CAPABILITY is wrong name |

**Findings:**
- 7 tools completely unmapped: `browser_evaluate`, `browser_fill_form`, `browser_install`, `browser_navigate_back`, `browser_run_code`, `browser_take_screenshot`, `browser_wait_for`
- 2 tools have WRONG names in registry: `browser_screenshot` should be `browser_take_screenshot`, `browser_wait` should be `browser_wait_for`
- `browser_pdf_save` in TOOL_TO_CAPABILITY does NOT exist in the actual server

### 10.2 Filesystem MCP — 14 tools (was assumed 0)

**Package:** `@modelcontextprotocol/server-filesystem`
**Naming:** snake_case

| # | Actual tool name | Description | In tool_registry? | Capability |
|---|---|---|---|---|
| 1 | `create_directory` | Create a new directory or ensure it exists | no | *unmapped* |
| 2 | `directory_tree` | Get a recursive tree view as JSON | no | *unmapped* |
| 3 | `edit_file` | Make line-based edits to a text file | no | *unmapped* |
| 4 | `get_file_info` | Retrieve detailed metadata about a file | no | *unmapped* |
| 5 | `list_allowed_directories` | Returns list of allowed directories | no | *unmapped* |
| 6 | `list_directory` | Get detailed listing of files/dirs | no | *unmapped* |
| 7 | `list_directory_with_sizes` | Listing with file sizes | no | *unmapped* |
| 8 | `move_file` | Move or rename files and directories | no | *unmapped* |
| 9 | `read_file` | Read file contents (deprecated) | no | *unmapped* |
| 10 | `read_media_file` | Read image/audio file as base64 | no | *unmapped* |
| 11 | `read_multiple_files` | Read multiple files simultaneously | no | *unmapped* |
| 12 | `read_text_file` | Read file as text | no | *unmapped* |
| 13 | `search_files` | Recursively search for files by pattern | no | *unmapped* |
| 14 | `write_file` | Create or overwrite a file | no | *unmapped* |

**Findings:**
- ALL 14 tools completely missing from tool_registry, TOOL_TO_CAPABILITY, and capability catalog
- No `filesystem.*` capability family exists
- `scopes_granted: []` in seed_installations — should define filesystem capabilities
- Need new `CapabilityFamily.FILESYSTEM` with capabilities like `filesystem.read`, `filesystem.write`, `filesystem.list`

### 10.3 Linear MCP — 24 tools (was assumed 10)

**Package:** `mcp-server-linear`
**Naming:** `linear_*` prefixed snake_case

| # | Actual tool name | In tool_registry? | In TOOL_TO_CAPABILITY? | Capability |
|---|---|---|---|---|
| 1 | `linear_auth_callback` | no | no | *unmapped* |
| 2 | `linear_bulk_update_issues` | no | no | *unmapped* |
| 3 | `linear_create_comment` | YES | YES | `workflow.comment` |
| 4 | `linear_create_customer_need_from_attachment` | no | no | *unmapped* |
| 5 | `linear_create_issue` | YES | YES | `workflow.create_issue` |
| 6 | `linear_create_issues` | no | no | *unmapped* |
| 7 | `linear_create_project_milestone` | no | no | *unmapped* |
| 8 | `linear_create_project_with_issues` | no | no | *unmapped* |
| 9 | `linear_delete_comment` | no | no | *unmapped* |
| 10 | `linear_delete_issue` | YES | YES | `workflow.delete` |
| 11 | `linear_delete_project_milestone` | no | no | *unmapped* |
| 12 | `linear_edit_issue` | YES | YES | `workflow.update_issue` |
| 13 | `linear_get_issue` | YES | YES | `workflow.get` |
| 14 | `linear_get_project` | no | no | *unmapped* |
| 15 | `linear_get_project_milestones` | no | no | *unmapped* |
| 16 | `linear_get_teams` | YES | YES | `workflow.get_teams` |
| 17 | `linear_get_user` | no | no | *unmapped* |
| 18 | `linear_list_projects` | no | no | *unmapped* |
| 19 | `linear_resolve_comment` | no | no | *unmapped* |
| 20 | `linear_search_issues` | YES | YES | `workflow.search` |
| 21 | `linear_search_issues_by_identifier` | no | no | *unmapped* |
| 22 | `linear_unresolve_comment` | no | no | *unmapped* |
| 23 | `linear_update_comment` | no | no | *unmapped* |
| 24 | `linear_update_project_milestone` | no | no | *unmapped* |

**Findings:**
- Only 7 of 24 tools are registered — 17 completely missing
- Tool names match what was assumed (prefixed snake_case) — naming convention correct
- `linear_comment` and `linear_list_issues` in tool_registry do NOT match actual server names (`linear_create_comment`, `linear_search_issues`)
- New capabilities needed: `workflow.get_project`, `workflow.list_projects`, `workflow.get_user`, `workflow.bulk_update`

### 10.4 Notion MCP — 22 tools (naming completely wrong)

**Package:** `@notionhq/notion-mcp-server`
**Naming:** `API-` prefixed kebab-case (NOT plain kebab-case as assumed)

| # | Actual tool name | Assumed name in registry | In tool_registry? | Capability |
|---|---|---|---|---|
| 1 | `API-create-a-comment` | `create-a-comment` | **WRONG NAME** | `doc.comment` |
| 2 | `API-create-a-data-source` | *(not in registry)* | no | *unmapped* |
| 3 | `API-delete-a-block` | *(not in registry)* | no | *unmapped* |
| 4 | `API-get-block-children` | *(not in registry)* | no | *unmapped* |
| 5 | `API-get-self` | *(not in registry)* | no | *unmapped* |
| 6 | `API-get-user` | *(not in registry)* | no | *unmapped* |
| 7 | `API-get-users` | *(not in registry)* | no | *unmapped* |
| 8 | `API-list-data-source-templates` | *(not in registry)* | no | *unmapped* |
| 9 | `API-move-page` | *(not in registry)* | no | *unmapped* |
| 10 | `API-patch-block-children` | `append-block-children` | **WRONG NAME** | `doc.append` |
| 11 | `API-patch-page` | `update-a-page` | **WRONG NAME** | `doc.update` |
| 12 | `API-post-page` | `create-a-page` | **WRONG NAME** | `doc.create` |
| 13 | `API-post-search` | *(closest: `query-data-source`)* | **WRONG NAME** | `doc.search` |
| 14 | `API-query-data-source` | `query-data-source` | **WRONG NAME** | `doc.query` |
| 15 | `API-retrieve-a-block` | *(not in registry)* | no | *unmapped* |
| 16 | `API-retrieve-a-comment` | *(not in registry)* | no | *unmapped* |
| 17 | `API-retrieve-a-data-source` | *(not in registry)* | no | *unmapped* |
| 18 | `API-retrieve-a-database` | *(not in registry)* | no | *unmapped* |
| 19 | `API-retrieve-a-page` | `retrieve-a-page` | **WRONG NAME** (missing `API-` prefix) | `doc.get` |
| 20 | `API-retrieve-a-page-property` | *(not in registry)* | no | *unmapped* |
| 21 | `API-update-a-block` | *(not in registry)* | no | *unmapped* |
| 22 | `API-update-a-data-source` | *(not in registry)* | no | *unmapped* |

**Findings:**
- **ALL 6 Notion tool names in the registry are WRONG** — they're all missing the `API-` prefix
- 16 additional tools exist that aren't in the registry at all
- The normalizer would convert `API-create-a-comment` → `api_create_a_comment` (not `create_a_comment` as was assumed)
- This is the strongest argument for the "use real names" design decision — normalization created a false sense of coverage

### 10.5 Seed Installation Bugs Found

| Bug | Severity | Details |
|---|---|---|
| **Google Workspace: wrong executable** | CRITICAL | Seed says `command: "uvx", args: ["google-workspace-mcp"]`. Actual package exposes `google-workspace-worker`, not `google-workspace-mcp`. Error: *"An executable named 'google-workspace-mcp' is not provided"* |
| **Slack: wrong env var name** | CRITICAL | Seed says `SLACK_BOT_TOKEN`. Actual server requires `SLACK_MCP_XOXP_TOKEN` or `SLACK_MCP_XOXB_TOKEN` (or both `SLACK_MCP_XOXC_TOKEN` + `SLACK_MCP_XOXD_TOKEN`). Error: *"Authentication required: Either SLACK_MCP_XOXP_TOKEN, SLACK_MCP_XOXB_TOKEN, or both..."* |
| **GitHub: toolsets not configured** | MEDIUM | Server supports `--toolsets` flag for enabling tool groups (actions, code_security, copilot, dependabot, discussions, gists, git, issues, labels, notifications, orgs, projects, pull_requests, repos, security_advisories, stargazers, users). Default is `context, copilot, issues, pull_requests, repos, users`. Our seed doesn't pass this flag. |
| **Notion: all tool names wrong** | HIGH | All 6 seeded names lack `API-` prefix. Will fail on tool dispatch. |
| **Filesystem: no capabilities defined** | MEDIUM | `scopes_granted: []` — even if tools are discovered, no agent can use them. |

### 10.6 Gap Summary (Live vs Registry)

| Source | Registry knows | Actually has | Gap |
|---|---|---|---|
| Playwright | 7 tools + 14 capability entries | 22 tools | 7 new, 2 renamed, 1 phantom |
| Filesystem | 0 | 14 tools | ALL missing |
| Linear | 10 (7 correct names) | 24 tools | 17 new, 3 wrong names |
| Notion | 6 (ALL wrong names) | 22 tools | 22 new naming scheme |
| Google | 10 entries (assumed) | **Broken seed** | Unknown — server won't start |
| Slack | 8 entries (assumed) | **Broken seed** | Unknown — server won't start |
| GitHub | 20 entries (from docs) | Not probed (no PAT) | Unverified |
| Atlassian | 11 entries (from docs) | Not probed (needs OAuth) | Unverified |
| Twilio | 1 entry | Not probed (no creds) | Unverified |
| **Totals** | 150 registry entries | 82 live-verified + unknown | **124 tools NOT in registry** |

### 10.7 Overall Tool Counts (Live)

**Current state (fragmented, pre-unification):**

| Layer | Count | Source |
|---|---|---|
| Pydantic tool schemas (Claude API) | 18 | `tool_schemas.py` |
| Tool registry defaults | 150 (many duplicates) | `_DEFAULT_TOOLS` |
| Capability mappings | 169 | `TOOL_TO_CAPABILITY` |
| Intelligence server (live) | 15 | FastMCP `list_tools()` |
| Communication server (live) | 3 | FastMCP `list_tools()` |
| Composed jarvis-tools (live) | 18 | FastMCP `list_tools()` (namespaced) |
| Playwright (live) | 22 | MCP subprocess `list_tools()` |
| Filesystem (live) | 14 | MCP subprocess `list_tools()` |
| Linear (live) | 24 | MCP subprocess `list_tools()` |
| Notion (live) | 22 | MCP subprocess `list_tools()` |
| **Total unique tools across all live servers** | **~100** | After dedup |
| **Total entries across all static registries** | **~340** | Many duplicates/aliases |

**After unification (single catalog):**

| Category | Count | Source |
|---|---|---|
| Internal tools (`INTERNAL_TOOLS`) | 19 | `catalog.py` |
| External tool seeds (`EXTERNAL_TOOL_SEEDS`) | 116 | `catalog.py` |
| Native tools | 0 | Eliminated (all through MCP) |
| **Total seed entries** | **135** (19 internal + 116 external) | Single file |
| Files with tool identity data | **2** | `catalog.py` + `intelligence_server.py` |
| Naming systems | **1** | Real MCP names everywhere |

**External tool seeds breakdown:**

| Server | Seeds | Live-verified? |
|---|---|---|
| Google Workspace (Gmail + Calendar) | 11 | No (broken seed) |
| GitHub | 8 | No (no PAT) |
| Slack | 8 | No (wrong env var) |
| Notion | 22 | **Yes** ✅ |
| Linear | 24 | **Yes** ✅ |
| Playwright | 22 | **Yes** ✅ |
| Filesystem | 14 | **Yes** ✅ |
| Atlassian (Jira) | 6 | No (needs OAuth) |
| Composite (`web_search`) | 1 | N/A (internal) |
| Twilio | 0 | No (no creds, needs probing) |
| **Total** | **82 verified + 34 unverified** | |
