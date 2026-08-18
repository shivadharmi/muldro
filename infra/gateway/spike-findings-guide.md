# Spike Findings: OpenConnector v1.3.5 `get_action_guide`

Task 0 SPIKE (throwaway/investigative). Resolves two unknowns before building the adapter warm-start.
Image: `ghcr.io/oomol-lab/open-connector:v1.3.5`. Endpoint: `POST http://localhost:3000/mcp` (JSON-RPC 2.0, Bearer runtime token).
Responses returned as plain `application/json` (HTTP 200), not SSE. Args are camelCase.

---

## Gate 2 — Exact JSON path to a guide's input schema

**CRITICAL FINDING: `get_action_guide` does NOT return a JSON Schema for the action's input.**

The guide payload contains only two things under `data`:
- `result.structuredContent.data.capability` — execution/auth/scope/policy/connection metadata (JSON, no input schema)
- `result.structuredContent.data.markdown` — a human-readable markdown doc whose `## Input Parameters` section is a **markdown table** (Name | Required | Type), NOT machine-readable JSON Schema

The same payload is mirrored as a JSON *string* in `result.content[0].text` (embedded JSON, must be re-parsed).

There is **no** `inputSchema`, `input_schema`, `schema`, or `guide.inputSchema` field anywhere in the guide response.
The ONLY JSON Schemas OpenConnector exposes are the 5 generic MCP tool schemas from `tools/list`
(`list_apps`, `list_connections`, `search_actions`, `get_action_guide`, `execute_action`) — these describe the MCP tools themselves, not per-action inputs.

### Implication for the adapter warm-start
Per-action input schemas cannot be pulled machine-readable from `get_action_guide`. Options:
- **(a) Parse the markdown `## Input Parameters` table** into a schema (brittle; type cells like `` `"ids" | "summary" | "full"` `` and `` `string | array` `` are prose unions, not JSON types).
- **(b) Hand-type the 7 schemas** (fork-(ii) fallback) — the verbatim parameter tables below are the authoritative source for that. Confirmed accurate against v1.3.5.

### Exact paths that DO exist in a guide response
```
result.structuredContent.ok                      -> bool
result.structuredContent.data.capability         -> object (auth/scopes/policy/connection)
result.structuredContent.data.capability.requiredScopes -> array of OAuth scope URLs
result.structuredContent.data.capability.connection     -> present ONLY if a connection is configured
result.structuredContent.data.markdown           -> string (## Input Parameters = markdown table)
result.content[0].text                           -> JSON string mirror of structuredContent.data
```

### Example excerpt (hackernews.get_item, no-auth)
```json
{ "ok": true, "data": { "capability": {...}, "markdown": "...\n## Input Parameters\n| Name | Required | Type |\n..." } }
```

---

## Gate 1 — Do gmail.* guides work WITHOUT a configured connection?

**VERDICT: PASS (all 7).** With ONLY the runtime token and no configured gmail connection, every gmail guide returned
`ok: true` with a full markdown guide, capability metadata, and required OAuth scopes.

The absence of a connection shows up only as:
- `data.capability.connection` is **omitted** (present for hackernews' virtual default; absent for gmail)
- `data.capability.execution.needsCredential: true`, `noAuthRunnable: false`
- markdown `## Current Connection` section reads: *"This provider is not connected in the local runtime."*

This means the adapter warm-start CAN fetch gmail parameter shapes + scopes at build time with no OAuth connection configured.
(Actual `execute_action` would still fail with a credential error — but guides do not.)

| Action | ok | connection block | needsCredential | requiredScopes |
|--------|----|--------|----|----|
| `gmail.get_profile` | true | absent | true | https://www.googleapis.com/auth/gmail.readonly |
| `gmail.fetch_emails` | true | absent | true | https://www.googleapis.com/auth/gmail.readonly |
| `gmail.search_threads` | true | absent | true | https://www.googleapis.com/auth/gmail.readonly |
| `gmail.get_message` | true | absent | true | https://www.googleapis.com/auth/gmail.readonly |
| `gmail.list_threads` | true | absent | true | https://www.googleapis.com/auth/gmail.readonly |
| `gmail.list_labels` | true | absent | true | https://www.googleapis.com/auth/gmail.labels |
| `gmail.send_email` | true | absent | true | https://www.googleapis.com/auth/gmail.send |

---

## Raw guide parameter data — 7 gmail actions (verbatim from v1.3.5)

No JSON Schema is emitted (see Gate 2). Below is the verbatim `## Input Parameters` markdown table for each action
(the authoritative source for hand-typed fallback schemas / test fixtures), followed by the full `capability` JSON object.

### `gmail.get_profile`

**Input Parameters (verbatim markdown):**

| Name     | Required | Type     |
| -------- | -------- | -------- |
| `userId` | No       | `string` |

- `userId`

  Gmail user ID. Omit to use the connected mailbox.

**capability (verbatim JSON):**
```json
{
  "execution": {
    "locallyExecutable": true,
    "catalogOnly": false,
    "requiredAuthTypes": [
      "oauth2"
    ],
    "noAuthRunnable": false,
    "needsCredential": true
  },
  "authTypes": [
    "oauth2"
  ],
  "requiredScopes": [
    "https://www.googleapis.com/auth/gmail.readonly"
  ],
  "providerPermissions": [],
  "policy": {
    "allowed": true,
    "checks": []
  }
}
```

### `gmail.fetch_emails`

**Input Parameters (verbatim markdown):**

| Name               | Required | Type                           |
| ------------------ | -------- | ------------------------------ |
| `query`            | No       | `string`                       |
| `labelIds`         | No       | `array`                        |
| `includeSpamTrash` | No       | `boolean`                      |
| `detail`           | No       | `"ids" \| "summary" \| "full"` |
| `maxResults`       | No       | `integer`                      |
| `pageToken`        | No       | `string`                       |

- `query`

  Gmail search query.
- `labelIds`

  Gmail label IDs.
- `includeSpamTrash`

  Whether to include Spam and Trash.
- `detail`

  Message detail level.
- `maxResults`

  Maximum number of results to return.
- `pageToken`

  Opaque pagination token returned by Gmail.

**capability (verbatim JSON):**
```json
{
  "execution": {
    "locallyExecutable": true,
    "catalogOnly": false,
    "requiredAuthTypes": [
      "oauth2"
    ],
    "noAuthRunnable": false,
    "needsCredential": true
  },
  "authTypes": [
    "oauth2"
  ],
  "requiredScopes": [
    "https://www.googleapis.com/auth/gmail.readonly"
  ],
  "providerPermissions": [],
  "policy": {
    "allowed": true,
    "checks": []
  }
}
```

### `gmail.search_threads`

**Input Parameters (verbatim markdown):**

| Name         | Required | Type      |
| ------------ | -------- | --------- |
| `query`      | Yes      | `string`  |
| `maxResults` | No       | `integer` |

- `query`

  Gmail search query.
- `maxResults`

  Maximum number of results to return.

**capability (verbatim JSON):**
```json
{
  "execution": {
    "locallyExecutable": true,
    "catalogOnly": false,
    "requiredAuthTypes": [
      "oauth2"
    ],
    "noAuthRunnable": false,
    "needsCredential": true
  },
  "authTypes": [
    "oauth2"
  ],
  "requiredScopes": [
    "https://www.googleapis.com/auth/gmail.readonly"
  ],
  "providerPermissions": [],
  "policy": {
    "allowed": true,
    "checks": []
  }
}
```

### `gmail.get_message`

**Input Parameters (verbatim markdown):**

| Name        | Required | Type     |
| ----------- | -------- | -------- |
| `messageId` | Yes      | `string` |

- `messageId`

  Gmail message ID.

**capability (verbatim JSON):**
```json
{
  "execution": {
    "locallyExecutable": true,
    "catalogOnly": false,
    "requiredAuthTypes": [
      "oauth2"
    ],
    "noAuthRunnable": false,
    "needsCredential": true
  },
  "authTypes": [
    "oauth2"
  ],
  "requiredScopes": [
    "https://www.googleapis.com/auth/gmail.readonly"
  ],
  "providerPermissions": [],
  "policy": {
    "allowed": true,
    "checks": []
  }
}
```

### `gmail.list_threads`

**Input Parameters (verbatim markdown):**

| Name         | Required | Type      |
| ------------ | -------- | --------- |
| `query`      | No       | `string`  |
| `verbose`    | No       | `boolean` |
| `maxResults` | No       | `integer` |
| `pageToken`  | No       | `string`  |

- `query`

  Gmail search query.
- `verbose`

  Hydrate each thread.
- `maxResults`

  Maximum number of results to return.
- `pageToken`

  Opaque pagination token returned by Gmail.

**capability (verbatim JSON):**
```json
{
  "execution": {
    "locallyExecutable": true,
    "catalogOnly": false,
    "requiredAuthTypes": [
      "oauth2"
    ],
    "noAuthRunnable": false,
    "needsCredential": true
  },
  "authTypes": [
    "oauth2"
  ],
  "requiredScopes": [
    "https://www.googleapis.com/auth/gmail.readonly"
  ],
  "providerPermissions": [],
  "policy": {
    "allowed": true,
    "checks": []
  }
}
```

### `gmail.list_labels`

**Input Parameters (verbatim markdown):**

| Name     | Required | Type     |
| -------- | -------- | -------- |
| `userId` | No       | `string` |

- `userId`

  Gmail user ID. Omit to use the connected mailbox.

**capability (verbatim JSON):**
```json
{
  "execution": {
    "locallyExecutable": true,
    "catalogOnly": false,
    "requiredAuthTypes": [
      "oauth2"
    ],
    "noAuthRunnable": false,
    "needsCredential": true
  },
  "authTypes": [
    "oauth2"
  ],
  "requiredScopes": [
    "https://www.googleapis.com/auth/gmail.labels"
  ],
  "providerPermissions": [],
  "policy": {
    "allowed": true,
    "checks": []
  }
}
```

### `gmail.send_email`

**Input Parameters (verbatim markdown):**

| Name              | Required | Type              |
| ----------------- | -------- | ----------------- |
| `recipientEmail`  | No       | `string`          |
| `to`              | No       | `string`          |
| `extraRecipients` | No       | `array`           |
| `cc`              | No       | `string \| array` |
| `bcc`             | No       | `string \| array` |
| `subject`         | No       | `string`          |
| `body`            | No       | `string`          |
| `messageBody`     | No       | `string`          |
| `isHtml`          | No       | `boolean`         |
| `fromEmail`       | No       | `string`          |

- `recipientEmail`

  Primary recipient email address.
- `to`

  Primary recipient email address.
- `extraRecipients`

  Additional To recipients.
- `cc`

  Cc recipients.
- `bcc`

  Bcc recipients.
- `subject`

  Email subject line.
- `body`

  Email body content.
- `messageBody`

  Reply or draft body content.
- `isHtml`

  Whether the body is HTML.
- `fromEmail`

  Verified Gmail send-as alias.

**capability (verbatim JSON):**
```json
{
  "execution": {
    "locallyExecutable": true,
    "catalogOnly": false,
    "requiredAuthTypes": [
      "oauth2"
    ],
    "noAuthRunnable": false,
    "needsCredential": true
  },
  "authTypes": [
    "oauth2"
  ],
  "requiredScopes": [
    "https://www.googleapis.com/auth/gmail.send"
  ],
  "providerPermissions": [],
  "policy": {
    "allowed": true,
    "checks": []
  }
}
```

---

## Appendix — hackernews.get_item (Gate 2 no-auth reference)

`## Input Parameters` (verbatim):
```
| Name    | Required | Type       |
| ------- | -------- | ---------- |
| `id`    | Yes      | `integer`  |
| `print` | No       | `"pretty"` |
```
Note: its `capability.connection` block IS present (virtual default `hackernews:default`), unlike gmail.
