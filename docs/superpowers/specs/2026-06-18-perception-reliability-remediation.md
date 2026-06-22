# Spec — Perception Reliability Remediation

Date: 2026-06-18
Branch: `review/architecture-remediation`
Status: Design — ready for implementation (verified against code; both open forks resolved)
Sequencing: **P1 (connector failures)** → **P2 (cursor scoping)** → **P3 (synthesis grouping)** → **P4 (atomic ingest+cursor)** → **P5 (webhook ingestion)**. Downstream decoupling split to a follow-up spec.

## Why

An external review of the perception subsystem raised six reliability concerns. We re-verified
all six against the current code (not the snapshot the review was written against — it cited a
`.codex/worktrees/822e` checkout). **Five confirmed; one was already fixed** by recent work on
this branch (`41c373b`, keying the Redis event layer by `workspace_id`).

The confirmed defects share one root principle: **every ingestion entry point should pass
through a single funnel, and every retry should happen in the domain that owns the data.** The
polled path already honors this (it ingests via `EventProcessor`); the failures are the places
that don't — connectors that hide errors, a webhook path that forks the funnel, a cursor that
isn't atomic with ingestion, and a scheduler that flattens tenants.

This spec is **plan-only**. No code changes are included. Each phase is independently shippable
in the stated order.

## Verification summary

| # | Finding | Verdict | Evidence (current `file:line`) |
|---|---------|---------|-------------------------------|
| 1 | Connectors swallow failures into empty polls; breaker resets on disguised failure | **CONFIRMED** | `gmail.py:99-103`, `slack_connector.py:78-82`, `calendar.py:61-65`, `github_connector.py:58-62` all `except Exception: ... return events, new_cursor`. Caller treats empty as success: `jarvis.py:1394-1402` → `perception_tick.py:86` `record_success` → breaker reset `perception_policy.py:236-238` |
| 2 | `ObservationCursor` scoped narrower than `PerceptionState` | **CONFIRMED** | `perception_state.py:65-69` unique `(workspace_id, user_id, source)` vs `observation_cursor.py:28-31` unique `(user_id, source)`. Poll reads cursor without workspace: `jarvis.py:1706-1710` |
| 3 | Webhook path bypasses `EventProcessor`, omits required fields | **CONFIRMED (worse than stated)** | `push_receiver.py:122-147` builds `NormalizedEvent` directly; omits NOT-NULL `source_account_id`/`occurred_at`/`idempotency_key` (`events.py:19,23,42`); passes nonexistent `raw_payload=` kwarg; no dedup, no DLQ. Likely raises `IntegrityError` at `flush()` |
| 4 | `event_processed` payload loses `workspace_id` | **ALREADY FIXED** — dropped | `41c373b`: published `event_processor.py:274`, stored `event_bus.py:92`, parsed `event_bus.py:356`, read by handlers `worker.py:218,278,336,366`. No `resolve_workspace_id` remains |
| 5 | Cursor advances before downstream stages complete; ingest+cursor not atomic | **CONFIRMED** | Cursor saved `jarvis.py:1410` (separate txn from ingest commit `jarvis.py:1802`); Librarian `1430`, relevance `1524`, Planner `1584`, queue `1598` all run after, inline, with no durable hand-off |
| 6 | Cross-source synthesis not grouped by tenant | **CONFIRMED** | `perception_tick.py:114-155` aggregates across all `due_states`, then `user_id = due_states[0].user_id` with `ws_id` from "first state that has one" — can attribute user B's events to user A in a multi-tenant tick |

## Architectural north star

The polled path already routes through the correct funnel (`_ingest_raw_events` →
`EventProcessor.process()` at `jarvis.py:1739-1803`), which gives idempotency dedup, scoring,
DLQ-on-failure, and publishes `event_processed` to the workspace-keyed bus. The remediation
makes *every* entry point and *every* retry obey the same shape:

```
(scheduled poll) ─┐
                  ├─► connector → RawEvents → EventProcessor.process()
(webhook signal) ─┘                            │  normalize · idempotency-dedup · score
   (verify sig,                                │  persist + advance cursor ATOMICALLY
    mark due,                                  │  publish event_processed (workspace-keyed)
    trigger poll)                              ▼
                                        event bus  ──► entity/memory extraction (DLQ)  ✓ exists
                                                   ──► relevance / Planner / queue (DLQ) ◄ move here
```

Principle restated per finding:
- **#1** — connectors must report failure honestly so the funnel's error domain (breaker) sees it.
- **#3** — webhooks *trigger* the funnel, never fork it.
- **#5** — the connector cursor means "durably ingested"; it advances atomically with ingestion, and downstream reasoning retries from the bus/DLQ, not the provider.
- **#6** — the scheduler must preserve tenant identity into the funnel, not flatten it.

---

## Phase 1 — Connector failure propagation `[Finding #1]`

**Highest impact, self-contained, no migration. Do first.**

**Problem.** All four connectors collapse three distinct outcomes — success-empty,
success-with-events, and *failure* — into two, by catching exceptions / non-200s and returning
`([], cursor)`. The caller (`jarvis.py:1394-1402`) treats any empty poll as `status="completed"`,
so `perception_tick.py:86` calls `record_success`, which resets the circuit breaker
(`perception_policy.py:236-238`). A provider in sustained outage (503/429/timeout) therefore
**never trips the breaker** — every failed poll silently clears the failure counter.

**Root cause.** No typed channel for "this poll failed and why."

**Changes.**
- Introduce a typed `PollResult` (frozen dataclass or Pydantic) with: `events: list`,
  `cursor: str | None`, `error_class: Literal["none","transient","permanent","rate_limited","auth_failed"]`,
  and derived flags. This aligns with the transient/permanent split `PerceptionPolicyService`
  already uses for backoff.
- Rewrite each connector's poll method: remove the bare `except Exception: return ([], cursor)`.
  On error, return a `PollResult` carrying the right `error_class` and the **unchanged** cursor.
- `_poll_connector` / `run_perception_cycle` (`jarvis.py:1383-1402`): map a failed poll to the
  existing error return so `perception_tick.py:77-86` routes it to `record_failure`, not
  `record_success`. Preserve the genuine "empty but OK" path → still `record_success`.

**Tests (TDD, write first).**
- Connector returns failure-typed `PollResult` on raised exception and on non-200.
- Empty poll *caused by error* increments breaker failures (`record_failure`).
- Genuine empty-but-OK poll still calls `record_success` and advances the cursor.
- Per-error-class mapping (rate-limited vs permanent) reaches the policy as expected.

**Migration.** None. **Risk.** Low — additive type plus localized return-path edits.

---

## Phase 2 — Cursor workspace scoping `[Finding #2]`

**Multi-tenant correctness. Requires a migration.**

**Problem.** `PerceptionState` is unique by `(workspace_id, user_id, source)` but
`ObservationCursor` is unique by `(user_id, source)` only (`observation_cursor.py:28-31`), and
the poll path reads/writes the cursor without `workspace_id` (`jarvis.py:1706-1710`, and the
`ON CONFLICT` upsert in `_update_cursor` keyed on `uq_cursor_user_source`). A user in two
workspaces shares one cursor row, so one workspace's poll advances the other's stream position.

**Root cause.** A state table and its cursor table that should share a key don't.

**Decision (resolved): do NOT add `source_account_id` to the cursor key.** The cursor key becomes
exactly `(workspace_id, user_id, source)`, mirroring `PerceptionState`. Verified rationale: Jarvis
is single-account-per-provider at every layer, by explicit design —
- `OAuthToken` unique `(user_id, provider)` with in-code ADR note TOOL-P2-2 ("a deliberate feature
  decision, not a defect — do not 'fix' the index in isolation");
- `source_account_id` is a hardcoded constant in every connector (`"gmail_primary"`, etc.) — a
  source label, not an account discriminator;
- `make_idempotency_key` (`event_processor.py:55-65`) excludes `source_account_id`, so real
  multi-account support would require redesigning dedup too.
Adding the constant to the cursor key would imply multi-account capability the rest of the system
does not honor. Multi-account is a coordinated cross-layer feature (OAuth model + poll-loop +
idempotency + cursor + webhook sub) and out of scope here.

**Changes.**
- Widen `ObservationCursor` uniqueness to `(workspace_id, user_id, source)`.
- Update the cursor read (`jarvis.py:1706-1710`) and the `ON CONFLICT` target in `_update_cursor`
  to include `workspace_id`.
- **Alembic migration** with a backfill: existing `(user_id, source)` rows must be assigned a
  `workspace_id`. Strategy to confirm: derive from the matching `PerceptionState`, or from the
  user's sole/primary workspace where unambiguous; log + quarantine ambiguous rows rather than
  guessing.

**Tests.** Same user in two workspaces keeps independent cursors; advancing one never moves the
other. Migration upgrade/downgrade round-trips.

**Migration.** Yes (constraint change + backfill). **Risk.** Medium — backfill correctness.

---

## Phase 3 — Synthesis tenant grouping `[Finding #6]`

**Finishes the `41c373b` workspace-keying refactor. Self-contained, no migration.**

**Problem.** `perception_tick.py:114-155` aggregates event counts across **all** due states,
then fires `run_cross_source_synthesis` with `user_id = due_states[0].user_id` and `ws_id` from
"the first due state that has one." In a tick spanning two tenants, this crosses the boundary —
e.g. attributing user B's Slack events to user A's workspace.

**Root cause.** The scheduler operates at global ("all due sources") scope, but synthesis is
inherently per-`(user_id, workspace_id)`. The stream-keying refactor didn't reach this aggregation.

**Changes.**
- Group `due_states` (and their poll results) by `(user_id, workspace_id)`. Apply the
  `≥2 sources / ≥3 events` threshold **within each group**, and fire `run_cross_source_synthesis`
  **per group** with that group's own `source_names`, `user_id`, `workspace_id`. Delete the
  `due_states[0]` / "first ws_id" guesswork.

**Tests.** A tick spanning two tenants triggers at most one synthesis per tenant, each scoped to
its own sources; never crosses. A single-tenant multi-source tick behaves as today.

**Migration.** None. **Risk.** Low — pure scheduler logic.

---

## Phase 4 — Atomic ingest + cursor advance `[Finding #5, in-scope portion]`

**Design fork — RESOLVED. Scope reduced to P4a; downstream decoupling split to its own spec (see below).**

**Problem (in scope here).** `_ingest_raw_events` commits in its own transaction (`jarvis.py:1802`);
`_update_cursor` opens a separate transaction (`jarvis.py:1821`). Ingestion and cursor-advance are
two independent commits, so a crash between them can persist events without advancing the cursor
(harmless — dedup catches the re-poll) or, after future restructuring, advance without persisting
(events skipped forever).

**Decision — the cursor means "durably ingested."** The connector cursor's correct semantic is
"these events are safe in `normalized_events`" — which is already where it advances. We make that
correct by construction:
- **Atomicity:** advance the cursor inside the **same transaction** as ingestion, so it is
  impossible to persist events without advancing, or advance without persisting. (Fold
  `_update_cursor` into the `_ingest_raw_events` unit of work.)

**Rejected alternatives (these are the workarounds):**
- *Advance cursor only after Librarian/Planner succeed.* Rejected: couples the **provider** cursor
  to **internal reasoning**. Recovery-by-re-poll is broken — dedup skips re-ingested events and
  provider history (e.g. Gmail `historyId`) may have expired by retry time. Wrong retry domain.
- *Two cursors (`fetched` vs `processed`).* Rejected: same conflation — "internal processing
  position" belongs to a bus/DLQ consumer offset, not a connector cursor.

**Why the downstream-decoupling half of #5 is split out (not done here).** Verified findings:
- The inline Librarian extraction (`jarvis.py:1430`) **and** the async worker's entity/memory
  handlers (consuming `event_processed`) **both** write entities/memories for the same event —
  **double extraction CONFIRMED** (duplicate memory writes → Qdrant retrieval pollution; Neo4j
  synced twice).
- The fix is **entangled with the Planner's location**: `librarian_result` is only a diagnostic
  return field (`jarvis.py:1625`, not consumed downstream), but the inline Librarian extracts
  entities *synchronously before* the inline Planner/EventCorrelator run. Removing it while the
  Planner is still inline risks **starving the Planner of freshly-extracted entities**. Gating the
  worker instead would need a new per-event `extracted_inline` flag + asymmetric logic.
- The real fix (move Librarian/relevance/Planner/queue onto the bus, single extraction path,
  ordering via events) is **MEDIUM→LARGE** (Planner alone ~7–14d) with context-reconstruction,
  ordering, and trace-coupling risk.
- A **cycle-level DLQ already exists** (`jarvis.py:1631-1645`, `operation_type="perception_cycle"`),
  so a downstream failure is DLQ'd, not silently lost — which lowers the urgency of the migration.

Therefore the downstream decoupling **+ double-extraction resolution** move to a dedicated spec:
**`<TBD>-perception-reasoning-to-event-bus.md`** (see "Follow-up specs" below). It resolves the
double extraction correctly as a byproduct.

**Tests (this phase).** Ingest failure leaves the cursor unmoved (atomic rollback); ingest success
advances the cursor exactly once, in the same transaction.

**Migration.** None (transaction restructuring). **Risk.** Low.

---

## Phase 5 — Webhook ingestion `[Finding #3]`

**Design fork — DECIDED. The fork is mostly an illusion.**

**Problem.** `PushReceiver._route_event` (`push_receiver.py:122-147`) hand-builds a
`NormalizedEvent`, omitting three NOT-NULL fields (`source_account_id`, `occurred_at`,
`idempotency_key`), passes a nonexistent `raw_payload=` kwarg, and performs no dedup and no DLQ.
It is the one ingestion entry that bypasses `EventProcessor`, and it likely raises `IntegrityError`
at `flush()`.

**Decision — why "route the payload through EventProcessor as data" is not the answer:** Gmail
push (Pub/Sub) carries only a `historyId` + mailbox; Google Calendar push carries only "this
calendar changed" + resource ID. These payloads contain **no event data**, so a complete
`RawEvent` cannot be built from them — the provider API must be called. Webhooks are also
at-least-once (must dedup), and bursts of deliveries are best coalesced into one poll-since-cursor.

**Decision — chosen (best solution):** The webhook becomes a **verified, tenant-scoped
wake-signal that triggers the existing funnel.** Concretely:
1. Verify the provider signature; reject unsigned/invalid deliveries.
2. Resolve `(workspace_id, user_id, source, account)` from the subscription.
3. Set `pending_run=True` on the matching `PerceptionState` **and** kick an immediate (debounced)
   poll cycle, so latency stays low rather than waiting for the next scheduler tick.
4. **Delete** the direct `NormalizedEvent` construction in `_route_event`.

The triggered poll runs through the Phase-1-hardened connector + `EventProcessor` funnel, so it
inherits required-field population, idempotency dedup, scoring, and DLQ for free. A signed
rich-payload fast-path (parse Slack/GitHub events directly, **still via `EventProcessor.process()`**)
is a valid later optimization, not correctness.

**Tests.** Invalid signature rejected. Valid webhook marks the correct `PerceptionState` due and
triggers a poll scoped to the right tenant. A burst of deliveries coalesces into a bounded number
of polls (debounce). No direct `NormalizedEvent` rows are created by the webhook path.

**Migration.** None. **Risk.** Medium — depends on Phase 1 (relies on the hardened funnel) and on
the debounce/trigger mechanism.

---

## Sequencing & dependencies

1. **P1** — independent; unblocks the hardened funnel that P5 relies on.
2. **P2** — independent; migration.
3. **P3** — independent; no migration.
4. **P4** — independent (atomic ingest+cursor only).
5. **P5** — depends on P1 (triggered poll must report failures honestly).

All five are bounded, shippable wins; doing P1 first maximizes the value of P5.

## Resolved decisions

- **P2 — `source_account_id` in cursor key: DEFER.** Cursor key = `(workspace_id, user_id, source)`,
  mirroring `PerceptionState`. Jarvis is single-account-per-provider by explicit design (OAuth ADR
  TOOL-P2-2; `source_account_id` is a constant label; idempotency key excludes it). Multi-account
  is a coordinated cross-layer feature, out of scope.
- **P4 — downstream decoupling: SPLIT to its own spec.** Double extraction CONFIRMED (duplicate
  memory writes), but the fix is entangled with moving the Planner off the inline path (MEDIUM→LARGE,
  ordering/trace risk) and a cycle-level DLQ already covers downstream failures. This remediation
  keeps only the atomic ingest+cursor invariant (P4a).

## Out of scope / follow-up specs

- **Perception reasoning → event bus** (`<TBD>-perception-reasoning-to-event-bus.md`): move
  Librarian / relevance / Planner / queue onto `event_processed` bus consumers with DLQ and
  event-driven ordering; resolves the **double extraction** as a byproduct and closes the
  downstream-replay half of Finding #5. MEDIUM→LARGE.
- **Source-specific cursor strategy**: Slack's single global timestamp across channels; Gmail
  `historyId` expiration handling (`404 history not found` → full resync). The review's priority #6.
- **Rich-payload webhook fast-path** — optional P5 optimization (parse signed Slack/GitHub events
  directly, still via `EventProcessor.process()`).
- **Multi-account-per-provider** — coordinated cross-layer feature if ever pursued (OAuth model +
  poll-loop + idempotency key + cursor key + webhook subscription, designed together).
