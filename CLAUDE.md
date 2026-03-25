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
- Orchestrator + agents: `backend/src/orchestrator/` (jarvis.py, agents.py, hooks.py, tracing.py, budget.py, perception.py, recovery.py)
- Services (business logic): `backend/src/services/` (planner, governor, operator, presenter, memory_service, world_model, event_processor, etc.)
- MCP tool servers: `backend/src/tools/` (intelligence_server.py, communication_server.py, mcp_config.py)
- Runtime contracts: `backend/src/orchestrator/contracts.py` (PlannerOutput, PolicyDecision, StepResult, ToolCallRequest, DomainEvent, WorkspaceSurfaceMetadata, WorkspaceSurfacePush)
- A2UI component system: `backend/src/ui/` (contracts.py, renderer.py, views.py)
- A2UI surface builder: `backend/src/services/surface_builder.py` (SurfaceService)
- API routes: `backend/src/api/` (30 routers, all `/v1/` prefixed)
- SQLAlchemy models: `backend/src/models/` (49 tables, all workspace-scoped)
- Frontend: `frontend/src/` (Next.js + A2UI renderer + chat split-pane, 22 pages)
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
- create_task → plan, draft_reply/recommend → recommendation, summarize/research → summary, add_to_brief → briefing, set_goal → summary, schedule_reminder → alert

**API:** `GET /v1/workspace/surfaces` — unified endpoint returning pre-built A2UI surfaces. Replaces separate canvas/dashboard, home, approvals calls on the workspace page.

**Do not:** create surfaces with empty `children[]`. Always use `renderer.py` builders to populate component trees. Do not create client-side surface conversion (e.g., `approvalToSurface()`). Do not use `useSurfaceState` hook (deleted — use `useSurfaceStore` only).

## Execution State Machine

`detected → planned → policy_checked → approved → executing → completed/failed`

TaskRun statuses: `pending, running, paused, awaiting_approval, completed, failed, cancelled, blocked, partially_completed, archived, timed_out`

TaskStep statuses: `pending, running, completed, failed, skipped, cancelled, awaiting_approval, blocked, timed_out`

State transitions are enforced by `src/services/execution_state.py` — never mutate status directly, use `transition_run()` / `transition_step()`.

Every external write requires approval in v1. Audit log with correlation IDs on all external writes.

## Multi-Tenant Workspace Isolation

All 49 data tables are scoped by `workspace_id` (NOT NULL FK to `workspaces`). Only 5 tables are user-level: `users`, `workspaces`, `workspace_members`, `sessions`, `magic_links`. Global tables: `agents`, `agent_routes`, `user_settings`.

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
