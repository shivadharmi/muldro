# Tool Resolution & MCP Architecture

## Unified Registry Dispatch

All tools are served through MCP. When an agent requests a tool, the orchestrator does one registry lookup and one match on `backend`:

```mermaid
sequenceDiagram
    participant A as Agent (Claude)
    participant O as Orchestrator
    participant H as Governor Pre-Hook (Audit-Only)
    participant R as ToolRegistry (DB)
    participant I as Internal FastMCP
    participant M as MCP Bridge
    participant C as Composite Handler
    participant AU as Audit Post-Hook

    A->>O: tool_use(name, input)

    Note over O,H: Pre-dispatch: Audit logging only
    O->>H: governor_pre_tool_hook(name, input, agent)
    H-->>O: {allowed: true} (always, unless blocked)

    Note over O,R: One registry lookup
    O->>R: get_tool(name) → backend, server, enabled
    alt Unknown or disabled
        R-->>O: None or enabled=false
        O-->>A: error
    end

    Note over O,I: Match on backend
    alt backend = "internal_mcp"
        O->>I: _call_internal_tool(name, input, server_prefix)
        I-->>O: result
    else backend = "external_mcp"
        O->>M: call_mcp_tool(name, input) — real MCP name, no normalization
        M-->>O: result
    else backend = "composite"
        O->>C: _call_composite_tool(name, input) — e.g., web_search
        C-->>O: result
    else server = "_special"
        O-->>O: return input as-is (report_governor_verdict)
    end

    O->>AU: audit_post_tool_hook(name, input, result, trace)
    O-->>A: tool_result
```

## Tool Catalog (Single Source of Truth)

Tool identity lives in 2 files:
- **`src/tools/catalog.py`** — Definitions (`InternalToolDef` + `ExternalToolSeed`)
- **`src/tools/intelligence_server.py`** — MCP function implementations

### Internal Tools

Defined as `InternalToolDef` entries in `catalog.py`. Served via in-process FastMCP.

| Tool | Server | Purpose |
|------|--------|---------|
| `ingest_event` | intelligence | Normalize, score, dedup raw events |
| `search` | intelligence | Unified search via TriSearch (Qdrant + FTS + Neo4j) |
| `evaluate_policy` | intelligence | Governor policy check |
| `get_briefing` | intelligence | Retrieve daily briefing |
| `get_observation_cursor` | intelligence | Read source cursor |
| `update_observation_cursor` | intelligence | Write source cursor |
| `report_observation` | intelligence | Record observation results |
| `approve_action` | intelligence | Process approval decision |
| `update_execution` | intelligence | Update execution status |
| `update_entity` | intelligence | Create/update entity |
| `get_active_plans` | intelligence | List in-flight plans |
| `extract_preferences` | intelligence | Learn user preferences |
| `get_goal_memories` | intelligence | Retrieve goal memories |
| `build_context` | intelligence | Assemble context pack |
| `verify_run` | intelligence | Verify execution output |
| `store_memory` | intelligence | Store a memory (any type) |
| `store_preference` | intelligence | Store a user preference |
| `get_plan_details` | intelligence | Get detailed plan info |
| `discover_capabilities` | intelligence | List available capabilities for Planner |
| `report_governor_verdict` | _special | Return input as-is (inline dispatch) |
| `push_ui_update` | communication | Push A2UI surface update |

### Capability Summary

The Planner does not receive raw tool schemas. Instead, `generate_capability_summary()` in `src/orchestrator/capability_summary.py` produces a ~200-token XML summary of available capabilities, injected into the Planner system prompt via the `{capability_summary}` placeholder. This keeps the Planner context lean while giving it enough information to produce capability-based plans.

### External Tool Seeds

Defined as `ExternalToolSeed` entries in `catalog.py`. Served via external MCP servers.

| Server | Tools | Verified | Naming |
|--------|-------|----------|--------|
| Google Workspace | 17 | Yes | snake_case (`search_gmail_messages`, `get_events`, etc.) |
| GitHub | 22 | No | snake_case (`create_pull_request`, `list_issues`, etc.) |
| Slack | 8 | No | snake_case (`slack_post_message`, `slack_get_channel_history`) |
| Notion | 22 | Yes | `API-` kebab-case (`API-post-page`, `API-patch-page`) |
| Linear | 18 | Yes | `linear_` snake_case (`linear_create_issue`, `linear_get_issue`) |
| Playwright | 22 | Yes | `browser_` snake_case (`browser_navigate`, `browser_snapshot`) |
| Filesystem | 14 | Yes | snake_case (`read_text_file`, `write_file`, `search_files`) |
| Atlassian | 13 | No | camelCase (`getJiraIssue`, `createJiraIssue`) |
| Composite | 1 | N/A | `web_search` (multi-MCP orchestration) |

### Unknown Discovered Tools

When an MCP server connects and reports tools not in the catalog, they auto-register:
- `capability=None` → invisible to all agents until admin maps capability
- `source="discovered"`, `risk_level="medium"`, `requires_approval=True`
- Safe by design — deny by default

## Authorization: Capability-Based

Agents have capability scopes (not tool lists). `SubAgent.can_use_tool()` does one registry lookup:

```
tool_name → ToolRegistry.get_tool() → tool.capability → check agent scope
```

| Agent | Capability Scope Summary |
|-------|-------------------------|
| Perceiver | email.*, calendar.*, doc.*, messaging.*, issue.*, repo.*, workflow.*, filesystem.read/list/search + internal cursor/observation/ingest tools + search.web, browser.* |
| Librarian | internal.update_entity, internal.search, internal.store_memory |
| Planner | internal.get_plans, internal.get_goals, internal.search, internal.store_memory, system.discovery |
| Governor | internal.evaluate_policy, internal.approve_action, internal.get_plan_details |
| Operator | email.send/draft/reply + email.list/read/search, calendar.*, messaging.*, issue.*, repo.*, workflow.*, doc.create/update/comment/append + internal.update_execution |
| Presenter | internal.get_briefing, internal.search, internal.push_ui, messaging.send |
| Persona | internal.search, internal.extract_preferences, internal.store_preference |

**Note:** 7 agents total. Perceiver replaces the former Observer + Researcher agents. Governor is edge-case-only (fires only when policy evaluation is needed).

## Authorization: TrustEngine in GraphExecutor

Governor hooks (`governor_pre_tool_hook`) are **audit-only** — they always return `{allowed: true}` (unless the tool is explicitly blocked). Approval gating has moved to `TrustEngine` in `GraphExecutor`:

- `TrustEngine` (`src/services/trust_engine.py`) evaluates whether a tool call requires user approval based on trust tier, risk level, and approval history
- Approval gates fire inside `GraphExecutor` per-step, not at the hook level
- This separation keeps the agent loop fast (no blocking on approval checks) while ensuring all external writes go through proper authorization

## MCP Bridge

Connected via `src/connectors/mcp_bridge.py`. Session pool manages per-user authenticated connections with circuit breaking.

### Session Pool

- Per `(workspace_id, server_name, user_id)` sessions
- Real MCP names stored and dispatched directly — no normalization
- Circuit breaker per server (consecutive failure tracking, cooldown)
- Retry with exponential backoff for transient errors

### Startup Flow

```
App startup → seed_defaults() (tools from catalog)
            → validate_registry() (6 cross-checks)
            → initialize_mcp_bridge()
            → Connect to configured MCP servers
            → list_tools() on each server
            → Register discovered tools in DB
```

## Audit Logging

Every tool invocation is logged via the audit post-hook:

```
AgentDecisionLog:
    log_id, trace_id, span_id, agent_name,
    tool_name, input_summary (500 chars),
    output_summary (500 chars), tokens_used, latency_ms
```
