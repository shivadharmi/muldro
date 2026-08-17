# Runbook: connecting real accounts through the gateway

This runbook walks the full multi-provider gateway: **3 OC providers across 2
Jarvis installations** — `google-workspace` -> (`gmail`, `googlecalendar`),
`github` -> (`github`) — served by one adapter process as 21 named per-action
MCP tools plus `list_connections` (see
[`backend/src/integrations/gateway_actions/`](../../backend/src/integrations/gateway_actions/),
the single source of truth for the provider/action registry). §§1-10 below
walk Gmail specifically (the vertical slice this runbook was first written
against); [§12](#12-multi-provider-acceptance) is the multi-provider
acceptance checklist covering all three providers together, including the
frontend's Google Workspace two-provider-in-one-installation connect flow and
its second-popup risk.

OpenConnector runs on host `:3001` (via `PORT=3001` + `-p 3001:3001`) so the
Next.js frontend keeps `:3000`; register `http://localhost:3001/oauth/callback`
as the Google OAuth client's authorized redirect URI.

This is a **manual, browser-in-the-loop** runbook, and it is the gateway's
**only** end-to-end proof: there is no automated real-HTTP e2e. Google's OAuth
consent screen cannot be driven headlessly, and OpenConnector verifies
credentials live on `POST /api/connections`, so a real, auth-required provider
like `gmail` cannot be seeded with fake creds either (see
[`spike-findings-connect.md`](./spike-findings-connect.md) §7). Every
end-to-end claim below — including the adapter's tenant-isolation boundary —
rests on a human walking this runbook and clicking "Allow" in a real browser.
Offline unit tests cover the adapter's enforcement logic; they do not cover
the wire.

Every endpoint, payload shape, and behavior below is taken verbatim from the
live spike recorded in
[`spike-findings-connect.md`](./spike-findings-connect.md). Section numbers
in parentheses (e.g. "spike §3") refer to that file. Do not improvise
endpoints beyond what is documented there and in this runbook — OpenConnector
`v1.3.5`'s admin API is small and was only verified for the calls listed.

Companion reading: [`README.md`](./README.md) (compose services, pinned
versions, required env vars) and
[`docs/architecture/toolhive-openconnector-assessment.md`](../../docs/architecture/toolhive-openconnector-assessment.md)
(why this shape — Connection Context Adapter + tenant-isolated
OpenConnector).

---

## 1. Prerequisites

- **Jarvis Postgres up and migrated.** From the repo root:

  ```bash
  cd backend
  docker compose up -d          # or: cd .. && docker compose up -d && cd backend
  alembic upgrade head
  ```

  This must include migration `877e3d55fc30_add_connection_map_table` — the
  `connection_map` table this whole flow writes to and reads from
  (`backend/src/models/connection_map.py`).

- **One STABLE platform-JWT RSA PEM, shared by BOTH the Jarvis API process
  and the adapter container.** `backend/src/orchestrator/platform_jwt.py`
  mints short-lived (5-minute) RS256 JWTs; if `JARVIS_PLATFORM_JWT_PRIVATE_PEM`
  is unset, it silently falls back to an **ephemeral per-process key**. Two
  separate processes (API and adapter) each generating their own ephemeral
  key means tokens minted by one are **unverifiable** by the other — every
  gateway call will fail identity verification (`IdentityError` in
  `src/adapter/identity.py`) with no obvious "wrong key" message, just a JWT
  decode failure. Generate ONE key and set it identically on both processes
  before doing anything else in this runbook:

  ```bash
  openssl genrsa -out /tmp/platform-jwt.pem 2048
  export JARVIS_PLATFORM_JWT_PRIVATE_PEM="$(cat /tmp/platform-jwt.pem)"
  ```

  Set this same value as `JARVIS_PLATFORM_JWT_PRIVATE_PEM` on:
  1. the Jarvis API process (`python run.py`, or your API container's env), and
  2. the `connection-adapter` container's env (via `docker-compose.yml`,
     step 2 below).

- **Docker installed** (for OpenConnector + the adapter container).

---

## 2. Bring up OpenConnector + the adapter

Use `infra/gateway/docker-compose.yml` — it brings up `openconnector` +
`connection-adapter` together. Export the four variables it requires first
(see [`README.md`](./README.md) §2 and the compose file's header), including
the `JARVIS_PLATFORM_JWT_PRIVATE_PEM` from §1 above. From `infra/gateway/`:

```bash
docker compose up -d
docker compose ps
```

Confirm both are healthy — `openconnector` listening on `:3001`,
`connection-adapter` listening on `:8100/mcp` (per `backend/run_adapter.py`).

On the **Jarvis API process** (not the compose file — same pattern as
`README.md` §4), set:

```bash
export JARVIS_OPENCONNECTOR_ADMIN_URL=http://localhost:3001       # or the container's mapped host:port
export JARVIS_OPENCONNECTOR_ADMIN_TOKEN=<the container's OOMOL_CONNECT_ADMIN_TOKEN>
export JARVIS_TOOLHIVE_VMCP_URL=http://localhost:8100/mcp         # see note below
```

(`JARVIS_OPENCONNECTOR_ADMIN_URL` / `JARVIS_OPENCONNECTOR_ADMIN_TOKEN` /
`JARVIS_TOOLHIVE_VMCP_URL` map to `settings.openconnector_admin_url` /
`settings.openconnector_admin_token` / `settings.toolhive_vmcp_url` in
`backend/src/config/settings.py`.) Restart the API process after setting
these.

**There is no per-provider gateway flag.** `_installation_to_config()` in
`backend/src/integrations/mcp_pool.py` routes an installation at the gateway
whenever it declares `auth_provider="platform_jwt"` **and**
`settings.toolhive_vmcp_url` is set — the adapter process itself serves
**every** provider in the registry (gmail, googlecalendar, github) with no
provider-selection env var. If `toolhive_vmcp_url` is unset, a gateway-declared installation is skipped
at registration with a loud `GatewayNotConfigured` error log (see
`WorkspaceMCPPool.initialize_from_db` in `mcp_pool.py`) rather than silently
falling back to a native path — so a missing vMCP URL shows up in the API
process's logs, not as a silent wrong-path bug at step 8. This runbook does
not stand up ToolHive itself (that lifecycle is
environment-specific — see [`README.md`](./README.md) §4/§5); pointing
`JARVIS_TOOLHIVE_VMCP_URL` straight at the adapter's own `:8100/mcp` endpoint
is sufficient for this verification, since the adapter is the MCP service
ToolHive would otherwise front.

**Note the two-token split** (spike §1): `OOMOL_CONNECT_ADMIN_TOKEN` gates
`/api/*` (what this runbook uses to register the OAuth client and poll
connection status) and is **distinct** from `OOMOL_CONNECT_RUNTIME_TOKEN`
(what the adapter uses for `/mcp` `execute_action` calls). Both return the
same generic `401 unauthorized` on a wrong token, so if step 4 or step 6
below 401s, double-check you used the **admin** token, not the runtime one.

---

## 3. Google Cloud Console — create an OAuth client

1. In Google Cloud Console, create an OAuth 2.0 Client ID (Web application type).
2. Register the **authorized redirect URI** as OpenConnector's own callback
   route — **not** a Jarvis URL (spike §3–§4):

   ```
   http://localhost:3001/oauth/callback
   ```

   (If your OpenConnector container is reachable at a different host:port
   than `localhost:3001`, use that instead — it must exactly match the
   `expectedRedirectUri` returned in step 4 below.)
3. Request Gmail's default scopes (spike §3): `gmail.readonly`,
   `gmail.modify`, `gmail.compose`, `gmail.send`, `gmail.labels`,
   `gmail.settings.basic`, `gmail.settings.sharing`.
4. Note the resulting **Client ID** and **Client Secret** — used in step 4.

---

## 4. Register the OAuth client in OpenConnector

`PUT /api/oauth/configs/gmail` (admin token, spike §3):

```bash
export OC_ADMIN_URL=http://localhost:3001
export OC_ADMIN_TOKEN=<OOMOL_CONNECT_ADMIN_TOKEN>

curl -sS -X PUT "$OC_ADMIN_URL/api/oauth/configs/gmail" \
  -H "Authorization: Bearer $OC_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "clientId": "<GOOGLE_OAUTH_CLIENT_ID>",
        "clientSecret": "<GOOGLE_OAUTH_CLIENT_SECRET>"
      }' | jq
```

Expected `200`:

```json
{
  "service": "gmail",
  "configured": true,
  "clientId": "<GOOGLE_OAUTH_CLIENT_ID>",
  "expectedRedirectUri": "http://localhost:3001/oauth/callback",
  "auth": {
    "type": "...",
    "authorizationUrl": "...",
    "tokenUrl": "...",
    "scopes": ["gmail.readonly", "gmail.modify", "gmail.compose", "gmail.send", "gmail.labels", "gmail.settings.basic", "gmail.settings.sharing"]
  }
}
```

Confirm `expectedRedirectUri` here matches exactly what you registered in
Google Cloud Console in step 3 — a mismatch produces Google's own
`redirect_uri_mismatch` error at consent time, not an OpenConnector error.

---

## 5. Begin the connection

`POST /v1/connections/begin` (authenticated as the Jarvis user — normal
session bearer, **not** the OpenConnector admin token). This is the Jarvis
API endpoint that mints the namespaced `connectionName` and calls
OpenConnector's `POST /api/oauth/authorizations` on your behalf (spike §2,
"Design implications for the connect flow"):

```bash
export JARVIS_API_URL=http://localhost:8000
export JARVIS_SESSION_TOKEN=<your Jarvis session bearer>

curl -sS -X POST "$JARVIS_API_URL/v1/connections/begin" \
  -H "Authorization: Bearer $JARVIS_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider": "gmail", "alias": "work"}' | jq
```

Expect a response carrying an `authorization_url`. **Open that URL in a
browser** and complete Google's consent screen for the account you want to
connect. You will land on OpenConnector's own `/oauth/callback` page (or a
generic success/error page it renders) — this is expected; there is no
redirect back to Jarvis (spike §4, KEY FINDING).

> If `/v1/connections/begin` is not yet available in your checkout (its
> implementation is a separate task in this increment), you can drive the
> same admin call directly as a fallback — mint the namespaced
> `connectionName` yourself (`{tenant_id}:{principal_id}:gmail:{alias}`,
> spike §6.3) and call:
>
> ```bash
> curl -sS -X POST "$OC_ADMIN_URL/api/oauth/authorizations" \
>   -H "Authorization: Bearer $OC_ADMIN_TOKEN" \
>   -H "Content-Type: application/json" \
>   -d '{"service": "gmail", "connectionName": "<tenant_id>:<principal_id>:gmail:work"}' | jq
> ```
>
> and open the returned `authorizationUrl`. You will then need to seed the
> `connection_map` row yourself (see step 7's columns) once step 6 confirms
> `configured: true`, since the `/v1/connections/begin` endpoint is what
> would normally do that bookkeeping.

---

## 6. Confirm the connection

`POST /v1/connections/confirm`, same body, same auth:

```bash
curl -sS -X POST "$JARVIS_API_URL/v1/connections/confirm" \
  -H "Authorization: Bearer $JARVIS_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider": "gmail", "alias": "work"}' | jq
```

Expect:

```json
{"status": "active"}
```

**This is poll-based, not push-based** (spike §4–§5): OpenConnector never
redirects back to Jarvis, and starting an authorization creates **no**
pending connection row — the connection only materializes after the OAuth
callback completes server-side on OpenConnector's end. If the browser
consent in step 5 hasn't finished yet (or failed), you'll get:

```json
{"status": "pending"}
```

Retry `confirm` after a few seconds. There is no webhook here — just poll
until `status` flips to `active`, or investigate if it stays `pending` for
more than a minute or two (check the OpenConnector container logs for OAuth
callback errors).

---

## 7. Verify

**`connection_map` row is `active`:**

```sql
SELECT tenant_id, principal_id, provider_id, account_alias, connection_id,
       connection_status, granted_scopes
FROM connection_map
WHERE provider_id = 'gmail' AND account_alias = 'work';
```

Expect `connection_status = 'active'` and `connection_id` equal to the
namespaced `{tenant_id}:{principal_id}:gmail:work` (spike §6.3,
`backend/src/models/connection_map.py`).

**Admin `GET /api/connections` shows the row, `configured: true`:**

```bash
curl -sS "$OC_ADMIN_URL/api/connections" \
  -H "Authorization: Bearer $OC_ADMIN_TOKEN" | jq
```

Find the entry whose `id == "gmail:{tenant_id}:{principal_id}:gmail:work"`
(connection `id = "{service}:{connectionName}"`, spike §6 — note `gmail`
appears twice: once as the OpenConnector `service`, once inside the
Jarvis-minted `connectionName`). It should show `configured: true` and a
`profile` with `accountId`/`displayName`/`grantedScopes` populated from the
real Google account you consented with (spike §5).

Remember `GET /api/connections` lists **every** connection on the shared
OpenConnector instance, not just yours (spike §5) — the `connection_map`
row you queried above is the tenant-scoped source of truth; this admin call
is just cross-checking OpenConnector's own state.

---

## 8. Drive a chat turn calling `gmail.get_profile`

`gmail.get_profile` is the right **first** real action to verify: it takes
no required input (an optional `userId`, omitted to mean "the connected
mailbox") and returns the connected Google account's profile — so a
successful call is unambiguous proof the whole chain (agent tool discovery →
gateway routing → adapter identity/capability checks → OpenConnector →
Google) worked, without needing to reason about search results or message
content.

With `JARVIS_TOOLHIVE_VMCP_URL` set on the API process (step 2) and the
`google-workspace` installation registered with `auth_provider="platform_jwt"`
(so it routes at the gateway per the note in step 2) and the connection
`active` (step 7), restart the API process so it picks up the setting, then
drive this **through the agent**, not a raw tool call: send a chat message
for the workspace/user whose `connection_map` row you just created that
should make the agent reach for `gmail_get_profile` on its own — e.g.:

> "Which Gmail account are we connected to?"

through the normal chat UI or `POST /v1/chat`. Confirm two things:

1. **The agent's response names the real Google account** you consented
   with in step 5 (not a placeholder, not an error surfaced as prose).
2. **The call routed through the gateway**, not the direct
   `google-workspace-mcp` process — check Jarvis server logs / traces for a
   tool call named `gmail_get_profile` reaching `connection-adapter:8100`
   (see the "Named tools" subsection below for how the agent sees this tool
   at all).

If the agent doesn't call `gmail.get_profile` on the first try, rephrase to
be more explicit ("use the gmail tool to check which account is connected")
— tool selection is the agent's own agentic discovery, not a scripted
dispatch (see CLAUDE.md "Agentic vs Scripted Execution"); this runbook
verifies the transport, not prompt-engineering the agent's tool choice.

### Named tools

Once the `google-workspace` installation routes at the gateway (per the note
in step 2), the agent discovers the **named per-action Gmail tools** directly
from the adapter's MCP tool list — no `search_gmail_messages`-style
catch-all, and no manual `execute_action(actionId=...)` translation step.
The adapter process serves the named tools of **every** provider in the
registry — Gmail, Google Calendar, and GitHub, 21 actions total — not
Gmail's alone; there is no per-provider on/off switch. Each tool name is the
provider's OC `actionId` with `.` -> `_` (dots are illegal in Anthropic/
OpenAI tool names — `src/integrations/gateway_naming.py`); the adapter
enforces the mapped capability on every call
(`GatewayProfile.action_required_capability` in
`backend/src/adapter/enforcement.py`, derived from
`backend/src/integrations/gateway_actions/` and seeded into the tool registry
via `backend/src/tools/catalog.py`). This table is generated from that
registry — regenerate it (see the YAML's caveat 2 for the command) rather
than hand-editing it if the registry changes:

| Tool name | Capability | Risk | Notes |
|---|---|---|---|
| `gmail_get_profile` | `email.read` | low | zero-input; returns the connected account's email — use this one for step 8 |
| `gmail_fetch_emails` | `email.search` | low | |
| `gmail_search_threads` | `email.search` | low | |
| `gmail_get_message` | `email.read` | low | |
| `gmail_list_threads` | `email.list` | low | |
| `gmail_list_labels` | `email.list` | low | |
| `gmail_send_email` | `email.send` | high | **write** — requires approval (TrustEngine gate on the autonomous path, `permission_gate` on chat once `deep_single_lead` is on; `capability_scope` enforces the boundary either way, see CLAUDE.md "Trust Infrastructure & Approval") |
| `googlecalendar_list_calendars` | `calendar.list` | low | |
| `googlecalendar_list_events` | `calendar.list` | low | |
| `googlecalendar_get_event` | `calendar.get` | low | |
| `googlecalendar_free_busy_query` | `calendar.get` | low | |
| `googlecalendar_create_event` | `calendar.create` | medium | **write** — requires approval |
| `googlecalendar_update_event` | `calendar.update` | medium | **write** — requires approval |
| `github_search_repositories` | `repo.search_repos` | low | |
| `github_search_code` | `repo.search_code` | low | |
| `github_list_pull_requests` | `repo.list_prs` | low | |
| `github_list_repository_issues` | `issue.list` | low | |
| `github_search_issues_and_pull_requests` | `issue.search` | low | |
| `github_create_pull_request` | `repo.create_pr` | high | **write** — requires approval |
| `github_create_issue` | `issue.create` | medium | **write** — requires approval |
| `github_create_issue_comment` | `issue.comment` | medium | **write** — requires approval |

---

## 9. Known caveat — `google-workspace` fans out to two OC providers

The `google-workspace` Jarvis installation serves **two** OC providers —
`gmail` and `googlecalendar` — as one gateway-routed MCP server: once that
installation routes at the gateway (step 2), the agent sees **both**
Gmail's and Calendar's named tools together, since they share one
installation-level `auth_provider="platform_jwt"` switch. There is no way to
route only Gmail while leaving Calendar on the native path (or vice versa)
— it is all-or-nothing per installation. This matters for §12's frontend
flow: connecting "Google Workspace" through the UI is really two independent
OC OAuth grants (Gmail, then Calendar) landing under one Jarvis installation
and one `connection_map` per-provider row pair, not one combined grant.

GitHub is unaffected — it is its own installation with a single OC provider.

### Optional lower-level debug fallback

Step 8's chat-driven `gmail.get_profile` call **is** the intended
verification and proves the transport end-to-end through the real agent
path. If it fails and you need to isolate whether the problem is in the
agent/gateway wiring versus the adapter/OpenConnector/Google chain itself,
you can bypass the agent entirely and call the adapter's `execute_action`
directly with a hand-minted platform JWT — this is strictly a lower-level
debug tool, not the primary verification step.

From `backend/`, with the venv active and `JARVIS_PLATFORM_JWT_PRIVATE_PEM`
set to the SAME pem as the adapter container (step 1):

```bash
cd backend
source .venv/bin/activate

python - <<'PY'
from src.orchestrator.platform_jwt import mint_platform_jwt

token = mint_platform_jwt(
    principal_id="<principal_id>",   # must match the connection_map row's principal_id
    tenant_id="<tenant_id>",         # must match the connection_map row's tenant_id
    workspace_id="<workspace_id>",
    capabilities=["email.read"],     # gmail.get_profile requires email.read (src/adapter/enforcement.py)
)
print(token)
PY
```

Save that token, then call the adapter's `execute_action` MCP tool directly
over its streamable-http transport (same JSON-RPC shape OpenConnector's own
`/mcp` uses — spike §4 of `spike-findings.md`, the sibling request-shape
spike) — mirroring step 8's `gmail.get_profile` call, just at the raw
`execute_action` layer instead of through agent tool discovery:

```bash
export PLATFORM_JWT=<token from above>

curl -sS -X POST "http://localhost:8100/mcp" \
  -H "Authorization: Bearer $PLATFORM_JWT" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
          "name": "execute_action",
          "arguments": {
            "actionId": "gmail.get_profile",
            "input": {},
            "account_alias": "work"
          }
        }
      }' | jq
```

(Swap `actionId`/`input` for any of the other 20 named actions in the table
above — e.g. `"actionId": "gmail.fetch_emails", "input": {"query": "is:unread"}`
— to debug a different action the same way. Note `execute_action` still takes
the dotted OC `actionId`, not the underscored tool name — it is the adapter's
raw internal call shape, not the agent-facing surface.)

A successful call proves: identity verification (the platform JWT
round-trips through the shared PEM), allowlist + capability enforcement
(`gmail.get_profile` + `email.read` both pass, `src/adapter/enforcement.py`),
connection resolution to YOUR `connection_map` row
(`src/adapter/connection_resolver.py`), the forced, server-side
`connectionName` reaching OpenConnector (never caller-supplied), and
secret-stripped Gmail profile data coming back. If it fails:

- `IdentityError` / JWT decode failure → mismatched PEM between this script's
  process and the adapter container (step 1).
- `CapabilityDenied` → the `capabilities` list passed to `mint_platform_jwt`
  doesn't include the capability `gmail.get_profile` requires (`email.read`,
  per `GatewayProfile.action_required_capability` in
  `src/adapter/enforcement.py`).
- `ConnectionDenied` → no `active` `connection_map` row for that
  `(tenant_id, principal_id, provider_id="gmail", account_alias)` — recheck
  step 7.

---

## 10. Verify the colon-laden `connectionName` round-trips

Per spike §8, this was flagged as an **unverified residual risk**: the
adapter forces a colon-laden `connectionName`
(`{tenant_id}:{principal_id}:gmail:{alias}`), and OpenConnector's own
connection `id` is *also* colon-delimited (`{service}:{connectionName}`) —
so a real connection ends up with an id like
`gmail:{tenant_id}:{principal_id}:gmail:work` (five colon-separated
segments, `gmail` appearing twice). Increment 1's P0 isolation test **mocks**
OpenConnector, so it never proved OC actually stores and resolves a
multi-colon name correctly end-to-end.

Step 8's successful chat-driven `gmail.get_profile` call (or the optional
`execute_action` debug fallback in step 9) **is** the round-trip proof, if
it succeeded — `force_connection_name()` in `src/adapter/enforcement.py` put
the full multi-colon `connectionName` on the outbound OpenConnector call,
and OpenConnector had to correctly resolve it back to the specific Gmail
connection you authorized in steps 5–6 (not silently fall back to some
other/default connection) to return the real account's profile data.

To double-check independently of the response payload, cross-reference two
things you already have:

1. The `id` field from step 7's `GET /api/connections` call — confirm it is
   exactly `gmail:{tenant_id}:{principal_id}:gmail:work`, not truncated or
   re-delimited at the first/last colon.
2. OpenConnector's container logs for the `execute_action` request handled
   in step 8 (or step 9's debug fallback) — confirm the `connectionName` it
   logged (if logged at info/debug level) matches the same full string, and
   that no other `gmail:*` connection on the shared instance was touched
   instead:

   ```bash
   docker compose logs openconnector | grep -i "connectionName\|gmail:"
   ```

If either check shows a truncated or mismatched name, OpenConnector is not
round-tripping the multi-colon `connectionName` safely — treat this as a
blocking finding against decision C (Jarvis owns naming) before relying on
this shape in production, per spike §8.

---

## 11. Frontend connect acceptance (popup-poll)

Everything above drives the gateway connection via `curl` — proving the
adapter/OpenConnector/Google transport. This section is a **separate,
browser-driven** acceptance pass proving the actual product surface: a user
clicking "Connect" on the `/integrations` page, consenting in a popup, and
the main window picking up the result without a redirect. Like the rest of
this runbook, it cannot be driven headlessly — a human has to click "Allow"
at Google.

### 11.1 Bring up the full stack

1. **Infra.** From the worktree's `backend/`:

   ```bash
   cd backend
   docker compose up -d          # Postgres, Redis, Qdrant, Neo4j, MinIO
   ```

2. **OpenConnector.** The gateway `openconnector` container on **:3001**, per
   §2 above (`docker compose up -d` from `infra/gateway/`).

3. **Adapter.** `run_adapter` on `:8100`, serving every registered provider
   (no provider env var; also brought up by the same compose file, per §2).

4. **API server.** `python run.py` (from `backend/`, venv active) with the
   gateway env from §2 set on the process, plus the platform-JWT PEM from §1:

   ```bash
   export JARVIS_TOOLHIVE_VMCP_URL=http://localhost:8100/mcp
   export JARVIS_OPENCONNECTOR_ADMIN_URL=http://localhost:3001
   export JARVIS_OPENCONNECTOR_ADMIN_TOKEN=<OOMOL_CONNECT_ADMIN_TOKEN>
   export JARVIS_PLATFORM_JWT_PRIVATE_PEM="$(cat /tmp/platform-jwt.pem)"   # same PEM as step 1
   export JARVIS_ANTHROPIC_API_KEY=<your key>

   python run.py
   ```

5. **Frontend.** From `frontend/`:

   ```bash
   npm run dev            # :3000
   ```

   `:3000` is the only window the user ever sees — the popup opened in
   step 11.3 below lands on OpenConnector's own callback page, not a Jarvis
   route.

### 11.2 Seed the installation

The frontend only offers the gateway popup-poll flow for a provider if the
unified-integrations API reports it as gateway-backed. Ensure a
`google-workspace` `IntegrationInstallation` row exists for the test
workspace, so `GET` on the unified-integrations endpoint returns
`oc_provider="gmail"` for it (`backend/src/integrations/gateway_providers.py`,
`backend/src/services/integration_status.py`). Without this row, the
Google/Gmail card falls back to the native OAuth-redirect flow instead of
the popup-poll flow this section is verifying.

### 11.3 Drive the flow in the browser

1. Log in as the seeded ULID user and open `/integrations`.
2. Click **Connect** on the Google/Gmail card.
3. A popup window opens to Google's consent screen. Consent with a real
   Google account (same caveat as §5 above — this cannot be scripted).
4. The main window (still on `:3000`) polls `POST /v1/connections/confirm`
   in the background (same endpoint as §6). On success it shows a
   "Connected" toast and the card flips to the Connected state.
5. The popup itself lands on OpenConnector's own callback page (`:3001`) —
   that is expected, same as the raw-curl flow in §5; the main app window
   never navigates away from `:3000`.

### 11.4 Acceptance check

Open chat and ask:

> "Which Gmail account am I connected to?"

Expect the agent to call the `gmail_get_profile` tool (§8's "Named tools"
table) and name the real account you consented with in 11.3. This confirms
the **frontend-initiated** connection — not the curl-driven one from §5 —
produced a working `connection_map` row and a live gateway credential; the
transport itself (adapter → OpenConnector → Google) was already proven end
to end in the prior increment (§8–§10 above).

### 11.5 Note — opaque `connectionName` hashing

The `connectionName` OpenConnector stores is now an opaque `blake2b` digest
minted by `ConnectionService` (`backend/src/services/connection_service.py`),
not the raw colon-delimited `{tenant_id}:{principal_id}:gmail:{alias}` string
discussed in §10. The digest is derived from that same tuple but rendered as
a short (≤64-char), OC-valid hex string, so real ULID-based tenant/principal
IDs no longer risk tripping OpenConnector's `invalid_connection_name`
rejection — the earlier hyphen-substitution workaround for that rejection is
no longer needed for real users going through this frontend flow.

---

## 12. Multi-provider acceptance

§11 proved the frontend connect flow for Gmail alone. This section is the
full acceptance pass across **all three providers** — `gmail`,
`googlecalendar`, `github` — through the real frontend, plus the two
highest-value live-run risks a code review flagged that no offline test can
cover: whether the Google Workspace flow's **second popup** actually opens,
and whether a **partial** Google Workspace connection (Gmail yes, Calendar
no) stays visible instead of collapsing to "disconnected". Like §11, this
cannot be driven headlessly — a human has to click "Allow" at Google and at
GitHub.

### 12.1 Configure OpenConnector OAuth for all three services

Repeat step 4's `PUT /api/oauth/configs/{service}` call for each of `gmail`,
`googlecalendar`, and `github` — OpenConnector models them as three
independent OAuth2 configs (`GET /api/oauth/configs` lists 66 entries total;
these three each have their own `clientId`/`clientSecret`/scopes, per
`spike-findings-multiprovider.md` "Auth shape"):

- **gmail** — same client as step 3 (Google Cloud Console OAuth client,
  Gmail scopes) and the same `PUT /api/oauth/configs/gmail` call from step 4.
- **googlecalendar** — a **separate** OC service entry, even though it is
  also Google. It can reuse the same Google Cloud Console OAuth client as
  Gmail (same `expectedRedirectUri`, `http://localhost:3001/oauth/callback`)
  as long as that client's consent screen also requests Calendar's scopes
  (`.../auth/calendar`, `.../calendar.readonly`, `.../calendar.events`, at
  minimum). Register it with `PUT /api/oauth/configs/googlecalendar`.
- **github** — a **GitHub OAuth App** (Settings -> Developer settings ->
  OAuth Apps), authorized callback URL `http://localhost:3001/oauth/callback`,
  scopes `read:user`, `user:email`, `repo`, `workflow`, `delete_repo` (the
  confirmed set from the spike). Register its client id/secret with
  `PUT /api/oauth/configs/github`. GitHub's OC entry is a full OAuth2 config
  (`authorizationUrl: https://github.com/login/oauth/authorize`) — not a
  static personal-access-token flow — so the popup-poll UX applies to it
  exactly like the two Google providers.

Confirm all three respond `200` with `configured: true` and the expected
`expectedRedirectUri` before moving to the browser steps, same as step 4.

### 12.2 Connect Google Workspace through the real frontend (two sequential consents)

Bring up the full stack per §11.1 (no `JARVIS_GMAIL_VIA_GATEWAY` — that flag
no longer exists; only `JARVIS_TOOLHIVE_VMCP_URL` is needed to route the
gateway-backed installations), seed the `google-workspace` installation per
§11.2, then open `/integrations` and click **Connect** on the Google
Workspace card.

The `google-workspace` installation fans out to its two OC providers **in
sequence**, not in parallel: `useConnectAccount`
(`frontend/src/hooks/useConnectAccount.ts`) awaits the first provider's full
popup-open + poll-to-`active` cycle before starting the second's. Expect, in
order:

1. **Popup 1 (Gmail)** opens to Google's consent screen. Consent with a real
   Google account. The main window polls `POST /v1/connections/confirm` for
   `gmail` in the background until it reports `active`.
2. Only after step 1 resolves, **popup 2 (Google Calendar)** should open —
   same Google account, Calendar's consent screen (it may be silent/instant
   if the account already granted overlapping scopes, but a `window.open`
   call still fires). The main window polls `confirm` for `googlecalendar`.
3. On both resolving, the card flips to fully Connected.

### 12.3 🔴 Verify the second popup actually opens (not silently blocked)

This is the single highest-value thing to check by hand — it is exactly the
risk no offline/unit test can exercise, because it depends on real browser
popup-blocking heuristics, not application logic.

**Why it's a real risk, not a hypothetical:** browsers only allow
`window.open` to succeed under *transient user activation* — roughly a ~5
second window following a direct user gesture (the "Connect" click). Popup 1
consumes that activation window immediately. Popup 2's `window.open` call
happens only after popup 1's *entire* consent-and-poll-to-`active` cycle
completes — which, with a human actually reading and clicking through
Google's consent screen, reliably exceeds 5 seconds. So popup 2 is a strong
candidate for the browser's popup blocker, not a synthetic edge case.

The frontend already anticipates this: `runProviderConnect` in
`useConnectAccount.ts` treats a `null` return from `window.open` as a
distinct `"blocked"` `ProviderOutcome` (as opposed to `"error"` or a normal
poll result), and `integrations/page.tsx` renders a **"Popup blocked — click
to connect \<providers\>"** button that re-fires `window.open` under a fresh
click (a fresh transient-activation window) for just the unfinished
provider(s) — see the code comments at `useConnectAccount.ts` around the
`"blocked"` outcome for the full reasoning.

**What to actually verify on a real browser** (this is the check — do not
skip it because the code "looks handled"):

1. Click Connect on the Google Workspace card and consent to popup 1 (Gmail)
   as a real human would — i.e. do not rush it artificially fast.
2. Watch what happens for provider 2 (Calendar): does a second popup open on
   its own, or does the card show the **"Popup blocked — click to connect
   Google Calendar"** button?
3. **Record which path actually happened** — this varies by browser (Chrome,
   Firefox, Safari all have different popup-blocking heuristics) and by how
   quickly the human clicked through consent. Test in at least the browser
   your team standardizes on.
4. If blocked: click the retry button and confirm popup 2 opens and the flow
   completes to fully Connected. This IS a supported, expected outcome, not
   a bug — the acceptance bar is that the retry affordance works, not that
   the second popup never blocks.
5. If NOT blocked: still note it — browser popup-blocking behavior can
   change across a browser version bump, so a clean pass today doesn't
   retire this check for good.

### 12.4 Connect GitHub through the real frontend (single provider)

Click **Connect** on the GitHub card. GitHub is its own installation with a
single OC provider, so this is a single popup + single poll cycle, no
sequencing risk — same shape as Gmail alone in §11.

### 12.5 Verify `connection_map` has an active row per provider

```sql
SELECT workspace_id, principal_id, provider_id, account_alias, connection_status
FROM connection_map
WHERE workspace_id = '<your test workspace_id>'
ORDER BY provider_id;
```

(Columns per `backend/src/models/connection_map.py`; the table also carries
`tenant_id`, `connection_id`, `credential_reference`, `granted_scopes`, and
`scope`, omitted above for brevity.) Expect three rows —
`provider_id IN ('gmail', 'googlecalendar', 'github')` — each with
`connection_status = 'active'` after §§12.2/12.4 complete successfully.

### 12.6 Verify partial connection stays visible (does not collapse to "disconnected")

Repeat the Google Workspace connect flow from §12.2, but this time **decline
or close** the Calendar consent popup instead of completing it (leave Gmail
connected). Confirm on `/integrations`:

- The Google Workspace card does **not** show a single collapsed
  "disconnected" state.
- It shows enough detail to tell Gmail is connected and Calendar is not —
  i.e. per-provider connection state is surfaced, not just one aggregate
  boolean for the whole `google-workspace` installation.
- The `connection_map` query from §12.5 shows an `active` row for `gmail`
  and no `active` row for `googlecalendar` (either no row, or a non-`active`
  `connection_status`).

This is the "Gmail connected, Calendar declined -- the working connector
must stay visible" behavior — verify it holds in the real UI, not just in
the underlying data.

### 12.7 One real agent turn per provider

With all three providers connected (§§12.2/12.4), open chat and drive one
turn per provider, confirming each reaches for the correct **named** tool
(§8's table) and returns real data — no Anthropic 400, no silent fallback to
a native transport (there is none left to fall back to once an installation
is gateway-routed):

- "Which Gmail account am I connected to?" -> `gmail_get_profile`
- "What's on my calendar this week?" -> a calendar read tool (e.g.
  `googlecalendar_list_events`)
- "What GitHub issues are assigned to me?" -> a github read tool (e.g.
  `github_search_issues_and_pull_requests` or `github_list_repository_issues`)

If an agent doesn't pick the expected tool on the first try, rephrase to be
more explicit — as in step 8, this verifies transport, not prompt-engineered
tool selection (see CLAUDE.md "Agentic vs Scripted Execution").

### 12.8 Record the outcome

Whatever actually happened in §12.3 (blocked vs. not, on which browser) is
the single most useful fact to write down here for the next person who runs
this runbook — popup-blocking behavior is environment-dependent and this
runbook cannot assert a universal answer, only a verified one for a specific
run.

---

## 13. KNOWN GAP — gateway perception data path does not exist yet

**Status: deliberately deferred to its own increment (decided 2026-08-17).**

Tool-calling through the gateway is complete and live-verifiable via §12. **Perception
(the scheduler polling gmail / calendar / github for new signal) is NOT.** Do not
report §12 as a full acceptance of Gmail/Calendar/GitHub support.

### Why

The perception connectors are a **separate data path** that the gateway never
replaced. `backend/src/connectors/gmail.py`, `calendar.py`, and `github_connector.py`
call the provider REST APIs directly over `httpx` with a raw OAuth access token
(e.g. `https://gmail.googleapis.com/gmail/v1/users/me/history` with
`Authorization: Bearer …`). They never touch MCP. Both paths happened to be fed by
the same OAuth tokens, which is why migrating the *tool* transport silently broke
the *polling* transport.

Native OAuth for google and github is now retired, so those tokens cannot exist and
cannot be minted. `connector_poller` therefore fails at credential acquisition and
classifies it `auth_failed` (permanent, threshold 1 → the circuit opens after one
attempt).

### What was fixed, and what was not

- **Fixed:** the scheduler's runnability gate no longer marks these sources
  `needs_reauth`. All three gateway providers declare their `perception_sources` in
  the registry, so an unconnected source is *skipped* (still due, self-healing)
  rather than paused unrecoverably. This is what stops the eventual port from
  needing a data migration to un-poison paused rows.
- **Not fixed:** the poll itself. A connected source becomes runnable and then fails
  at credential acquisition.

### The decided direction (not yet built)

Port the connectors to poll **via gateway MCP tools** (`gmail_fetch_emails`,
`googlecalendar_list_events`, `github_*`) instead of provider REST, so OpenConnector
remains the single credential store.

**Known cost, accepted:** the curated gateway action set has **no incremental-sync
action**. Gmail perception currently does cursor-based incremental sync via the
`history` endpoint (`startHistoryId`, with a 404 → full-resync fallback); calendar
uses `syncToken` with a 410 fallback. Neither has a gateway equivalent, so the port
is a semantics change (full-list + dedup), not a transport swap. It likely needs a
re-spike of OpenConnector's catalog for a history-capable action before the cursor
model can be redesigned.

### Also orphaned by retirement (same increment)

`_register_webhooks_for_sources` (`backend/src/api/routes_auth_oauth_integration.py`)
has **zero production callers** — its `resource_map` covers exactly `gmail` and
`calendar`, so Gmail/Calendar push webhooks are never registered and those sources
would be poll-only even once polling works.
