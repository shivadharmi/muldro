# Setup

End-to-end local setup for Muldro. Two paths: **Docker** (one command, no hot reload) and
**native** (hot reload, what you want for development). Do the Docker path first to confirm
the stack is sound, then switch.

Muldro is pre-1.0 and pre-launch. Some of this is genuinely rough — where it is, this
document says so rather than pretending otherwise.

---

## 1. Prerequisites

| Tool | Version | Why |
|---|---|---|
| Docker + Docker Compose | any current release | Postgres 17 (pgvector), Redis 7, Qdrant 1.17, Neo4j 5.26, MinIO |
| Python | **3.12** | `backend/pyproject.toml` requires `>=3.12`; CI and `backend/Dockerfile` both pin 3.12. Do not use 3.13 — the README's `--python 3.13` is wrong. |
| [uv](https://docs.astral.sh/uv/) | any current release | the only supported installer; `pyproject.toml` declares no `[build-system]` |
| Node.js | **20+** (22 recommended) | `frontend/Dockerfile` uses `node:20-alpine`; CI uses 22; Next.js 16 |
| `npx` (bundled with Node) | — | on-demand stdio MCP servers, e.g. Slack |
| `uvx` (bundled with uv) | — | on-demand local MCP processes |

`npx` and `uvx` are not required to boot. `src/integrations/runtime_preflight.py` only logs a
warning at startup when they are missing; the failure surfaces later, when a tool call needs
one.

An **Anthropic API key** is required — get one at <https://console.anthropic.com>.

---

## 2. Fastest path: the whole stack in Docker

```bash
git clone https://github.com/shivadharmi/muldro.git
cd muldro

cp .env.minimal backend/.env
```

Edit `backend/.env` and set **two** values:

```dotenv
MULDRO_ANTHROPIC_API_KEY=sk-ant-...
MULDRO_SKIP_GATEWAY_VALIDATION=true
```

The second one is not optional in practice — see [Troubleshooting §7.1](#71-startup-aborts-in-register_gateway_oauth_configs).

```bash
docker compose --profile app up
```

This builds and runs everything: the five infrastructure services, the backend
(`backend/docker-entrypoint.sh` runs `alembic upgrade head` and then `python run.py
--worker`, so migrations apply on first boot), and the Next.js frontend.

Compose overrides `MULDRO_DATABASE_URL`, `MULDRO_REDIS_URL`, `MULDRO_QDRANT_URL` and the
Neo4j settings with the compose service hostnames, so any `localhost` values in your
`backend/.env` are ignored on this path. That is deliberate.

Bare `docker compose up` (no `--profile app`) starts **infrastructure only** — that is what
the native path below uses.

---

## 3. Native development (hot reload)

### 3.1 Infrastructure

From the repo root:

```bash
docker compose up -d
docker compose ps      # all five services should be up; postgres reports healthy
```

### 3.2 Backend

```bash
cd backend
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -r pyproject.toml --extra dev

cp .env.example .env
```

Edit `backend/.env` — at minimum:

```dotenv
MULDRO_ANTHROPIC_API_KEY=sk-ant-...
MULDRO_SKIP_GATEWAY_VALIDATION=true
MULDRO_DATABASE_URL=postgresql+asyncpg://muldro:muldro@localhost:5432/muldro
MULDRO_REDIS_URL=redis://localhost:6379/0
MULDRO_QDRANT_URL=http://localhost:6333
MULDRO_NEO4J_URL=bolt://localhost:7687
MULDRO_NEO4J_USER=neo4j
MULDRO_NEO4J_PASSWORD=muldrodev
```

Every remaining setting, its real default and where it is enforced is documented in
`backend/.env.example`.

Then migrate and run:

```bash
alembic upgrade head
python run.py --worker      # API on :8000, plus StreamConsumer + Scheduler
```

`python run.py` without `--worker` starts the API alone. You want `--worker` for anything
involving perception, scheduling, background runs or DLQ retries.

> `uv pip install -r pyproject.toml --extra dev` is what `backend/Dockerfile` does, adapted to
> include the dev extras. The project declares no `[build-system]`, so an editable install of
> the package itself (`uv pip install -e ".[dev]"`, as the README currently says) is not a
> path this repo exercises anywhere.

### 3.3 Frontend

In a second shell, from the repo root:

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
```

No frontend environment file is needed for the standard setup. Next.js rewrites `/api/:path*`
to `http://localhost:8000/v1/:path*` by default (`frontend/next.config.ts`). If your backend
is somewhere else, copy `frontend/.env.example` to `frontend/.env.local` and set
`BACKEND_URL`.

---

## 4. Verify it works

```bash
curl -s http://localhost:8000/v1/health
```

**The health endpoint is `/v1/health`.** There is no bare `/health` — a `404` from
`http://localhost:8000/health` means the route does not exist, not that the server is down.

Two more, once you are signed in:

- `GET /v1/health/loop` — scheduler loop health
- `GET /v1/health/stores` — Postgres / Redis / Qdrant / Neo4j reachability
- `GET /v1/system/dashboard` — the system health dashboard

### Signing in

Open <http://localhost:3000>. Unauthenticated visitors are redirected to `/login`.

Enter any email address. Because `MULDRO_BACKEND_TOKEN` is unset, the backend is in **dev
mode**: `POST /v1/auth/magic-link` returns the sign-in token directly in its response body
instead of emailing it, and the login page fills it in for you. Submit it and you land on
`/chat`.

If you set `MULDRO_BACKEND_TOKEN`, dev mode turns off and magic links go out over AWS SES —
which then needs `MULDRO_SES_ENABLED=true`, `MULDRO_SES_FROM_ADDRESS` and working AWS
credentials. For local work, leave `MULDRO_BACKEND_TOKEN` empty.

### Checking the model layer

Once signed in, the Settings popup has a Models tab (`PUT /v1/model-config`). The seeded
defaults bind the `reasoning` / `balanced` / `fast` tiers to Claude Opus / Sonnet / Haiku, so
your Anthropic key is all that is needed. Adding an OpenAI or Gemini key through that UI
requires `MULDRO_CONFIG_ENCRYPTION_KEY` — set it **before** you save a key, or see
[§7.2](#72-muldro_config_encryption_key-is-unset-but-encrypted-provider-credentials-exist).

---

## 5. What does not work out of the box

**Google Workspace, GitHub, Notion and Atlassian are inert without the OpenConnector
gateway.** This is by design, not a bug.

`backend/src/integrations/seed_installations.py` seeds all four with
`auth_provider="platform_jwt"` and `remote_url: None` — their tools are served by the
OpenConnector adapter, and there is deliberately **no native fallback transport**. When
`MULDRO_TOOLHIVE_VMCP_URL` is unset, `src/integrations/mcp_pool.py` raises
`GatewayNotConfigured` for any of them at session-open time.

**Slack does work without the gateway.** It is a stdio installation launched via
`npx slack-mcp-server`, credentialed from the bare (non-`MULDRO_`-prefixed) environment
variables `SLACK_MCP_XOXP_TOKEN` / `SLACK_MCP_XOXB_TOKEN`. Either token suffices — they are
alternatives, not a conjunction.

Everything that does not need an external connector — chat, planning, the world model,
memory, TriSearch, the trust and approval machinery, the view layer — works fine with just an
Anthropic key.

### Standing up the gateway

Budget **hours, not minutes**, and expect to do most of it by hand.
[`infra/gateway/README.md`](../infra/gateway/README.md) is the runbook. Its §4 and §5 are
explicit that ToolHive itself is not in any compose file here, that
`toolhive-vmcp-gateway.yaml` was authored from docs and never validated against a running
operator, and that steps 1–7 (install ToolHive, verify the config schema, point it at
Muldro's JWKS endpoint, do real OAuth consent per provider, seed `connection_map` rows, set
`MULDRO_TOOLHIVE_VMCP_URL`, run an end-to-end verification) are all manual.

> **Broken pointer in that runbook:** its "Design reference" line points at
> `docs/superpowers/specs/2026-08-16-toolhive-openconnector-gmail-slice-design.md`.
> `docs/superpowers/` is gitignored (see the root `.gitignore`) and **will not exist in a
> clone**. Read [`docs/architecture/toolhive-openconnector-assessment.md`](architecture/toolhive-openconnector-assessment.md)
> instead — it is tracked and covers the same architecture.

Note also that turning the gateway **on** (leaving `MULDRO_SKIP_GATEWAY_VALIDATION` at its
`false` default) makes the startup registrar require an OAuth client id **and** secret for
every provider in the registry — google, github, notion and atlassian — not just the ones you
intend to use. Any incomplete pair aborts startup.

---

## 6. Running the checks

```bash
# Backend (from backend/, venv active)
pytest tests/ -v
ruff check src/ tests/
ruff format src/ tests/

# Frontend (from frontend/)
npm test
npm run lint
npm run build
```

The backend test suite talks to a real Postgres. Point `MULDRO_DATABASE_URL` at a **throwaway
database**, not one holding data you care about.

---

## 7. Troubleshooting

### 7.1 Startup aborts in `register_gateway_oauth_configs`

Symptom — one of:

```
RuntimeError: Gateway OAuth registration requires openconnector_admin_url and
openconnector_admin_token (env MULDRO_OPENCONNECTOR_ADMIN_URL /
MULDRO_OPENCONNECTOR_ADMIN_TOKEN). Set them, or set
MULDRO_SKIP_GATEWAY_VALIDATION=true to run without the gateway.
```

```
RuntimeError: Gateway provider 'gmail' needs MULDRO_GOOGLE_OAUTH_CLIENT_ID and
MULDRO_GOOGLE_OAUTH_CLIENT_SECRET, but one or both are empty. Set them, or set
MULDRO_SKIP_GATEWAY_VALIDATION=true.
```

Cause: `skip_gateway_validation` defaults to `False`, so `src/api/app.py`'s lifespan calls the
registrar unguarded. Failing at boot is deliberate — gateway-routed installations have no
native fallback, so a Muldro that booted without them would be broken anyway, just later and
less visibly.

Fix, if you are not running the gateway:

```dotenv
MULDRO_SKIP_GATEWAY_VALIDATION=true
```

### 7.2 `MULDRO_CONFIG_ENCRYPTION_KEY is unset but encrypted provider credentials exist`

This failure is **delayed**, which is what makes it confusing. Startup succeeds for as long as
the `provider_credentials` table is empty. The moment you save any provider API key through
the Settings UI, that row is written encrypted — and the **next** restart aborts:

```
RuntimeError: MULDRO_CONFIG_ENCRYPTION_KEY is unset but encrypted provider credentials
exist in the database. Set the master key so credentials can be decrypted at
model-build time, or remove the affected provider_credentials rows.
```

Fix — either set the key (and keep it; rotating it orphans every credential encrypted under
the old one):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dotenv
MULDRO_CONFIG_ENCRYPTION_KEY=<the generated key>
```

…or delete the affected `provider_credentials` rows. Set the key **before** entering any
provider key in the UI and you never meet this.

### 7.3 CORS errors from the browser

`MULDRO_CORS_ALLOWED_ORIGINS` defaults to the **empty string**, and `src/api/app.py` installs
the CORS middleware only when it is non-empty. With no origins configured, there is no CORS
middleware at all.

That is correct for the standard setup: the browser only ever talks to Next.js on :3000, and
Next proxies `/api/*` to the backend server-side, so no cross-origin request happens.

You only need this when something in a browser calls `:8000` directly:

```dotenv
MULDRO_CORS_ALLOWED_ORIGINS=http://localhost:3000
```

Comma-separated for multiple origins.

### 7.4 A setting appears to have no effect

`Settings` is configured with `"extra": "ignore"`
(`backend/src/config/settings.py`). A misspelled or obsolete `MULDRO_*` variable is **silently
discarded** — no error, no warning, no log line. `MULDRO_ANTHROPC_API_KEY` looks set and is
not.

Check the spelling against `backend/src/config/settings.py` before investigating anything
else. Note in particular that several variables documented in older copies of
`backend/.env.example` have no reader anywhere in `backend/src`:
`MULDRO_USE_BEDROCK`, `MULDRO_BEDROCK_REGION`, `MULDRO_SESSION_SECRET_KEY`,
`MULDRO_ELASTICSEARCH_URL`, `MULDRO_FILESYSTEM_MCP_ROOT`. Setting them does nothing.

### 7.5 `MULDRO_ANTHROPIC_API_KEY is not set`

```
RuntimeError: MULDRO_ANTHROPIC_API_KEY is not set. Muldro cannot talk to any agent
without it.
```

Raised by `settings.validate_startup()`. Check that `backend/.env` exists in the directory you
launched from — pydantic-settings reads `.env` **relative to the process working directory**,
so `python run.py` must be run from `backend/`.

### 7.6 `MULDRO_OAUTH_ENCRYPTION_KEY is required in production`

Raised by `validate_startup()` when `MULDRO_ENVIRONMENT=production` and no OAuth encryption
key is set — without it, OAuth tokens would be stored as plaintext. Generate one the same way
as in §7.2 and set `MULDRO_OAUTH_ENCRYPTION_KEY`.

### 7.7 Registry validation fails at startup

The lifespan runs `validate_registry()` plus post-condition and identity coverage checks, and
raises rather than serving traffic with a malformed tool registry. `MULDRO_SKIP_REGISTRY_VALIDATION=true`
bypasses all three. It is an emergency escape hatch, not configuration — a registry that fails
validation will misbehave at tool-call time.

### 7.8 `GatewayNotConfigured` when using an integration

```
Installation 'google-workspace' declares auth_provider='platform_jwt' but
settings.toolhive_vmcp_url is not set (env MULDRO_TOOLHIVE_VMCP_URL).
```

Expected without the gateway. See [§5](#5-what-does-not-work-out-of-the-box). Either stand up
the gateway or disable the installation.

### 7.9 Migrations

`alembic upgrade head` is run from `backend/` with the venv active. The Docker path runs it
for you on container start (`backend/docker-entrypoint.sh`). If a migration fails on a
half-initialised database, the fastest local recovery is to drop the compose volume and start
clean:

```bash
docker compose down -v      # ⚠ destroys all local data in the compose volumes
docker compose up -d
cd backend && alembic upgrade head
```

---

## 8. Where to go next

- [`docs/architecture/README.md`](architecture/README.md) — sequence diagrams, data model,
  service reference, design decisions
- [`docs/engineering-standards.md`](engineering-standards.md) — the binding coding standards
- [`CLAUDE.md`](../CLAUDE.md) — architecture invariants, agent boundaries, the trust and
  permission gates
- [`infra/gateway/README.md`](../infra/gateway/README.md) — the gateway runbook
