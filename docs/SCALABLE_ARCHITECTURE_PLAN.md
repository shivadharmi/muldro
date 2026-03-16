# Jarvis AI OS — Data-Driven Scalable Architecture Redesign

> **Status**: Saved for future implementation. This plan transforms all hardcoded systems
> (agents, tools, policies, routing) into database-driven, extensible systems.
> Implement this AFTER the original blueprint features are complete.

## Context

The Jarvis codebase has strong foundations (8-agent orchestrator, memory system, entity graph, event bus, A2UI frontend, 435 tests) but the core systems — agents, tools, policies, routing — are all **hardcoded in Python**. This blocks extensibility: adding a new agent, tool, policy, or connector requires code changes and redeployment.

This plan transforms every hardcoded system into a **data-driven** one. Agents, tools, policies, workflows, and routing all become database records. The orchestrator becomes a generic execution engine that reads configuration from the database. The system can evolve toward full autonomy while always respecting user-defined rules.

**No backward compatibility needed** — early stage product, clean redesign.

---

## Design Principles

1. **Everything is data**: Agents, tools, policies, workflows — all DB records, never hardcoded sets
2. **Workspace-scoped from day 1**: Every data table gets `workspace_id` FK
3. **Proper relational integrity**: Real FKs with CASCADE rules, no orphan data
4. **JSONB only for truly semi-structured data**: Typed columns for everything queryable
5. **Index for every query pattern**: Composite indexes matching actual service queries
6. **Extension without code changes**: New agent/tool/policy/connector = data insert, not deployment
7. **Autonomy with guardrails**: Policy engine is the safety layer, not hardcoded Python sets

---

## Phase Dependency Graph

```
Phase 1 (Foundation: workspace_id + tool_definitions)
    |
    +---> Phase 2 (Dynamic Agent Registry)
    |         |
    |         +---> Phase 3 (Data-Driven Orchestrator rewrite)
    |                   |
    |                   +---> Phase 4 (Policy Engine)
    |                   |         |
    |                   |         +---> Phase 10 (Autonomy + Agent Creator)
    |                   |
    |                   +---> Phase 6 (Connector Framework)
    |                   |
    |                   +---> Phase 9 (Browser + Observability)
    |
    +---> Phase 5 (Notifications + Task Handlers)
    |
    +---> Phase 7 (Memory Enhancement)
    |
    +---> Phase 8 (Workflow Registry) — needs Phase 2 + 5
```

Phases 5, 6, 7 can parallelize after their dependencies.

---

## Phase 1: Foundation — Workspace Scoping + Tool Registry

**Goal**: Add `workspace_id` FK to every data table. Create tool_definitions table. This is the foundation everything else builds on.

### Migration 017: Workspace scoping on all data tables

Add `workspace_id: String(64), ForeignKey("workspaces.workspace_id"), NOT NULL, INDEX` to these tables (30 tables total):

| Table | Notes |
|-------|-------|
| `normalized_events` | Update composite indexes to include workspace_id |
| `entities` | Update `ix_entities_user_type_name` |
| `entity_relationships` | |
| `memories` | Update composite indexes |
| `plans` | |
| `plan_tasks` | Inherits via plan FK, but add for direct query |
| `executions` | |
| `approvals` | |
| `briefings` | |
| `briefing_feedback` | |
| `audit_logs` | |
| `agent_decision_logs` | |
| `token_usage` | |
| `schedules` | |
| `triggers` | |
| `connectors` | |
| `connector_accounts` | |
| `oauth_tokens` | |
| `conversations` | |
| `goals` | |
| `trust_scores` | |
| `observation_status` | |
| `observation_cursors` | |
| `dead_letter_queue` | |
| `ui_surfaces` | |
| `working_memory` | |
| `artifacts` | |
| `procedures` | |
| `task_runs` | |
| `browser_sessions` | |

Child tables (`task_steps`, `task_checkpoints`, `messages`, `browser_actions`, `entity_aliases`, `execution_task_runs`) inherit scope through parent FK — no direct workspace_id needed.

**Migration strategy**: Add column with default `'ws_default'`, backfill, then alter to NOT NULL.

### Migration 018: Tool registry + workspace quotas

**New table: `tool_definitions`**
```
tool_id          String(64) PK                           -- "tool_abc123"
workspace_id     String(64) FK workspaces NOT NULL INDEX
name             String(128) NOT NULL                    -- "gmail_send"
display_name     String(256)
description      Text NOT NULL
category         String(32) NOT NULL                     -- communication, data_source, internal, browser
provider         String(64)                              -- gmail, slack, github, NULL for internal
input_schema     JSONB NOT NULL                          -- JSON Schema
output_schema    JSONB
risk_level       String(16) NOT NULL DEFAULT 'low'       -- low, medium, high, critical
requires_approval Boolean DEFAULT false
is_read_only     Boolean DEFAULT true
is_idempotent    Boolean DEFAULT false
timeout_ms       Integer DEFAULT 30000
rate_limit_per_minute Integer                            -- NULL = no limit
connector_type   String(32)                              -- mcp, rest, internal, browser
connector_config JSONB                                   -- MCP server name, endpoint, etc.
handler_ref      String(256)                             -- "intelligence_server.ingest_event" or MCP path
enabled          Boolean DEFAULT true
version          Integer DEFAULT 1
status           String(16) DEFAULT 'active'             -- active, deprecated, disabled
created_at, updated_at (TimestampMixin)

UNIQUE(workspace_id, name)
INDEX(workspace_id, category, enabled)
INDEX(workspace_id, provider, enabled)
```

**New table: `workspace_quotas`**
```
quota_id                 String(64) PK
workspace_id             String(64) FK workspaces UNIQUE NOT NULL
daily_token_budget_usd   Float DEFAULT 5.0
max_agents               Integer DEFAULT 20
max_tools                Integer DEFAULT 100
max_connectors           Integer DEFAULT 10
max_memory_entries       Integer DEFAULT 100000
max_events_per_day       Integer DEFAULT 10000
storage_bytes_limit      BigInteger DEFAULT 1073741824
storage_bytes_used       BigInteger DEFAULT 0
created_at, updated_at (TimestampMixin)
```

### Files to Create
- `backend/src/models/tool_definitions.py` — ToolDefinition
- `backend/src/models/workspace_quotas.py` — WorkspaceQuota
- `backend/src/services/tool_registry.py` — CRUD, lookup, `seed_default_tools(workspace_id)`
- `backend/src/services/workspace_service.py` — Workspace creation, quota mgmt, default seeding
- `backend/alembic/versions/017_add_workspace_scoping.py`
- `backend/alembic/versions/018_create_tool_registry.py`
- `backend/tests/test_tool_registry.py`
- `backend/tests/test_workspace_service.py`

### Files to Modify
- `backend/src/models/__init__.py` — Add new imports
- `backend/src/api/deps.py` — Add `get_current_workspace_id()` dependency
- `backend/src/config/settings.py` — Add `default_workspace_name`
- **Every model file** — Add workspace_id column
- **Every service file** — Add workspace_id filter to all queries

### Key Service Methods
- `ToolRegistry.register_tool(workspace_id, name, description, input_schema, ...)`
- `ToolRegistry.get_tool_by_name(workspace_id, name)`
- `ToolRegistry.list_tools(workspace_id, category=None, enabled_only=True)`
- `ToolRegistry.get_tools_as_claude_schema(workspace_id, agent_id)` — Returns Claude API tool format
- `ToolRegistry.seed_default_tools(workspace_id)` — Seeds 14 internal + MCP tools from current hardcoded `jarvis.py._build_tool_definitions()`
- `WorkspaceService.create_workspace(user_id, name)` — Creates workspace + quota + seeds defaults
- `WorkspaceService.check_quota(workspace_id, resource_type)` — Enforces limits

---

## Phase 2: Dynamic Agent Registry

**Goal**: Move agent definitions from hardcoded dicts (`AGENT_MODEL_TIERS`, `AGENT_TOOL_SCOPES`, `AGENT_PROMPTS`) to database records.

### Migration 019: Agent definitions + tool bindings + prompt versioning

**New table: `agent_definitions`**
```
agent_id              String(64) PK                      -- "agt_abc123"
workspace_id          String(64) FK workspaces NOT NULL INDEX
name                  String(128) NOT NULL               -- "observer", "planner", or custom "inbox_triager"
display_name          String(256)
description           Text
system_prompt         Text NOT NULL
model_tier            String(16) NOT NULL DEFAULT 'sonnet' -- opus, sonnet, haiku
max_tokens            Integer DEFAULT 4096
temperature           Float DEFAULT 0.3
output_schema         JSONB                              -- Expected JSON output format
behavioral_constraints JSONB                             -- {never_do: [...], always_do: [...]}
context_enrichment    Boolean DEFAULT false               -- Pre-load memories/entities into context
max_tool_rounds       Integer DEFAULT 10
budget_limit_usd_day  Float                              -- Per-agent daily cap
is_system             Boolean DEFAULT false               -- Core 8 agents (non-deletable)
enabled               Boolean DEFAULT true
version               Integer DEFAULT 1
status                String(16) DEFAULT 'active'         -- active, draft, archived
created_at, updated_at (TimestampMixin)

UNIQUE(workspace_id, name)
INDEX(workspace_id, is_system, enabled)
INDEX(workspace_id, status)
```

**New table: `agent_tool_bindings`**
```
id             Integer PK AUTOINCREMENT
workspace_id   String(64) FK workspaces NOT NULL
agent_id       String(64) FK agent_definitions ON DELETE CASCADE NOT NULL
tool_id        String(64) FK tool_definitions ON DELETE CASCADE NOT NULL
granted_at     DateTime(tz) server_default now()
granted_by     String(64)                                -- user_id or "system"

UNIQUE(agent_id, tool_id)
INDEX(agent_id)
INDEX(tool_id)
```

**New table: `agent_prompt_versions`**
```
version_id       String(64) PK
agent_id         String(64) FK agent_definitions ON DELETE CASCADE NOT NULL
version_number   Integer NOT NULL
system_prompt    Text NOT NULL
change_reason    String(512)
created_by       String(64)
created_at       DateTime(tz) server_default now()

UNIQUE(agent_id, version_number)
INDEX(agent_id, version_number DESC)
```

### Files to Create
- `backend/src/models/agent_definitions.py` — AgentDefinition, AgentToolBinding, AgentPromptVersion
- `backend/src/services/agent_registry.py` — AgentRegistry service
- `backend/src/api/routes_agents.py` — CRUD API
- `backend/alembic/versions/019_create_agent_registry.py`
- `backend/tests/test_agent_registry.py`
- `backend/tests/test_agent_routes.py`

### Files to Modify
- `backend/src/models/__init__.py` — Add imports
- `backend/src/orchestrator/agents.py` — Remove `AGENT_MODEL_TIERS`, `AGENT_TOOL_SCOPES`, `AGENT_PROMPTS`, `create_sub_agents()`, `AGENTS` global. Keep `SubAgent` dataclass with `from_db_record()` classmethod.
- `backend/src/api/app.py` — Register agent routes

### What Gets Replaced
| Hardcoded | Replaced By |
|-----------|-------------|
| `AGENT_MODEL_TIERS` dict | `agent_definitions.model_tier` column |
| `AGENT_TOOL_SCOPES` dict | `agent_tool_bindings` junction table |
| `AGENT_PROMPTS` dict | `agent_definitions.system_prompt` column |
| `create_sub_agents()` | `AgentRegistry.get_agent()` |
| `AGENTS` global | Registry lookups (cached 5min TTL) |

### API Endpoints
- `GET /v1/agents` — List agents
- `POST /v1/agents` — Create agent
- `GET /v1/agents/{agent_id}` — Detail with tools
- `PUT /v1/agents/{agent_id}` — Update (creates prompt version)
- `DELETE /v1/agents/{agent_id}` — Archive (is_system=true cannot delete)
- `PUT /v1/agents/{agent_id}/tools` — Set tool bindings
- `GET /v1/agents/{agent_id}/prompt-history` — Prompt versions

---

## Phase 3: Data-Driven Orchestrator Rewrite

**Goal**: Rewire `jarvis.py` and `hooks.py` to load agents/tools from DB instead of hardcoded dicts. The orchestrator becomes a generic execution engine.

### Files to Create
- `backend/src/services/tool_executor.py` — Unified tool execution with handler registry:
  ```python
  HANDLER_REGISTRY: dict[str, Callable] = {}

  def tool_handler(name: str):
      def decorator(fn):
          HANDLER_REGISTRY[name] = fn
          return fn
      return decorator

  class ToolExecutor:
      async def execute(self, workspace_id, tool_name, tool_input, agent_name, trace_id):
          tool_def = await self._tool_registry.get_tool_by_name(workspace_id, tool_name)
          if tool_def.connector_type == "internal":
              handler = HANDLER_REGISTRY[tool_def.handler_ref]
              return await handler(**tool_input)
          elif tool_def.connector_type == "mcp":
              return await self._call_mcp_tool(tool_def, tool_input)
  ```
- `backend/tests/test_tool_executor.py`

### Files to Modify — Major Rewrites

**`backend/src/orchestrator/jarvis.py`** (1023 lines):
- Remove `_build_tool_definitions()` (hardcoded 14 tool schemas) -> `ToolRegistry.get_tools_as_claude_schema(workspace_id, agent_id)`
- Remove `_execute_tool()` hardcoded tool_map dict -> `ToolExecutor.execute()`
- Remove `AGENTS.get(agent_name)` -> `AgentRegistry.get_agent(workspace_id, agent_name)`
- Remove `CONTEXT_ENRICHED_AGENTS` set -> `agent_definitions.context_enrichment` boolean
- `__init__` takes `workspace_id` parameter
- `_call_agent()` and `_call_agent_stream()` resolve agent from registry
- Tool filtering uses `agent_tool_bindings` instead of `SubAgent.can_use_tool()`

**`backend/src/orchestrator/hooks.py`** (213 lines):
- Remove `WRITE_TOOLS`, `READ_ONLY_TOOLS`, `BLOCKED_TOOLS` frozensets
- `governor_pre_tool_hook()` queries `tool_definitions` for `risk_level`, `requires_approval`, `is_read_only`

**`backend/src/orchestrator/agents.py`** (149 lines):
- Remove `AGENT_MODEL_TIERS`, `AGENT_TOOL_SCOPES`, `create_sub_agents()`, `AGENTS` global
- Keep `SubAgent` dataclass as DTO with `from_db_record(agent_def)` classmethod

### What Gets Replaced
| Hardcoded | Replaced By |
|-----------|-------------|
| `_build_tool_definitions()` | `ToolRegistry.get_tools_as_claude_schema()` |
| `_execute_tool()` tool_map | `ToolExecutor.execute()` |
| `_get_tools_for_agent()` | Query `agent_tool_bindings` JOIN `tool_definitions` |
| `WRITE_TOOLS` frozenset | `tool_definitions.is_read_only = false` |
| `READ_ONLY_TOOLS` frozenset | `tool_definitions.is_read_only = true` |
| `BLOCKED_TOOLS` frozenset | `tool_definitions.status = 'disabled'` |
| `CONTEXT_ENRICHED_AGENTS` | `agent_definitions.context_enrichment` |

---

## Phase 4: Data-Driven Policy Engine

**Goal**: Replace ALL hardcoded policy sets in `governor.py` with a rules engine. Policies become data with hierarchical resolution.

### Migration 020: Policy engine tables

**New table: `policy_rules`**
```
policy_id     String(64) PK                              -- "pol_abc123"
workspace_id  String(64) FK workspaces NOT NULL
name          String(256) NOT NULL
description   Text
scope_type    String(16) NOT NULL                        -- global, workspace, agent, tool, task_type
scope_ref     String(64)                                 -- agent_id, tool_id, or NULL for global
priority      Integer NOT NULL DEFAULT 100               -- Lower = higher. 0-49=system, 50-99=workspace, 100+=user
conditions    JSONB NOT NULL                             -- Matching conditions
action        String(32) NOT NULL                        -- auto_execute, approval_required, blocked, rate_limit, notify
action_config JSONB                                      -- {expires_hours: 24, notify_surfaces: ["telegram"]}
enabled       Boolean DEFAULT true
is_system     Boolean DEFAULT false                      -- System rules non-deletable
created_by    String(64)
created_at, updated_at (TimestampMixin)

INDEX(workspace_id, scope_type, enabled, priority)
INDEX(workspace_id, scope_ref)
```

Condition examples:
- `{"is_read_only": true}` — matches read-only tools
- `{"risk_level": ["high", "critical"]}` — matches risky actions
- `{"tool_name": "gmail_send", "time_window": {"start": 22, "end": 6}}` — specific tool at night
- `{"agent_name": "operator", "action_type": "send_email"}` — agent+action combo

**New table: `policy_evaluation_log`**
```
eval_id          String(64) PK
workspace_id     String(64) FK NOT NULL
user_id          String(64) NOT NULL
policy_id        String(64) FK policy_rules nullable
trigger_type     String(32) NOT NULL                     -- tool_call, plan_evaluation, scheduled_action
trigger_ref      String(128)
context_snapshot JSONB
decision         String(32) NOT NULL
reason           Text
trust_score_used Float
created_at       DateTime(tz) server_default now()

INDEX(workspace_id, user_id, created_at DESC)
INDEX(policy_id, created_at DESC)
```

### Default System Policies (seeded per workspace)

| Policy | Conditions | Action | Priority |
|--------|-----------|--------|----------|
| Block critical ops | `{"risk_level": "critical"}` | blocked | 10 |
| High risk needs approval | `{"risk_level": "high"}` | approval_required | 20 |
| Auto-execute reads | `{"is_read_only": true}` | auto_execute | 30 |
| All writes need approval | `{"is_read_only": false}` | approval_required | 50 |

### Files to Create
- `backend/src/models/policy_rules.py` — PolicyRule, PolicyEvaluationLog
- `backend/src/services/policy_engine.py` — PolicyEngine (hierarchical rule evaluation)
- `backend/src/api/routes_policies.py` — CRUD API
- `backend/alembic/versions/020_create_policy_engine.py`
- `backend/tests/test_policy_engine.py`
- `backend/tests/test_policy_routes.py`

### Files to Modify
- `backend/src/services/governor.py` — Remove `APPROVAL_REQUIRED_ACTIONS`, `AUTO_EXECUTE_ACTIONS`, `BLOCKED_ACTIONS`. `_apply_policy()` calls `PolicyEngine.evaluate()`.
- `backend/src/orchestrator/hooks.py` — `governor_pre_tool_hook()` calls `PolicyEngine.evaluate_tool_call()`
- `backend/src/services/trust_engine.py` — Trust thresholds now configurable via policy rules
- `backend/src/api/app.py` — Register policy routes

### Policy Resolution Logic
```
1. Load all enabled rules for workspace, ordered by priority ASC
2. For each rule, check if conditions match the context
3. First match wins (lowest priority number)
4. If no match, default to "approval_required"
5. If trust engine applies, check if trust score overrides -> auto_execute
6. Log evaluation to policy_evaluation_log
```

### What Gets Replaced
| Hardcoded | Replaced By |
|-----------|-------------|
| `APPROVAL_REQUIRED_ACTIONS` set | Policy rules with `action=approval_required` |
| `AUTO_EXECUTE_ACTIONS` set | Policy rules with `action=auto_execute` |
| `BLOCKED_ACTIONS` set | Policy rules with `action=blocked` |
| `_get_time_based_policy_override()` | Time_window condition in policy rules |
| Trust threshold (5 decisions, 0.9) | Configurable in policy rule action_config |

---

## Phase 5: Notification Persistence + Task Handlers

**Goal**: Persist notifications in DB with priority scoring. Replace task execution if/elif with pluggable handler registry.

### Migration 021: Notifications + task handlers

**New table: `notifications`**
```
notification_id    String(64) PK
workspace_id       String(64) FK NOT NULL
user_id            String(64) NOT NULL
notification_type  String(32) NOT NULL
title              String(512) NOT NULL
body               Text
data               JSONB
priority           Integer DEFAULT 50
source_type        String(32)
source_ref         String(128)
status             String(16) DEFAULT 'pending'
read_at            DateTime(tz)
dismissed_at       DateTime(tz)
acted_on_at        DateTime(tz)
expires_at         DateTime(tz)
created_at, updated_at (TimestampMixin)

INDEX(workspace_id, user_id, status, created_at DESC)
INDEX(workspace_id, user_id, notification_type, status)
```

**New table: `notification_deliveries`**
```
delivery_id      String(64) PK
notification_id  String(64) FK notifications ON DELETE CASCADE NOT NULL
surface          String(32) NOT NULL
status           String(16) DEFAULT 'pending'
attempt_count    Integer DEFAULT 0
sent_at          DateTime(tz)
error_message    Text
external_ref     String(256)
created_at       DateTime(tz) server_default now()

INDEX(notification_id)
INDEX(surface, status)
```

**New table: `task_handlers`**
```
handler_id    String(64) PK
workspace_id  String(64) FK NOT NULL
task_type     String(64) NOT NULL
handler_type  String(32) NOT NULL
handler_ref   String(256) NOT NULL
config        JSONB
enabled       Boolean DEFAULT true
created_at, updated_at (TimestampMixin)

UNIQUE(workspace_id, task_type)
INDEX(workspace_id, handler_type, enabled)
```

---

## Phase 6: Connector Framework

**Goal**: Bidirectional connectors (poll + webhook + actions). Connector actions auto-register as tools.

### Migration 022: Enhance connectors

**Add columns to `connectors`**: capabilities (JSONB), poll_interval_seconds, webhook_url, webhook_secret, health_status, last_health_check_at, error_count, last_error

**New table: `connector_events`** — event_id, workspace_id, connector_id (FK), direction, event_type, payload_hash, status, error_message, created_at

---

## Phase 7: Memory Enhancement

**Goal**: Contradiction detection, memory supersession, multi-factor retrieval ranking.

### Migration 023: Memory enhancements

**Add columns to `memories`**: superseded_by (FK self), supersedes (FK self), contradiction_flag, access_count, retrieval_boost, source_count

**New table: `memory_contradictions`** — contradiction_id, workspace_id, memory_id_a (FK), memory_id_b (FK), contradiction_type, description, resolution, resolved_at, resolved_by, detected_at

### Ranking Formula
```
score = 0.35*embedding_similarity + 0.20*recency_decay + 0.15*confidence
      + 0.10*access_frequency + 0.10*source_count_bonus + 0.05*stability + 0.05*retrieval_boost
```

---

## Phase 8: Workflow Registry

**Goal**: Multi-step orchestrated workflows as data.

### Migration 024: Workflow tables

**New table: `workflows`** — workflow_id, workspace_id, name, description, trigger_type, trigger_config (JSONB), steps (JSONB), input_schema, output_schema, enabled, is_system, version, status

**New table: `workflow_runs`** — run_id, workspace_id, workflow_id (FK), user_id, status, input_data, output_data, current_step, step_results, error, started_at, completed_at, trace_id

---

## Phase 9: Browser Automation + Observability

**Goal**: Playwright session pool, browser safety via policy engine, MCP health persistence.

### Migration 025: Browser pool + observability

**New table: `browser_pool`** — pool_entry_id, workspace_id, session_id (FK), status, allocated_at, released_at, max_session_duration_seconds

**New table: `mcp_server_health`** — health_id, workspace_id, server_name, status, last_check_at, response_time_ms, error_message, consecutive_failures, circuit_state

---

## Phase 10: Autonomy Path + Agent Creator

**Goal**: Autonomy graduation system and meta-agent that creates new agents through conversation.

### Migration 026: Autonomy + agent creation

**New table: `autonomy_levels`** — level_id, workspace_id, name, description, ordinal, policy_overrides (JSONB), graduation_criteria (JSONB), is_current, activated_at

**New table: `agent_creation_sessions`** — session_id, workspace_id, user_id, status, conversation_history (JSONB), proposed_agent (JSONB), created_agent_id (FK)

### Autonomy Graduation
- **Level 0 (Supervised)**: All actions need approval
- **Level 1 (Guided)**: Read-only + low-risk internal auto-execute
- **Level 2 (Assisted)**: Low-risk external writes auto-execute for trusted action types (trust > 0.85)
- **Level 3 (Autonomous)**: All actions auto-execute except high/critical risk
- User can always override level manually. System NEVER self-promotes past user-set maximum.

### Agent Creator Flow
1. User: "create an agent for X"
2. Orchestrator routes to AgentCreator meta-agent
3. AgentCreator converses to understand purpose, constraints
4. Proposes: name, prompt, model tier, tool scope, constraints
5. Shows preview, gets confirmation
6. Calls `AgentRegistry.create_agent()` + `bind_tools()`
7. New agent immediately available for routing

---

## File Summary

| Phase | New Files | Modified Files | Migrations | Tests |
|-------|-----------|----------------|------------|-------|
| 1 | 4 | ~35 (workspace_id on all) | 2 | 2 |
| 2 | 4 | 3 | 1 | 2 |
| 3 | 2 | 3 | 0 | 1 |
| 4 | 4 | 4 | 1 | 2 |
| 5 | 6 | 4 | 1 | 2 |
| 6 | 4 | 5 | 1 | 1 |
| 7 | 4 | 2 | 1 | 2 |
| 8 | 4 | 2 | 1 | 1 |
| 9 | 5 | 3 | 1 | 2 |
| 10 | 6 | 3 | 1 | 2 |
| **Total** | **~43** | **~25 unique** | **10** | **~17** |
