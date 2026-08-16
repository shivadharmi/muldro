# OpenConnector MCP Request-Shape Spike

**Status:** DONE (throwaway POC, no production code touched)
**Date:** 2026-08-16
**Scratch dir:** `/tmp/oc-spike` (torn down after the spike — `docker compose down -v` + dir removed)

## 1. Image / tag

Pinned tag pulled cleanly on first try — no `:latest` fallback was needed:

```
ghcr.io/oomol-lab/open-connector:v1.3.5
```

- Digest: `sha256:f53e91414e314b44fa58571f5c0009fbbeaee16195b0c4daf2bc0359ea013907`
- Image label `org.opencontainers.image.version` = `v1.3.5` (confirms the tag is not aliased to a different real version)
- Image label `org.opencontainers.image.revision` = `5719a69468c698c7cb8108e062ff64ecef8a2e65`
- Base: `node:24.19.0`, exposes `3000/tcp`, entrypoint `docker-entrypoint.sh` → `node src/server/index.ts`
- Built-in env defaults: `PORT=3000`, `HOST=0.0.0.0`, `OOMOL_CONNECT_DATA_DIR=/app/data`

## 2. Container run

`docker-compose.yml` used:

```yaml
services:
  open-connector:
    image: ghcr.io/oomol-lab/open-connector:v1.3.5
    ports:
      - "3000:3000"
    environment:
      OOMOL_CONNECT_ENCRYPTION_KEY: ${OOMOL_CONNECT_ENCRYPTION_KEY}
      OOMOL_CONNECT_RUNTIME_TOKEN: ${OOMOL_CONNECT_RUNTIME_TOKEN}
    volumes:
      - oc-data:/app/data
volumes:
  oc-data:
```

`OOMOL_CONNECT_ENCRYPTION_KEY` = `openssl rand -hex 32`, `OOMOL_CONNECT_RUNTIME_TOKEN` = `openssl rand -hex 24`, both set via a `.env` file consumed by `docker compose up -d`.

Container came up `healthy` immediately; startup log ran 10 sqlite migrations (0001–0010, covering runtime, run_service, action_idempotency, action_run_audit, run_retention, connection_identity, runtime_policy, runtime_token_policy, runtime_token_proxy, connection_revision) then logged:

```
{"msg":"connect server listening","url":"http://0.0.0.0:3000"}
{"msg":"runtime data directory","dataDir":"/app/data"}
{"level":40,"msg":"local admin authentication is disabled; set OOMOL_CONNECT_ADMIN_TOKEN to require bearer tokens"}
```

Note: there is a *separate* `OOMOL_CONNECT_ADMIN_TOKEN` (not requested in this spike) that gates a local admin surface — distinct from `OOMOL_CONNECT_RUNTIME_TOKEN`, which gates `/mcp`.

## 3. Auth enforcement on `POST /mcp`

Called `tools/list` with no `Authorization` header:

```
HTTP/1.1 401
{"error":{"code":"unauthorized","message":"A valid local bearer token is required."}}
```

**Result: YES — `POST /mcp` returns 401 without the runtime token.**

## 4. `tools/list` — exact schemas (PRIMARY DELIVERABLE)

Request:

```json
POST /mcp
Authorization: Bearer <OOMOL_CONNECT_RUNTIME_TOKEN>
Content-Type: application/json
Accept: application/json, text/event-stream

{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
```

Response came back as a **plain `application/json` body** (HTTP 200), not an SSE stream — no `text/event-stream` framing was needed to read it, despite the `Accept` header offering both.

Five tools are exposed: `list_apps`, `list_connections`, `search_actions`, `get_action_guide`, `execute_action`. All have `"execution":{"taskSupport":"forbidden"}`.

### `list_connections` — exact input schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "service": {
      "description": "Optional provider service id such as github, gmail, or notion.",
      "type": "string"
    }
  }
}
```

- **No `required` array at all** — the schema has zero required fields (calling with `{}` is valid).
- Property name is **`service`**, not `connection_name` / `service_id` — our prior assumption of a `connection_name` filter arg was wrong; the only filter is by provider service id.

### `execute_action` — exact input schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "actionId": {
      "type": "string",
      "description": "Full action id, for example hackernews.get_item."
    },
    "input": {
      "default": {},
      "description": "Action input object matching the selected action guide.",
      "type": "object",
      "propertyNames": { "type": "string" },
      "additionalProperties": {}
    },
    "connectionName": {
      "description": "Optional named connection. Omit it to use the default connection.",
      "type": "string",
      "minLength": 1
    }
  },
  "required": ["actionId"]
}
```

- **Required:** `actionId` only.
- **Optional:** `input` (object, defaults to `{}`), `connectionName` (string, `minLength: 1`, omit to use the default connection).

### Correction to the assumed arg names

The task's working assumption was `action_id`, `input`, `connection_name` (snake_case). **Actual property names are camelCase: `actionId`, `input`, `connectionName`.** `input` was the only one guessed correctly. Downstream adapter code must use `actionId` / `connectionName`, not the snake_case forms.

(For completeness, the other three tools: `list_apps` takes optional `query: string`; `search_actions` takes optional `query: string`, `service: string`, `limit: integer` [default 20, min 1, max 50]; `get_action_guide` takes required `actionId: string` + optional `connectionName: string, minLength 1` — same camelCase convention throughout.)

## 5. Does `execute_action` require a pre-stored connection, or accept an inline credential?

**Answer: requires a pre-stored connection. There is no inline-credential injection path.**

Evidence:

1. The `execute_action` input schema (above) has exactly three properties — `actionId`, `input`, `connectionName` — and no credential/token/secret field of any kind.
2. For a **no-auth** provider (`hackernews`, `authTypes: ["no_auth"]`), calling `execute_action` with `actionId: "hackernews.get_item"`, `input: {id: 1}`, and **no `connectionName`** succeeded (200, real HN API data returned) because OpenConnector auto-resolves a **virtual default connection** (`hackernews:default`, `virtual: true`, `default: true`) — there is nothing to inject; the provider itself needs no credential.
3. For an **auth-required** provider (`github`, `authTypes: ["oauth2","api_key"]`, `needsCredential: true`), calling `execute_action` with `actionId: "github.get_current_user"` and an extra ad-hoc `"credential":{"token":"fake-inline-token"}` field (not part of the schema) was **silently ignored** and the call failed:
   ```json
   {"ok":false,"error":{"code":"authorization_failed","message":"Configure github credentials first.","details":{"status":401}}}
   ```
   This proves credentials must be configured server-side ahead of time (via a named/default `Connection` record persisted in OpenConnector's own store, presumably through its console/admin surface — out of scope for this spike) and referenced at call time only by `connectionName` (or the default), never passed inline in the MCP call.

**Implication for the downstream Gmail gateway adapter:** the adapter cannot pass a caller-supplied Gmail OAuth token through `execute_action` per-call. It must rely on a connection pre-provisioned in OpenConnector (named via `connectionName`, or the workspace's default `gmail` connection) before any `execute_action` call.

## 6. Secret-leak check

Grepped every captured MCP response body (`tools/list`, `list_apps`, `search_actions`, `get_action_guide`, `execute_action` × 2) and the full container log output (`docker logs`, migrations + startup + all request handling) for both `OOMOL_CONNECT_RUNTIME_TOKEN` and `OOMOL_CONNECT_ENCRYPTION_KEY` literal values.

**Result: ABSENT from both.** Zero matches in tool results, zero matches in container logs (grep count = 0 for both secrets in all files checked).

## 7. Teardown

`docker compose down -v` (removed container + named volume `oc-spike_oc-data` + network) then `rm -rf /tmp/oc-spike`. No state left behind; no production code was touched.
