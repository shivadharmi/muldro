# Unified Tool Registry — Design Spec

**Date:** 2026-03-29
**Status:** Design approved, pending implementation plan
**Scope:** Internal + External MCP tool architecture redesign

---

## 1. Problem Statement

Adding or removing a single tool (like `search`) requires touching 7 files with 3 naming systems. Miss any one and you get a silent failure.

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
5. **Capability mapping is a 340-line hardcoded dict** — `TOOL_TO_CAPABILITY` maps every possible name variant (camelCase, kebab-case, snake_case, namespaced, bare). Fragile, redundant, un-auditable
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

### 2.4 External MCP tools don't need Pydantic schema definitions

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

### 2.5 Capabilities stay for agent authorization

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

#### Population 1: Internal Tools (18 tools — we own the code)

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

INTERNAL_TOOLS: list[InternalToolDef] = [
    InternalToolDef(
        name="search",
        input_model=SearchInput,
        capability="internal.search",
        server="intelligence",
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
    ),
    InternalToolDef(
        name="get_briefing",
        input_model=GetBriefingInput,
        capability="internal.get_briefing",
        server="intelligence",
    ),
    InternalToolDef(
        name="get_observation_cursor",
        input_model=GetObservationCursorInput,
        capability="internal.get_cursor",
        server="intelligence",
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
    ),
    InternalToolDef(
        name="extract_preferences",
        input_model=ExtractPreferencesInput,
        capability="internal.extract_preferences",
        server="intelligence",
    ),
    InternalToolDef(
        name="create_task",
        input_model=CreateTaskInput,
        capability="internal.create_task",
        server="intelligence",
    ),
    InternalToolDef(
        name="get_task",
        input_model=GetTaskInput,
        capability="internal.get_task",
        server="intelligence",
    ),
    InternalToolDef(
        name="get_goals",
        input_model=GetGoalsInput,
        capability="internal.get_goals",
        server="intelligence",
    ),
    InternalToolDef(
        name="build_context",
        input_model=BuildContextInput,
        capability="internal.build_context",
        server="intelligence",
    ),
    InternalToolDef(
        name="verify_run",
        input_model=VerifyRunInput,
        capability="internal.verify_run",
        server="intelligence",
    ),
    InternalToolDef(
        name="report_governor_verdict",
        input_model=ReportGovernorVerdictInput,
        capability="internal.report_governor_verdict",
        server="intelligence",
    ),
]
```

**What is derived from this single list:**
- Claude tool schemas: `input_model.model_json_schema()`
- Dispatch routing: `server` field (e.g., `intelligence` -> call `intelligence_search`)
- Governor policy: `risk_level` field
- DB seed: auto-seeded into `tool_definitions` table
- Internal tools set: `{t.name for t in INTERNAL_TOOLS}` (replaces hardcoded set in jarvis.py)

#### Population 2: Known External Tools (seeded, pre-configured connectors)

```python
@dataclass(frozen=True)
class ExternalToolSeed:
    name: str                        # "sendGmailDraft" — REAL MCP tool name
    capability: str                  # "email.draft"
    risk_level: str = "medium"
    requires_approval: bool = True
    connector: str = "gmail"         # which integration/connector

EXTERNAL_TOOL_SEEDS: list[ExternalToolSeed] = [
    # ── Gmail MCP (camelCase tool names) ──
    ExternalToolSeed("sendGmailDraft", "email.send", "high", True, "gmail"),
    ExternalToolSeed("createGmailDraft", "email.draft", "medium", True, "gmail"),
    ExternalToolSeed("listGmailMessages", "email.list", "low", False, "gmail"),
    ExternalToolSeed("readGmailMessage", "email.read", "low", False, "gmail"),
    ExternalToolSeed("searchGmail", "email.search", "low", False, "gmail"),
    ExternalToolSeed("deleteGmailMessage", "email.delete", "critical", True, "gmail"),
    # ── Calendar MCP (camelCase) ──
    ExternalToolSeed("createCalendarEvent", "calendar.create", "medium", True, "calendar"),
    ExternalToolSeed("updateCalendarEvent", "calendar.update", "medium", True, "calendar"),
    ExternalToolSeed("deleteCalendarEvent", "calendar.delete", "critical", True, "calendar"),
    ExternalToolSeed("listCalendarEvents", "calendar.list", "low", False, "calendar"),
    ExternalToolSeed("getCalendarEvent", "calendar.get", "low", False, "calendar"),
    # ── GitHub MCP (snake_case) ──
    ExternalToolSeed("create_pull_request", "repo.create_pr", "high", True, "github"),
    ExternalToolSeed("merge_pull_request", "repo.merge_pr", "high", True, "github"),
    ExternalToolSeed("update_pull_request", "repo.update_pr", "medium", True, "github"),
    ExternalToolSeed("list_issues", "issue.list", "low", False, "github"),
    ExternalToolSeed("search_issues", "issue.search", "low", False, "github"),
    ExternalToolSeed("search_code", "repo.search_code", "low", False, "github"),
    ExternalToolSeed("issue_write", "issue.create", "medium", True, "github"),
    ExternalToolSeed("add_issue_comment", "issue.comment", "medium", True, "github"),
    # ── Slack MCP (prefixed snake_case) ──
    ExternalToolSeed("slack_send_message", "messaging.send", "high", True, "slack"),
    ExternalToolSeed("slack_reply_to_thread", "messaging.reply", "high", True, "slack"),
    ExternalToolSeed("slack_add_reaction", "messaging.react", "medium", True, "slack"),
    ExternalToolSeed("slack_list_channels", "messaging.list_channels", "low", False, "slack"),
    ExternalToolSeed("slack_get_channel_history", "messaging.get_history", "low", False, "slack"),
    ExternalToolSeed("slack_get_thread_replies", "messaging.get_thread", "low", False, "slack"),
    ExternalToolSeed("slack_get_users", "messaging.get_users", "low", False, "slack"),
    ExternalToolSeed("slack_get_user_profile", "messaging.get_profile", "low", False, "slack"),
    # ── Notion MCP (kebab-case) ──
    ExternalToolSeed("create-a-page", "doc.create", "medium", True, "notion"),
    ExternalToolSeed("update-a-page", "doc.update", "medium", True, "notion"),
    ExternalToolSeed("retrieve-a-page", "doc.get", "low", False, "notion"),
    ExternalToolSeed("query-data-source", "doc.query", "low", False, "notion"),
    ExternalToolSeed("create-a-comment", "doc.comment", "medium", True, "notion"),
    ExternalToolSeed("append-block-children", "doc.append", "medium", True, "notion"),
    # ── Linear MCP ──
    ExternalToolSeed("linear_create_issue", "workflow.create_issue", "medium", True, "linear"),
    ExternalToolSeed("linear_edit_issue", "workflow.update_issue", "medium", True, "linear"),
    ExternalToolSeed("linear_create_comment", "workflow.comment", "medium", True, "linear"),
    ExternalToolSeed("linear_search_issues", "workflow.search", "low", False, "linear"),
    ExternalToolSeed("linear_get_teams", "workflow.get_teams", "low", False, "linear"),
    ExternalToolSeed("linear_delete_issue", "workflow.delete", "critical", True, "linear"),
    # ── Jira MCP (camelCase) ──
    ExternalToolSeed("getJiraIssue", "issue.get", "low", False, "jira"),
    ExternalToolSeed("searchJiraIssuesUsingJql", "issue.search", "low", False, "jira"),
    ExternalToolSeed("createJiraIssue", "issue.create", "medium", True, "jira"),
    ExternalToolSeed("editJiraIssue", "issue.update", "medium", True, "jira"),
    ExternalToolSeed("transitionJiraIssue", "issue.transition", "medium", True, "jira"),
    ExternalToolSeed("addCommentToJiraIssue", "issue.comment", "medium", True, "jira"),
    # ── Playwright MCP ──
    ExternalToolSeed("browser_navigate", "browser.open", "medium", False, "browser"),
    ExternalToolSeed("browser_snapshot", "browser.snapshot", "low", False, "browser"),
    ExternalToolSeed("browser_screenshot", "browser.screenshot", "low", False, "browser"),
    # ── Research ──
    ExternalToolSeed("web_search", "search.web", "low", False, "browser"),
]
```

**Key difference from current system:** One entry per tool using the REAL MCP name. No `gmail_send` AND `sendGmailDraft` AND `gmail_send_email` mapping to the same thing. Just the name the MCP server actually uses.

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
backend         VARCHAR DEFAULT 'external_mcp',  -- 'internal_mcp', 'external_mcp', 'native', 'composite'
source          VARCHAR DEFAULT 'seed',           -- 'internal', 'seed', 'discovered', 'override'

-- Remove (no longer needed)
-- canonical_name  -- normalizer concept, eliminated
```

**UNIQUE constraint:** `(workspace_id, name)` — a tool name is unique within a workspace.

### 3.5 Dispatch: Registry-Driven

**Current dispatch** (6 special cases in `_execute_tool`):
```
report_governor_verdict -> special case
web_search -> special case
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
            # server="intelligence" -> MCP name is "intelligence_search"
            mcp_name = f"{tool.server}_{tool_name}"
            return await self._call_internal_tool(mcp_name, tool_input)
        case "external_mcp":
            # Real name goes directly to MCP bridge — no translation
            return await call_mcp_tool(tool_name, tool_input, user_id=user_id, workspace_id=workspace_id)
        case "native":
            return await self._call_native_tool(tool_name, tool_input, user_id=user_id)
        case "composite":
            return await self._call_composite_tool(tool_name, tool_input, user_id=user_id, workspace_id=workspace_id)
```

Four backends, one lookup, zero hardcoded sets.

**Backend types:**
| Backend | Description | Example tools |
|---------|-------------|--------------|
| `internal_mcp` | In-process FastMCP server | `search`, `ingest_event`, `evaluate_policy` |
| `external_mcp` | External MCP server via session pool | `sendGmailDraft`, `create_pull_request` |
| `native` | Direct API connector (Python code) | `gmail_list_unread` (if kept as native) |
| `composite` | Special multi-step handler | `web_search` (Playwright MCP internally) |

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

---

## 4. What Gets Eliminated vs What Stays

### 4.1 Files Eliminated

| File | Lines | Reason |
|------|-------|--------|
| `orchestrator/tool_schemas.py` | ~207 | Pydantic models move to `catalog.py`, `TOOL_INPUT_MODELS` derived from catalog |
| `integrations/tool_normalizer.py` | ~185 | No normalization — use real names |

### 4.2 Files Significantly Reduced

| File | What's removed | What stays |
|------|---------------|------------|
| `integrations/capabilities.py` | `TOOL_TO_CAPABILITY` (340 lines), `get_capability_for_tool()` | `CAPABILITY_CATALOG`, `CapabilityFamily`, helper functions |
| `services/tool_registry.py` | `_DEFAULT_TOOLS` (230 lines), `CANONICAL_ALIASES`, `resolve_canonical()` | `ToolRegistry` class (simplified to DB CRUD + seed from catalog) |
| `services/governor.py` | `AUTO_EXECUTE_ACTIONS` set (6 entries) | Governor class (uses registry for auto-execute decisions). Note: verify during implementation whether this set is tool-level or decision-level. |
| `orchestrator/jarvis.py` | `internal_tools` set (17 entries), `_build_native_connector_tools()`, `_build_tool_definitions()`, 6-step dispatch chain | `_execute_tool()` (simplified match dispatch), `_get_tools_for_agent()` (reads from catalog + MCP bridge) |
| `orchestrator/agents.py` | `can_use_tool()` normalizer chain (3-step) | `can_use_tool()` (1-step registry lookup) |
| `integrations/session_pool.py` | Normalizer integration, bidirectional mapping | Direct real-name storage and dispatch |

### 4.3 New Files

| File | Purpose | ~Lines |
|------|---------|--------|
| `src/tools/catalog.py` | Single source of truth: `InternalToolDef`, `ExternalToolSeed`, `INTERNAL_TOOLS`, `EXTERNAL_TOOL_SEEDS` | ~200 |
| Migration | Add `backend`, `source`, `server` columns; remove `canonical_name` | ~30 |

### 4.4 Estimated Net Impact

- **~800+ lines deleted** (tool_schemas.py, TOOL_TO_CAPABILITY, _DEFAULT_TOOLS, normalizer, dispatch chain, AUTO_EXECUTE_ACTIONS)
- **~200 lines added** (catalog.py, validation, migration)
- **~600 net lines removed**

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

1. **Native connectors** — Currently `gmail_list_unread`, `gmail_get_message`, etc. have hardcoded handlers in jarvis.py. Should these become MCP tools (served by a native-connector MCP server) or stay as `backend="native"` with explicit handlers?

2. **`AUTO_EXECUTE_ACTIONS` in governor.py** — This set contains decision types (`fetch_info`, `summarize`, `search`) not tool names. It operates at the Planner decision level, not the tool level. Verify during implementation whether it should merge into registry or remain as a separate decision-level policy.

3. **`CapabilityResolver`** — The capability resolver (`src/integrations/capability_resolver.py`) routes tools to the "best backend" (native > official MCP > user MCP) based on capability bindings. With the unified registry, does this layer still add value, or can backend selection be handled by the registry's `backend` field?

4. **Agent scope: code or DB?** — `AGENT_CAPABILITY_SCOPES` currently lives in `agents.py`. Should it stay in code (simple, changes infrequently) or move to DB (dynamic, per-workspace customization)?

5. **MCP server tool name conflicts** — If two MCP servers (e.g., user installs two different Notion MCP servers) report a tool with the same name, how to handle? Options: namespace by server name (defeats real-names principle), or reject the second install with a conflict error.

6. **Pydantic models location** — The Pydantic `BaseModel` classes for internal tools (`SearchInput`, `IngestEventInput`, etc.) move from `tool_schemas.py` to `catalog.py`. If `catalog.py` gets too large, they could live in `src/tools/schemas.py` with `catalog.py` importing them.

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
6. All tests pass after migration
7. Net reduction of ~600 lines
