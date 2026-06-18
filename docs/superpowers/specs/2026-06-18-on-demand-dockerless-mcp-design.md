# On-Demand, Docker-Free MCP Servers — Design

**Date:** 2026-06-18
**Branch:** `review/architecture-remediation`
**Status:** Approved design; pending implementation plan.

## Problem

External MCP servers are partly "always-on" and depend on Docker:

1. **Google Workspace** runs as a persistent Docker container (`docker-compose.yml` `google-workspace-mcp`,
   `infra/docker/google-workspace-mcp/Dockerfile`), reached over `streamable-http` at `http://localhost:8001/mcp`.
2. **GitHub** runs as a stdio subprocess whose command is literally
   `docker run -i --rm ghcr.io/github/github-mcp-server` (`seed_installations.py`).
3. **Eager startup discovery** (`WorkspaceMCPPool.initialize_from_db()`) connects to servers and calls
   `list_tools()` at boot, and stdio sessions linger on a 30-minute idle TTL.

Goal: remove **all Docker dependency for MCP servers**, spin servers up **on demand within an agent turn**,
reuse them for that turn, and **tear them down when the turn ends**. Resolve and pass auth tokens at request
time (already the case). Do no MCP network/process I/O at server startup.

## Scope

**In scope:** the 7 external servers (google-workspace, github, slack, notion, playwright, filesystem,
atlassian), their transports, session lifecycle, removal of eager discovery, and removal of the
`google-workspace-mcp` Docker service + GitHub `docker run`.

**Out of scope:** datastore containers (Postgres/Redis/Qdrant/Neo4j/MinIO) — they remain in
`docker-compose.yml` (dev) and `docker-compose.prod.yml` (prod). Internal in-process FastMCP tools
(`src/tools/server.py`) are unchanged.

## Key facts established during research

- **The backend runs natively in prod** (systemd `jarvis-backend` in a venv), not in a container. The host
  (`infra/user-data.sh`) has Docker, Node 22, Python 3.12, and `uv`/`uvx`. So the backend process can spawn
  `npx`/`uvx` children directly. The GitHub `docker run` path works today only because the *host* has Docker.
- **GitHub remote MCP**: `https://api.githubcopilot.com/mcp/`, streamable-HTTP, `Authorization: Bearer <PAT>`.
  Same model Atlassian already uses; a GitHub token is already stored via `OAuthManager`.
- **Google Workspace stdio is unusable for us**: stdio mode uses interactive browser OAuth and on-disk,
  single-user credentials. Our multi-user, server-side, per-request-Bearer model requires its
  `streamable-http` + `MCP_ENABLE_OAUTH21` + `EXTERNAL_OAUTH21_PROVIDER` + `WORKSPACE_MCP_STATELESS_MODE`
  mode (per-request Bearer in the `Authorization` header, zero disk writes). De-Dockering it means running
  `uvx workspace-mcp --transport streamable-http` as a host process we manage, not a transport switch.
- **Tool exposure depends on schema availability**: `_get_tools_for_agent` (jarvis.py) exposes a tool to
  Claude only if it has an `input_schema` (live MCP schema *or* DB schema) and **skips tools with neither**.
  `seed_installations` currently *clears* HTTP-server DB schemas on every restart so live discovery
  repopulates them. Removing eager discovery without changing this would hide Google Workspace tools and
  create a chicken-and-egg deadlock (no schema → not exposed → never called → never discovered).
- Session creation is already lazy per `(workspace_id, server_name, user_id)`; FastMCP's `Client` context
  manager already spawns the stdio subprocess on `__aenter__` and kills it on `__aexit__`. The lever is
  *when we exit that context*, plus removing the eager discovery pass.

## Per-server changes

| Server | Today | After |
|---|---|---|
| **github** | stdio `docker run ghcr.io/github/github-mcp-server` | Remote HTTP `https://api.githubcopilot.com/mcp/`, `auth_provider=github`, Bearer from OAuthManager. No local process. |
| **google-workspace** | persistent Docker container (HTTP) | Host subprocess `uvx workspace-mcp --transport streamable-http` on an ephemeral localhost port, managed on demand by `LocalMCPProcessManager`; keeps OAuth21-stateless Bearer auth. |
| **atlassian** | remote HTTP | unchanged |
| **slack / notion / playwright / filesystem** | `npx` stdio | unchanged commands; lifecycle becomes per-turn teardown |

## Component: `LocalMCPProcessManager` (new)

A small module owning locally-spawned HTTP MCP processes (only google-workspace today).

- **Launch:** pick a free localhost port; spawn `uvx workspace-mcp --transport streamable-http --tool-tier
  complete --tools gmail calendar` with env `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
  `MCP_ENABLE_OAUTH21=true`, `EXTERNAL_OAUTH21_PROVIDER=true`, `WORKSPACE_MCP_STATELESS_MODE=true`,
  `WORKSPACE_MCP_PORT=<port>`. Map from `JARVIS_`-prefixed settings (replicating the old `entrypoint.sh`).
- **Readiness:** poll the HTTP endpoint until ready or timeout (bounded; circuit-break on failure).
- **Expose:** provide the base URL (`http://127.0.0.1:<port>/mcp`) to the session pool config dynamically.
- **Reference-counted lifecycle (approved):** the process is started when the first turn needs Google and
  stopped when the last active turn releases it — **not** hard-restarted per turn. For a single sequential
  user this equals per-turn teardown but avoids cold-start thrash when turns overlap.
- **Terminate:** SIGTERM then SIGKILL; always killed on `shutdown()`; PID tracked for orphan cleanup.

## Lifecycle mechanism: `TurnScope`

- A `contextvars.ContextVar` holds the current turn's `TurnScope`, set at the start of an agent turn (chat
  process paths in `jarvis.py` and per-step run in `graph_executor.py`) and closed in a `finally`.
- `UserMCPSessionPool.get_or_create_session` registers newly-opened sessions into the active scope and
  **reference-counts** reused ones.
- On turn end, the scope closes the sessions it opened (refcount→0): exits the FastMCP `Client` context
  (kills stdio subprocess / closes HTTP client) and releases the `LocalMCPProcessManager` refcount.
- **Safety net:** a short idle reaper (~120s, configurable) for orphans from background tasks/crashes, plus
  `shutdown()` closing everything. Reference counting prevents one turn from killing a session another
  concurrent turn is mid-call on.
- Background/scheduler runs establish their own `TurnScope` per run so their sessions are torn down too.

## Decoupling tool exposure from discovery (approved)

- **DB `ToolDefinition.input_schema` is the durable source of truth.** Stop clearing HTTP-server schemas on
  restart in `seed_installations`.
- **Lazy "discover-once-and-persist":** the first time `_get_tools_for_agent` finds a registered server whose
  tools lack schemas, trigger a one-shot discovery for *that server* (spawn → `list_tools` → persist to DB →
  tear down), then continue. Self-healing; no startup cost; live schemas still override when a session is
  already warm within a turn.
- **Startup `initialize_mcp_bridge` only registers configs** (transports/URLs — no network/process I/O). The
  eager connect+`list_tools` pass and its background discovery task are removed. The worker handshake keeps
  waiting only for pool wiring (already the case), not discovery.

## Host runtime

The host is already provisioned with Node 22 + `uv` (`infra/user-data.sh`). Approach:

- Rely on preinstalled `uvx`/`npx`.
- **Pin MCP package versions** in the seed config (e.g. `workspace-mcp==X.Y.Z`, `slack-mcp-server@<ver>`,
  `@playwright/mcp@<ver>`, `@modelcontextprotocol/server-filesystem@<ver>`, `@notionhq/notion-mcp-server@<ver>`)
  for reproducibility.
- **Startup preflight** verifying `uvx` and `npx` exist; fail fast with a clear message if missing.
- **Pre-warm** the package cache in `deploy.sh` (one `uvx`/`npx` invocation per server) so first real use is fast.
- No binary vendoring required.

## Removals

- `infra/docker/google-workspace-mcp/` (Dockerfile + entrypoint.sh).
- `google-workspace-mcp` service in `docker-compose.yml`.
- GitHub `docker run` args in `seed_installations.py`.
- Eager-discovery code path in `mcp_pool.py` / `mcp_bridge.py` and its background task.

## Files likely touched

- `src/integrations/seed_installations.py` — github→remote HTTP; google-workspace url now dynamic (provided by
  process manager); stop clearing HTTP schemas; pin package versions.
- `src/integrations/session_pool.py` — TurnScope registration + refcounting; dynamic url resolution for
  managed local processes.
- `src/integrations/mcp_pool.py`, `src/connectors/mcp_bridge.py` — drop eager discovery; add lazy
  discover-once-and-persist; register-config-only startup.
- `src/integrations/local_process_manager.py` — **new** `LocalMCPProcessManager`.
- `src/integrations/turn_scope.py` — **new** `TurnScope` + ContextVar helpers.
- `src/orchestrator/jarvis.py` — set/close `TurnScope` in chat process paths; lazy discovery trigger in
  `_get_tools_for_agent`.
- `src/orchestrator/agent_loop.py` / `src/services/graph_executor.py` — `TurnScope` per step-run.
- `src/services/scheduler.py` — `TurnScope` per background run.
- `src/config/settings.py` — google url becomes dynamic; add preflight/timeout/reaper settings.
- `docker-compose.yml` — remove `google-workspace-mcp`.
- `infra/user-data.sh`, `infra/scripts/deploy.sh` — preflight + cache pre-warm; drop google-workspace docker build.
- `backend/CLAUDE.md` / architecture docs — update the "MCP via Docker" mentions.

## Error handling

- `uvx`/`npx` missing → clear startup preflight error; tool call returns a structured MCP error.
- `workspace-mcp` not ready within timeout → circuit breaker opens; structured error to caller.
- Process leaks → PID tracking, idle reaper, and `shutdown()` teardown.
- All existing MCP error classification / retry / circuit-breaker behavior is preserved.

## Risks / tradeoffs

- **Cold-start latency** on first tool use per turn (npx ~1-3s; google ~2-4s first time) — mitigated by
  within-turn reuse, warm package cache, and the refcounted Google process.
- **Token scopes:** workspace-mcp `EXTERNAL_OAUTH21_PROVIDER` validates Google tokens via userinfo; our
  stored token already carries `openid email profile gmail.modify calendar`.
- **Concurrency:** refcounting in both `TurnScope` and `LocalMCPProcessManager` prevents premature teardown.

## Testing

- Unit: `TurnScope` open/reuse/close/refcount; `LocalMCPProcessManager` port selection, readiness probe,
  terminate (mocked subprocess); lazy discover-once-and-persist; github remote config; startup registers
  configs without discovery.
- Integration: a turn that calls a tool spawns then tears down; two overlapping turns don't kill each other's
  sessions; HTTP-server tools remain exposed across a restart with no eager discovery.
- Keep the existing suite green.

## Out-of-scope / deferred

- De-Dockerizing datastores.
- Migrating Atlassian or other remote servers (already remote).
- Per-tool-call (vs per-turn) teardown granularity.
