# Jarvis — Development Guide for Claude Code

## What is Jarvis

Jarvis is a **Personal AI Operating System** for founders. It continuously observes, understands, plans, acts, and briefs. It is NOT a chatbot with tools — it is an operating system with a core loop: Perceive → Understand → Update Model → Plan → Act → Communicate.

## System Boundary (non-negotiable)

**OpenClaw owns the surface. Jarvis backend owns the intelligence.**

- OpenClaw: chat channels, voice, sessions, tool dispatch, Canvas UI, cron triggers, webhook intake
- Jarvis backend: connectors, events, world model, memory, planner, governor, operator, presenter, audit
- The `jarvis-tools` plugin is a **thin bridge** — HTTP calls only, no business logic, no state

Never put business logic in the OpenClaw plugin. Never use OpenClaw sessions as durable task state. Never use OpenClaw memory as the product memory system.

## Project Structure

```
jarvis/
├── backend/                 # Python FastAPI backend (the brain)
│   ├── src/
│   │   ├── api/             # REST endpoints (called by OpenClaw plugin)
│   │   ├── config/          # Settings (pydantic-settings)
│   │   ├── connectors/      # Source connectors (Gmail, Calendar, Slack)
│   │   ├── models/          # SQLAlchemy models (Postgres)
│   │   ├── services/        # Business logic (planner, governor, operator, etc.)
│   │   └── workflows/       # Durable workflows (Temporal)
│   ├── tests/
│   ├── alembic/             # Database migrations
│   └── pyproject.toml
├── jarvis-tools/            # OpenClaw plugin (TypeScript, thin bridge)
│   ├── src/
│   │   ├── index.ts         # Plugin entry point
│   │   ├── tools.ts         # Agent tool registrations
│   │   ├── routes.ts        # HTTP route registrations (webhooks)
│   │   └── backend-client.ts
│   └── openclaw.plugin.json
├── jarvis-agent/            # OpenClaw agent config
│   └── SOUL.md              # Agent system prompt
├── docs/                    # Architecture and design docs
├── docker-compose.yml       # Local dev (Postgres + Redis)
└── openclaw.example.json5   # Example OpenClaw config
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+ / FastAPI |
| Database | PostgreSQL 17 (pgvector extension) |
| Cache/Queue | Redis 7 |
| Workflows | Temporal (or background workers for v0) |
| AI Model | Claude (Anthropic API) |
| Gateway | OpenClaw (self-hosted) |
| Plugin | TypeScript / Node.js |
| Migrations | Alembic |

## Coding Standards

### Python (backend/)

- **Style**: ruff with line-length 100
- **Types**: Use type hints everywhere. Pydantic for API schemas. SQLAlchemy mapped types for models.
- **Async**: All service methods and route handlers are async. Use `asyncpg` for DB, `httpx` for HTTP clients.
- **Naming**: snake_case for everything. Prefixed IDs (e.g., `evt_`, `plan_`, `exec_`, `mem_`, `apr_`).
- **Errors**: Let FastAPI handle HTTP errors with `HTTPException`. Never return bare dicts from endpoints — always use Pydantic response models.
- **Tests**: pytest + pytest-asyncio. Test files mirror source structure. Each service gets unit tests.
- **Imports**: Absolute imports from `src.` prefix. Ruff handles sorting.

### TypeScript (jarvis-tools/)

- **Style**: TypeScript strict mode. No `any` types.
- **Plugin rule**: Tools only do HTTP calls to backend. No business logic. No state.
- **Schema**: Use TypeBox (`@sinclair/typebox`) for tool parameter schemas.

### Database

- **Migrations**: Always use Alembic. Never modify tables by hand.
- **IDs**: String IDs with type prefix (e.g., `evt_01HXYZ`). Use ULID for ordering.
- **JSON columns**: Use JSONB for flexible data. Keep indexed columns as proper typed columns.
- **Indexes**: Add indexes for every query pattern used in services.

### API Contracts

- **Versioned**: All endpoints prefixed with `/v1/`
- **REST-first**: Use proper HTTP methods. Return stable IDs and status.
- **Internal vs external**: Internal execution APIs don't leak to OpenClaw. Only the schemas in `api/schemas.py` are external contracts.

## Architecture Rules

### Agent Roles (do not violate these boundaries)

| Agent | Responsibility | Write Scope |
|-------|---------------|-------------|
| Observer (Event Processor) | Classify, score, dedupe events | normalized_events |
| Librarian (World Model + Memory) | Update entities, extract memories | entities, relationships, memories |
| Planner | Decide what to do, produce task graphs | plans, plan_tasks |
| Governor | Evaluate policies, gate approvals | policy decisions, approvals |
| Operator | Execute approved plans | executions, execution_task_runs |
| Presenter | Generate user-facing output | briefings, canvas payloads |

Only Planner decides intent. Only Operator calls external tools. Only Presenter talks to the user. Governor sits before every external write.

### Core Contracts (freeze these before prompt tuning)

1. **Normalized Event** — the universal event schema
2. **Plan + Task Graph** — structured planner output (never free-form text)
3. **Execution State Machine** — detected → planned → policy_checked → approved → executing → completed/failed
4. **Approval** — every external write goes through approval in v1
5. **Briefing** — structured daily brief with priorities, changes, approvals, actions

### Security Rules

- Single trusted user boundary in v1
- All external writes require approval (no auto-send)
- Connector credentials encrypted at rest, never in model context
- Audit log for every external write with full correlation IDs
- Idempotency keys on all events to prevent duplicates
- Rate limit planning triggered by events

## Development Workflow

### Running locally

```bash
# Start infrastructure
docker compose up -d

# Run backend
cd backend && source .venv/bin/activate
python run.py  # starts on :8000

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

### Running OpenClaw (when ready)

```bash
openclaw onboard --anthropic-api-key "$ANTHROPIC_API_KEY"
# Edit ~/.openclaw/openclaw.json to add jarvis-tools plugin
openclaw gateway
# Open http://localhost:18789
```

## Current Phase

We are in **Phase 1: Foundation + Plugin Wiring**. The skeleton is built. Next steps:
1. Wire planner service to `/v1/jarvis/command` endpoint
2. Implement Gmail connector with real OAuth
3. Implement event processor with importance scoring
4. Build the daily briefing workflow
5. Add Alembic initial migration

## Common Mistakes to Avoid

- Do not put business logic in the OpenClaw plugin
- Do not use OpenClaw sessions for task state
- Do not let the planner output free-form text — always structured JSON
- Do not skip the Governor for external writes
- Do not store secrets in memory or model context
- Do not start with prompts — start with contracts and schemas
- Do not over-engineer with Kafka/Neo4j in v1 — Postgres + Redis is enough
