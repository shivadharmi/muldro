# Jarvis Development Guide

## Prerequisites

- Python 3.13+ (via pyenv or system)
- Node.js 22+ (via nvm)
- Docker & Docker Compose
- uv (Python package manager)
- An Anthropic API key

## Initial Setup

### 1. Infrastructure

```bash
# Start Postgres (with pgvector) and Redis
docker compose up -d

# Verify
docker compose ps
# postgres should be running on :5432
# redis should be running on :6379
```

### 2. Backend

```bash
cd backend

# Create and activate virtualenv
uv venv .venv --python 3.13
source .venv/bin/activate

# Install dependencies
uv pip install -e ".[dev]"

# Copy env file and configure
cp .env.example .env
# Edit .env with your Anthropic API key and tokens

# Run migrations (after Postgres is up)
alembic upgrade head

# Run the server
python run.py
# Backend runs on http://localhost:8000

# Run the background worker (for async callback processing)
python run.py --worker

# Verify
curl http://localhost:8000/v1/health
```

### 3. Plugin (jarvis-tools)

```bash
cd jarvis-tools

# Install dependencies
npm install

# Type check
npx tsc --noEmit

# Build (optional, OpenClaw loads TS via jiti)
npm run build
```

### 4. OpenClaw (when ready to integrate)

```bash
# Install OpenClaw
npm i -g openclaw

# Onboard with API key
openclaw onboard --anthropic-api-key "$ANTHROPIC_API_KEY"

# Copy our example config as a starting point
cp openclaw.example.json5 ~/.openclaw/openclaw.json
# Edit to adjust paths to your local jarvis-tools directory

# Copy agent persona
cp -r jarvis-agent/* ~/.openclaw/workspace-jarvis/

# Start gateway
openclaw gateway
# Gateway runs on http://localhost:18789
```

## Running Tests

```bash
cd backend
source .venv/bin/activate

# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_health.py -v

# With coverage (when coverage is added)
pytest tests/ --cov=src --cov-report=term-missing
```

## Linting & Formatting

```bash
cd backend
source .venv/bin/activate

# Check
ruff check src/ tests/

# Fix auto-fixable issues
ruff check --fix src/ tests/

# Format
ruff format src/ tests/
```

## Database Migrations

```bash
cd backend
source .venv/bin/activate

# Create a new migration after model changes
alembic revision --autogenerate -m "add xyz table"

# Apply migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1

# View current state
alembic current
```

## Adding a New Service

1. Create `backend/src/services/your_service.py`
2. Define the class with async methods
3. Add any new models to `backend/src/models/`
4. Update `backend/src/models/__init__.py` with new imports
5. Create an Alembic migration
6. Add API endpoints if needed in `backend/src/api/`
7. Update schemas in `backend/src/api/schemas.py`
8. Write tests in `backend/tests/`

## Adding a New OpenClaw Tool

1. Add the tool definition in `jarvis-tools/src/tools.ts`
2. Add the corresponding backend endpoint in `backend/src/api/`
3. Add the tool name to the `tools.allow` list in `openclaw.json`
4. Type-check: `cd jarvis-tools && npx tsc --noEmit`

## Adding Event Ingestion for a New Source

The agent reads data from sources (Gmail via `gog gmail`, GitHub via `gh`, etc.) and ingests it to the backend:

1. Teach the agent (in `SOUL.md`) how to read the new source
2. Agent calls `jarvis_ingest_event` with normalized data
3. Backend's EventProcessor handles scoring, dedup, and callbacks
4. No new backend code needed unless you need source-specific scoring logic

## Environment Variables

See `backend/.env.example` for all configuration options. Key ones:

| Variable | Purpose | Default |
|----------|---------|---------|
| `JARVIS_DATABASE_URL` | Postgres connection | `postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis` |
| `JARVIS_REDIS_URL` | Redis connection | `redis://localhost:6379/0` |
| `JARVIS_ANTHROPIC_API_KEY` | Claude API key | (required) |
| `JARVIS_BACKEND_TOKEN` | Auth token for plugin → backend calls | (optional for dev) |
| `JARVIS_OPENCLAW_GATEWAY_URL` | OpenClaw gateway URL | `http://localhost:18789` |
| `JARVIS_OPENCLAW_HOOK_TOKEN` | Auth token for backend → OpenClaw calls | (optional for dev) |
| `JARVIS_DEBUG` | Enable debug mode / auto-reload | `false` |
