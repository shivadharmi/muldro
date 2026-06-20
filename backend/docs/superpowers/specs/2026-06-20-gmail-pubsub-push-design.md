# Gmail real-time perception via Pub/Sub — Design

**Date:** 2026-06-20
**Status:** Approved (pending spec review)
**Branch:** `review/architecture-remediation`
**Related:** webhook subsystem (`feat(webhooks)` 12a4f5c), perception wake-signal model (P5), `[[project_perception_briefing_webhook_remediation]]`

## Goal

Make Gmail perception **real-time** by accepting Google Pub/Sub push notifications as **wake signals** that trigger an immediate poll, closing the one remaining gap in the webhook subsystem (Calendar push already works end-to-end; Gmail was left poll-only because its transport differs).

## Non-goals

- Parsing email data from the webhook. The push is a **wake signal only**; the existing Gmail connector poll fetches the actual data from its stored cursor.
- Backfilling `account_email` for Gmail connections that predate email capture (documented limitation; resolver fails safe).
- A live end-to-end test against a real Pub/Sub subscription (requires operator GCP setup; deferred to deployment validation).
- Any in-app UI surfacing of push status.

## Background: why Gmail push is a different path from Calendar

Confirmed against Google's official docs:

1. **One shared endpoint, not per-channel.** Every user's `users.watch` publishes to the *same* Pub/Sub topic → *same* push subscription → *same* endpoint URL. The notification identifies the mailbox **only** by `emailAddress` inside the base64 payload, never by the URL path. Calendar's per-`subscription_id` routing cannot apply.
2. **Auth is an OIDC JWT, not an HMAC header token.** Pub/Sub signs a Bearer JWT (`iss=https://accounts.google.com`, `aud=`the configured audience, `email=`the push service account, `email_verified=true`, `exp`). There is no shared secret; verification is RS256-signature-against-Google-JWKS plus claim checks.
3. **The payload `historyId` must be ignored.** Notifications coalesce and can arrive out of order. The correct pattern is `users.history.list(startHistoryId=<stored cursor>)`. This fits our wake-signal model exactly: resolve the user, set `pending_run`, done.

Payload shapes (authoritative):
- Pub/Sub envelope: `{"message": {"data": "<base64>", "messageId": "...", "publishTime": "...", "attributes": {...}}, "subscription": "projects/.../subscriptions/..."}`
- `message.data` (base64-decoded): `{"emailAddress": "user@example.com", "historyId": "9876543210"}`

## Architecture

```
Pub/Sub push  --POST-->  /v1/webhooks/gmail/pubsub
   Authorization: Bearer <OIDC JWT>
        |
        v
  1. Verify JWT (google-auth)         -> invalid/missing -> 401
        |
  2. Decode envelope -> emailAddress (lowercased)
        |
  3. resolve_tenants_by_google_email  -> [] (unknown) -> 200 ack (log, no-op)
        |  -> [(user_id, workspace_id), ...]
  4. per tenant: backpressure check -> over -> skip that tenant (log)
        |
  5. per tenant: PerceptionPolicy.request_run(ws, user, "gmail", signal_source="webhook")
        |
        v
  scheduler perception_tick -> Gmail connector poll from STORED cursor
```

The receiver does only steps 1–5. All real ingestion uses the existing connector + cursor + EventProcessor pipeline.

## Components

Each is small and independently testable.

| Component | Responsibility | Location |
|-----------|----------------|----------|
| `google-auth` dependency | Canonical OIDC JWT verification (`google.oauth2.id_token.verify_oauth2_token`) | `pyproject.toml` (+ lockfile) |
| Inbound endpoint | `POST /v1/webhooks/gmail/pubsub` — verify, decode, resolve, wake. Declared **before** `/{provider}/{subscription_id}` so the literal path wins. | `src/api/routes_webhooks.py` |
| JWT verifier | Wraps `verify_oauth2_token(token, Request(), audience=<derived>)`; asserts `email_verified` and (optional) `email == settings.gmail_pubsub_sa_email`. Fail-closed. | new helper, e.g. `src/integrations/sync/pubsub_auth.py` |
| Email→tenant resolver | `resolve_tenants_by_google_email(db, email) -> list[tuple[user_id, workspace_id]]` over `IntegrationInstallation` (`server_name='google-workspace'`, `lower(config->>'account_email') == email.lower()`, `status='active'`). Returns a **list** (one email may span workspaces). | new helper, e.g. `src/services/google_account_resolver.py` |
| Expression index | Partial index on `lower(config->>'account_email')` where `server_name='google-workspace'` | Alembic migration |
| Registration re-enable | Add `"gmail"` to `_PUSH_PROVIDERS`, gated on `gmail_pubsub_configured`. `_gmail_watch` already calls `users.watch(topicName)`; renewal tick already re-watches before expiry. | `src/integrations/sync/webhook_manager.py` |
| Terraform module | Pub/Sub topic + `gmail-api-push@system` publisher IAM + OIDC push subscription | `infra/` (GCP) |

## Configuration & gating

**Net-new required settings: zero.** The feature reuses existing config and derives the rest.

- **Gate:** `gmail_pubsub_configured` ≝ `webhooks_configured` (i.e. `webhooks_enabled` AND `webhook_callback_base_url` set) **AND** `gmail_pubsub_topic` set. (`gmail_pubsub_topic` already exists.)
- **Audience (derived, not configured):** `f"{webhook_callback_base_url}/v1/webhooks/gmail/pubsub"`.
- **Optional hardening:** `gmail_pubsub_sa_email` (default `""`). When set, the verifier additionally asserts the JWT `email` claim equals it. When unset, verification stops at Google-signature + audience + `email_verified`.

With default/empty config: Gmail stays exactly poll-only — no watch registered, the endpoint rejects everything. We register Gmail watches **only** when the inbound can be verified, so we never create a push we cannot authenticate.

Principle adopted for the spec generally: **derive-from-existing and convention-over-configuration; add a setting only when it is deployment-specific, non-derivable, AND required.**

## Security model

Verification order (fail-closed at every step):

1. Extract `Authorization: Bearer <JWT>`; missing → **401**.
2. `verify_oauth2_token(token, Request(), audience=<derived endpoint URL>)` — checks RS256 signature against Google JWKS, `aud`, `exp`; raises on failure → **401/403**.
3. Assert `claim["email_verified"] is True` → else reject.
4. If `gmail_pubsub_sa_email` set: assert `claim["email"] == gmail_pubsub_sa_email` → else reject.

**Threat analysis of the optional SA pin.** Without the SA pin, an actor with any GCP project could mint a Google-signed token with our URL as `aud` and send forged **wake signals** for a known email. Blast radius is bounded and low: it can only cause us to **poll a mailbox we are already authorized to poll, slightly early** — no data reaches the actor (we always return 2xx to valid-JWT requests), it **cannot** un-pause a paused source (`signal_source="webhook"` is excluded from `_wake_signals`), and it is capped by per-workspace backpressure + the perception circuit breaker. This residual is acceptable for a wake-only endpoint; operators wanting it fully locked set `gmail_pubsub_sa_email`.

**No replay protection.** A wake signal is idempotent (`pending_run=True`); replaying it is harmless, so the Redis dedup the GitHub path uses is unnecessary here.

## Email → tenant resolution (the load-bearing dependency)

The Google account email is captured on OAuth connect and persisted to `IntegrationInstallation.config["account_email"]` (JSONB) on the `server_name='google-workspace'` row, which also carries `user_id` and `workspace_id`. The resolver:

- Normalizes the incoming `emailAddress` to lowercase; stored value is matched lowercased.
- Filters `status='active'`.
- Returns a **list** of `(user_id, workspace_id)` — one human/email may have connected the same Google account across multiple workspaces; all are woken.
- Backed by a partial expression index on `lower(config->>'account_email')` for `server_name='google-workspace'` (added via Alembic).

**Known limitation:** connections that predate `account_email` capture will not resolve. The resolver fails safe — an unresolved email logs a warning and returns `[]`, the endpoint 200-acks, and that mailbox simply stays on its poll timer. No backfill is built now (YAGNI); add one only if such accounts exist.

## Wake semantics

- `PerceptionPolicyService.request_run(workspace_id, user_id, "gmail", signal_source="webhook")` per resolved tenant.
- `signal_source="webhook"` deliberately does **not** resurrect a paused gmail source (consistent with the webhook security model). Gmail is active after connect, so normal operation wakes correctly.
- The pushed `historyId` is **ignored**; the connector polls `history.list` from the stored `observation_cursors` value (handles coalescing/reordering; 404 → full re-sync already handled by the connector).

## HTTP response semantics

- Invalid / missing JWT → **401**.
- Valid JWT, malformed envelope → **400**.
- Valid JWT, unmappable email → **200** ack (log, no-op) — avoids infinite Pub/Sub retries for unknown mailboxes.
- Valid JWT, resolved, woken (or backpressured-skip) → **200**.
- Transient internal error → **5xx** so Pub/Sub legitimately retries.

## Registration & renewal

- Add `"gmail"` to `_PUSH_PROVIDERS`, gated on `gmail_pubsub_configured`. `register()` then calls `_gmail_watch` (already implemented) → `users.watch(topicName=gmail_pubsub_topic, labelIds=["INBOX"])` on Google connect, best-effort (never fails the connect).
- The Gmail `WebhookSubscription` row is **renewal-only bookkeeping**: inbound routing is by email, not `subscription_id`, so the row's `secret`/`external_id` are inert for inbound. The row exists solely so the existing renewal tick re-calls `users.watch` before Gmail's ≤7-day expiry. (Documented in code.)
- The existing hourly renewal tick (6h buffer) covers Gmail's 7-day expiry comfortably.

## Infrastructure (operator, one-time)

A Terraform module in `infra/` provisions exactly three resources, so operator setup is `terraform apply` + the existing `JARVIS_GMAIL_PUBSUB_TOPIC` env var:

1. `google_pubsub_topic` (e.g. `jarvis-gmail-push`).
2. `google_pubsub_topic_iam_member` — `gmail-api-push@system.gserviceaccount.com` as `roles/pubsub.publisher`.
3. `google_pubsub_subscription` (push) — `push_endpoint = {callback_base_url}/v1/webhooks/gmail/pubsub`, `oidc_token { service_account_email, audience = push_endpoint }`.

The runbook also documents the equivalent `gcloud` commands.

## Testing strategy

Unit tests (patch `id_token.verify_oauth2_token`; no live GCP):

- Valid JWT + known email → `request_run` called for the right `(user, workspace)`.
- Multi-workspace email → `request_run` called for **all** matches.
- Missing/invalid JWT → 401.
- Valid JWT, `email_verified=false` → reject.
- Optional SA pin: matching email passes, mismatched email rejected; unset skips the check.
- Unmappable email → 200, no `request_run`.
- Malformed envelope → 400.
- Route ordering: `/v1/webhooks/gmail/pubsub` resolves to the dedicated handler, not `/{provider}/{subscription_id}`.
- Resolver: lowercase match, `status` filter, multi-workspace list.
- Registration: `gmail` registers a watch when `gmail_pubsub_configured`, stays poll-only otherwise.

**Not covered locally:** live Pub/Sub delivery (documented as deployment validation).

## File-level change summary

- `pyproject.toml` (+ lockfile) — add `google-auth`.
- `src/config/settings.py` — add optional `gmail_pubsub_sa_email = ""`; add `gmail_pubsub_configured` property.
- `src/integrations/sync/pubsub_auth.py` (new) — JWT verification helper.
- `src/services/google_account_resolver.py` (new) — `resolve_tenants_by_google_email`.
- `src/api/routes_webhooks.py` — new `POST /v1/webhooks/gmail/pubsub` (declared before the catch-all).
- `src/integrations/sync/webhook_manager.py` — re-add `gmail` to `_PUSH_PROVIDERS` (gated); doc the renewal-only row.
- Alembic migration — partial expression index on `lower(config->>'account_email')`.
- `infra/` — GCP Pub/Sub Terraform module + runbook.
- Tests — new test files for the endpoint, verifier, and resolver; extend webhook_manager tests for gmail re-enable.

## Open questions

None — all design forks resolved:
- JWT verification: **add `google-auth`** (canonical).
- Config: **zero net-new required**; optional SA pin off by default.
- Operator setup: **Terraform module** in `infra/`.
- In-app push-status UI: **no**.
- `account_email` backfill: **deferred** (documented limitation).
