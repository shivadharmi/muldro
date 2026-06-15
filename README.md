# Jarvis

A **Personal AI Operating System** for founders. Not a chatbot — an OS with a core loop:

```
Perceive -> Understand -> Update Model -> Plan -> Act -> Communicate
```

Jarvis continuously observes your data sources (Gmail, Calendar, Slack, GitHub), extracts entities and memories, plans actions, seeks approval for external writes, executes approved plans, and communicates results through Telegram and a Next.js web frontend.

## Architecture

```mermaid
graph TB
    subgraph UI["User Interfaces"]
        TG[Telegram Bot]
        WEB[Next.js Frontend<br/>A2UI + Chat + SSE]
    end

    subgraph API["API Layer"]
        FA[FastAPI<br/>/v1/ prefix]
    end

    subgraph ORCH["Orchestrator"]
        JO[JarvisOrchestrator<br/>Hub-and-spoke routing]
        TR[TraceManager] ~~~ BU[BudgetTracker]
    end

    subgraph AGENTS["Sub-Agents — Claude API"]
        direction LR
        PCV[Perceiver<br/>Sonnet] ~~~ LIB[Librarian<br/>Sonnet]
        PLN[Planner<br/>Opus] ~~~ GOV[Governor<br/>Sonnet]
        OPR[Operator<br/>Sonnet] ~~~ PRS[Presenter<br/>Sonnet]
        PER[Persona<br/>Haiku]
    end

    subgraph TOOLS["Tool Layer"]
        CAT[Tool Catalog<br/>catalog.py]
        INT[Internal FastMCP<br/>2 servers]
        MCP[MCP Bridge<br/>Google · GitHub · Slack<br/>Notion · Linear · Playwright · Filesystem]
    end

    subgraph SVC["Services"]
        EP[EventProcessor] ~~~ WM[WorldModel]
        MS[MemoryService] ~~~ PL[Planner]
        GV[Governor] ~~~ GE[GraphExecutor]
        NT[Notifier] ~~~ TS[TriSearchService]
    end

    subgraph INFRA["Infrastructure"]
        PG[(Postgres 17<br/>tsvector FTS · source of truth)]
        RD[(Redis 7<br/>streams · cache · locks)]
        QD[(Qdrant<br/>vector search)]
        N4J[(Neo4j 5<br/>knowledge graph)]
        S3[(MinIO / S3<br/>artifact storage)]
    end

    TG & WEB --> FA
    FA --> JO
    JO --> TR & BU
    JO --> PCV & LIB & PLN & GOV & OPR & PRS & PER
    PCV & LIB & PLN & GOV & OPR & PRS --> INT
    OPR & PCV --> MCP
    INT --> SVC
    MCP --> SVC
    SVC --> PG & RD
    TS --> QD & PG
    WM --> N4J & QD
    GE -.->|artifact files| S3
```

### The 7 Sub-Agents

| Agent | Model | Role |
|-------|-------|------|
| **Perceiver** | Sonnet | Observe external sources, gather context, detect changes (merges former Observer + Researcher) |
| **Librarian** | Sonnet | Extract entities, update world model |
| **Planner** | Opus | Determine intent, produce capability-based plans |
| **Governor** | Sonnet | Evaluate policies, gate approvals via TrustEngine |
| **Operator** | Sonnet | Execute approved plans via MCP tools |
| **Presenter** | Sonnet | Generate user-facing output and live execution surfaces |
| **Persona** | Haiku | Learn user preferences from interactions |

Only Planner decides intent. Only Operator executes external actions. Only Presenter talks to the user. TrustEngine gates approvals with graduated autonomy (first_use, learning, trusted, autonomous).

> **Detailed architecture docs:** [`docs/architecture/`](docs/architecture/README.md) — sequence diagrams, data model, service reference, design decisions

## Quick Start

The only thing you must provide is an Anthropic API key. Everything else —
Postgres, Redis, Qdrant, Neo4j, the backend (API + background worker) and the
Next.js frontend — comes up together in Docker.

```bash
# 1. Prerequisites: Docker + Docker Compose, and an Anthropic API key
#    (get one at https://console.anthropic.com)

# 2. Provide your key
cp .env.minimal backend/.env
#    then edit backend/.env and set JARVIS_ANTHROPIC_API_KEY

# 3. Build and start the whole stack (migrations run automatically on first boot)
docker compose --profile app up

# 4. Open the app
#    Frontend → http://localhost:3000
#    API      → http://localhost:8000/v1/health
```

> Bare `docker compose up` (without `--profile app`) starts **infrastructure
> only** — use that for the native development loop below.

### Develop natively (hot reload)

```bash
# Infrastructure only
docker compose up -d

# Backend
cd backend
uv venv .venv --python 3.13 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env          # edit with your keys
alembic upgrade head
python run.py --worker        # API + background worker

# Frontend
cd frontend && npm install && npm run dev
```

## Deployment

Infrastructure is managed with Terraform in `infra/`. A single EC2 instance runs Postgres, Redis, the Jarvis backend, and Caddy (reverse proxy with auto-TLS).

## Project Structure

```
jarvis/
├── backend/
│   ├── src/
│   │   ├── api/            # REST/SSE routers (/v1/ prefix)
│   │   ├── config/         # Settings (pydantic-settings, JARVIS_ env prefix)
│   │   ├── connectors/     # MCP bridge, perception connectors
│   │   ├── models/         # SQLAlchemy models (all workspace-scoped)
│   │   ├── orchestrator/   # JarvisOrchestrator, agents, hooks, tracing, budget, contracts
│   │   ├── services/       # Business logic (planner, governor, operator, tri_search, etc.)
│   │   ├── tools/          # Tool catalog, schemas, validation, FastMCP servers
│   │   ├── ui/             # A2UI renderer + contracts
│   │   └── workflows/      # inbox_triage, meeting_prep, research
│   ├── tests/              # pytest + pytest-asyncio
│   └── alembic/            # database migrations
├── frontend/               # Next.js + A2UI renderer + chat panel
├── infra/                  # Terraform (AWS: EC2, VPC, Route53, IAM, SSM)
├── docs/architecture/      # Detailed architecture documentation
└── docker-compose.yml      # Local dev infrastructure
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13+ / FastAPI |
| Frontend | Next.js / React / A2UI |
| Database | PostgreSQL 17 (tsvector FTS) — source of truth |
| Vector Search | Qdrant 1.12 — semantic similarity (enriched payloads) |
| Reranking | AWS Bedrock amazon.rerank-v1:0 |
| Knowledge Graph | Neo4j 5 — multi-hop traversal, community detection |
| Object Storage | MinIO / S3 — artifact documents and media |
| Cache/Queue | Redis 7 — streams, cache, locks, pubsub, surface tracking |
| AI Models | Claude Opus/Sonnet/Haiku via Anthropic API or AWS Bedrock |
| Embeddings | AWS Bedrock Titan V2 (1024 dim) |
| Tool Protocol | MCP (Model Context Protocol) via FastMCP |
| Delivery | Telegram Bot API + Web SSE |
| Infrastructure | AWS (Terraform), Caddy reverse proxy |

## Key Features

- **Multi-tenant workspace isolation**: All data tables scoped by `workspace_id` with CASCADE deletes
- **Real-time streaming**: Claude API streaming with extended thinking (Opus) + SSE to frontend
- **Full cost tracking**: Cache tokens (1.25x write, 0.1x read), thinking tokens, per-agent cost breakdown
- **Graduated autonomy**: TrustEngine with 4 trust tiers (first_use, learning, trusted, autonomous)
- **Capability-based routing**: CapabilityResolver maps plans to agents by capability scope (not decision type)
- **Live execution surfaces**: Real-time step progress via A2UI during plan execution
- **TriSearch**: Parallel Qdrant + Postgres FTS + Neo4j search with Bedrock reranking
- **Knowledge graph**: Neo4j with typed relationship edges, weighted traversal, temporal scoping
- **Signal-driven perception**: Relevance assessment with tiered notification (not fixed-interval polling)
- **Runtime contracts**: Pydantic-validated boundaries (PlanOutput, PolicyDecision, StepResult, ToolCallRequest)
- **Auth system**: Magic links, OAuth (Google/GitHub), session tokens, Fernet-encrypted credentials
- **Command palette**: Cmd+K with quick commands, slash command support

## Status

Unified tool registry; workspace-scoped models; Alembic migrations; lint clean.
