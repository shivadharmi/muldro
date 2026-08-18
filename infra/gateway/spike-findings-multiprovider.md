# Spike Findings: OpenConnector Multi-Provider (googlecalendar + github)

**Status:** DONE (throwaway/investigative, no production code touched, no commits)
**Date:** 2026-08-17
**Image:** `ghcr.io/oomol-lab/open-connector:v1.3.5`

Gates Wave 1 of the multi-provider increment (spec item #1): can Muldro extend the
gateway adapter past Gmail to Google Calendar and GitHub, and what exactly does it
transcribe into the action registry?

## How to reproduce

```bash
docker run -d --name oc-multi -p 3001:3001 \
  -e PORT=3001 \
  -e OOMOL_CONNECT_ENCRYPTION_KEY=<throwaway hex32> \
  -e OOMOL_CONNECT_ADMIN_TOKEN=<throwaway> \
  -e OOMOL_CONNECT_RUNTIME_TOKEN=<throwaway> \
  ghcr.io/oomol-lab/open-connector:v1.3.5
```

`PORT=3001` with a matching `-p 3001:3001` is the shape established by
[spike-findings-port.md](spike-findings-port.md) — a `-p 3001:3000` remap alone leaves OC
emitting `:3000` in the OAuth `redirect_uri`. This spike re-confirms that: every
`expectedRedirectUri` observed below reads `http://localhost:3001/oauth/callback`.

## Method

1. Started the container above with throwaway secrets. No real provider credentials used.
2. **Admin catalog:** `GET http://localhost:3001/api/actions` with the admin bearer token.
   Returned a **13,533-element JSON array**. Filtered by `service` to enumerate
   `googlecalendar` (37), `github` (145), and `gmail` (46).
3. **Runtime guides:** `POST http://localhost:3001/mcp` (JSON-RPC 2.0, `tools/call`,
   `get_action_guide`) with the **runtime** bearer token only, for each of the 14 curated
   action ids. No connection configured for any provider.
4. **Drift check:** ran `src.adapter.warm_start._param_names_from_guide` — the exact parser
   the adapter warm-start uses — over each captured guide markdown and compared the result
   against the admin `inputSchema.properties` key set.
5. **Existing-Gmail regression:** compared the 7 hand-typed schemas in
   `src/integrations/gateway_actions.py` against the admin ground truth for the same ids.
6. **OAuth shape:** `GET /api/oauth/configs` (66 entries), then
   `POST /api/oauth/authorizations` with dummy client credentials for `googlecalendar` and
   `github`, supplying a caller-chosen `connectionName`.

---

## Corrections to the plan document

### 1. MCP endpoint is `POST /mcp`, not `/v1//mcp`

The plan document's Wave-0 snippet used `POST /v1//mcp`. Observed on v1.3.5:

| Path | Status |
|---|---|
| `/v1//mcp` | **404** |
| `/mcp` | **200** |

Anything transcribed from that snippet must use `/mcp`. (This matches
[spike-findings-guide.md](spike-findings-guide.md), which already used `/mcp` — the plan doc
diverged from the earlier spike, not the other way round.)

### 2. Env var names are `OOMOL_CONNECT_*`

The plan document guessed `OOMOL_ENCRYPTION_KEY` / `OOMOL_RUNTIME_TOKEN` /
`OOMOL_ADMIN_TOKEN`. The names v1.3.5 actually reads are:

| Plan doc guessed | Actual |
|---|---|
| `OOMOL_ENCRYPTION_KEY` | `OOMOL_CONNECT_ENCRYPTION_KEY` |
| `OOMOL_ADMIN_TOKEN` | `OOMOL_CONNECT_ADMIN_TOKEN` |
| `OOMOL_RUNTIME_TOKEN` | `OOMOL_CONNECT_RUNTIME_TOKEN` |

The `_CONNECT_` infix is required. This is the same set already used by
`spike-findings-port.md` and `docker-compose.yml`; only the plan doc drifted.

### 3. Gotcha — the `/mcp` response is a streamable-HTTP SSE stream that does not close

A plain `curl -X POST .../mcp` **hangs forever**. The JSON-RPC response body arrives
immediately, but the connection stays open (streamable-HTTP transport keeps the SSE channel
alive). Anyone re-running this spike must pass `--max-time` (or read until the first complete
`data:` frame and stop) or the capture loop will never terminate. Note this differs from what
[spike-findings-guide.md](spike-findings-guide.md) observed on the same image (plain
`application/json`, HTTP 200) — the difference is in how the request is framed
(`Accept: text/event-stream` negotiation), not the image.

---

## Key finding: machine-readable schemas via the admin API

**OpenConnector DOES expose a real, machine-readable JSON Schema per action — through the
ADMIN API, not the runtime MCP tool.**

`GET /api/actions` (admin bearer token) returns a 13,533-element array. Every element has
exactly these keys:

```
id, service, name, description, requiredScopes, providerPermissions,
inputSchema, outputSchema, execution
```

`inputSchema` is a JSON Schema **object** — not a string, not a markdown table. Example
(`github.search_code`, verbatim):

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1
    },
    "sort": {
      "type": "string",
      "enum": [
        "indexed",
        "updated"
      ]
    },
    "order": {
      "type": "string",
      "enum": [
        "asc",
        "desc"
      ]
    },
    "perPage": {
      "type": "integer"
    },
    "page": {
      "type": "integer"
    }
  },
  "additionalProperties": false,
  "required": [
    "query"
  ]
}
```

### This refines, not contradicts, spike-findings-guide.md

[spike-findings-guide.md](spike-findings-guide.md)'s "no JSON Schema" conclusion was
specifically about the **runtime `get_action_guide` MCP tool**, and it remains correct:
`get_action_guide` still returns only `capability` metadata plus a markdown doc whose
`## Input Parameters` section is a markdown table. The admin API is a different surface with
a different token.

### Consequence for the design (unchanged in shape, better in provenance)

- Registry schemas **stay hand-declared** in `src/integrations/gateway_actions.py`. The
  adapter must not hold the admin token — that boundary is deliberate, and this finding does
  not relax it.
- What changes is the **transcription source**: Wave 1 transcribes from exact JSON (the
  `inputSchema` blocks reproduced in full below) rather than from a prose markdown table.
  That removes the transcription risk that produced 7 invented action ids in an earlier
  increment.
- The **hybrid drift-check design is unchanged** — warm-start still compares hand-typed
  property names against runtime-guide param names, using only the runtime token.

---

## Gate results

### Gate 1 — do guides work with NO configured connection, for the new providers? **PASS (14/14)**

With only the runtime token and no connection configured for `googlecalendar` or `github`,
every one of the 14 curated actions returned a full guide. Every one reported
`execution.needsCredential: true` and omitted the `capability.connection` block — the same
signature Gmail showed in the earlier spike.

| actionId | guide `ok` | `connection` block | `needsCredential` |
|---|---|---|---|
| `googlecalendar.list_calendars` | true | absent | true |
| `googlecalendar.list_events` | true | absent | true |
| `googlecalendar.get_event` | true | absent | true |
| `googlecalendar.free_busy_query` | true | absent | true |
| `googlecalendar.create_event` | true | absent | true |
| `googlecalendar.update_event` | true | absent | true |
| `github.list_repository_issues` | true | absent | true |
| `github.search_issues_and_pull_requests` | true | absent | true |
| `github.create_issue` | true | absent | true |
| `github.create_issue_comment` | true | absent | true |
| `github.search_code` | true | absent | true |
| `github.search_repositories` | true | absent | true |
| `github.list_pull_requests` | true | absent | true |
| `github.create_pull_request` | true | absent | true |

### Gate 2 — do guide param names match admin schema properties? **PASS (14/14)**

Ran `src.adapter.warm_start._param_names_from_guide` over each guide and compared to
`inputSchema.properties`. Every action matched exactly, so warm-start's drift check will
**not** false-alarm against admin-sourced schemas.

| actionId | guide param names | admin schema properties | match |
|---|---|---|---|
| `googlecalendar.list_calendars` | 6 | 6 | YES |
| `googlecalendar.list_events` | 18 | 18 | YES |
| `googlecalendar.get_event` | 2 | 2 | YES |
| `googlecalendar.free_busy_query` | 6 | 6 | YES |
| `googlecalendar.create_event` | 2 | 2 | YES |
| `googlecalendar.update_event` | 3 | 3 | YES |
| `github.list_repository_issues` | 9 | 9 | YES |
| `github.search_issues_and_pull_requests` | 18 | 18 | YES |
| `github.create_issue` | 7 | 7 | YES |
| `github.create_issue_comment` | 4 | 4 | YES |
| `github.search_code` | 5 | 5 | YES |
| `github.search_repositories` | 5 | 5 | YES |
| `github.list_pull_requests` | 9 | 9 | YES |
| `github.create_pull_request` | 8 | 8 | YES |

### Gate 3 — do the 7 existing Gmail action ids and schemas still hold? **PASS, with one nuance**

All 7 ids referenced by `src/integrations/gateway_actions.py` exist in OC v1.3.5's catalog:
`gmail.get_profile`, `gmail.fetch_emails`, `gmail.search_threads`, `gmail.get_message`,
`gmail.list_threads`, `gmail.list_labels`, `gmail.send_email`.

All 7 hand-typed schemas match the ground-truth `inputSchema` on **property names, property
`type`s, per-property `description`s, `enum` members, and the `required` array** — Increment
3's markdown transcription was accurate on everything the markdown table could express.

**Nuance found in this spike (not previously visible):** the admin schemas additionally carry
fields the markdown table never exposed, which the hand-typed versions therefore omit:

| Omitted from hand-typed schemas | Where |
|---|---|
| `additionalProperties: false` | all 7 |
| top-level `"description": "The input payload for this action."` | all 7 |
| `maxResults.minimum: 1`, `maxResults.maximum: 500` | `fetch_emails`, `search_threads`, `list_threads` |
| `detail.default: "summary"` | `fetch_emails` |
| `messageId.minLength: 1` | `get_message` |

These are validation constraints, not shape differences — no parameter is missing, misnamed,
or mistyped. They are worth folding in when Wave 1 touches the file, since the admin JSON now
makes them free to copy.

---

## googlecalendar

OC service: `googlecalendar` (37 actions total; 6 curated). Muldro `server_name`:
`google-workspace`.

| actionId | capability | risk | requires_approval |
|---|---|---|---|
| `googlecalendar.list_calendars` | `calendar.list` | low | false |
| `googlecalendar.list_events` | `calendar.list` | low | false |
| `googlecalendar.get_event` | `calendar.get` | low | false |
| `googlecalendar.free_busy_query` | `calendar.get` | low | false |
| `googlecalendar.create_event` | `calendar.create` | medium | true |
| `googlecalendar.update_event` | `calendar.update` | medium | true |

### `googlecalendar.list_calendars`

List the current user's Google Calendar list entries.

- **requiredScopes:** `https://www.googleapis.com/auth/calendar.readonly`
- **requiredAuthTypes:** `oauth2`
- **Muldro capability:** `calendar.list` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name            | Required | Type      |
| --------------- | -------- | --------- |
| `maxResults`    | No       | `integer` |
| `pageToken`     | No       | `string`  |
| `syncToken`     | No       | `string`  |
| `showHidden`    | No       | `boolean` |
| `showDeleted`   | No       | `boolean` |
| `minAccessRole` | No       | `string`  |

- `maxResults`

  Maximum calendar list entries to return.
- `pageToken`

  Page token.
- `syncToken`

  Incremental sync token.
- `showHidden`

  Include hidden calendars.
- `showDeleted`

  Include deleted calendars.
- `minAccessRole`

  Minimum access role.

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "maxResults": {
      "type": "integer",
      "minimum": 1,
      "maximum": 250,
      "description": "Maximum calendar list entries to return."
    },
    "pageToken": {
      "type": "string",
      "description": "Page token."
    },
    "syncToken": {
      "type": "string",
      "description": "Incremental sync token."
    },
    "showHidden": {
      "type": "boolean",
      "description": "Include hidden calendars."
    },
    "showDeleted": {
      "type": "boolean",
      "description": "Include deleted calendars."
    },
    "minAccessRole": {
      "type": "string",
      "description": "Minimum access role."
    }
  },
  "additionalProperties": false,
  "description": "The input payload for this action."
}
```

### `googlecalendar.list_events`

List events from a Google Calendar.

- **requiredScopes:** `https://www.googleapis.com/auth/calendar.readonly`
- **requiredAuthTypes:** `oauth2`
- **Muldro capability:** `calendar.list` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name                      | Required | Type              |
| ------------------------- | -------- | ----------------- |
| `calendarId`              | Yes      | `string`          |
| `q`                       | No       | `string`          |
| `iCalUID`                 | No       | `string`          |
| `orderBy`                 | No       | `string`          |
| `timeMin`                 | No       | `string`          |
| `timeMax`                 | No       | `string`          |
| `timeZone`                | No       | `string`          |
| `pageToken`               | No       | `string`          |
| `syncToken`               | No       | `string`          |
| `eventTypes`              | No       | `string \| array` |
| `maxResults`              | No       | `integer`         |
| `updatedMin`              | No       | `string`          |
| `showDeleted`             | No       | `boolean`         |
| `maxAttendees`            | No       | `integer`         |
| `singleEvents`            | No       | `boolean`         |
| `showHiddenInvitations`   | No       | `boolean`         |
| `sharedExtendedProperty`  | No       | `string \| array` |
| `privateExtendedProperty` | No       | `string \| array` |

- `calendarId`

  Google Calendar ID. Omit to use the primary calendar when supported.
- `q`

  Full-text event search query.
- `iCalUID`

  iCalendar UID filter.
- `orderBy`

  Sort order.
- `timeMin`

  RFC 3339 timestamp.
- `timeMax`

  RFC 3339 timestamp.
- `timeZone`

  Response time zone.
- `pageToken`

  Page token.
- `syncToken`

  Incremental sync token.
- `eventTypes`

  One string or an array of strings.
- `maxResults`

  Maximum events to return.
- `updatedMin`

  RFC 3339 timestamp.
- `showDeleted`

  Include deleted events.
- `maxAttendees`

  Maximum attendees per event.
- `singleEvents`

  Expand recurring events.
- `showHiddenInvitations`

  Include hidden invitations.
- `sharedExtendedProperty`

  One string or an array of strings.
- `privateExtendedProperty`

  One string or an array of strings.

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "calendarId": {
      "type": "string",
      "minLength": 1,
      "description": "Google Calendar ID. Omit to use the primary calendar when supported."
    },
    "q": {
      "type": "string",
      "description": "Full-text event search query."
    },
    "iCalUID": {
      "type": "string",
      "description": "iCalendar UID filter."
    },
    "orderBy": {
      "type": "string",
      "description": "Sort order."
    },
    "timeMin": {
      "type": "string",
      "description": "RFC 3339 timestamp.",
      "format": "date-time"
    },
    "timeMax": {
      "type": "string",
      "description": "RFC 3339 timestamp.",
      "format": "date-time"
    },
    "timeZone": {
      "type": "string",
      "description": "Response time zone."
    },
    "pageToken": {
      "type": "string",
      "description": "Page token."
    },
    "syncToken": {
      "type": "string",
      "description": "Incremental sync token."
    },
    "eventTypes": {
      "anyOf": [
        {
          "type": "string",
          "minLength": 1
        },
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "minItems": 1
        }
      ],
      "description": "One string or an array of strings."
    },
    "maxResults": {
      "type": "integer",
      "minimum": 1,
      "maximum": 2500,
      "description": "Maximum events to return."
    },
    "updatedMin": {
      "type": "string",
      "description": "RFC 3339 timestamp.",
      "format": "date-time"
    },
    "showDeleted": {
      "type": "boolean",
      "description": "Include deleted events."
    },
    "maxAttendees": {
      "type": "integer",
      "minimum": 1,
      "description": "Maximum attendees per event."
    },
    "singleEvents": {
      "type": "boolean",
      "description": "Expand recurring events."
    },
    "showHiddenInvitations": {
      "type": "boolean",
      "description": "Include hidden invitations."
    },
    "sharedExtendedProperty": {
      "anyOf": [
        {
          "type": "string",
          "minLength": 1
        },
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "minItems": 1
        }
      ],
      "description": "One string or an array of strings."
    },
    "privateExtendedProperty": {
      "anyOf": [
        {
          "type": "string",
          "minLength": 1
        },
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "minItems": 1
        }
      ],
      "description": "One string or an array of strings."
    }
  },
  "additionalProperties": false,
  "required": [
    "calendarId"
  ],
  "description": "The input payload for this action."
}
```

### `googlecalendar.get_event`

Fetch one Google Calendar event.

- **requiredScopes:** `https://www.googleapis.com/auth/calendar.readonly`
- **requiredAuthTypes:** `oauth2`
- **Muldro capability:** `calendar.get` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name         | Required | Type     |
| ------------ | -------- | -------- |
| `calendarId` | Yes      | `string` |
| `eventId`    | Yes      | `string` |

- `calendarId`

  Google Calendar ID. Omit to use the primary calendar when supported.
- `eventId`

  Google Calendar event ID.

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "calendarId": {
      "type": "string",
      "minLength": 1,
      "description": "Google Calendar ID. Omit to use the primary calendar when supported."
    },
    "eventId": {
      "type": "string",
      "minLength": 1,
      "description": "Google Calendar event ID."
    }
  },
  "additionalProperties": false,
  "required": [
    "calendarId",
    "eventId"
  ],
  "description": "The input payload for this action."
}
```

### `googlecalendar.free_busy_query`

Query busy intervals for calendars and groups.

- **requiredScopes:** `https://www.googleapis.com/auth/calendar.readonly`
- **requiredAuthTypes:** `oauth2`
- **Muldro capability:** `calendar.get` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name                   | Required | Type             |
| ---------------------- | -------- | ---------------- |
| `items`                | Yes      | `array \| array` |
| `timeMin`              | Yes      | `string`         |
| `timeMax`              | Yes      | `string`         |
| `timeZone`             | No       | `string`         |
| `groupExpansionMax`    | No       | `integer`        |
| `calendarExpansionMax` | No       | `integer`        |

- `items`

  Calendar or group IDs to include in the freeBusy query.
- `timeMin`

  RFC 3339 timestamp.
- `timeMax`

  RFC 3339 timestamp.
- `timeZone`

  Response time zone.
- `groupExpansionMax`

  Maximum calendars to expand per group.
- `calendarExpansionMax`

  Maximum calendars to return after expansion.

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "items": {
      "anyOf": [
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "minItems": 1
        },
        {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "id": {
                "type": "string",
                "minLength": 1
              }
            },
            "additionalProperties": false,
            "required": [
              "id"
            ]
          },
          "minItems": 1
        }
      ],
      "description": "Calendar or group IDs to include in the freeBusy query."
    },
    "timeMin": {
      "type": "string",
      "description": "RFC 3339 timestamp.",
      "format": "date-time"
    },
    "timeMax": {
      "type": "string",
      "description": "RFC 3339 timestamp.",
      "format": "date-time"
    },
    "timeZone": {
      "type": "string",
      "description": "Response time zone."
    },
    "groupExpansionMax": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100,
      "description": "Maximum calendars to expand per group."
    },
    "calendarExpansionMax": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "description": "Maximum calendars to return after expansion."
    }
  },
  "additionalProperties": false,
  "required": [
    "items",
    "timeMin",
    "timeMax"
  ],
  "description": "The input payload for this action."
}
```

### `googlecalendar.create_event`

Create a Google Calendar event.

- **requiredScopes:** `https://www.googleapis.com/auth/calendar.events`
- **requiredAuthTypes:** `oauth2`
- **Muldro capability:** `calendar.create` &nbsp;&nbsp; **risk:** `medium` &nbsp;&nbsp; **requires_approval:** `true`

**Runtime guide `## Input Parameters` (verbatim):**

| Name         | Required | Type     |
| ------------ | -------- | -------- |
| `calendarId` | Yes      | `string` |
| `event`      | Yes      | `object` |

- `calendarId`

  Google Calendar ID. Omit to use the primary calendar when supported.
- `event`

  Event creation payload.

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "calendarId": {
      "type": "string",
      "minLength": 1,
      "description": "Google Calendar ID. Omit to use the primary calendar when supported."
    },
    "event": {
      "type": "object",
      "properties": {
        "summary": {
          "type": "string",
          "description": "Event title."
        },
        "description": {
          "type": "string",
          "description": "Event description."
        },
        "location": {
          "type": "string",
          "description": "Event location."
        },
        "start": {
          "type": "object",
          "properties": {
            "date": {
              "type": "string",
              "minLength": 1,
              "description": "All-day event date in YYYY-MM-DD format."
            },
            "dateTime": {
              "type": "string",
              "description": "RFC 3339 timestamp.",
              "format": "date-time"
            },
            "timeZone": {
              "type": "string",
              "minLength": 1,
              "description": "IANA time zone used to interpret the event time."
            }
          },
          "additionalProperties": false,
          "description": "Event date or date-time."
        },
        "end": {
          "type": "object",
          "properties": {
            "date": {
              "type": "string",
              "minLength": 1,
              "description": "All-day event date in YYYY-MM-DD format."
            },
            "dateTime": {
              "type": "string",
              "description": "RFC 3339 timestamp.",
              "format": "date-time"
            },
            "timeZone": {
              "type": "string",
              "minLength": 1,
              "description": "IANA time zone used to interpret the event time."
            }
          },
          "additionalProperties": false,
          "description": "Event date or date-time."
        },
        "attendees": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "email": {
                "type": "string",
                "minLength": 1,
                "description": "Attendee email address."
              },
              "displayName": {
                "type": "string",
                "description": "Attendee display name."
              },
              "optional": {
                "type": "boolean",
                "description": "Whether attendance is optional."
              },
              "resource": {
                "type": "boolean",
                "description": "Whether the attendee represents a resource."
              },
              "responseStatus": {
                "type": "string",
                "description": "Attendee response status."
              },
              "comment": {
                "type": "string",
                "description": "Additional attendee comment."
              },
              "additionalGuests": {
                "type": "integer",
                "description": "Number of additional guests."
              }
            },
            "additionalProperties": false,
            "required": [
              "email"
            ],
            "description": "Event attendee."
          },
          "description": "Event attendees."
        },
        "recurrence": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "description": "Recurrence rules."
        },
        "conferenceData": {
          "type": "object",
          "additionalProperties": true,
          "description": "Google Calendar API object."
        },
        "reminders": {
          "type": "object",
          "properties": {
            "useDefault": {
              "type": "boolean",
              "description": "Whether to use default calendar reminders."
            },
            "overrides": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "method": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Reminder delivery method, such as email or popup."
                  },
                  "minutes": {
                    "type": "integer",
                    "description": "Minutes before the event."
                  }
                },
                "additionalProperties": false,
                "required": [
                  "method",
                  "minutes"
                ],
                "description": "Reminder override."
              },
              "description": "Reminder overrides."
            }
          },
          "additionalProperties": false,
          "description": "Event reminders."
        },
        "colorId": {
          "type": "string",
          "description": "Google Calendar color ID."
        },
        "visibility": {
          "type": "string",
          "description": "Event visibility."
        },
        "transparency": {
          "type": "string",
          "description": "Whether the event blocks time."
        },
        "status": {
          "type": "string",
          "description": "Event status."
        },
        "extendedProperties": {
          "type": "object",
          "additionalProperties": true,
          "description": "Google Calendar API object."
        },
        "attachments": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": true,
            "description": "Google Calendar API object."
          },
          "description": "Google Calendar API objects."
        },
        "source": {
          "type": "object",
          "additionalProperties": true,
          "description": "Google Calendar API object."
        }
      },
      "additionalProperties": false,
      "required": [
        "start",
        "end"
      ],
      "description": "Event creation payload."
    }
  },
  "additionalProperties": false,
  "required": [
    "calendarId",
    "event"
  ],
  "description": "The input payload for this action."
}
```

### `googlecalendar.update_event`

Replace writable fields on a Google Calendar event.

- **requiredScopes:** `https://www.googleapis.com/auth/calendar.events`
- **requiredAuthTypes:** `oauth2`
- **Muldro capability:** `calendar.update` &nbsp;&nbsp; **risk:** `medium` &nbsp;&nbsp; **requires_approval:** `true`

**Runtime guide `## Input Parameters` (verbatim):**

| Name         | Required | Type     |
| ------------ | -------- | -------- |
| `calendarId` | Yes      | `string` |
| `eventId`    | Yes      | `string` |
| `event`      | Yes      | `object` |

- `calendarId`

  Google Calendar ID. Omit to use the primary calendar when supported.
- `eventId`

  Google Calendar event ID.
- `event`

  Writable Google Calendar event fields.

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "calendarId": {
      "type": "string",
      "minLength": 1,
      "description": "Google Calendar ID. Omit to use the primary calendar when supported."
    },
    "eventId": {
      "type": "string",
      "minLength": 1,
      "description": "Google Calendar event ID."
    },
    "event": {
      "type": "object",
      "properties": {
        "summary": {
          "type": "string",
          "description": "Event title."
        },
        "description": {
          "type": "string",
          "description": "Event description."
        },
        "location": {
          "type": "string",
          "description": "Event location."
        },
        "start": {
          "type": "object",
          "properties": {
            "date": {
              "type": "string",
              "minLength": 1,
              "description": "All-day event date in YYYY-MM-DD format."
            },
            "dateTime": {
              "type": "string",
              "description": "RFC 3339 timestamp.",
              "format": "date-time"
            },
            "timeZone": {
              "type": "string",
              "minLength": 1,
              "description": "IANA time zone used to interpret the event time."
            }
          },
          "additionalProperties": false,
          "description": "Event date or date-time."
        },
        "end": {
          "type": "object",
          "properties": {
            "date": {
              "type": "string",
              "minLength": 1,
              "description": "All-day event date in YYYY-MM-DD format."
            },
            "dateTime": {
              "type": "string",
              "description": "RFC 3339 timestamp.",
              "format": "date-time"
            },
            "timeZone": {
              "type": "string",
              "minLength": 1,
              "description": "IANA time zone used to interpret the event time."
            }
          },
          "additionalProperties": false,
          "description": "Event date or date-time."
        },
        "attendees": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "email": {
                "type": "string",
                "minLength": 1,
                "description": "Attendee email address."
              },
              "displayName": {
                "type": "string",
                "description": "Attendee display name."
              },
              "optional": {
                "type": "boolean",
                "description": "Whether attendance is optional."
              },
              "resource": {
                "type": "boolean",
                "description": "Whether the attendee represents a resource."
              },
              "responseStatus": {
                "type": "string",
                "description": "Attendee response status."
              },
              "comment": {
                "type": "string",
                "description": "Additional attendee comment."
              },
              "additionalGuests": {
                "type": "integer",
                "description": "Number of additional guests."
              }
            },
            "additionalProperties": false,
            "required": [
              "email"
            ],
            "description": "Event attendee."
          },
          "description": "Event attendees."
        },
        "recurrence": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "description": "Recurrence rules."
        },
        "conferenceData": {
          "type": "object",
          "additionalProperties": true,
          "description": "Google Calendar API object."
        },
        "reminders": {
          "type": "object",
          "properties": {
            "useDefault": {
              "type": "boolean",
              "description": "Whether to use default calendar reminders."
            },
            "overrides": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "method": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Reminder delivery method, such as email or popup."
                  },
                  "minutes": {
                    "type": "integer",
                    "description": "Minutes before the event."
                  }
                },
                "additionalProperties": false,
                "required": [
                  "method",
                  "minutes"
                ],
                "description": "Reminder override."
              },
              "description": "Reminder overrides."
            }
          },
          "additionalProperties": false,
          "description": "Event reminders."
        },
        "colorId": {
          "type": "string",
          "description": "Google Calendar color ID."
        },
        "visibility": {
          "type": "string",
          "description": "Event visibility."
        },
        "transparency": {
          "type": "string",
          "description": "Whether the event blocks time."
        },
        "status": {
          "type": "string",
          "description": "Event status."
        },
        "extendedProperties": {
          "type": "object",
          "additionalProperties": true,
          "description": "Google Calendar API object."
        },
        "attachments": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": true,
            "description": "Google Calendar API object."
          },
          "description": "Google Calendar API objects."
        },
        "source": {
          "type": "object",
          "additionalProperties": true,
          "description": "Google Calendar API object."
        }
      },
      "additionalProperties": false,
      "description": "Writable Google Calendar event fields."
    }
  },
  "additionalProperties": false,
  "required": [
    "calendarId",
    "eventId",
    "event"
  ],
  "description": "The input payload for this action."
}
```

---

## github

OC service: `github` (145 actions total; 8 curated). Muldro `server_name`: `github`.

| actionId | capability | risk | requires_approval |
|---|---|---|---|
| `github.list_repository_issues` | `issue.list` | low | false |
| `github.search_issues_and_pull_requests` | `issue.search` | low | false |
| `github.create_issue` | `issue.create` | medium | true |
| `github.create_issue_comment` | `issue.comment` | medium | true |
| `github.search_code` | `repo.search_code` | low | false |
| `github.search_repositories` | `repo.search_repos` | low | false |
| `github.list_pull_requests` | `repo.list_prs` | low | false |
| `github.create_pull_request` | `repo.create_pr` | high | true |

### `github.list_repository_issues`

List issues for a GitHub repository. Pull requests are filtered out of the response; pageInfo.fetched reports the raw page length before filtering, so paginating callers must continue while fetched equals perPage (30 by default) even when the issues array comes back short or empty.

- **requiredScopes:** `repo`
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `issue.list` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name        | Required | Type                                   |
| ----------- | -------- | -------------------------------------- |
| `owner`     | Yes      | `string`                               |
| `repo`      | Yes      | `string`                               |
| `state`     | No       | `"open" \| "closed" \| "all"`          |
| `labels`    | No       | `array`                                |
| `sort`      | No       | `"created" \| "updated" \| "comments"` |
| `direction` | No       | `"asc" \| "desc"`                      |
| `since`     | No       | `string`                               |
| `perPage`   | No       | `integer`                              |
| `page`      | No       | `integer`                              |

- `owner`
- `repo`
- `state`
- `labels`
- `sort`
- `direction`
- `since`
- `perPage`

  Number of results requested per page. Defaults to 30.
- `page`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "owner": {
      "type": "string",
      "minLength": 1
    },
    "repo": {
      "type": "string",
      "minLength": 1
    },
    "state": {
      "type": "string",
      "enum": [
        "open",
        "closed",
        "all"
      ]
    },
    "labels": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "sort": {
      "type": "string",
      "enum": [
        "created",
        "updated",
        "comments"
      ]
    },
    "direction": {
      "type": "string",
      "enum": [
        "asc",
        "desc"
      ]
    },
    "since": {
      "type": "string"
    },
    "perPage": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100,
      "description": "Number of results requested per page. Defaults to 30.",
      "default": 30
    },
    "page": {
      "type": "integer"
    }
  },
  "additionalProperties": false,
  "required": [
    "owner",
    "repo"
  ]
}
```

### `github.search_issues_and_pull_requests`

Search GitHub issues and pull requests with raw GitHub search syntax or structured filters.

- **requiredScopes:** _(none declared)_
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `issue.search` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name         | Required | Type                                                                                                                                                                                                   |
| ------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `query`      | No       | `string`                                                                                                                                                                                               |
| `q`          | No       | `string`                                                                                                                                                                                               |
| `owner`      | No       | `string`                                                                                                                                                                                               |
| `repo`       | No       | `string`                                                                                                                                                                                               |
| `state`      | No       | `"open" \| "closed" \| "all"`                                                                                                                                                                          |
| `label`      | No       | `string`                                                                                                                                                                                               |
| `author`     | No       | `string`                                                                                                                                                                                               |
| `assignee`   | No       | `string`                                                                                                                                                                                               |
| `mentions`   | No       | `string`                                                                                                                                                                                               |
| `language`   | No       | `string`                                                                                                                                                                                               |
| `baseBranch` | No       | `string`                                                                                                                                                                                               |
| `headBranch` | No       | `string`                                                                                                                                                                                               |
| `isMerged`   | No       | `boolean`                                                                                                                                                                                              |
| `type`       | No       | `"issue" \| "pr"`                                                                                                                                                                                      |
| `sort`       | No       | `"comments" \| "reactions" \| "reactions-+1" \| "reactions--1" \| "reactions-smile" \| "reactions-thinking_face" \| "reactions-heart" \| "reactions-tada" \| "interactions" \| "created" \| "updated"` |
| `order`      | No       | `"asc" \| "desc"`                                                                                                                                                                                      |
| `perPage`    | No       | `integer`                                                                                                                                                                                              |
| `page`       | No       | `integer`                                                                                                                                                                                              |

- `query`
- `q`
- `owner`
- `repo`
- `state`
- `label`
- `author`
- `assignee`
- `mentions`
- `language`
- `baseBranch`
- `headBranch`
- `isMerged`
- `type`
- `sort`
- `order`
- `perPage`
- `page`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string"
    },
    "q": {
      "type": "string"
    },
    "owner": {
      "type": "string"
    },
    "repo": {
      "type": "string"
    },
    "state": {
      "type": "string",
      "enum": [
        "open",
        "closed",
        "all"
      ]
    },
    "label": {
      "type": "string"
    },
    "author": {
      "type": "string"
    },
    "assignee": {
      "type": "string"
    },
    "mentions": {
      "type": "string"
    },
    "language": {
      "type": "string"
    },
    "baseBranch": {
      "type": "string"
    },
    "headBranch": {
      "type": "string"
    },
    "isMerged": {
      "type": "boolean"
    },
    "type": {
      "type": "string",
      "enum": [
        "issue",
        "pr"
      ]
    },
    "sort": {
      "type": "string",
      "enum": [
        "comments",
        "reactions",
        "reactions-+1",
        "reactions--1",
        "reactions-smile",
        "reactions-thinking_face",
        "reactions-heart",
        "reactions-tada",
        "interactions",
        "created",
        "updated"
      ]
    },
    "order": {
      "type": "string",
      "enum": [
        "asc",
        "desc"
      ]
    },
    "perPage": {
      "type": "integer"
    },
    "page": {
      "type": "integer"
    }
  },
  "additionalProperties": false
}
```

### `github.create_issue`

Create an issue in a GitHub repository.

- **requiredScopes:** `repo`
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `issue.create` &nbsp;&nbsp; **risk:** `medium` &nbsp;&nbsp; **requires_approval:** `true`

**Runtime guide `## Input Parameters` (verbatim):**

| Name        | Required | Type      |
| ----------- | -------- | --------- |
| `owner`     | Yes      | `string`  |
| `repo`      | Yes      | `string`  |
| `title`     | Yes      | `string`  |
| `body`      | No       | `string`  |
| `assignees` | No       | `array`   |
| `labels`    | No       | `array`   |
| `milestone` | No       | `integer` |

- `owner`
- `repo`
- `title`
- `body`
- `assignees`
- `labels`
- `milestone`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "owner": {
      "type": "string",
      "minLength": 1
    },
    "repo": {
      "type": "string",
      "minLength": 1
    },
    "title": {
      "type": "string",
      "minLength": 1
    },
    "body": {
      "type": "string"
    },
    "assignees": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "labels": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "milestone": {
      "type": "integer",
      "minimum": 1
    }
  },
  "additionalProperties": false,
  "required": [
    "owner",
    "repo",
    "title"
  ]
}
```

### `github.create_issue_comment`

Create a comment on a GitHub issue.

- **requiredScopes:** `repo`
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `issue.comment` &nbsp;&nbsp; **risk:** `medium` &nbsp;&nbsp; **requires_approval:** `true`

**Runtime guide `## Input Parameters` (verbatim):**

| Name          | Required | Type      |
| ------------- | -------- | --------- |
| `owner`       | Yes      | `string`  |
| `repo`        | Yes      | `string`  |
| `issueNumber` | Yes      | `integer` |
| `body`        | Yes      | `string`  |

- `owner`
- `repo`
- `issueNumber`
- `body`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "owner": {
      "type": "string",
      "minLength": 1
    },
    "repo": {
      "type": "string",
      "minLength": 1
    },
    "issueNumber": {
      "type": "integer",
      "minimum": 1
    },
    "body": {
      "type": "string",
      "minLength": 1
    }
  },
  "additionalProperties": false,
  "required": [
    "owner",
    "repo",
    "issueNumber",
    "body"
  ]
}
```

### `github.search_code`

Search GitHub code with GitHub search syntax.

- **requiredScopes:** _(none declared)_
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `repo.search_code` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name      | Required | Type                     |
| --------- | -------- | ------------------------ |
| `query`   | Yes      | `string`                 |
| `sort`    | No       | `"indexed" \| "updated"` |
| `order`   | No       | `"asc" \| "desc"`        |
| `perPage` | No       | `integer`                |
| `page`    | No       | `integer`                |

- `query`
- `sort`
- `order`
- `perPage`
- `page`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1
    },
    "sort": {
      "type": "string",
      "enum": [
        "indexed",
        "updated"
      ]
    },
    "order": {
      "type": "string",
      "enum": [
        "asc",
        "desc"
      ]
    },
    "perPage": {
      "type": "integer"
    },
    "page": {
      "type": "integer"
    }
  },
  "additionalProperties": false,
  "required": [
    "query"
  ]
}
```

### `github.search_repositories`

Search GitHub repositories with GitHub search syntax.

- **requiredScopes:** _(none declared)_
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `repo.search_repos` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name      | Required | Type                                                      |
| --------- | -------- | --------------------------------------------------------- |
| `query`   | Yes      | `string`                                                  |
| `sort`    | No       | `"stars" \| "forks" \| "help-wanted-issues" \| "updated"` |
| `order`   | No       | `"asc" \| "desc"`                                         |
| `perPage` | No       | `integer`                                                 |
| `page`    | No       | `integer`                                                 |

- `query`
- `sort`
- `order`
- `perPage`
- `page`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1
    },
    "sort": {
      "type": "string",
      "enum": [
        "stars",
        "forks",
        "help-wanted-issues",
        "updated"
      ]
    },
    "order": {
      "type": "string",
      "enum": [
        "asc",
        "desc"
      ]
    },
    "perPage": {
      "type": "integer"
    },
    "page": {
      "type": "integer"
    }
  },
  "additionalProperties": false,
  "required": [
    "query"
  ]
}
```

### `github.list_pull_requests`

List pull requests for a GitHub repository.

- **requiredScopes:** `repo`
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `repo.list_prs` &nbsp;&nbsp; **risk:** `low` &nbsp;&nbsp; **requires_approval:** `false`

**Runtime guide `## Input Parameters` (verbatim):**

| Name        | Required | Type                                                       |
| ----------- | -------- | ---------------------------------------------------------- |
| `owner`     | Yes      | `string`                                                   |
| `repo`      | Yes      | `string`                                                   |
| `state`     | No       | `"open" \| "closed" \| "all"`                              |
| `head`      | No       | `string`                                                   |
| `base`      | No       | `string`                                                   |
| `sort`      | No       | `"created" \| "updated" \| "popularity" \| "long-running"` |
| `direction` | No       | `"asc" \| "desc"`                                          |
| `perPage`   | No       | `integer`                                                  |
| `page`      | No       | `integer`                                                  |

- `owner`
- `repo`
- `state`
- `head`
- `base`
- `sort`
- `direction`
- `perPage`
- `page`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "owner": {
      "type": "string",
      "minLength": 1
    },
    "repo": {
      "type": "string",
      "minLength": 1
    },
    "state": {
      "type": "string",
      "enum": [
        "open",
        "closed",
        "all"
      ]
    },
    "head": {
      "type": "string"
    },
    "base": {
      "type": "string"
    },
    "sort": {
      "type": "string",
      "enum": [
        "created",
        "updated",
        "popularity",
        "long-running"
      ]
    },
    "direction": {
      "type": "string",
      "enum": [
        "asc",
        "desc"
      ]
    },
    "perPage": {
      "type": "integer"
    },
    "page": {
      "type": "integer"
    }
  },
  "additionalProperties": false,
  "required": [
    "owner",
    "repo"
  ]
}
```

### `github.create_pull_request`

Create a pull request in a GitHub repository.

- **requiredScopes:** `repo`
- **requiredAuthTypes:** `oauth2`, `api_key`
- **Muldro capability:** `repo.create_pr` &nbsp;&nbsp; **risk:** `high` &nbsp;&nbsp; **requires_approval:** `true`

**Runtime guide `## Input Parameters` (verbatim):**

| Name                  | Required | Type      |
| --------------------- | -------- | --------- |
| `owner`               | Yes      | `string`  |
| `repo`                | Yes      | `string`  |
| `title`               | Yes      | `string`  |
| `head`                | Yes      | `string`  |
| `base`                | Yes      | `string`  |
| `body`                | No       | `string`  |
| `draft`               | No       | `boolean` |
| `maintainerCanModify` | No       | `boolean` |

- `owner`
- `repo`
- `title`
- `head`
- `base`
- `body`
- `draft`
- `maintainerCanModify`

**Admin `inputSchema` (verbatim, the Wave-1 transcription source):**

```json
{
  "type": "object",
  "properties": {
    "owner": {
      "type": "string",
      "minLength": 1
    },
    "repo": {
      "type": "string",
      "minLength": 1
    },
    "title": {
      "type": "string",
      "minLength": 1
    },
    "head": {
      "type": "string",
      "minLength": 1
    },
    "base": {
      "type": "string",
      "minLength": 1
    },
    "body": {
      "type": "string"
    },
    "draft": {
      "type": "boolean"
    },
    "maintainerCanModify": {
      "type": "boolean"
    }
  },
  "additionalProperties": false,
  "required": [
    "owner",
    "repo",
    "title",
    "head",
    "base"
  ]
}
```

---

## Auth shape

### All three providers are OAuth2 — none is a static token

`GET /api/oauth/configs` returned 66 entries. `gmail`, `googlecalendar`, and `github` each
have their **own separate** entry, each with `auth.type == "oauth2"` and
`expectedRedirectUri: http://localhost:3001/oauth/callback`.

github (verbatim, `configured: false` because no client credentials were set at capture time):

```json
{
  "service": "github",
  "configured": false,
  "clientId": null,
  "expectedRedirectUri": "http://localhost:3001/oauth/callback",
  "auth": {
    "type": "oauth2",
    "authorizationUrl": "https://github.com/login/oauth/authorize",
    "tokenUrl": "https://github.com/login/oauth/access_token",
    "scopes": [
      "read:user",
      "user:email",
      "repo",
      "workflow",
      "delete_repo"
    ],
    "tokenEndpointAuthMethod": "client_secret_post"
  }
}
```

googlecalendar (verbatim):

```json
{
  "service": "googlecalendar",
  "configured": false,
  "clientId": null,
  "expectedRedirectUri": "http://localhost:3001/oauth/callback",
  "auth": {
    "type": "oauth2",
    "authorizationUrl": "https://accounts.google.com/o/oauth2/v2/auth",
    "tokenUrl": "https://oauth2.googleapis.com/token",
    "scopes": [
      "https://www.googleapis.com/auth/calendar",
      "https://www.googleapis.com/auth/calendar.readonly",
      "https://www.googleapis.com/auth/calendar.events",
      "https://www.googleapis.com/auth/calendar.calendars",
      "https://www.googleapis.com/auth/calendar.calendarlist",
      "https://www.googleapis.com/auth/calendar.settings.readonly",
      "https://www.googleapis.com/auth/calendar.acls",
      "https://www.googleapis.com/auth/calendar.acls.readonly"
    ],
    "tokenEndpointAuthMethod": "client_secret_post",
    "authorizationParams": {
      "access_type": "offline",
      "prompt": "consent"
    }
  }
}
```

Two things follow:

- **googlecalendar's scopes are disjoint from gmail's.** googlecalendar declares
  `calendar`, `calendar.readonly`, `calendar.events`, `calendar.calendars`,
  `calendar.calendarlist`, `calendar.settings.readonly`, `calendar.acls`,
  `calendar.acls.readonly`; gmail declares only `gmail.*` scopes. They are separate OC
  services with separate configs, so they are **separate connections** and separate consent
  flows even though both are Google.
- **Some github actions list `requiredAuthTypes: ["oauth2", "api_key"]`.** All 8 curated
  github actions do. `api_key` is an **alternative**, not a replacement: `oauth2` is present
  and is listed first, and github's oauth config entry is a full OAuth2 config with an
  `authorizationUrl`. Nothing forces a static token.

### `POST /api/oauth/authorizations` accepts a caller-supplied `connectionName` for both new providers

Verified with dummy client credentials for each service. The `connectionName` sent was a
20-hex-char string, matching increment 1's blake2b `mint_connection_name` scheme. Both calls
were accepted and returned a `state` uuid plus an `authorizationUrl` whose `redirect_uri`
query param is `http%3A%2F%2Flocalhost%3A3001%2Foauth%2Fcallback` — i.e. the URI correctly
follows `PORT=3001`.

| service | connectionName accepted | `state` returned | authorizationUrl host | encoded `redirect_uri` |
|---|---|---|---|---|
| `googlecalendar` | yes (20-hex) | uuid | `accounts.google.com` | `http%3A%2F%2Flocalhost%3A3001%2Foauth%2Fcallback` |
| `github` | yes (20-hex) | uuid | `github.com` | `http%3A%2F%2Flocalhost%3A3001%2Foauth%2Fcallback` |

**Therefore spec D6's popup-poll fan-out is valid for GitHub**: mint a connection name, POST
an authorization, open the returned `authorizationUrl` in a popup, poll for the connection.
Identical mechanics to Gmail's, per provider.

---

## Blockers

**None.**

The plan document flagged one risk explicitly: *"github's OC auth may be a static token,
which would break the popup-poll connect UX."* That risk was **CHECKED and DISPROVEN**:

- github has its own `auth.type: "oauth2"` config entry with
  `authorizationUrl: https://github.com/login/oauth/authorize`,
  `tokenUrl: https://github.com/login/oauth/access_token`,
  `tokenEndpointAuthMethod: "client_secret_post"`, and scopes
  `["read:user", "user:email", "repo", "workflow", "delete_repo"]`.
- `POST /api/oauth/authorizations` for `github` accepted a caller-supplied `connectionName`
  and returned a `state` + `authorizationUrl` — the exact two values the popup-poll flow
  needs.
- The `api_key` entry in `requiredAuthTypes` is an alternative auth path OC also supports,
  not a constraint that github must use a static token.

Wave 1 can proceed on both providers.

---

## Appendix A: full googlecalendar action list (37)

The 6 curated ids are the ones with a policy row above. Reproduced in full so a future
increment can widen the curated set **without inventing ids**.

| actionId | description |
|---|---|
| `googlecalendar.add_calendar_to_list` | Add a calendar to the current user's Google Calendar list. |
| `googlecalendar.clear_calendar` | Clear all events from a Google Calendar. |
| `googlecalendar.create_acl_rule` | Create an ACL rule on a Google Calendar. |
| `googlecalendar.create_calendar` | Create a Google Calendar. |
| `googlecalendar.create_event` | Create a Google Calendar event. |
| `googlecalendar.delete_acl_rule` | Delete an ACL rule from a Google Calendar. |
| `googlecalendar.delete_calendar` | Delete a Google Calendar. |
| `googlecalendar.delete_event` | Delete a Google Calendar event. |
| `googlecalendar.find_event` | Search events in a Google Calendar using a query string. |
| `googlecalendar.find_free_slots` | Derive free slots from Google Calendar freeBusy data. |
| `googlecalendar.free_busy_query` | Query busy intervals for calendars and groups. |
| `googlecalendar.get_acl_rule` | Fetch one ACL rule from a Google Calendar. |
| `googlecalendar.get_calendar` | Fetch one Google Calendar resource by ID. |
| `googlecalendar.get_calendar_list_entry` | Fetch one Google Calendar list entry by calendar ID. |
| `googlecalendar.get_colors` | Fetch the Google Calendar colors resource. |
| `googlecalendar.get_event` | Fetch one Google Calendar event. |
| `googlecalendar.get_setting` | Fetch one Google Calendar setting. |
| `googlecalendar.import_event` | Import an event into Google Calendar without conferenceData or attachments. |
| `googlecalendar.list_acl` | List ACL rules for a Google Calendar. |
| `googlecalendar.list_calendars` | List the current user's Google Calendar list entries. |
| `googlecalendar.list_event_instances` | List instances of a recurring Google Calendar event. |
| `googlecalendar.list_events` | List events from a Google Calendar. |
| `googlecalendar.list_events_all_calendars` | List events across multiple Google Calendars and aggregate the result. |
| `googlecalendar.list_settings` | List Google Calendar settings. |
| `googlecalendar.move_event` | Move a Google Calendar event to another calendar. |
| `googlecalendar.patch_acl_rule` | Patch writable fields on a Google Calendar ACL rule. |
| `googlecalendar.patch_calendar` | Patch writable fields on a Google Calendar resource. |
| `googlecalendar.patch_calendar_list_entry` | Patch writable fields on a Google Calendar list entry. |
| `googlecalendar.patch_event` | Patch writable fields on a Google Calendar event. |
| `googlecalendar.quick_add_event` | Create a Google Calendar event with natural language text. |
| `googlecalendar.remove_attendee` | Remove one attendee email from a Google Calendar event. |
| `googlecalendar.remove_calendar_from_list` | Remove a calendar from the current user's Calendar list. |
| `googlecalendar.sync_events` | Incrementally sync events from a Google Calendar. |
| `googlecalendar.update_acl_rule` | Replace writable fields on a Google Calendar ACL rule. |
| `googlecalendar.update_calendar` | Replace writable fields on a Google Calendar resource. |
| `googlecalendar.update_calendar_list_entry` | Replace writable fields on a Google Calendar list entry. |
| `googlecalendar.update_event` | Replace writable fields on a Google Calendar event. |

## Appendix B: github and gmail catalog sizes

- `github`: **145** actions in a single OC service. Not reproduced in full here — the 8
  curated ids are documented above with their exact schemas. Re-derive the full list with
  `GET /api/actions` filtered on `service == "github"` if a future increment needs to widen
  the set. Do not guess ids.
- `gmail`: **46** actions (7 currently wired).
- Whole catalog: **13,533** actions across all services.

---

## Footnote: the guide's own `curl` example hardcodes port 3000

Every runtime guide's `## Execute` block prints
`curl -s http://localhost:3000/v1/actions/<actionId> ...` even when the container is running
with `PORT=3001`. This is a **documentation string only** — it is not the OAuth
`redirect_uri`, which correctly followed `PORT` in every observation above
(`http://localhost:3001/oauth/callback`). Do not read the guide's `:3000` as evidence that
`PORT` is broken.
