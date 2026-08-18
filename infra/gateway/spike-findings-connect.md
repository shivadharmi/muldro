# OpenConnector Connect-Flow Spike (Increment 2, Task B)

**Status:** DONE (throwaway POC, no production code touched)
**Date:** 2026-08-16
**Image:** `ghcr.io/oomol-lab/open-connector:v1.3.5` (same digest as `spike-findings.md`)
**Scratch dir:** torn down (`docker compose down -v`) after the spike.

Gates the increment-2 connect-account flow. Answers: (1) does OpenConnector's
authorization-creation API accept a caller-supplied `connectionName`? (2) what
is its post-consent callback/redirect behavior, and how does a caller confirm
a connection is stored + active?

## 1. Two auth surfaces (NOT one)

OpenConnector exposes **two** independently-gated HTTP surfaces, with **two
different bearer tokens**:

| Surface | Token env | Purpose |
|---|---|---|
| `/api/*` | `OOMOL_CONNECT_ADMIN_TOKEN` | **admin/control plane** — connection + OAuth lifecycle (create, list, configs) |
| `/v1/*`, `/mcp` | `OOMOL_CONNECT_RUNTIME_TOKEN` | **runtime/data plane** — execute actions, proxy, list apps |

- `/openapi.json` is admin-gated (200 with admin token) — the full API map.
- Both return the same `401 {"error":{"code":"unauthorized",...}}` on a wrong/absent token, so a 401 does not tell you *which* token a path wants.
- **Implication:** the increment-1 adapter uses the RUNTIME token (`execute_action`). The connect flow needs the **ADMIN token** — a NEW secret Muldro must hold, distinct from the runtime token. Add `openconnector_admin_token` + `openconnector_admin_url` settings.

## 2. Authorization-creation API — accepts a caller-supplied connectionName ✓

`POST /api/oauth/authorizations` (admin token):

```
body: { "service": "<required>", "connectionName": "<optional string>" }   # additionalProperties: false
200 : { "service", "authorizationUrl", "state" }
```

Live test with `{"service":"gmail","connectionName":"muldrows:usr_alice:gmail:work"}`
returned a Google consent URL (200). **This is the gate for decision C
(Muldro owns naming) — it passes.** Muldro mints the namespaced
`connectionName` up front and passes it into the authorization request.

The `authorizationUrl` is the browser-consent URL:
`https://accounts.google.com/o/oauth2/v2/auth?...&redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Foauth%2Fcallback&response_type=code&state=<uuid>&scope=<gmail scopes>`

## 3. OAuth client config

`PUT /api/oauth/configs/{service}` (admin token):

```
body: { "clientId": "<required>", "clientSecret", "extra"?, "secretExtra"? }
200 : { service, configured:true, clientId, expectedRedirectUri, auth:{ type, authorizationUrl, tokenUrl, scopes[] } }
```

- Returns `expectedRedirectUri` = **`{OC_base}/oauth/callback`** — FIXED, OpenConnector-owned. This is the redirect URI to register in the Google Cloud OAuth app (runbook E).
- Gmail default scopes: `gmail.readonly, gmail.modify, gmail.compose, gmail.send, gmail.labels, gmail.settings.basic, gmail.settings.sharing`.

## 4. Post-consent behavior — POLL, not redirect-back (KEY FINDING)

- `redirect_uri` is OpenConnector's **own** `/oauth/callback` route (confirmed: `GET /oauth/callback` with no args → 400, route exists). There is **NO configurable app-return URL** — the authorization body is `additionalProperties:false` with no `returnUrl`/`redirectUri` param.
- `state` is a server-generated **UUID**, not the connectionName. OpenConnector maps `state → connectionName` internally.
- **⇒ Muldro gets no redirect-back signal.** After the user consents, the browser lands on OpenConnector's own callback/UI, not on Muldro. Confirmation must be **poll-based**.

## 5. Confirmation model

- Starting an authorization creates **NO pending connection row** (verified: `GET /api/connections` and `GET /v1/apps` still show only the no_auth virtual defaults after the gmail authorization was started). The connection materializes **only after the OAuth callback completes**.
- Confirm via `GET /api/connections` (admin) — find the row whose `id == "{service}:{connectionName}"` with **`configured: true`**, then flip `connection_map` `pending → active`.
- `GET /api/connections` lists **ALL** connections on the shared instance (no per-tenant filter) — reinforces D3 (adapter is the tenant boundary) and the `list_connections` suppression.
- Connection row shape: `{ id, service, connectionName, authType, configured, virtual, default, profile:{accountId, displayName, grantedScopes} }`.

## 6. Connection identity: `id = {service}:{connectionName}`

Every row observed has `id == "{service}:{connectionName}"` (e.g. `arxiv:default` = service `arxiv`, connectionName `default`). Gmail service = **`gmail`**.

**Google Calendar is a SEPARATE service `googlecalendar`** (not one
`google-workspace` server) — so the future gmail/calendar split (PR #12
review #2) is naturally provider-separated in OpenConnector, not a monolith
to carve up.

## 7. Credential verification on create

`PUT /api/connections/{service}` (the direct, non-OAuth path; body
`{authType, connectionName?, values{}}`) performs **live credential
verification** before storing — a dummy `openai` api key returned
`400 credential_verification_failed`. So the direct path can't be seeded with
fake creds; the automated harness (D) must use the no-auth `hackernews`
provider (its virtual `default` connection needs no creation).

## 8. Residual risk (verify in Task D, do NOT assume)

The adapter forces a **colon-laden** `connectionName`
(`{tenant}:{principal}:gmail:{alias}`), and OpenConnector's connection `id`
is **also colon-delimited** (`{service}:{connectionName}`). The authorization
endpoint *accepted* the multi-colon name, but increment-1's P0 isolation test
**mocks** OpenConnector, so it never proved OC stores/round-trips a
multi-colon `connectionName` and that `execute_action` resolves it. Confirm
against real OC in the integration harness (D) before relying on it.

## Design implications for the connect flow (Task C)

- `begin_connection(provider, alias)`:
  1. mint `connectionName = {tenant}:{principal}:{provider}:{alias}`
  2. admin `POST /api/oauth/authorizations {service, connectionName}` → get `authorizationUrl`
  3. upsert `connection_map` `status=pending`; return `authorizationUrl` to the user
- Completion (no redirect-back): **poll** admin `GET /api/connections` for `id == "{service}:{connectionName}"` && `configured:true` → flip `connection_map` `status=active`. Poll on a scheduler tick and/or on-demand when the user returns. Resolver already denies non-`active`, so fail-closed until confirmed.
- New secrets/settings: `openconnector_admin_url`, `openconnector_admin_token`.
- Runbook E: register the Gmail OAuth client via `PUT /api/oauth/configs/gmail`; register `{OC_base}/oauth/callback` as the authorized redirect URI in Google Cloud Console.
