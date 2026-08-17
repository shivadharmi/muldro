# Spike findings — perception port (increment 3, Wave 0)

**HARD GATE.** Increment 3 once shipped seven Gmail action ids that did not exist
in OpenConnector, and every unit test passed because the tests asserted the same
invented constants. Everything below is copied verbatim from a live
`GET /api/actions` response by a generator script — no action id, description or
schema in this document was typed by hand.

## Provenance

| field | value |
|---|---|
| OpenConnector image | `ghcr.io/oomol-lab/open-connector:v1.3.5` |
| date | 2026-08-17 |
| endpoint | `GET http://localhost:3001/api/actions` (admin token) |
| auth | `OOMOL_CONNECT_ADMIN_TOKEN` (throwaway, scratchpad-only) |
| total actions | **13533** |
| distinct services | 1277 |

Headless: no browser consent, no configured connection. `GET /api/actions`
enumerates the whole catalog under the admin token, and every element carries
both `inputSchema` and `outputSchema` as real JSON Schema objects.

---

## Q1 — all `gmail.*` actions (46)

| actionId | description |
|---|---|
| `gmail.add_label_to_email` | Add and/or remove labels on a single Gmail message. |
| `gmail.batch_modify_messages` | Add and/or remove labels on up to 1,000 Gmail messages. |
| `gmail.create_draft` | Create a Gmail draft with a simplified input and output shape. |
| `gmail.create_email_draft` | Create a Gmail draft with recipients, subject, body, and optional threading. |
| `gmail.create_filter` | Create a Gmail filter with matching criteria and resulting actions. |
| `gmail.create_label` | Create a new Gmail label and return its internal label ID. |
| `gmail.delete_draft` | Permanently delete a Gmail draft by draft ID. |
| `gmail.delete_filter` | Permanently delete a Gmail filter by filter ID. |
| `gmail.delete_label` | Permanently delete a user-created Gmail label. |
| `gmail.fetch_emails` | List Gmail messages with optional query, label, and pagination filters. Use detail to choose IDs, summaries, or full messages. |
| `gmail.fetch_message_by_message_id` | Fetch a Gmail message by message ID with a controllable response format. |
| `gmail.fetch_message_by_thread_id` | Fetch all messages in a Gmail thread. |
| `gmail.get_auto_forwarding` | Get the current Gmail auto-forwarding configuration. |
| `gmail.get_draft` | Get a Gmail draft by draft ID. |
| `gmail.get_filter` | Get a Gmail filter by filter ID. |
| `gmail.get_label` | Get details for a Gmail label. |
| `gmail.get_language_settings` | Get the Gmail display language settings. |
| `gmail.get_message` | Get a Gmail message by message ID with a simplified normalized output. |
| `gmail.get_profile` | Get the connected Gmail profile, including mailbox totals and the current historyId. |
| `gmail.get_vacation_settings` | Get the Gmail vacation responder settings. |
| `gmail.list_drafts` | List Gmail drafts with pagination. |
| `gmail.list_filters` | List Gmail filters for the mailbox. |
| `gmail.list_forwarding_addresses` | List registered forwarding addresses. |
| `gmail.list_history` | List Gmail mailbox change history after a known startHistoryId. |
| `gmail.list_labels` | List all system and user-created Gmail labels. |
| `gmail.list_threads` | List Gmail threads with optional query filtering and pagination. |
| `gmail.modify_thread_labels` | Add and/or remove labels on every message in a Gmail thread. |
| `gmail.move_thread_to_trash` | Move an entire Gmail thread to trash. |
| `gmail.move_to_trash` | Move a Gmail message to trash. |
| `gmail.patch_label` | Patch a user-created Gmail label. |
| `gmail.reply_email` | Reply to an existing Gmail thread using the original message's reply headers. |
| `gmail.reply_to_thread` | Reply to an existing Gmail thread while preserving Gmail threading. |
| `gmail.search_threads` | Search Gmail threads by query and return lightweight thread summaries. Spam and trash stay excluded unless explicitly targeted in the query. |
| `gmail.send_draft` | Send an existing Gmail draft as-is. |
| `gmail.send_email` | Send an email from the connected Gmail account. |
| `gmail.settings_get_imap` | Get the Gmail IMAP settings. |
| `gmail.settings_get_pop` | Get the Gmail POP settings. |
| `gmail.stop_watch` | Stop Gmail push watch notifications for the mailbox. |
| `gmail.untrash_message` | Restore a previously trashed Gmail message. |
| `gmail.untrash_thread` | Restore a previously trashed Gmail thread. |
| `gmail.update_draft` | Update an existing Gmail draft in place. |
| `gmail.update_imap_settings` | Update the Gmail IMAP settings. |
| `gmail.update_label` | Update an existing Gmail label. |
| `gmail.update_language_settings` | Update the Gmail display language settings. |
| `gmail.update_pop_settings` | Update the Gmail POP settings. |
| `gmail.update_vacation_settings` | Update the Gmail vacation responder settings. |

**Does a history-capable action exist?** **YES** — `gmail.list_history`.

Recorded for the record, **not for adoption**. D1 rejected the history cursor
*even if the spike found one*: the native connector already narrowed history to
`historyTypes=messageAdded`, so the extra fidelity history buys (label changes,
deletes, read-state) was already discarded at the filter. A `historyId` also
expires, which is what forced the 404-resync recursion and `MAX_HISTORY_PAGES`.
A timestamp cursor cannot expire, so all of that disappears rather than being
ported. This finding does not reopen D1.

<details><summary><code>gmail.list_history</code> inputSchema (recorded, unused)</summary>

```json
{
  "type": "object",
  "properties": {
    "startHistoryId": {
      "type": "string",
      "minLength": 1,
      "description": "History checkpoint."
    },
    "pageToken": {
      "type": "string",
      "description": "Opaque pagination token returned by Gmail."
    },
    "maxResults": {
      "type": "integer",
      "minimum": 1,
      "maximum": 500,
      "description": "Maximum number of results to return."
    },
    "labelId": {
      "type": "string",
      "minLength": 1,
      "description": "Gmail label ID."
    },
    "historyTypes": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "History event types to include."
    },
    "userId": {
      "type": "string",
      "description": "Gmail user ID. Omit to use the connected mailbox."
    }
  },
  "additionalProperties": false,
  "required": [
    "startHistoryId"
  ],
  "description": "The input payload for this action."
}
```
</details>

---

## Q2 — github notifications action? (145 github actions)

**NO. Zero hits.** Searching every one of the 145 `github.*` actions for
`notif` in **either** the actionId or the description returned
**0 results**.

### Control — the search method works

A null result proves nothing unless the method is known to find the thing when
it is present. Running the same substring over the **entire** catalog:

| service | actions whose id contains `notification` |
|---|---|
| `bark` | 3 |
| `dokploy` | 42 |
| `komari` | 12 |
| `nasa` | 1 |
| `onesignal_rest_api` | 1 |
| `push_by_techulus` | 2 |
| `v2ex` | 2 |
| `wachete` | 1 |

So OpenConnector does model notification actions for other services. GitHub
simply has none — its `/notifications` endpoint is not in the OC catalog.

### Gate result

**D3 resolves to (C) — DEFER.** GitHub perception is NOT ported in this
increment. Task 10 is replaced by Task 10-Defer.

Re-sourcing GitHub perception onto `search_issues_and_pull_requests` was already
rejected in the spec as *a product change wearing a port's clothes* — it swaps
"what GitHub decided to notify me about" (cross-repo, read-state aware,
comment-level granularity) for "issue/PR objects matching a query I authored".
That rejection stands and is NOT reopened by this result.

**Consequence that must be handled in code (spec D3 x D4):** github is now
gateway-backed *and* its registered connector is still the native one. The
poller's discriminator therefore needs TWO conditions — gateway-backed AND the
connector is a `GatewayConnector` subclass — otherwise github takes the gateway
branch, skips `OAuthManager`, receives a caller it ignores, reads an empty
`access_token`, and returns `auth_failed`, which is PERMANENT with threshold 1,
so the circuit opens after one attempt. That is the Wave 5.3 bug reached by a
new route.

---

## Q3 — github "who am I" action?

**YES** — `github.get_current_user`: Get the current authenticated GitHub user profile.

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

Zero-input, so it would serve as a health check and as the actor source for
normalization. **Moot under Q2's defer** — recorded for the deferred increment.

---

## Q4 — `gmail.fetch_emails` outputSchema (decides D8)

### inputSchema

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Gmail search query."
    },
    "labelIds": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "description": "Gmail label IDs."
    },
    "includeSpamTrash": {
      "type": "boolean",
      "description": "Whether to include Spam and Trash."
    },
    "detail": {
      "type": "string",
      "enum": [
        "ids",
        "summary",
        "full"
      ],
      "description": "Message detail level.",
      "default": "summary"
    },
    "maxResults": {
      "type": "integer",
      "minimum": 1,
      "maximum": 500,
      "description": "Maximum number of results to return."
    },
    "pageToken": {
      "type": "string",
      "description": "Opaque pagination token returned by Gmail."
    }
  },
  "additionalProperties": false,
  "description": "The input payload for this action."
}
```

### outputSchema

```json
{
  "type": "object",
  "properties": {
    "messages": {
      "type": "array",
      "items": {
        "anyOf": [
          {
            "type": "object",
            "properties": {
              "messageId": {
                "type": "string",
                "minLength": 1,
                "description": "Gmail message ID."
              },
              "threadId": {
                "type": "string",
                "minLength": 1,
                "description": "Gmail thread ID."
              },
              "labelIds": {
                "type": "array",
                "items": {
                  "type": "string",
                  "minLength": 1
                },
                "description": "Gmail label IDs."
              },
              "subject": {
                "type": "string",
                "description": "Message subject."
              },
              "sender": {
                "type": "string",
                "description": "Message sender."
              },
              "to": {
                "type": "string",
                "description": "Message recipients."
              },
              "messageTimestamp": {
                "type": "string",
                "description": "Message timestamp."
              }
            },
            "additionalProperties": true,
            "required": [
              "messageId",
              "threadId",
              "labelIds",
              "subject",
              "sender",
              "to",
              "messageTimestamp"
            ],
            "description": "Normalized Gmail message summary."
          },
          {
            "type": "object",
            "properties": {
              "messageId": {
                "type": "string",
                "minLength": 1,
                "description": "Gmail message ID."
              },
              "threadId": {
                "type": "string",
                "minLength": 1,
                "description": "Gmail thread ID."
              },
              "labelIds": {
                "type": "array",
                "items": {
                  "type": "string",
                  "minLength": 1
                },
                "description": "Gmail label IDs."
              },
              "subject": {
                "type": "string",
                "description": "Message subject."
              },
              "sender": {
                "type": "string",
                "description": "Message sender."
              },
              "to": {
                "type": "string",
                "description": "Message recipients."
              },
              "messageTimestamp": {
                "type": "string",
                "description": "Message timestamp."
              },
              "preview": {
                "type": "object",
                "additionalProperties": true,
                "description": "Gmail API object."
              },
              "payload": {
                "anyOf": [
                  {
                    "type": "object",
                    "additionalProperties": true,
                    "description": "Gmail API object."
                  },
                  {
                    "type": "null"
                  }
                ]
              },
              "messageText": {
                "type": "string",
                "description": "Extracted message body text."
              },
              "attachmentList": {
                "type": "array",
                "items": {
                  "type": "object",
                  "additionalProperties": true,
                  "description": "Gmail API object."
                },
                "description": "Message attachments."
              },
              "raw": {
                "type": "string",
                "description": "Raw RFC 2822 message when requested."
              }
            },
            "additionalProperties": true,
            "required": [
              "messageId",
              "threadId",
              "labelIds",
              "subject",
              "sender",
              "to",
              "messageTimestamp"
            ],
            "description": "Normalized Gmail message."
          },
          {
            "type": "object",
            "additionalProperties": true,
            "description": "Gmail API object."
          }
        ]
      },
      "description": "Returned messages."
    },
    "nextPageToken": {
      "anyOf": [
        {
          "type": "string",
          "description": "Opaque pagination token returned by Gmail."
        },
        {
          "type": "null"
        }
      ]
    },
    "resultSizeEstimate": {
      "type": "integer",
      "description": "Approximate result count."
    }
  },
  "additionalProperties": false,
  "required": [
    "messages"
  ],
  "description": "Message list result."
}
```

### Verdict against D8's four required fields

| field D8 requires | present in `fetch_emails` `detail="full"`? |
|---|---|
| `threadId` | **YES** — declared and `required` |
| `internalDate` | **NO** — not declared. `messageTimestamp` (string) replaces it |
| `snippet` | **NO** — not declared. `preview` (opaque object) is the likely analogue |
| `payload.headers` | **PARTIAL** — `payload` is declared but typed as an opaque `{"type":"object","additionalProperties":true,"description":"Gmail API object."}` passthrough, so `headers` is not schema-guaranteed |

OpenConnector reshapes Gmail's response into its own DTO. This is exactly the
reshape D8 was written to detect.

### But D8's stated fallback is STRICTLY WORSE — do not use it

D8 said: if `full` drops those fields, fall back to `detail="ids"` +
`gmail.get_message`. The catalog says that fallback is the worse branch.
`gmail.get_message` returns a *different, poorer* DTO:

```json
{
  "type": "object",
  "properties": {
    "messageId": {
      "type": "string",
      "minLength": 1,
      "description": "Gmail message ID."
    },
    "threadId": {
      "type": "string",
      "minLength": 1,
      "description": "Gmail thread ID."
    },
    "subject": {
      "type": "string"
    },
    "from": {
      "type": "string"
    },
    "to": {
      "type": "string"
    },
    "date": {
      "type": "string"
    },
    "body": {
      "type": "string"
    }
  },
  "additionalProperties": false,
  "required": [
    "messageId",
    "threadId"
  ],
  "description": "Simplified Gmail message."
}
```

It is `"additionalProperties": false` with seven flat string fields and carries
**no `payload`, no headers, and no `labelIds` at all**. Falling back to it would
destroy the very `List-Unsubscribe` / `List-Id` / `Precedence` capture that D8
exists to protect, and would additionally lose `labelIds`. The "safe fallback"
is the unsafe branch.

### DECISION: `detail="full"`

`detail="full"` is the only option that *can* carry the triage headers, via the
`payload` passthrough. Two consequences for the implementation:

1. **Header capture must be defensive.** Because `payload` is an opaque
   passthrough rather than a guaranteed shape, missing headers must degrade
   gracefully (triage falls back to its normal path) and must never raise.
   Whether `payload.headers` actually arrives is confirmed at live acceptance
   (Task 18), which is the only place a real Gmail payload exists.
2. **The cursor cannot assume `internalDate`.** D1 specifies "max `internalDate`,
   epoch seconds". That field is not declared. The connector reads `internalDate`
   when present (permitted by `"additionalProperties": true`) and otherwise
   parses `messageTimestamp`. The *cursor model* is unchanged; only the field
   it is read from becomes a two-step lookup.

### If a per-message call is ever needed, it is NOT `get_message`

`gmail.fetch_message_by_message_id` — Fetch a Gmail message by message ID with a controllable response format. — takes a `format` enum
(`minimal` / `full` / `raw` / `metadata`) and returns the **same** richer
`"Normalized Gmail message"` DTO as `fetch_emails detail="full"`:

```json
{
  "type": "object",
  "properties": {
    "messageId": {
      "type": "string",
      "minLength": 1,
      "description": "Gmail message ID."
    },
    "format": {
      "type": "string",
      "enum": [
        "minimal",
        "full",
        "raw",
        "metadata"
      ],
      "description": "Gmail response format to request."
    }
  },
  "additionalProperties": false,
  "required": [
    "messageId"
  ],
  "description": "The input payload for this action."
}
```

---

## Q5 — gmail/calendar watch or subscribe action? (decides D9 follow-up)

| actionId | description |
|---|---|
| `gmail.stop_watch` | Stop Gmail push watch notifications for the mailbox. |

**There is a `stop_watch` and no `watch`.** OpenConnector exposes the action that
*stops* a Gmail push channel and no action that *starts* one, and
`googlecalendar` has neither.

**D9 stands unchanged and its gap is now measured, not assumed.**
`_register_webhooks_for_sources` is kept (it is the only entry point into the
mounted webhook route + `PushReceiver` + `webhook_renewal_tick` chain, so
deleting it would make all three permanently unreachable), and re-homing it to
`confirm_connection` remains **structurally blocked**: registration needs a
`watch` call that OpenConnector cannot make. Push perception stays out of scope
until OC gains a watch capability.

---

## Q6 — github merge action? (RECORDED ONLY)

| actionId | description |
|---|---|
| `github.check_pull_request_merged` | Check whether a GitHub pull request has been merged. |
| `github.merge_branch` | Merge one branch into another in a GitHub repository. |
| `github.merge_pull_request` | Merge a GitHub pull request. |

`repo.merge_pr` was lost when the curated github set was authored (increment 2)
and the multi-provider spike contained zero `merge` occurrences. It exists.
**Recorded only — adding it is a curated-set widening with its own capability,
risk and approval decision, and is explicitly out of scope here.**

---

## Q7 — distinct services (decides whether D13's providers CAN migrate)

| service | actions | D13 relevance |
|---|---|---|
| `confluence` | **5** | atlassian — increment 5+ candidate |
| `github` | **145** | migrated (increment 2); perception DEFERRED per Q2 |
| `gmail` | **46** | migrated (increment 2) |
| `googlecalendar` | **37** | migrated (increment 2) |
| `googledrive` | **43** | `drive` connector's provider, if D11 keeps it |
| `jira` | **7** | atlassian — increment 5+ candidate |
| `notion` | **25** | increment 5+ candidate |
| `slack` | **23** | increment 5+ candidate |

**Every D13 candidate exists in the catalog.** `slack`, `notion`, `jira`,
`confluence` and `googledrive` are all present, so none of them is blocked from
migrating for want of an OpenConnector service. Note that Atlassian is split into
two OC services (`jira`, `confluence`) rather than one, which the per-provider
registry shape already accommodates.

**Recorded only — no provider migrates in this increment.** D13's ordering is a
correctness constraint: retiring a provider's native OAuth is what breaks its
perception connector, so a migration must not precede the `GatewayConnector` this
increment builds.

---

## DECISIONS

| decision | resolved branch | basis |
|---|---|---|
| **D3** github | **(C) DEFER** | Q2: zero notifications actions in 145 github actions, with a positive control |
| **D8** payload fidelity | **`detail="full"`** | Q4: `full` carries an opaque `payload` passthrough; the stated fallback `get_message` is a strictly poorer DTO with no headers and no `labelIds` |
| D1 gmail cursor | unchanged (timestamp) | Q1 records that `gmail.list_history` exists; D1 rejected history regardless, and that rejection is not reopened |
| D2 calendar cursor | unchanged (`updatedMin`) | `googlecalendar.list_events` declares both `updatedMin` and `syncToken`; D2 chose `updatedMin` because 410-expiry cannot survive the gateway |
| D9 push | unchanged (keep, document) | Q5: `stop_watch` exists, `watch` does not |
| D13 sequencing | unchanged | Q7: every candidate service exists, so nothing is blocked — but nothing migrates here |

### Scope change entering this increment

GitHub perception is deferred, so this increment lands as **Gmail + Calendar**.
The github connector stays native and un-runnable, and the poller must skip it
**non-permanently** — never `auth_failed`, which is permanent at threshold 1.

