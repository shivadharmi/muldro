# Jarvis — Development Guide for Claude Code

## What is Jarvis

Jarvis is a **Personal AI Operating System** for founders. It continuously observes, understands, plans, acts, and briefs. It is NOT a chatbot with tools — it is an operating system with a core loop: Perceive → Understand → Update Model → Plan → Act → Communicate.

## Architecture

Multi-agent hub-and-spoke topology with 8 specialized sub-agents orchestrated by a central JarvisOrchestrator. External MCP servers provide connectors (Google Workspace, GitHub, Slack, etc.), while internal FastMCP servers wrap the intelligence layer.

```
User <-> Telegram Bot / Web Frontend (Next.js + A2UI)
              |
         JarvisOrchestrator (Claude API)
         Routes to 8 sub-agents:
         Observer, Librarian, Planner, Governor,
         Operator, Presenter, Researcher, Persona
              |
         Tool Layer (MCP Servers)
         Internal: Intelligence + Communication
         External: Google Workspace, GitHub, Slack, etc.
              |
         Jarvis Intelligence Backend
         +-------------------------------------+
         | EventProcessor (score, dedup)       |
         | WorldModel + MemoryService          |
         | (entities, relationships, pgvector) |
         | Planner (Claude structured planning)|
         | Governor (policy + approval gates)  |
         | Operator (execution tracking)       |
         | Presenter (briefings, A2UI, notify) |
         | Audit + DLQ + Heartbeat + Locking   |
         +-------------------------------------+
```

## Project Structure

```
jarvis/
├── backend/                 # Python FastAPI backend
│   ├── src/
│   │   ├── api/             # REST + WebSocket endpoints
│   │   ├── config/          # Settings, logging (pydantic-settings)
│   │   ├── interface/       # Telegram bot
│   │   ├── middleware/      # Observability, security
│   │   ├── models/          # SQLAlchemy models (Postgres)
│   │   ├── orchestrator/    # JarvisOrchestrator, agents, prompts, hooks, tracing, budget
│   │   ├── services/        # Business logic (planner, governor, operator, etc.)
│   │   ├── tools/           # FastMCP intelligence + communication servers
│   │   ├── ui/              # A2UI contracts and rendering helpers
│   │   └── workflows/       # High-level workflow compositions
│   ├── tests/
│   ├── alembic/             # Database migrations
│   └── pyproject.toml
├── frontend/                # Next.js + A2UI dynamic UI
│   └── src/
│       ├── app/             # Pages and layout
│       ├── components/      # A2UI renderer + Jarvis chat panel
│       ├── hooks/           # WebSocket + surface state hooks
│       └── lib/             # API client + A2UI types
├── infra/                   # Terraform (AWS: EC2, VPC, Route53, IAM, SSM)
│   └── scripts/             # deploy.sh, backup-postgres.sh
├── docs/                    # Architecture and design docs
└── docker-compose.yml       # Local dev (Postgres + Redis)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13+ / FastAPI |
| Database | PostgreSQL 17 (pgvector extension) |
| Cache/Queue | Redis 7 (caching, rate limiting, locks, task streams) |
| AI Model | Claude (Anthropic API or AWS Bedrock) |
| Embeddings | Bedrock Titan V2 (amazon.titan-embed-text-v2:0) |
| Agent Runtime | JarvisOrchestrator (Claude API + sub-agent prompts) |
| MCP Tools | FastMCP (internal), external MCP servers |
| Frontend | Next.js + A2UI protocol |
| Interface | Telegram Bot (python-telegram-bot) |
| Reverse Proxy | Caddy (automatic TLS, production) |
| Infrastructure | AWS (Terraform: EC2, VPC, Route53, IAM, SSM) |
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

### Database

- **Migrations**: Always use Alembic. Never modify tables by hand.
- **IDs**: String IDs with type prefix (e.g., `evt_01HXYZ`). Use ULID for ordering.
- **JSON columns**: Use JSONB for flexible data. Keep indexed columns as proper typed columns.
- **Indexes**: Add indexes for every query pattern used in services.

### API Contracts

- **Versioned**: All endpoints prefixed with `/v1/`
- **REST-first**: Use proper HTTP methods. Return stable IDs and status.

## Architecture Rules

### Agent Roles (do not violate these boundaries)

| Agent | Responsibility | Write Scope |
|-------|---------------|-------------|
| Observer | Perceive the world — read sources, detect changes, ingest events | normalized_events, observation_health |
| Librarian | Understand events — extract entities, update world model | entities, relationships, memories |
| Planner | Decide what to do, produce task graphs | plans, plan_tasks |
| Governor | Evaluate policies, gate approvals | policy decisions, approvals |
| Operator | Execute approved plans via MCP tools | executions, execution_task_runs |
| Presenter | Generate user-facing output, A2UI surfaces | briefings, UI payloads |
| Researcher | Deep context gathering, cross-source synthesis | None (read-only) |
| Persona | Learn preferences, adapt communication style | memories (preference type) |

Only Planner decides intent. Only Operator executes external actions. Only Presenter talks to the user. Governor sits before every external write.

### Data Flow

1. **Observer** reads data sources via MCP tools (Gmail, Calendar, GitHub, Slack)
2. **Observer** ingests events to intelligence pipeline → `/v1/events/ingest`
3. **EventProcessor** normalizes, scores importance, deduplicates
4. **Librarian** extracts entities and memories
5. **Planner** evaluates and creates task graphs
6. **Governor** evaluates policy → creates approval if needed
7. **Operator** executes approved plans
8. **Presenter** delivers results via Telegram + web A2UI surfaces

### Core Contracts

1. **Normalized Event** — the universal event schema
2. **Plan + Task Graph** — structured planner output (never free-form text)
3. **Execution State Machine** — detected → planned → policy_checked → approved → executing → completed/failed
4. **Approval** — every external write goes through approval in v1
5. **Briefing** — structured daily brief with priorities, changes, approvals, actions

### Security Rules

- Single trusted user boundary in v1
- All external writes require approval (no auto-send)
- Audit log for every external write with full correlation IDs
- Idempotency keys on all events to prevent duplicates
- Rate limiting (Redis-backed with in-memory fallback)
- Request size limits and CORS enforcement

## Development Workflow

### Running locally

```bash
# Start infrastructure
docker compose up -d

# Run backend
cd backend && source .venv/bin/activate
python run.py  # starts on :8000

# Run background worker (processes async callbacks)
python run.py --worker

# Run with Telegram bot
python run.py --worker --bot

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Common Mistakes to Avoid

- Do not let the planner output free-form text — always structured JSON
- Do not skip the Governor for external writes
- Do not store secrets in memory or model context
- Do not start with prompts — start with contracts and schemas
- Do not over-engineer with Kafka/Neo4j — Postgres + Redis is enough
