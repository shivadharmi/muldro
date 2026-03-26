## Jarvis: MCP-First Architecture, Platform Foundation, and Workspace-First Redesign

### Summary

This branch delivers a full-stack overhaul of Jarvis across 4 workstreams:

1. **Platform foundation**: New integrations framework, models, migrations, services, API routes, frontend shell system with stores, command workspace, and surface dock.
2. **MCP-first architecture**: FastMCP composed server, OAuth providers, session pool, workspace MCP pool, production hardening.
3. **Perception hardening**: Circuit breaker, adaptive backoff, EventProcessor service wiring, SLO checks.
4. **Workspace-first product redesign**: 27 pages reduced to 5 surfaces. Goals absorbed into memory. Intent-based instructions replace admin config UI.

**292 files changed, +20,163 / -12,209 lines. 34 commits. 1,032 tests passing.**

---

### 1. Platform Foundation (15 commits)

New database models, services, API routes, and frontend infrastructure built before the MCP and redesign phases.

**Backend -- New Models & Migrations (032-040)**
- `approval_policy.py`: Capability-pattern approval rules (glob matching, trust tier thresholds)
- `capability_binding.py`: Capability-to-backend mapping with priority-ordered fallback
- `connector_installation.py`: MCP server installations (replaces old mcp_config.py)
- `server_trust.py`: MCP server trust tiers (T0-T3)
- `runtime_event.py`: Lifecycle events for SSE streaming
- `mcp_server_catalog.py`: Browsable MCP server catalog
- `org_allowlist.py`: MCP server allowlist/blocklist policies
- `integration_audit.py`: Cross-boundary audit events
- `webhook_subscription.py`: External webhook registrations

**Backend -- New Services**
- `agent_analytics.py`, `route_analytics.py`: Per-agent and routing performance metrics
- `capability_health.py`, `connector_insight.py`: Tool and connector health monitoring
- `approval_policy_engine.py`, `approval_impact.py`: Policy evaluation and approval pattern analysis
- `briefing_read_model.py`: CQRS denormalized briefing read model
- `evidence_bundle.py`: Evidence artifact collection for approval decisions
- `home_feed.py`: Home dashboard data aggregation
- `memory_influence.py`: Track which memories drive planner decisions
- `runtime_events.py`, `runtime_projection.py`: Real-time event emission and forecasting
- `unified_search.py`: Multi-source search orchestrator
- `workspace_provisioner.py`: Per-workspace onboarding provisioner

**Backend -- New API Routes**
- `routes_home.py`: Home feed aggregate endpoint
- `routes_integrations.py`: MCP installation lifecycle
- `routes_mcp.py`: MCP server catalog, allowlist, audit
- `routes_runtime.py`: Runtime summary, activity, runs, agents

**Backend -- Orchestrator Overhaul**
- Overhauled orchestrator workflows and tool layer
- Gateway-first tool exposure with native Gmail tools
- Mode-aware routing (ask/plan/execute)
- Runtime event emission from graph executor
- Removed old Connector model, unified on ConnectorInstallation

**Frontend -- Shell System**
- `shell/top-bar.tsx`: Global command input + activity badge
- `shell/command-launcher.tsx`: Global modal (Cmd+K), command history, fuzzy search
- `shell/context-sidebar.tsx`: Context/Evidence/Activity tabs with entity and memory refs
- `shell/surface-dock.tsx`: Right-pane surface management with position controls
- `shell/activity-strip.tsx`: Minimal bar showing latest runtime event
- `shell/center-pane-surface.tsx`: Full-screen surface modal

**Frontend -- Stores**
- `stores/command-store.ts`: Active conversation, mode, cached messages, command history
- `stores/shell-store.ts`: Workspace state, sidebar toggles, launcher state
- `stores/surface-store.ts`: Generated surface management (4 positions)
- `stores/activity-store.ts`: Live system events with SSE subscription

**Frontend -- Feature Components**
- Command workspace with auto-submit from launcher
- Evidence panel for approval context
- Home feed components: priority items, live activity, recommendations, intelligence feed
- Briefing list and detail views
- Search result grouping and detail pane

---

### 2. MCP-First Architecture (5 commits)

**Phase 1 -- Composed Server** (`11ff32c`)
- Mounted intelligence + communication servers under one FastMCP instance (`src/tools/server.py`)
- FastMCP Context + ToolAnnotations on all 23 internal tools
- MCP Resources (`entities://`, `plans://`) for live data
- MCP Prompts for 5 agent templates (plan_execution, policy_evaluation, etc.)
- In-process Client dispatch replaces hardcoded handler dicts
- Component Manager enabled for runtime tool control

**Phase 2 -- OAuth & Session Pool** (`dc4c552`, `57e85a7`)
- Auth providers (`src/integrations/auth_providers.py`): Google, GitHub, Discord built-in + OAuthProxy for 6 more
- Tool normalizer (`src/integrations/tool_normalizer.py`): camelCase/kebab to snake_case, bidirectional map
- Session pool (`src/integrations/session_pool.py`): Per-user authenticated MCP Client instances with circuit breaking
- `gateway.py` eliminated -- trust in resolver, CB in resilience, tools in pool
- OAuth callback wired to MCP session refresh in routes_auth.py

**Phase 3 -- Dynamic Workspace Pool** (`2a88b1d`)
- WorkspaceMCPPool (`src/integrations/mcp_pool.py`): Per-workspace add/remove/reload at runtime
- Onboarding wired: activate() -> pool.add_server(), revoke() -> pool.remove_server()
- DB-backed initialization at startup, dynamic add/remove at runtime

**Phase 4 -- Production Hardening** (`d01f8c5`)
- Structured errors (`src/integrations/mcp_errors.py`): classify, sanitize, envelope
- Retry with backoff for transient errors (timeout/429/503), 3x with jitter
- Per-server health monitoring: call counts, error rate, p50/p95 latency
- Error masking: `FASTMCP_MASK_ERROR_DETAILS=true` strips internals in production
- All 12 bare `str(e)` errors in intelligence_server replaced with `make_error_response(e)`

---

### 3. Perception Hardening (6 commits)

- Circuit breaker and adaptive backoff for perception coordinator
- Wired MemoryService, WorldModel, DeadLetterService, EventBus, Notifier, Planner into EventProcessor
- Fixed EventProcessor instantiation in perception cycle
- Added `cursor_type` class attribute to all connectors for type-safe cursor handling
- Fixed SLO check logging format error
- Removed dead gateway code from run.py
- Added perception integration tests

---

### 4. Workspace-First Product Redesign (5 commits + 3 fixes)

**Problem**: 27 pages, 17 sidebar items, 7-tab system health page, trigger/workflow/schedule config forms. Users overwhelmed with developer-facing internals.

**Solution**: 4 core surfaces -- Workspace, Chat, Connectors, Settings.

**Phase 1 -- Backend Cleanup** (`b8920ce`)
- Removed standalone tasks (model, service, routes, MCP tools) -- orphaned from core loop
- Absorbed goals into memory system (`memory_type="goal"`, retrieved via `memory_service.retrieve()`)
- Extracted TrustScore to own model file
- Removed user-facing CRUD routes: triggers, workflows, schedules, agents, agent-routes, executions
- Kept TriggerEngine + Scheduler as internal services
- Replaced `get_goals` MCP tool with read-only `get_goal_memories`
- Removed `create_task`, `get_task` MCP tools
- Cleaned cascade references across 15 files
- Deleted `trace_explanation.py` (production dead code)

**Phase 2 -- Frontend Cleanup** (`5a91506`)
- Deleted 18 page directories, ~17 component directories
- Sidebar: 5 sections / 17 items -> flat 5 items (Workspace, Chat, Search, Connectors, Settings)
- Cleaned api.ts: removed ~50 unused API functions (1,100 -> 669 lines)
- Rewrote settings page: 475 -> 186 lines (Account, Preferences, Policy, Budget)

**Phase 3 -- Workspace Canvas** (`224c946`)
- Living surface grid as primary UI -- Jarvis pushes surfaces, user does not navigate pages
- Pending approvals as workspace cards with inline approve/reject buttons
- Briefing headline, priority items, recommended actions rendered as surfaces
- WebSocket-connected via `useJarvisWs()` for real-time proactive surfaces
- Surfaces without `source_message_id` go to workspace; chat surfaces stay inline
- Empty state with CTA to chat or connect sources

**Phase 4 -- Merged Connectors** (`39a1f67`)
- Unified OAuth providers + MCP servers on one page
- Collapsible "Advanced: MCP Servers" section (lazy-loaded)
- Removed 6 dead integration API functions from api.ts

**Phase 5 -- Intent-Based Instructions** (`48274d0`)
- Added `set_goal` and `set_instruction` to PlannerOutput decision Literal
- Added `InstructionSpec` model: instruction_text, instruction_type (trigger/schedule/preference), trigger_conditions, schedule_config
- Orchestrator handles directly: stores as memory, optionally creates Trigger or Schedule via `self._db_factory()`
- Updated Planner prompt with instruction decision examples and guidelines
- Added `store_goal_memory()` and `store_instruction_memory()` to MemoryService
- Preferences panel in Settings shows active goals + instructions with remove button
- Added `DELETE /v1/memories/{id}` endpoint for archiving

---

### Bugfixes

- **pgvector numpy formatting** (`f2b4f84`): `str(numpy_array)` produces space-separated values. Added `_vec_to_pg()` helper for 5 raw SQL call sites in memory_service and world_model.
- **Private API access** (`b5e8d81`): Refactored `_handle_set_instruction` to use public `store_instruction_memory()` and `self._db_factory()` instead of accessing private `memory_svc._embedder` and `memory_svc._db`.
- **OAuthManager perception** (`57e5013`): Pass settings to OAuthManager during perception poll.

---

### Breaking Changes

| Change | Impact |
|--------|--------|
| 8 API route files deleted | `/v1/tasks/*`, `/v1/goals/*`, `/v1/triggers/*`, `/v1/workflows/*`, `/v1/schedules/*`, `/v1/agents/*`, `/v1/agent-routes/*`, `/v1/executions/*` return 404 |
| `models/tasks.py` deleted | `tasks` + `task_dependencies` tables unused (migration to drop pending) |
| `models/goals.py` deleted | `goals` table unused (migration to drop pending) |
| `models/connectors.py` deleted | Replaced by `connector_installation.py` |
| `tools/mcp_config.py` deleted | Replaced by MCP composed server + session pool |
| MCP tools removed | `create_task`, `get_task`, `get_goals` no longer available to agents |
| MCP tool added | `get_goal_memories` (read-only) replaces `get_goals` |
| New decision types | `set_goal`, `set_instruction` added to PlannerOutput |

### Migration Required

- Migrations 032-040 add new tables (run `alembic upgrade head`)
- Follow-up migration needed to drop `tasks`, `task_dependencies`, `goals` tables (not included)

---

### Testing

- **Backend**: 1,032 tests passing, 0 failures (1 pre-existing skip: `test_pinned_briefing_actions`)
- **Frontend**: TypeScript 0 errors, ESLint 0 errors (2 pre-existing warnings)
- **Linting**: ruff all checks passed
- **New test files**: 18 test files added covering integrations, capabilities, perception, approvals, runtime
