# Gmail gateway slice — local dev runbook

This directory holds the deployment config for the ToolHive + OpenConnector
Gmail vertical slice: **config authoring only**. Bringing the stack fully up
(installing ToolHive, doing a real Gmail OAuth connect, running an
end-to-end chat turn) is a **manual follow-up**, not automated by these
files — see §5 below.

Design reference: [`docs/superpowers/specs/2026-08-16-toolhive-openconnector-gmail-slice-design.md`](../../docs/superpowers/specs/2026-08-16-toolhive-openconnector-gmail-slice-design.md)
(untracked/gitignored planning doc — present on the machine that authored
this slice; if it's missing on yours, the parent assessment
[`docs/architecture/toolhive-openconnector-assessment.md`](../../docs/architecture/toolhive-openconnector-assessment.md)
is tracked and covers the same architecture in more depth).

Spike findings (OpenConnector request shapes, confirmed against a real
container): [`spike-findings.md`](./spike-findings.md).

Files in this directory:

| File | Purpose |
|---|---|
| `docker-compose.yml` | Two services: `openconnector` (shared connector runtime) + `connection-adapter` (`backend/run_adapter.py`, the tenant-isolation boundary) |
| `toolhive-vmcp-gmail.yaml` | ToolHive VirtualMCPServer config fronting the adapter — OIDC validation, tool allowlist, Cedar default-deny policy. **Authored from docs, unverified against a running operator — read the header comment before applying.** |
| `README.md` | This file |

## 1. Pinned versions (never `:latest`)

| Component | Pinned to | Why |
|---|---|---|
| OpenConnector | `ghcr.io/oomol-lab/open-connector:v1.3.5` | Confirmed clean pull + digest in `spike-findings.md` §1. `:latest` on a credential-holding service is an unreviewed-upgrade risk — a new tag could silently change the MCP tool schemas the adapter parses (camelCase `actionId`/`connectionName`, no `required` array on some tools — see spike-findings.md §4). |
| ToolHive | **v0.43.0** (not in a compose file here — see §4) | Config field names in `toolhive-vmcp-gmail.yaml` were authored against this version's docs. A different version may have a different `VirtualMCPServer` schema. |
| Jarvis backend image | built from `../../backend/Dockerfile` (local source, not a registry tag) | Same commit as the rest of the stack you're testing against. |

Do not change any image reference to `:latest` in `docker-compose.yml`. If you need to bump `open-connector`, re-run the request-shape spike (see `spike-findings.md`'s method) against the new tag before trusting it in the adapter — tool schemas are not guaranteed stable across versions.

## 2. Required environment variables

| Variable | Used by | How to generate / obtain |
|---|---|---|
| `OOMOL_CONNECT_ENCRYPTION_KEY` | `openconnector` | `openssl rand -hex 32` — **see the mandatory rule below** |
| `OOMOL_CONNECT_RUNTIME_TOKEN` | `openconnector`, `connection-adapter` (as `JARVIS_OPENCONNECTOR_RUNTIME_TOKEN`) | `openssl rand -hex 24` — gates `POST /mcp`; confirmed by `spike-findings.md` §3 (401 without it) |
| `JARVIS_PLATFORM_JWT_PRIVATE_PEM` | `connection-adapter` | An RSA private key PEM — **must be the same key** the Jarvis API process uses to mint platform JWTs (`backend/src/orchestrator/platform_jwt.py`). Generate one: `openssl genrsa -out platform-jwt.pem 2048`, then set both the API process's and the adapter's `JARVIS_PLATFORM_JWT_PRIVATE_PEM` to `$(cat platform-jwt.pem)`. If unset, `platform_jwt.py` falls back to an ephemeral per-process key — tokens minted by the API container would then be **unverifiable** by the adapter container (they're separate processes). |
| `JARVIS_DATABASE_URL` | `connection-adapter` | Point at Jarvis's **existing** Postgres (started by the repo-root `docker-compose.yml`, not by this file). From inside this compose network to a host-run Postgres: `postgresql+asyncpg://jarvis:jarvis@host.docker.internal:5432/jarvis` (Mac/Windows) — Linux users may need the bridge gateway IP instead of `host.docker.internal`. |
| `JARVIS_GMAIL_VIA_GATEWAY` | Jarvis API process (not this compose file — see §4) | Set to `true` on the **Jarvis API/worker** process (`backend/src/config/settings.py`, default `False`) to route Gmail tool calls through this gateway instead of the native `google-workspace-mcp` process. The `connection-adapter` container in `docker-compose.yml` also sets this for consistency, though the adapter binary itself doesn't currently branch on it. |
| `JARVIS_OPENCONNECTOR_MCP_URL` | `connection-adapter` | Set automatically by `docker-compose.yml` to `http://openconnector:3001/mcp` — no action needed unless you're running the adapter outside this compose network. |
| `JARVIS_TOOLHIVE_VMCP_URL` | Jarvis API process (not this compose file) | Point Jarvis's `settings.toolhive_vmcp_url` at wherever ToolHive ends up listening once you bring it up manually (§5). |

### The mandatory encryption-key rule

`OOMOL_CONNECT_ENCRYPTION_KEY` **must** be set before `openconnector` starts —
`docker-compose.yml` hard-fails compose interpolation if it's missing
(`${OOMOL_CONNECT_ENCRYPTION_KEY:?...}`), mirroring the same
fail-fast-on-missing-secret pattern Jarvis's own `settings.py::validate_startup`
uses for `JARVIS_OAUTH_ENCRYPTION_KEY` (`backend/src/config/settings.py:252-262`).

**Do not lose or rotate this key without a migration plan.** OpenConnector
uses it to encrypt every stored connection (including the Gmail OAuth
tokens); losing the key orphans every connection it protects, and rotating
it in place (without a companion re-encryption pass) does the same. Store it
in a secret manager for anything beyond a disposable local sandbox — a
`.env` file is fine for local dev only, and must never be committed.

## 3. Bringing up the stack (local dev)

From `infra/gateway/`:

```bash
# 1. Make sure Jarvis's own stack (Postgres etc.) is already up and migrated,
#    from the repo root:
cd ../.. && docker compose up -d && cd backend && alembic upgrade head && cd ../infra/gateway

# 2. Generate secrets (do this once; save them somewhere durable — see §2)
export OOMOL_CONNECT_ENCRYPTION_KEY=$(openssl rand -hex 32)
export OOMOL_CONNECT_RUNTIME_TOKEN=$(openssl rand -hex 24)
export JARVIS_PLATFORM_JWT_PRIVATE_PEM="$(openssl genrsa 2048)"
export JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:jarvis@host.docker.internal:5432/jarvis

# 3. Bring up openconnector + connection-adapter
docker compose up -d

# 4. Confirm both are healthy
docker compose ps
docker compose logs -f connection-adapter
```

At this point `openconnector` is listening on host `:3001` (via `PORT=3001` +
`-p 3001:3001` — this keeps host `:3000` free for the Next.js frontend; see
RUNBOOK-gmail.md) and `connection-adapter`
(the MCP service ToolHive will front) is listening on `:8100/mcp`. Neither
Gmail nor ToolHive is connected yet — see §5.

## 4. What is NOT automated here

- **ToolHive itself** is not in `docker-compose.yml`. Its own
  container/operator lifecycle is environment-specific (Kubernetes operator
  vs. local `thv` CLI mode — see the header comment in
  `toolhive-vmcp-gmail.yaml` for the two different config surfaces those
  modes expect). Fabricating an unverified ToolHive image tag here would be
  worse than leaving it out; install it per Stacklok's own docs for your
  target environment, then point it at `toolhive-vmcp-gmail.yaml`.
- **`JARVIS_GMAIL_VIA_GATEWAY=true` and `JARVIS_TOOLHIVE_VMCP_URL`** need to
  be set on the **Jarvis API/worker process** itself (not just the adapter)
  for Jarvis to actually route Gmail calls through this path — that process
  lives in the repo-root `docker-compose.yml` / your local `python run.py`,
  outside this directory's scope.

## 5. Manual follow-up (not automated here)

These steps require a live ToolHive instance and real Gmail OAuth consent —
neither is something to script blind. Do them by hand, in order:

1. **Install ToolHive v0.43.0** per Stacklok's docs for your target
   environment (Kubernetes operator or local `thv` CLI —
   https://docs.stacklok.com/toolhive/). If using the CLI/local mode, adapt
   `toolhive-vmcp-gmail.yaml` per its own header comment (strip the
   `apiVersion`/`kind`/`metadata` wrapper; the `spec:` body's inner fields
   carry over).
2. **Verify `toolhive-vmcp-gmail.yaml` field names against the running
   ToolHive's actual schema** (`thv vmcp validate --config ...` in CLI mode,
   or `kubectl explain virtualmcpserver.spec` in operator mode) before
   applying — see the file's header comment for the specific fields flagged
   as best-effort guesses (the outgoing-auth passthrough type name, the
   per-workload tool-filter key, the OIDC issuer/jwksUrl split).
3. **Point ToolHive at Jarvis's JWKS endpoint** — `<jarvis-api-base>/.well-known/jwks.json`
   (root-level, not under `/v1` — `backend/src/api/routes_jwks.py`). Replace
   the `<jarvis-api-base>` placeholder in `toolhive-vmcp-gmail.yaml`.
4. **Connect a TEST Gmail account** through OpenConnector's OAuth flow (not
   this repo's own `OAuthManager` — the design deliberately uses
   OpenConnector as the credential system-of-record for gateway providers,
   design spec decision D2). Use a disposable/test Gmail account, not a real
   one, for this slice.
5. **Seed the `connection_map` row** for that test principal — table already
   exists (migration `877e3d55fc30_add_connection_map_table`). Columns:
   `tenant_id, workspace_id, principal_id, provider_id='gmail',
   provider_account_id, connection_id, credential_reference, granted_scopes,
   connection_status='active', account_alias, scope`. `connection_id` must be
   the namespaced OpenConnector `connectionName`:
   `{tenant_id}:{principal_id}:gmail:{account_alias}` (design spec §7).
6. **Set `JARVIS_GMAIL_VIA_GATEWAY=true` and `JARVIS_TOOLHIVE_VMCP_URL`** on
   the Jarvis API/worker process (see §4) and restart it.
7. **Run a chat turn that calls `email.search`** for the seeded test
   principal and confirm, end-to-end: the call reaches Gmail through
   ToolHive → connection-adapter → OpenConnector, results come back with no
   raw token visible in the response, traces, or logs (spike-findings.md §6
   already confirmed no leak inside OpenConnector's own logs — re-verify
   across the adapter and ToolHive hops too), and a **second** principal
   cannot see or use the first principal's connection (design spec §11's
   headline P0 acceptance test).

None of step 1–7 is exercised by these config files — they define the
target shape of the stack, not a live run of it.
