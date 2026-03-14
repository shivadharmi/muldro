# Jarvis

A Personal AI Operating System for founders. Jarvis continuously observes your data sources (email, calendar, GitHub), understands context, plans actions, seeks approval, and executes — all through a structured intelligence loop built on top of [OpenClaw](https://openclaw.com) as the interaction gateway.

## Architecture

```
User <-> OpenClaw Gateway (Telegram, Web UI, Canvas, cron)
              |
         OpenClaw Agent (Claude-powered)
         Has: gog, gh, message, browser, memory, cron
              |
         jarvis-tools plugin (thin HTTP bridge)
              | HTTP
         Jarvis Intelligence Backend (FastAPI)
         +-----------------------------------------+
         | EventProcessor -> WorldModel -> Memory  |
         | Planner -> Governor -> Operator         |
         | Presenter -> Briefings, Meeting Prep    |
         | Audit + DLQ + Heartbeat + Locking       |
         +-----------------------------------------+
              |
         Postgres (pgvector) + Redis
```

**Jarvis = the brain** (decides, scores, remembers, audits)
**OpenClaw agent = the hands** (reads data, writes emails, sends messages)

## Quick Start

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for full local setup instructions.

```bash
# 1. Start Postgres + Redis
docker compose up -d

# 2. Set up backend
cd backend
uv venv .venv --python 3.13 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env  # edit with your API keys
alembic upgrade head
python run.py

# 3. Set up OpenClaw
npm i -g openclaw
cp openclaw.example.json5 ~/.openclaw/openclaw.json  # edit config
openclaw gateway
```

## Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the AWS deployment runbook.

Infrastructure is managed with Terraform in `infra/`. A single EC2 instance runs Postgres, Redis, the Jarvis backend, OpenClaw, and Caddy (reverse proxy with auto-TLS).

## Project Structure

```
jarvis/
├── backend/              # Python FastAPI backend (the brain)
│   ├── src/
│   │   ├── api/          # REST endpoints
│   │   ├── config/       # Settings (pydantic-settings)
│   │   ├── middleware/    # Rate limiting, CORS, observability
│   │   ├── models/       # SQLAlchemy models (Postgres)
│   │   └── services/     # Business logic (planner, governor, operator, etc.)
│   ├── tests/
│   └── alembic/          # Database migrations
├── jarvis-tools/         # OpenClaw plugin (TypeScript, thin HTTP bridge)
├── jarvis-agent/         # Agent system prompt (SOUL.md)
├── infra/                # Terraform (AWS: EC2, VPC, Route53, IAM, SSM)
│   └── scripts/          # deploy.sh, backup-postgres.sh
├── docs/                 # Architecture, development, deployment, roadmap
├── docker-compose.yml    # Local dev (Postgres + Redis)
└── openclaw.example.json5
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13+ / FastAPI |
| Database | PostgreSQL 17 (pgvector) |
| Cache/Queue | Redis 7 |
| AI Model | Claude via Anthropic API or AWS Bedrock |
| Embeddings | Voyage AI (voyage-3-lite) |
| Gateway | OpenClaw (self-hosted) |
| Infrastructure | AWS (Terraform), Caddy reverse proxy |

## Status

Production deployment running. See [docs/ROADMAP.md](docs/ROADMAP.md) for detailed progress.
