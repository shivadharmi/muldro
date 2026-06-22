# Polling System Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the correctness defects found in the 2026-06-20 polling audit across the perception pipeline and all polled connectors, with TDD coverage for each fixed path.

**Architecture:** Each connector's `poll()` must (a) return a `PollResult` with a correct `error_class`, (b) never advance its cursor on error, and (c) paginate to completion before advancing the cursor. The shared pipeline (`perception_policy` → `connector_poller` → scheduler tick) classifies errors into circuit-breaker thresholds. Fixes are ordered by operational risk: pipeline foundation → rate-limit misclassification (disables live sources) → pagination data-loss → dormant connectors → test backfill.

**Tech Stack:** Python 3.12, async (httpx, asyncpg/SQLAlchemy), pytest + pytest-asyncio, ruff. Connectors in `src/connectors/`, pipeline in `src/services/perception_policy.py` + `src/orchestrator/connector_poller.py` + `src/services/scheduler/perception_tick.py`.

**Source of truth:** `docs/superpowers/specs`-adjacent audit findings recorded in memory `project_polling_audit_2026_06_20.md`. Live sources = gmail, calendar, slack, github. Dormant (not scheduled, fix anyway) = notion, drive.

**Reference patterns (study before starting):**
- Correct PollResult connector: `src/connectors/github_connector.py` (structure) and `src/connectors/calendar.py` (410 recursion at lines 47-49).
- `_classify_http_status` in `src/connectors/poll_result.py:67-81` maps HTTP status → error_class.
- `error_class_to_policy_error` + sentinels in `src/connectors/poll_result.py` feed `perception_policy.classify_error`.
- Cursor-never-advance-on-error is asserted in `tests/test_poll_result.py` (Failure-propagation classes).

**Global testing note:** run a connector's tests with `source .venv/bin/activate && pytest tests/<file> -q`. Run the full suite before each phase's final commit: `pytest tests/ -q --ignore=tests/e2e`. Keep ruff clean: `ruff check src/ tests/ && ruff format src/ tests/`.

---

## Phase 1 — Pipeline foundation

### Task 1.1: Multi-process race in due-source selection

**Files:**
- Modify: `src/services/perception_policy.py` (`get_due_sources_all_users`, ~lines 204-227)
- Test: `tests/test_perception_policy.py`

The due-source query uses a plain `SELECT` + `flush`; two worker processes both pick the same row → duplicate polls/ingest. The background-task path already solved this (`tests/test_background_task_locking.py` asserts `FOR UPDATE SKIP LOCKED`).

- [ ] **Step 1: Write the failing test** in `tests/test_perception_policy.py` — `test_get_due_sources_uses_skip_locked`: build the select statement the method issues (or patch the session's `execute` to capture the compiled statement) and assert the query carries `FOR UPDATE SKIP LOCKED`. Mirror the assertion style of `tests/test_background_task_locking.py`.
- [ ] **Step 2: Run** `pytest tests/test_perception_policy.py::test_get_due_sources_uses_skip_locked -v` → expect FAIL.
- [ ] **Step 3: Implement** — add `.with_for_update(skip_locked=True)` to the due-source `select(...)` in `get_due_sources_all_users` (and `get_due_sources` if it issues the same query). Confirm the surrounding transaction commits/rolls back so locks release per tick.
- [ ] **Step 4: Run** the test → PASS. Run `pytest tests/test_perception_policy.py tests/test_scheduler.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(perception): claim due sources with FOR UPDATE SKIP LOCKED`.

### Task 1.2: Generic exceptions bucket as unknown/threshold-3

**Files:**
- Modify: `src/services/scheduler/perception_tick.py` (~line 93), `src/orchestrator/perception.py` (~line 93)
- Test: `tests/test_perception_policy.py` (or `tests/test_perception.py`)

The two outer `except Exception` handlers call `record_failure(state, str(e)[:512])`; an arbitrary exception string has no keyword → `classify_error` returns `unknown` (threshold 3). The poller paths were already hardened to fail-safe to transient; these outer handlers were missed.

- [ ] **Step 1: Write the failing test** — `test_generic_cycle_exception_is_transient`: drive `run_perception_cycle` (or the tick) so it raises a bare `RuntimeError("boom")`, and assert the resulting `record_failure` classifies as `transient` (threshold 6), not `unknown` (3). Assert via the recorded `consecutive_failures`/circuit state needing 6 to open.
- [ ] **Step 2: Run** → FAIL (opens at 3 today).
- [ ] **Step 3: Implement** — at both sites wrap the error through the transient sentinel: replace `str(e)[:512]` with `error_class_to_policy_error("transient")` (import from `src.connectors.poll_result`), or prefix the string with the transient sentinel keyword. Keep the original exception in the log `extra` for debuggability.
- [ ] **Step 4: Run** test → PASS; `pytest tests/test_perception.py tests/test_perception_policy.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(perception): generic cycle exceptions fail-safe to transient, not unknown`.

### Task 1.3: Dead `budget_multiplier` (budget-aware interval stretching never fires)

**Files:**
- Modify: `src/services/perception_policy.py` (`record_success`, `_compute_next_run` ~line 357, call sites ~255/288/334), `src/services/scheduler/perception_tick.py` (passes the multiplier)
- Test: `tests/test_perception_policy.py`

`_compute_next_run` accepts `budget_multiplier` but all call sites pass the default `1`; the scheduler computes a real multiplier and passes it only to the due-query, which drops it. Make it actually stretch intervals.

- [ ] **Step 1: Write the failing test** — `test_budget_multiplier_stretches_next_run`: call the success path with `budget_multiplier=2.0` and assert `next_run_at` is ~2× the base interval out, vs `1.0` baseline.
- [ ] **Step 2: Run** → FAIL (no stretch today).
- [ ] **Step 3: Implement** — thread `budget_multiplier` from `perception_tick` (`get_perception_interval_multiplier`) into `record_success` and on into `_compute_next_run`, where `interval = base_interval * budget_multiplier` (clamped to existing min/max + starvation ceiling). Remove any now-redundant unused param.
- [ ] **Step 4: Run** test → PASS; full `pytest tests/test_perception_policy.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(perception): apply budget_multiplier to interval stretching`.

---

## Phase 2 — Rate-limit misclassification (live sources disabling themselves)

### Task 2.1: GitHub 403 rate-limit misclassified as auth_failed

**Files:**
- Modify: `src/connectors/github_connector.py` (poll, ~lines 47-67)
- Test: `tests/test_poll_result.py` (replace `test_403_returns_auth_failed`)

`_classify_http_status` maps both 401 and 403 → `auth_failed` → `permanent` (threshold 1). GitHub returns **403 with `X-RateLimit-Remaining: 0`** (primary limit) or **`Retry-After`** (secondary) for rate limits — not auth failures. The header discrimination must live in the connector (the shared helper can't see headers).

- [ ] **Step 1: Write the failing tests** in `tests/test_poll_result.py`:
  - `test_github_403_ratelimit_is_rate_limited`: 403 response with `headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "...”}` → assert `error_class == "rate_limited"`, cursor unchanged.
  - `test_github_403_secondary_ratelimit_is_rate_limited`: 403 + `Retry-After: 60` → `rate_limited`.
  - `test_github_403_no_ratelimit_headers_is_auth_failed`: 403 with no rate headers → `auth_failed`.
  - Delete/replace the existing `test_403_returns_auth_failed` (it codifies the bug).
- [ ] **Step 2: Run** → first two FAIL.
- [ ] **Step 3: Implement** — in `github_connector.poll`, before delegating 403 to `_classify_http_status`, special-case: `if resp.status_code == 403 and (resp.headers.get("X-RateLimit-Remaining") == "0" or resp.headers.get("Retry-After")): error_class = "rate_limited"` else fall through to `_classify_http_status`. Return `PollResult(events=[], cursor=cursor, error_class=...)`.
- [ ] **Step 4: Run** the new tests → PASS; `pytest tests/test_poll_result.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(github): distinguish 403 rate-limit from auth failure`.

### Task 2.2: Slack `ok:false` errors misclassified as permanent

**Files:**
- Modify: `src/connectors/slack_connector.py` (~lines 50-61, the `conversations.list` ok-false branch)
- Test: `tests/test_poll_result.py` (new Slack ok:false cases)

Slack returns HTTP 200 with `{"ok": false, "error": "..."}`. Today only invalid_auth/not_authed/token_revoked → auth_failed; everything else (incl. `ratelimited`) → `permanent` (opens circuit at 1).

- [ ] **Step 1: Write the failing tests** — parametrized over `conversations.list` returning 200 + `{"ok": false, "error": X}`:
  - `ratelimited` → `rate_limited`
  - `token_revoked`, `invalid_auth`, `not_authed`, `account_inactive` → `auth_failed`
  - `internal_error`, `fatal_error`, `service_unavailable` → `transient`
  - unknown error string → `transient` (fail-safe)
  - assert cursor unchanged in every case.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — replace the ok:false mapping with an explicit dict: `{"ratelimited": "rate_limited", "account_inactive": "auth_failed", "token_revoked": "auth_failed", "invalid_auth": "auth_failed", "not_authed": "auth_failed", "internal_error": "transient", "fatal_error": "transient", "service_unavailable": "transient"}` with `.get(error, "transient")` default. Return `PollResult(events=[], cursor=cursor, error_class=mapped)`.
- [ ] **Step 4: Run** → PASS; `pytest tests/test_poll_result.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(slack): map ok:false errors to correct error_class (ratelimited→rate_limited)`.

### Task 2.3: Slack `conversations.history` 429/ok:false swallowed as success

**Files:**
- Modify: `src/connectors/slack_connector.py` (~lines 78-91, the per-channel history loop)
- Test: `tests/test_poll_result.py`

A non-200 (incl. 429) or `ok:false` on `conversations.history` is `continue`d → poll returns `error_class="none"` (success), so the breaker never sees the rate-limit and the cursor advances past unfetched messages.

- [ ] **Step 1: Write the failing tests**:
  - `test_slack_history_429_returns_rate_limited`: history call returns 429 → poll returns `error_class="rate_limited"`, cursor unchanged.
  - `test_slack_history_okfalse_ratelimited_not_success`: history returns 200 + `{"ok": false, "error": "ratelimited"}` → `rate_limited`, cursor unchanged.
- [ ] **Step 2: Run** → FAIL (returns success today).
- [ ] **Step 3: Implement** — in the history loop, detect 429 / `ok:false ratelimited` / auth errors and **return** a failing `PollResult(events=collected_so_far_OR_empty, cursor=cursor, error_class=...)` rather than `continue`. Decision: on a rate-limit mid-poll, return `rate_limited` with the **incoming** cursor (do not advance) so the whole poll retries; do not partially advance.
- [ ] **Step 4: Run** → PASS; `pytest tests/test_poll_result.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(slack): surface conversations.history rate-limit instead of silent success`.

---

## Phase 3 — Pagination & cursor correctness (data loss on live sources)

### Task 3.1: Gmail pagination + first-sync + profile-fail

**Files:**
- Modify: `src/connectors/gmail.py` (incremental history path ~45-64; initial full-sync ~83-117)
- Test: `tests/test_gmail_connector.py`

`history.list` reads only page 1 (drops page-2+ then advances cursor); first-sync caps at 25 with no pagination; a `getProfile` failure after a successful `messages.list` leaves `new_cursor=None` but returns success.

- [ ] **Step 1: Write the failing tests**:
  - `test_history_list_paginates`: history responses across two pages (`nextPageToken` then final) → all `messagesAdded` ingested before cursor advances.
  - `test_initial_sync_paginates_bounded`: `messages.list` returns a `nextPageToken`; assert the connector follows it up to a bounded page cap (define `MAX_BACKFILL_PAGES`, e.g. 4) and does not silently drop within that bound.
  - `test_profile_failure_after_list_returns_transient`: `messages.list` 200 but `getProfile` non-200 → `error_class="transient"`, cursor unchanged (NOT success-with-null-cursor).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**:
  - Wrap the `history.list` call in a `while` loop following `nextPageToken`, accumulating `messagesAdded`, then advance the cursor to the final `historyId`.
  - Wrap the initial `messages.list` similarly with a `MAX_BACKFILL_PAGES` bound; `log()` when the bound truncates.
  - On `getProfile` failure after a successful list, `return PollResult(events=events, cursor=cursor, error_class="transient")`.
- [ ] **Step 4: Run** `pytest tests/test_gmail_connector.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(gmail): paginate history + bounded first-sync; profile-fail is transient`.

### Task 3.2: Calendar pagination → syncToken on last page only

**Files:**
- Modify: `src/connectors/calendar.py` (poll ~40-58)
- Test: rebuild `tests/test_calendar_connector.py` (deleted) — see Task 5.1; add the pagination test here as the failing driver.

Single GET; `nextSyncToken` only appears on the final page. Multi-page results drop events AND lose the sync token → cursor falls back to the old value → stuck re-fetching the same page forever.

- [ ] **Step 1: Write the failing test** (create `tests/test_calendar_connector.py`): `test_calendar_paginates_and_takes_synctoken_from_last_page`: page 1 returns `items=[...]` + `nextPageToken` (no `nextSyncToken`); page 2 returns more `items` + `nextSyncToken` (no `nextPageToken`). Assert all items collected and `new_cursor == page2.nextSyncToken`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — loop following `nextPageToken`: first request carries `syncToken` (or `timeMin` on first sync); subsequent requests in the same sync carry **only** `pageToken` (NOT `syncToken`/`timeMin` — the API rejects combining them). Accumulate `items`; take `nextSyncToken` from the page that omits `nextPageToken`. Keep the existing 410→full-resync behavior.
- [ ] **Step 4: Run** `pytest tests/test_calendar_connector.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(calendar): paginate events and take nextSyncToken from the final page`.

### Task 3.3: GitHub pagination + cursor = max(updated_at)

**Files:**
- Modify: `src/connectors/github_connector.py` (poll ~36-55)
- Test: `tests/test_github_connector.py` (create) or `tests/test_poll_result.py`

Reads only page 1 (Link `rel=next` ignored); advances cursor to wall-clock `now()` → notifications updated between the last item and `now()` are skipped forever.

- [ ] **Step 1: Write the failing tests**:
  - `test_github_follows_link_pagination`: response 1 has `Link: <...&page=2>; rel="next"`, response 2 no next → all notifications across pages ingested.
  - `test_github_cursor_is_max_updated_at_not_now`: notifications with known `updated_at` values → assert `new_cursor == max(updated_at)` (parsed), not `datetime.now()`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — follow the `Link` header `rel=next` until exhausted, accumulating notifications; set `new_cursor` to the max `updated_at` of returned notifications (fallback: response `Date` header) instead of `now()`. Keep `since` inclusive; rely on `EventProcessor` dedup (entity_id = notification id) for the boundary item.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `fix(github): paginate notifications via Link header; cursor = max updated_at`.

### Task 3.4: Slack pagination + per-channel cursor

**Files:**
- Modify: `src/connectors/slack_connector.py` (channel list ~33-65; history loop ~67-99; cursor handling)
- Possibly Modify: `src/models/observation_cursor.py` (cursor_value already `String(512)`; per-channel map needs JSON — store a JSON string, no schema change if it fits, else widen to `Text`)
- Test: `tests/test_slack_connector.py` (create)

Single global `ts` cursor applied as `oldest` to every channel → chatty channel's watermark skips quiet channels' messages; `conversations.list` capped at 20/then-10 with no pagination; `conversations.history` `limit:10` with `next_cursor` never followed.

- [ ] **Step 1: Write the failing tests**:
  - `test_slack_history_paginates`: a channel returns 10 messages + `response_metadata.next_cursor`, then a final page → all messages ingested.
  - `test_slack_channel_list_paginates`: `conversations.list` returns `next_cursor` → all channels polled (not capped at 10).
  - `test_slack_per_channel_cursor_isolation`: two channels (one chatty, one quiet) → quiet channel's older messages are NOT skipped by the chatty channel's watermark; assert the persisted cursor is a per-channel map.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**:
  - Change `cursor_value` to a JSON object `{channel_id: last_ts}` (serialize/deserialize in the connector; tolerate a legacy bare-string cursor by treating it as empty/migrating on first poll).
  - Paginate `conversations.list` (follow `response_metadata.next_cursor`) and `conversations.history` per channel (follow `next_cursor`), using each channel's own `oldest` watermark.
  - Advance each channel's watermark to its own max `ts` only after that channel's pages are drained.
- [ ] **Step 4: Run** `pytest tests/test_slack_connector.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(slack): paginate channels+history; per-channel cursor watermarks`.

---

## Phase 4 — Dormant connectors → PollResult contract + correctness

### Task 4.1: Notion → PollResult + pagination + sort + dedup

**Files:**
- Modify: `src/connectors/notion_connector.py` (whole poll), `src/services/event_processor.py` (idempotency key for notion, ~lines 56-66)
- Test: `tests/test_notion_connector.py` (create)

Returns a bare tuple (errors swallowed as success), no pagination, sorts descending (wrong for a watermark), repeat-edits deduped into one event.

- [ ] **Step 1: Write the failing tests**:
  - `test_notion_returns_pollresult`: success returns a `PollResult` instance (not a tuple).
  - `test_notion_429_is_rate_limited` / `test_notion_401_is_auth_failed`: error responses → correct `error_class`, cursor unchanged.
  - `test_notion_paginates_has_more`: two-page `has_more`/`next_cursor` response → all pages collected.
  - `test_notion_repeat_edits_distinct_events`: same `page_id` edited twice (distinct `last_edited_time`) → two distinct idempotency keys.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**:
  - Return `PollResult` on success/failure; map non-200 via `_classify_http_status` and the Notion error-body `code` (`rate_limited`→rate_limited, `unauthorized`/`restricted_resource`→auth_failed); exceptions→transient; honor 429.
  - Sort `last_edited_time` **ascending** and paginate `has_more`/`next_cursor` to completion; advance cursor to max processed `last_edited_time`.
  - In `make_idempotency_key`, add a notion branch incorporating `last_edited_time` (analogous to the gmail `message_id` branch) so distinct edits are distinct events.
- [ ] **Step 4: Run** `pytest tests/test_notion_connector.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(notion): PollResult contract, pagination, ascending sort, per-edit dedup`.

### Task 4.2: Drive → PollResult + 410 reinit + pagination + removed files

**Files:**
- Modify: `src/connectors/drive_connector.py` (whole poll)
- Test: `tests/test_drive_connector.py` (create)

Bare tuple (errors swallowed); no HTTP 410 handling (expired pageToken = permanent silent stall); no pagination; removed/trashed files dropped.

- [ ] **Step 1: Write the failing tests**:
  - `test_drive_returns_pollresult`.
  - `test_drive_410_reinitializes_page_token`: incremental `changes.list` returns 410 → connector re-fetches `startPageToken` and recovers (mirrors calendar 410).
  - `test_drive_paginates_changes`: two-page changes (`nextPageToken` then `newStartPageToken`) → all changes collected; cursor = final `newStartPageToken`.
  - `test_drive_removed_file_emits_event`: a change with `removed=true` (no `file`) → a `file_removed` RawEvent with the right `entity_id`.
  - error-class cases (401→auth, 429→rate_limited, 5xx→transient, exception→transient), cursor unchanged.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**:
  - Return `PollResult`; on 410 re-init via `changes.getStartPageToken` (or recurse with `cursor=None`); map other statuses via `_classify_http_status`.
  - Add `includeRemoved=true`; paginate `nextPageToken` until the page carrying `newStartPageToken`; advance cursor only to that final token.
  - Emit `file_removed` events for `change.removed`/`file.trashed`.
- [ ] **Step 4: Run** `pytest tests/test_drive_connector.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(drive): PollResult contract, 410 reinit, pagination, removed-file events`.

> After Task 4.2, remove the now-resolved `# TODO: migrate to PollResult (LSP violation)` comments in both connectors and the legacy-2-tuple branch note in `integration_manager.py` / `connector_poller.py` if both dormant connectors now return PollResult. (Verify no other caller still depends on the tuple branch before deleting it.)

---

## Phase 5 — Test backfill (happy-path + normalization coverage)

These add coverage not tied to a specific Phase 1-4 bug fix. Each is test-only (no source change unless a test surfaces a new bug — if so, fix under systematic-debugging and note it).

### Task 5.1: Calendar connector test module
**Files:** `tests/test_calendar_connector.py` (extend from Task 3.2)
- [ ] Add: happy-path 200 normalization (entity_id, event_type map), cancelled event (`status=cancelled`, no start/end), all-day event tz-aware `occurred_at`, empty calendar, 410-resync depth-1, error-class mapping for 401/403/429/500, first-poll (`timeMin`, no `syncToken`). Commit `test(calendar): full connector coverage`.

### Task 5.2: Notion connector tests
**Files:** `tests/test_notion_connector.py` (extend from Task 4.1)
- [ ] Add: success normalization (`page_created` vs `page_updated`), empty workspace, `last_edited_time` boundary (equal-timestamp edits), malformed/empty `id` guard. Commit `test(notion): full connector coverage`.

### Task 5.3: Drive connector tests
**Files:** `tests/test_drive_connector.py` (extend from Task 4.2)
- [ ] Add: first-poll seeding (`files.list` + `startPageToken`), startPageToken-failure path, empty incremental poll, normalization (`source="drive"`, entity_id, actor). Commit `test(drive): full connector coverage`.

### Task 5.4: Pipeline behavior tests
**Files:** `tests/test_perception_policy.py`, `tests/test_perception.py`
- [ ] Add: half-open→success→closed full recovery cycle and half-open→failure→re-open; rate_limited/auth_failed sentinel round-trip through `error_class_to_policy_error` to the right thresholds; cursor-non-advance at the `run_perception_cycle` level on poll_error; a guard test that `web_search`/`whatsapp` are absent from the registry/poll set (post-removal). Commit `test(perception): circuit recovery, sentinel round-trips, registry guards`.

---

## Phase 6 — Lower-priority normalization (optional; defer if time-boxed)

Each is small and independent; do as capacity allows.

- [ ] **Calendar all-day tz:** normalize `start.date`-only events to UTC-aware `occurred_at` (`calendar.py` ~188-205). Test + commit.
- [ ] **Slack edits/deletions:** handle `subtype=message_changed`/`message_deleted` (re-ingest edits, mark deletions) (`slack_connector.py` ~227-260). Test + commit.
- [ ] **Slack threads:** optionally fetch `conversations.replies` so thread replies are perceived (currently missed). scope and size before committing — may be larger.
- [ ] **GitHub normalization tests:** `_normalize_notification` shape + event_type mapping. Test-only.
- [ ] **Multi-account note:** `source_account_id` is hardcoded `<source>_primary` in every connector; leave as-is but add a single doc comment in `base.py` noting multi-account is unsupported and dedup keys omit account.

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** every audit finding maps to a task — pipeline race (1.1), generic-exception (1.2), budget_multiplier (1.3); slack/github rate-limit (2.1-2.3); pagination for all 4 live + cursor fixes (3.1-3.4); notion/drive PollResult+correctness (4.1-4.2); test backfill (5.x); minors (6). ✓
- **Placeholder scan:** no TBD/TODO-as-spec; each task names files, the concrete bug, the fix, and named tests with assertions. ✓
- **Type consistency:** all tasks use the existing `PollResult(events, cursor, error_class)` contract and `error_class` string values (`transient`/`permanent`/`rate_limited`/`auth_failed`/`none`) consistently. ✓
- **Ordering:** pipeline first (shared), then risk-ranked. Each task is independently committable; later connector tasks don't depend on earlier ones except where noted (3.2↔5.1 share the test file). ✓
