# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Jarvis

Jarvis is a **Personal AI Operating System** for founders. It is NOT a chatbot — it is an OS with a core loop: Perceive → Understand → Update Model → Plan → Act → Communicate.

## Architecture

Multi-agent hub-and-spoke: a central `JarvisOrchestrator` (`backend/src/orchestrator/jarvis.py`) routes to 8 sub-agents via Claude API. Internal FastMCP servers wrap the intelligence layer; external MCP servers provide connectors (Google, GitHub, Slack).

```
User <-> Telegram Bot / Next.js Frontend (A2UI)
              |
         JarvisOrchestrator (Claude API)
         Routes to: Observer, Librarian, Planner, Governor,
                    Operator, Presenter, Researcher, Persona
              |
         Tool Layer: FastMCP (intelligence + communication) + external MCP servers
              |
         Intelligence Backend (Postgres + Redis)
         EventProcessor, WorldModel, MemoryService, Planner,
         Governor, Operator, Presenter, Audit, DLQ
```

**Key paths:**
- Orchestrator + agents: `backend/src/orchestrator/` (jarvis.py, agents.py, agent_loop.py, hooks.py, prompts.py, tracing.py, budget.py, perception.py, recovery.py, intent_classifier.py, api_circuit_breaker.py)
- Services (business logic): `backend/src/services/` (planner, governor, operator, presenter, memory_service, world_model, event_processor, etc.)
- MCP tool servers: `backend/src/tools/` (intelligence_server.py, communication_server.py, mcp_config.py)
- Runtime contracts: `backend/src/orchestrator/contracts.py` (PlannerOutput, PolicyDecision, StepResult, ToolCallRequest, DomainEvent, WorkspaceSurfaceMetadata, WorkspaceSurfacePush)
- A2UI component system: `backend/src/ui/` (contracts.py, renderer.py, views.py)
- A2UI surface builder: `backend/src/services/surface_builder.py` (SurfaceService)
- API routes: `backend/src/api/` (30 routers, all `/v1/` prefixed)
- SQLAlchemy models: `backend/src/models/` (54 tables, all workspace-scoped)
- Frontend: `frontend/src/` (Next.js + A2UI renderer + chat split-pane, 7 pages)
- Infra: `infra/` (Terraform for AWS) + `docker-compose.yml` (local dev)

## Commands

### Backend (run from `backend/`)

```bash
# Infrastructure (Postgres, Redis, MinIO, Elasticsearch, Qdrant, Neo4j)
docker compose up -d

# Run backend API server (port 8000)
source .venv/bin/activate
python run.py

# Run with background worker (StreamConsumer + Scheduler)
python run.py --worker

# Run with Telegram bot
python run.py --worker --bot

# Tests
pytest tests/ -v                          # all tests
pytest tests/test_planner.py -v           # single file
pytest tests/test_planner.py::test_name -v  # single test
pytest tests/ -v -k "planner"             # keyword filter

# Lint and format
ruff check src/ tests/
ruff format src/ tests/
ruff check src/ tests/ --fix              # auto-fix

# Database migrations (from backend/)
alembic revision --autogenerate -m "description"
alembic upgrade head
```

### Frontend (run from `frontend/`)

```bash
npm install
npm run dev     # dev server (port 3000)
npm run build   # production build
npm run lint    # eslint
```

## Configuration

All backend settings via env vars with `JARVIS_` prefix (pydantic-settings in `src/config/settings.py`). Key vars: `JARVIS_DATABASE_URL`, `JARVIS_REDIS_URL`, `JARVIS_ANTHROPIC_API_KEY`, `JARVIS_USE_BEDROCK`, `JARVIS_TELEGRAM_BOT_TOKEN`, `JARVIS_TELEGRAM_CHAT_ID`, `JARVIS_LOG_JSON`, `JARVIS_DAILY_TOKEN_BUDGET_USD`. Uses `.env` file.

## Coding Standards

### Python

- **ruff**: line-length 100, target py312, rules: E, F, I, N, W
- **Async everywhere**: all service methods and route handlers are async. Use `asyncpg` for DB, `httpx` for HTTP.
- **Types**: type hints everywhere. Pydantic for API schemas. SQLAlchemy mapped types for models.
- **Naming**: snake_case. Prefixed IDs with ULID (e.g., `evt_`, `plan_`, `exec_`, `mem_`, `apr_`).
- **Imports**: absolute from `src.` prefix. Ruff handles sorting.
- **Errors**: `HTTPException` for HTTP errors. Always use Pydantic response models, never bare dicts.
- **Tests**: pytest + pytest-asyncio (asyncio_mode = "auto"). Test files mirror `src/` structure. Use `make_mock_settings()` from `tests/conftest.py`. Mock Anthropic client via `@patch("src.orchestrator.jarvis.get_anthropic_client")`.

### Frontend (React/Next.js)

- **Hooks order**: NEVER place hooks after conditional returns (`if (!x) return`). All `useState`, `useEffect`, `useCallback`, etc. must be called unconditionally at the top of the component. Move early returns below all hook calls.
- **No side effects during render**: Never assign `window.location.href` or mutate refs (`ref.current = ...`) directly in the component body. Use `useEffect` for side effects like navigation and ref updates.
- **No synchronous setState in effects**: Avoid calling `setState` synchronously in `useEffect` bodies. Instead, derive state from props/params (compute during render) or use lazy `useState` initializers for values from `localStorage`/URL params.
- **Lazy state initialization**: For state derived from `localStorage` or other sync browser APIs, use `useState(() => getValueFromStorage())` instead of `useState(null)` + `useEffect(() => setState(...))`.
- **Router navigation**: Use Next.js `useRouter().replace()` inside `useEffect` for redirects, never `window.location.href`.

### Database

- Always use Alembic for migrations. String IDs with type prefix + ULID. JSONB for flexible data; proper typed columns for indexed fields.

### API

- All endpoints `/v1/` prefixed. REST-first with proper HTTP methods.

## Agent Boundaries (do not violate)

| Agent | Role | Write Scope |
|-------|------|-------------|
| Observer | Read sources, detect changes, ingest events | normalized_events |
| Librarian | Extract entities, update world model | entities, relationships, memories |
| Planner | Produce task graphs (structured JSON, never free-form) | plans, plan_tasks |
| Governor | Evaluate policies, gate approvals | policy decisions, approvals |
| Operator | Execute approved plans via MCP tools | task_runs, task_steps |
| Presenter | Generate user-facing output | briefings, A2UI surfaces (via SurfaceService + renderer.py) |
| Researcher | Deep context gathering | None (read-only) |
| Persona | Learn preferences | memories (preference type) |

**Only Planner decides intent. Only Operator executes external actions. Only Presenter talks to the user. Governor sits before every external write.**

## Agent Routing & Execution

The `RouteResolver` (`src/services/route_resolver.py`) maps Planner decisions to agent pipelines via DB-backed routes. 16 default routes are seeded on startup.

**Decision → Pipeline mapping:**
| Decision | Pipeline | Handler |
|----------|----------|---------|
| `create_task` | Governor → Operator (execute_plan) | GraphExecutor DAG |
| `draft_reply` | Governor → Operator (execute_plan) | `_draft_action` → Gmail draft |
| `read_source` | Observer → Presenter | Tool calls (gmail_*, calendar_*) |
| `research` | Researcher | search_memory, web tools |
| `observe` | Observer | Background observation |
| `remember` | Librarian | Entity/memory updates |
| `add_to_brief` | Librarian | Stores as `briefing_item` memory |
| `search_memory` | Researcher | Knowledge search |
| `watcher_create` | Observer | Watcher setup |
| `goal_update` | Planner | Goal modification |
| `set_goal` | (direct handler) | `_handle_set_goal` → memory |
| `set_instruction` | (direct handler) | `_handle_set_instruction` → trigger/schedule |
| `schedule_reminder` | (direct handler) | `_handle_schedule_reminder` → one-shot schedule |
| `answer_directly` | (Presenter only) | Context-based answer |
| `ask_user/recommend/summarize` | (Presenter only) | Format for user |
| `acknowledge` | (Presenter only) | Default fallback |

**Direct handlers** (`set_goal`, `set_instruction`, `schedule_reminder`, `add_to_brief`) execute before pipeline resolution in both `process_message` and `process_message_stream`.

**Route conditions:** `has_key`, `has_truthy_key`, `not_has_key`, `field:<name>`, direct key=value.

## Agent Prompt Architecture

System prompts are split into two parts (`src/orchestrator/prompts.py`):
- `JARVIS_SOUL_CORE` — shared by all 8 agents (role, agent table, rules)
- `JARVIS_DECISION_FRAMEWORK` — Planner-only (decision routing logic)

Only the Planner sees the decision framework. Other agents receive only the core soul + their role prompt. This prevents non-Planner agents from making routing decisions.

## Intent Classification

Fast Haiku-based intent classification is extracted into `src/orchestrator/intent_classifier.py`:
- `classify_intent()` — calls Haiku, returns `(intent, confidence, sources)`
- `intent_to_decision()` — synthesizes lightweight PlannerOutput from fast intents
- `extract_decision()` — parses structured JSON from Planner response text
- Constants: `FAST_INTENTS`, `INTENT_CONFIDENCE_THRESHOLD` (0.7), `VALID_PERCEPTION_SOURCES`

Fast intents (`greeting`, `chitchat`, `simple_question`, `data_fetch`, `status_query`, `approval_response`) skip the Planner entirely and route directly to the appropriate agent.

## Data Flow

Observer → EventProcessor (normalize, score, dedup) → Librarian (entities, memories) → Planner (task graphs) → Governor (policy/approval gate) → Operator (execute) → Presenter (deliver via Telegram/A2UI)

## A2UI System (Agent-to-UI)

A2UI is the dynamic interface generation layer. Backend agents produce typed component trees that the frontend renders via a recursive React dispatcher.

**Backend pipeline:**
```
SurfaceService (surface_builder.py) or _push_workspace_surface (jarvis.py)
  → uses renderer.py builders: card(), heading(), text(), badge(), button(), alert(), etc.
  → uses views.py generators: briefing_full_view(), dashboard_view(), etc.
  → produces A2UISurface with populated children[]
  → delivered via: GET /v1/workspace/surfaces (REST) or jarvis:a2ui:{user_id} (Redis → WebSocket)
  → persisted to ui_surfaces table (24h TTL)
```

**Frontend pipeline:**
```
fetchWorkspaceSurfaces() or useJarvisWs hook
  → A2UISurface objects with children[]
  → useSurfaceStore (Zustand) — single store for all surface state
  → A2UIRenderer (renderer.tsx) — 27-case switch dispatcher
  → 29 React components in components/a2ui/components/
```

**Key files:**
- Contracts: `src/ui/contracts.py` (A2UIComponent, A2UISurface, ComponentType enum — 25+ types)
- Builders: `src/ui/renderer.py` (36 builder functions: card, text, button, table, metric, etc.)
- Views: `src/ui/views.py` (10 view generators: briefing, dashboard, execution trace, etc.)
- Surface builder: `src/services/surface_builder.py` (SurfaceService — builds workspace surfaces from DB)
- WS surface push: `src/orchestrator/jarvis.py` `_push_workspace_surface()` + `_build_surface_children()`
- Notifier: `src/services/notifier.py` `_deliver_web()` (approval surfaces with A2UI buttons)
- Frontend renderer: `frontend/src/components/a2ui/renderer.tsx`
- Frontend store: `frontend/src/stores/surface-store.ts` (single Zustand store)
- Workspace: `frontend/src/app/page.tsx` → `workspace-canvas.tsx` (pure A2UIRenderer grid)
- Chat: `frontend/src/app/chat/page.tsx` → split-pane layout (chat left, surfaces right)

**Surface kinds:** summary, briefing, plan, checklist, approval, comparison, alert, timeline, table, recommendation, activity

**Decision → Surface mapping** (in `_push_workspace_surface`):
- create_task → plan, draft_reply/recommend → recommendation, summarize/research/read_source/observe → summary, add_to_brief → briefing, set_goal/set_instruction/answer_directly/search_memory/remember → summary, schedule_reminder → alert

**API:** `GET /v1/workspace/surfaces` — unified endpoint returning pre-built A2UI surfaces. Replaces separate canvas/dashboard, home, approvals calls on the workspace page.

**Do not:** create surfaces with empty `children[]`. Always use `renderer.py` builders to populate component trees. Do not create client-side surface conversion (e.g., `approvalToSurface()`). Do not use `useSurfaceState` hook (deleted — use `useSurfaceStore` only).

## Execution State Machine

`detected → planned → policy_checked → approved → executing → completed/failed`

TaskRun statuses: `pending, running, paused, awaiting_approval, completed, failed, cancelled, blocked, partially_completed, archived, timed_out`

TaskStep statuses: `pending, running, completed, failed, skipped, cancelled, awaiting_approval, blocked, timed_out`

State transitions are enforced by `src/services/execution_state.py` — never mutate status directly, use `transition_run()` / `transition_step()`.

Every external write requires approval in v1. Audit log with correlation IDs on all external writes.

## Runtime Resilience (agent_loop.py)

- **Tool timeout**: 60s via `asyncio.wait_for`. Timed-out tools return `{"error": "...", "timed_out": true}`.
- **API retry**: 3 attempts with exponential backoff (2s/4s/8s) for `anthropic.RateLimitError` only.
- **API circuit breaker**: `AnthropicCircuitBreaker` (`src/orchestrator/api_circuit_breaker.py`) tracks failures per model. CLOSED/OPEN/HALF_OPEN states, 5-failure threshold, 120s cooldown. When OPEN, agent_loop yields LoopError without calling the API. Mirrors `MCPCircuitBreaker` pattern.
- **Tool error signaling**: Error dicts (`{"error": "..."}`) are flagged with `is_error: true` in tool results so Claude knows the tool failed.
- **Governor approval notification**: When the governor hook creates an approval, the Notifier immediately sends an `approval_request` notification to the user (Telegram + Web). Tool-level approvals now include `run_id` and `artifact_refs` (tool_name + tool_params) for resume after approval.
- **Background task tracking**: `_spawn_background()` replaces bare `asyncio.create_task()`. Tasks are tracked in `_background_tasks` set with done-callback cleanup. `shutdown()` awaits pending tasks.
- **Perception idempotency**: `pending_run=False` is set atomically BEFORE running perception cycles, preventing the next scheduler tick from double-picking the same source.
- **EventBus init race**: `asyncio.Lock()` with double-check pattern guards lazy `_ensure_event_bus()` initialization.
- **JSON parsing protection**: `_draft_action` and `_summarize_action` in GraphExecutor catch `JSONDecodeError` with graceful fallback.
- **Budget hydration**: BudgetTracker in-memory counter hydrates from DB on day change (survives restarts).
- **Input validation**: `process_message` and `process_message_stream` reject empty `user_id`, `workspace_id`, or `message` before starting a trace.

## Background Tasks

The `SchedulerLoop` (`src/services/scheduler.py`) runs a `_tick_background_tasks()` method every 30s that picks up `TaskRun` records with `status="pending"` and `source="background"`, executes them via `GraphExecutor`, and notifies the user on completion.

## Conversation Context

`_load_conversation_history` loads up to 20 messages (8000 chars) including `metadata_` column. Assistant messages are annotated with their decision type (e.g., `Assistant [create_task]: ...`), giving downstream agents execution lineage. When history overflows, older messages are summarized via Haiku (`_summarize_history`) and prepended as `[Earlier conversation summary]`. Most recent 5 messages are kept verbatim.

## Intelligence Loop (Soul)

The system learns from execution outcomes and synthesizes across perception sources:

- **Outcome learning** (`_learn_from_outcome`): After each run completes, checks for linked approval decisions. Approved/rejected actions are stored as preference memories. Failed plans are stored as task_context memories (30-day TTL). These flow into future context packs via the preference system.
- **Cross-source synthesis**: When 2+ perception sources have new events in the same scheduler tick, triggers a Planner synthesis call to identify cross-cutting insights. Throttled to once per 30 minutes, budget-aware.
- **Explicit preference injection**: `ContextBuilder.build()` fetches ALL active preferences via `get_user_preferences()` and merges with semantic matches, ensuring preferences always influence decisions even when they don't match the current query semantically.
- **Context enrichment**: `_assemble_context()` builds a full ContextPack with entities, memories, preferences, graph relationships (Neo4j), related runs, procedures, artifacts, goals, constraints, and risks. Rendered as structured sections in the agent system prompt.

## Multi-Tenant Workspace Isolation

All 54 data tables are scoped by `workspace_id` (NOT NULL FK to `workspaces`). Only 5 tables are user-level: `users`, `workspaces`, `workspace_members`, `sessions`, `magic_links`. Global tables: `agents`, `agent_routes`, `user_settings`.

- API routes: resolve workspace via `get_current_workspace_id()` dependency (reads from session, zero queries)
- Background services: resolve via `resolve_workspace_id(db, user_id)` helper (queries WorkspaceMember)
- No default users — every function requires explicit `user_id` from auth context

## Git

- Do NOT add `Co-Authored-By` lines to commit messages
- Follow conventional commits: `<type>: <description>`

## Common Mistakes

- Do not let the planner output free-form text — always structured JSON
- Do not skip the Governor for external writes
- Do not store secrets in memory or model context
- Do not over-engineer — Postgres + Redis is the core stack
- Do not use bare `db = db_factory()` — always `async with db_factory() as db:` + `await db.commit()`
- Do not mutate TaskRun/TaskStep status directly — use `transition_run()` / `transition_step()`
- Do not hardcode user IDs — resolve from auth context
- Do not push A2UI surfaces with empty `children[]` — use `renderer.py` builders
- Do not create client-side surface conversion functions — all surfaces are built server-side by `SurfaceService` or `_push_workspace_surface()`
- Do not use `useSurfaceState` hook — it was deleted. Use `useSurfaceStore` (Zustand) as the single surface store
- Do not add new REST endpoints for workspace data — add methods to `SurfaceService` instead
- Do not add new PlannerOutput decision types without a matching route in `DEFAULT_ROUTES` (`route_resolver.py`) — unrouted decisions fall to the `acknowledge` fallback and nothing executes
- Do not put `<decision_framework>` in non-Planner agent prompts — only `JARVIS_SOUL_CORE` is shared; `JARVIS_DECISION_FRAMEWORK` is Planner-only
- Do not add direct decision handlers only to `process_message` — always wire into BOTH `process_message` and `process_message_stream` (the chat UI uses the streaming path)
- Do not use `has_key` condition for plan_id checks — use `has_truthy_key` since Pydantic dumps include `plan_id: null`
- Do not use `JARVIS_SOUL` directly — use `JARVIS_SOUL_CORE` (all agents) + `JARVIS_DECISION_FRAMEWORK` (Planner only)
- Do not use bare `asyncio.create_task()` in jarvis.py — use `self._spawn_background()` for lifecycle tracking
- Do not import `classify_intent`/`extract_decision`/`intent_to_decision` from `jarvis.py` — they moved to `src/orchestrator/intent_classifier.py`
- Do not create tool-level approvals without `run_id` and `artifact_refs` — the approval resume path needs these to re-execute the tool after user approval
- Do not skip `workspace_id` when calling `_assemble_context()` or `ContextBuilder.build()` — preferences and related runs are workspace-scoped
