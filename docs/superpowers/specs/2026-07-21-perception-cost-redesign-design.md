# Perception Cost Redesign — Design Spec

**Date:** 2026-07-21
**Branch:** `rebuild/first-principles`
**Status:** Approved design → ready for implementation plan
**Scope:** Subsystem A (perception/extraction cost). Subsystem B (A2UI surfaces) is a **separate** spec, deliberately excluded here.

---

## 1. Problem (data-proven)

The perception pipeline extracts entities and memories from **100% of ingested email regardless of value**, at high and largely **unmeasured** token cost. Evidence from the live dev DB (86 events, 2026-07-19/20):

- **No relevance/importance gate before extraction.** The importance scorer works (marketing scored 0.05), but nothing uses it to gate. Junk was fully extracted: `product` entities = *MacBook Neo, Shotgun 650 Limited Edition, FLEXI EMI, Digital Rupee*; `merchant` = *Swiggy, Zepto, California Burrito*. The only existing gate is on the vector embedding (`importance >= 0.3`), applied *after* the expensive extraction already ran.
- **Fan-out:** 86 events → 201 entities + 706 facts + 192 relationships + 77 memories. Emails like `"Your Swiggy order was delivered on time"` (×3), `"🔥 Special offer. Ends tomorrow."`, `"Top companies are hiring on Naukri"` all paid full extraction.
- **~3 Sonnet calls per email minimum** — deterministic scoring (`event_processor.py:520`), entity extraction (`world_model.py:793`), memory extraction (`memory_service/extraction.py:261`) — all direct `complete_text`, plus a contradiction check per stored memory.
- **Double extraction.** The worker consumers (`entity_extractor`/`memory_extractor`) *and* the Librarian deep agent's `store_memory`/`update_entity` tool calls (which re-run `wm.extract_from_text`, `intelligence_server/memory.py:339`) both extract over overlapping content.
- **Opus Planner on every autonomous poll with events** (`perception_runner.py:445`, 8192 thinking) plus a second Opus cross-source synthesis call — unconditional, *before* checking whether anything is actionable. `_queue_perception_plan` discards the plan only *after* Opus has run.
- **Extraction cost is invisible.** `token_usage` holds only `trigger='chat'` rows; the direct `complete_text` extraction calls never reach the budget middleware, so the real per-email cost is unmeasured (and the `trigger` label appears hardcoded to `chat`).
- **Dead batch code.** `process_batch`/`_score_events_batch` (BATCH_SIZE=10, `event_processor.py:585`) would cut scoring ~10× but has no live caller.

## 2. Approach — "Score *is* triage"

A single **TriageService** becomes the one control point. It runs once per batch, combines cheap deterministic signals with a **batched Haiku** call, and emits per-event `{importance, urgency, category, tier, actionable}`. This replaces the per-email Sonnet scoring (making it ~10× cheaper) and its output gates all downstream cost.

```
poll → [connector captures header signals] → TriageService (rules-first, then 1 batched Haiku for the remainder)
         → tier=skip  → store normalized_event row only (no extraction, no embed)
         → tier=light → 1 compact call (deterministic parser / Haiku) → structured facts (spend/receipt ledger)
         → tier=full  → full extraction (entities + memories + relationships), single deterministic path
         → if any event.actionable → Opus Planner (+ cross-source synthesis); else skip Opus entirely
```

**Rejected alternatives:**
- *Separate pre-extraction filter* (keep Sonnet scoring, add a rules gate in front of extraction) — leaves the biggest cost untouched and adds a parallel decision layer that can drift from scoring.
- *Deterministic-only, no LLM triage* — brittle on the ambiguous middle (real invoice from a new sender; personal mail with no unsubscribe header); recall risk.
- *Agentic extraction (Librarian owns it)* — pays deep-agent overhead on a narrow high-frequency transform; harder to gate/batch/test. Note: **skills / agent features are explicitly the wrong lever here** — a skill's mechanism is injecting instructions into context, which *adds* tokens; the hot extraction path wants the smallest prompt over the fewest items. The agentic principle stays in *execution*, not *ingestion*.

## 3. Triage taxonomy (category → tier)

| Category | Tier | Actionable? |
|---|---|---|
| marketing, newsletter, social_notification, delivery_ping | **skip** | no |
| financial (card spend, receipts, subscription renewals), transactional-with-fact | **light** | no |
| personal, work_thread, security_alert, calendar_invite, direct_request | **full** | yes if urgency high / needs response |

**Deterministic pre-pass (free, runs first):** `List-Unsubscribe` / `List-Id` headers, `Precedence: bulk`, `noreply@`/`no-reply@` senders, known-ESP domains → obvious `skip`; sender in contacts / prior `interaction_count > 0` → personal signal. Haiku classifies **only the ambiguous remainder** (rules-before-models). A batch of pure marketing can therefore cost **zero** LLM calls.

**Tier behaviors:**
- **skip** — persist the `normalized_event` row only. No entity extraction, no memory extraction, no embedding. (Importance is low, so the existing `>=0.3` embed gate already agrees.)
- **light** — capture durable structured facts (founder spend/receipt ledger) via a deterministic parser for known bank/card formats, falling back to **one** combined Haiku call (entities + memories together) over title+summary. Not the full Sonnet fan-out.
- **full** — current extraction depth (entities + memories + relationships) through the **single** deterministic path (see §4).

**Actionable flag** gates the Opus Planner: derived as `tier == full AND category in {security_alert, calendar_invite, direct_request, work_thread} AND (needs_response OR urgency >= threshold)`. Marketing/transactional/financial never set `actionable`.

## 4. Components that change

- **Gmail connector** — surface header signals (`List-Unsubscribe`, `List-Id`, `Precedence`, sender-type) into `normalized_events.importance_signals` (jsonb) at poll time, so triage needs no re-fetch of the raw body.
- **`event_processor.py`** — replace per-email Sonnet `_score_event` with batched triage; revive `process_batch`/`_score_events_batch`; wire the connector poller to feed batches instead of calling `process()` per event.
- **New `TriageService`** (`src/services/triage.py` or similar) — deterministic rules + batched Haiku + taxonomy; returns `{importance, urgency, category, tier, actionable}` per event. Single control point.
- **Worker consumers** (`worker.py`) — gate `entity_extractor`/`memory_extractor` on tier; these become the **exclusive** extraction owners.
- **`perception_runner.py`** — drop the Librarian deep-agent call from routine polls (its only perception job is extraction, now owned by the worker); gate the Opus Planner **and** cross-source synthesis on presence of ≥1 `actionable` event.
- **`intelligence_server/memory.py`** — the `store_memory` tool stops re-running `wm.extract_from_text` (removes the double-extraction path).
- **Budget / instrumentation** — record a `TokenUsage` span for triage and direct extraction calls; fix the `trigger` label to distinguish `perception` from `chat`.
- **Cleanup migration** — re-triage the existing `normalized_events`; cascade-delete entities/memories/relationships whose only source is a now-`skip`-tier event, across Postgres + Qdrant + Neo4j.

## 5. Phasing

Each phase is independently valuable and independently shippable behind a flag.

1. **Triage foundation — shadow mode.** TriageService + taxonomy + connector header capture + batched Haiku scoring. Computes and **logs** the tier it would assign but does **not** gate. Validates classification against the real 86 events before anything is dropped; tune the taxonomy here.
2. **Gate extraction** on tier (skip/light/full) in the worker consumers + collapse the double path (drop Librarian from routine perception, stop `store_memory` re-extraction).
3. **Planner fast-path** — skip the Opus Planner + cross-source synthesis when no event is `actionable`.
4. **Instrumentation** — token spans for triage/extraction + correct `trigger` label; verify the savings.
5. **One-time cleanup migration** — re-triage existing events; cascade-delete skip-only-sourced derived data (dry-run first).

## 6. Testing & rollback

- **Characterization tests** capturing current extraction output before the refactor (per repo refactoring standards: structure vs behavior commit separation).
- **Triage unit tests** — deterministic rules (headers/senders) + mocked-Haiku classification over fixtures drawn from the real event titles in the DB.
- **Tier-gating tests** — `skip` → 0 extraction calls; `light` → 1; `full` → full set.
- **Planner fast-path tests** — all-skip batch → no Opus call; ≥1 actionable → Opus called once.
- **Cleanup migration** — dry-run mode reports what it *would* delete before executing; cascade verified across all three stores.
- **Rollback** — `JARVIS_PERCEPTION_TRIAGE_ENABLED` flag; off = current behavior. Shadow mode (Phase 1) is separately flag-gated so it can run in prod safely with no gating effect.

## 7. Success criteria

- Skip-tier events incur **0** extraction LLM calls (verified by span instrumentation).
- Autonomous polls with no actionable events make **0** Opus Planner calls.
- Per-email extraction cost is **measurable** in `token_usage` with a correct `trigger`.
- No new junk entities from marketing/delivery/newsletter mail on fresh polls.
- Existing junk (skip-only-sourced) removed by the cleanup migration.
- Recall preserved: full-tier (personal/work/security/calendar) mail still fully extracted — validated in shadow mode before gating.

## 8. Out of scope

- All A2UI surface issues (empty structured preview fields, step-status collapse, double-render modal) → **Subsystem B**, its own spec.
- Changes to the chat-path intent classifier (already fast-pathed correctly).
- Non-Gmail connectors' header signals (calendar has no equivalent; other connectors handled if/when they carry analogous signals).
