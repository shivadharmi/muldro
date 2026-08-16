# Runbook: connecting a real Gmail account through the gateway

This is a **manual, browser-in-the-loop** runbook. Google's OAuth consent
screen cannot be driven headlessly, so — unlike the automated integration
e2e, which proves the adapter's tenant-isolation boundary against
OpenConnector's **no-auth `hackernews` provider** (`GET /api/connections`
live credential verification means a real, auth-required provider like
`gmail` can't be seeded with fake creds — see
[`spike-findings-connect.md`](./spike-findings-connect.md) §7) — this path
requires a human to click "Allow" in a real browser.

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
  2. the `connection-adapter` container's env (via `docker-compose.integration.yml`,
     step 2 below).

- **Docker installed** (for OpenConnector + the adapter container).

---

## 2. Bring up OpenConnector + the adapter

Use `infra/gateway/docker-compose.integration.yml` (built by a parallel task
in this increment — brings up `openconnector` + `connection-adapter` wired
for the automated e2e and reusable here). From `infra/gateway/`:

```bash
docker compose -f docker-compose.integration.yml up -d
docker compose -f docker-compose.integration.yml ps
```

Confirm both are healthy — `openconnector` listening on `:3000`,
`connection-adapter` listening on `:8100/mcp` (per `backend/run_adapter.py`).

On the **Jarvis API process** (not the compose file — same pattern as
`README.md` §4), set:

```bash
export JARVIS_OPENCONNECTOR_ADMIN_URL=http://localhost:3000       # or the container's mapped host:port
export JARVIS_OPENCONNECTOR_ADMIN_TOKEN=<the container's OOMOL_CONNECT_ADMIN_TOKEN>
export JARVIS_GMAIL_VIA_GATEWAY=true
export JARVIS_TOOLHIVE_VMCP_URL=http://localhost:8100/mcp         # see note below
```

(`JARVIS_OPENCONNECTOR_ADMIN_URL` / `JARVIS_OPENCONNECTOR_ADMIN_TOKEN` /
`JARVIS_GMAIL_VIA_GATEWAY` / `JARVIS_TOOLHIVE_VMCP_URL` map to
`settings.openconnector_admin_url` / `settings.openconnector_admin_token` /
`settings.gmail_via_gateway` / `settings.toolhive_vmcp_url` in
`backend/src/config/settings.py`.) Restart the API process after setting
these.

**`JARVIS_GMAIL_VIA_GATEWAY=true` alone is a no-op** — `_installation_to_config()`
in `backend/src/integrations/mcp_pool.py` only routes the `google-workspace`
installation at the gateway when **both** `settings.gmail_via_gateway` is on
**and** `settings.toolhive_vmcp_url` is set; otherwise it silently falls
through to the native local `google-workspace-mcp` process, and step 8 below
would exercise the wrong path without any error. This runbook does not stand
up ToolHive itself (that lifecycle is environment-specific — see
[`README.md`](./README.md) §4/§5); pointing `JARVIS_TOOLHIVE_VMCP_URL`
straight at the adapter's own `:8100/mcp` endpoint is sufficient for this
verification, since the adapter is the MCP service ToolHive would otherwise
front.

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
   http://localhost:3000/oauth/callback
   ```

   (If your OpenConnector container is reachable at a different host:port
   than `localhost:3000`, use that instead — it must exactly match the
   `expectedRedirectUri` returned in step 4 below.)
3. Request Gmail's default scopes (spike §3): `gmail.readonly`,
   `gmail.modify`, `gmail.compose`, `gmail.send`, `gmail.labels`,
   `gmail.settings.basic`, `gmail.settings.sharing`.
4. Note the resulting **Client ID** and **Client Secret** — used in step 4.

---

## 4. Register the OAuth client in OpenConnector

`PUT /api/oauth/configs/gmail` (admin token, spike §3):

```bash
export OC_ADMIN_URL=http://localhost:3000
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
  "expectedRedirectUri": "http://localhost:3000/oauth/callback",
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

With `JARVIS_GMAIL_VIA_GATEWAY=true` **and** `JARVIS_TOOLHIVE_VMCP_URL` set
on the API process (step 2) and the connection `active` (step 7), restart
the API process so it picks up the flag, then drive this **through the
agent**, not a raw tool call: send a chat message for the workspace/user
whose `connection_map` row you just created that should make the agent
reach for `gmail.get_profile` on its own — e.g.:

> "Which Gmail account are we connected to?"

through the normal chat UI or `POST /v1/chat`. Confirm two things:

1. **The agent's response names the real Google account** you consented
   with in step 5 (not a placeholder, not an error surfaced as prose).
2. **The call routed through the gateway**, not the direct
   `google-workspace-mcp` process — check Jarvis server logs / traces for a
   tool call named `gmail.get_profile` reaching `connection-adapter:8100`
   (see the "Named tools" subsection below for how the agent sees this tool
   at all).

If the agent doesn't call `gmail.get_profile` on the first try, rephrase to
be more explicit ("use the gmail tool to check which account is connected")
— tool selection is the agent's own agentic discovery, not a scripted
dispatch (see CLAUDE.md "Agentic vs Scripted Execution"); this runbook
verifies the transport, not prompt-engineering the agent's tool choice.

### Named tools

Once `JARVIS_GMAIL_VIA_GATEWAY=true` is on, the `google-workspace`
installation routes at the gateway (per the note in step 2) and the agent
discovers **7 named per-action Gmail tools** directly from the adapter's MCP
tool list — no `search_gmail_messages`-style catch-all, and no manual
`execute_action(actionId=...)` translation step. Each tool name **is** the
adapter's `actionId`; the adapter enforces the mapped capability on every
call (`ACTION_REQUIRED_CAPABILITY` in `backend/src/adapter/enforcement.py`,
seeded into the tool registry via `backend/src/tools/catalog.py`):

| Tool name | Capability | Notes |
|---|---|---|
| `gmail.get_profile` | `email.read` | zero-input; returns the connected account's email — use this one for step 8 |
| `gmail.fetch_emails` | `email.search` | |
| `gmail.search_threads` | `email.search` | |
| `gmail.get_message` | `email.read` | |
| `gmail.list_threads` | `email.list` | |
| `gmail.list_labels` | `email.list` | |
| `gmail.send_email` | `email.send` | **write** — requires approval (TrustEngine gate on the autonomous path, `permission_gate` on chat once `deep_single_lead` is on; `capability_scope` enforces the boundary either way, see CLAUDE.md "Trust Infrastructure & Approval") |

---

## 9. Known caveat — the whole `google-workspace` server is redirected

Per PR #12 review finding #2 (finding #1 — the agent-facing tool not being
translated to `execute_action` — is now resolved by the named-tool discovery
described above): the `JARVIS_GMAIL_VIA_GATEWAY` flag currently redirects
the **whole** `google-workspace` MCP server, not just Gmail — so **calendar
is unavailable** for the duration the flag is on, until the Gmail/calendar
split lands (naturally provider-separated in OpenConnector already —
`gmail` vs. `googlecalendar` are separate services, spike §6 — so this is a
routing-layer gap, not an OpenConnector modeling gap).

This is tracked as a separate, later increment (the Gmail/calendar split —
see the project's `project_toolhive_increment_build` notes; north star is a
compact verb→capability+risk policy table derived from OpenConnector's
action namespace).

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

(Swap `actionId`/`input` for any of the other 6 named tools in the table
above — e.g. `"actionId": "gmail.fetch_emails", "input": {"query": "is:unread"}`
— to debug a different action the same way.)

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
  per `ACTION_REQUIRED_CAPABILITY` in `src/adapter/enforcement.py`).
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
   docker compose -f docker-compose.integration.yml logs openconnector | grep -i "connectionName\|gmail:"
   ```

If either check shows a truncated or mismatched name, OpenConnector is not
round-tripping the multi-colon `connectionName` safely — treat this as a
blocking finding against decision C (Jarvis owns naming) before relying on
this shape in production, per spike §8.
