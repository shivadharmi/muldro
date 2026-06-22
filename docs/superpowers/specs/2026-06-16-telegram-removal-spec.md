# Spec: Remove the Telegram integration completely

**Status:** Draft — for approval. **Not** started. Owner decision (2026-06-16): remove Telegram
entirely as an integration.

**Owner decisions recorded:**
1. **Remove the `send_telegram` tool + `internal.send_telegram` capability entirely**, and **add a
   cleanup migration** for the orphaned `tool_definitions` row (required — `seed_defaults()` is
   upsert-only and never deletes removed tools; confirmed in `tool_registry.py`).
2. **Actively scrub historical rows** that reference the Telegram surface (migration deletes/updates
   per §4 — this is **destructive and irreversible**; see §7).
3. **Ordering:** Telegram removal lands **before** the chat-pipeline fold
   (`2026-06-16-chat-pipeline-fold-spec.md` §11) — it deletes a `process_message` caller and makes
   `build_telegram_hint` / `surface=="telegram"` dead, simplifying that fold.

This is a **feature deletion**, lower-risk than the fold, but **cross-layer** (interface, MCP
tool/capability, notifier delivery, surface registry, settings, and a destructive data migration).

---

## 1. Footprint (5 layers)

Telegram appears in ~19 backend source files + ~20 test files + 1 frontend file.

| Layer | Files | Removal shape |
|---|---|---|
| **1. Bot interface** | `interface/telegram.py` (`TelegramInterface`, `TelegramRateLimiter`, uses python-telegram-bot `Application`) | Delete the file. **Discovery first (§3):** the class is instantiated **nowhere** in `src/`; `run.py` has only a stale comment — the `--bot` path may already be disconnected. |
| **2. MCP tool + capability** | `tools/communication_server.py` (`send_telegram`, `configure(telegram_bot=…)`), `tools/catalog.py` (`InternalToolDef` `send_telegram`, `SendTelegramInput` import), `tools/schemas.py` (`SendTelegramInput`), `integrations/capabilities.py` (`internal.send_telegram` → MESSAGING) | Remove from all four. **Cleanup migration** deletes the `tool_definitions` row (§4). |
| **3. Notifier delivery** | `services/notifier.py` (`telegram:5`/hr rate limit, `_telegram_sender`, `_deliver_telegram`, `surface=="telegram"` dispatch) | Remove only the telegram branch; keep the multi-surface machinery (web/slack/email). |
| **4. Surface registry** | `services/surface_registry.py` (`TELEGRAM_TTL_SECONDS`, "telegram always active" priority, surface selection) | Remove telegram-specific TTL + priority; clear Redis telegram surface keys at deploy (§4). |
| **5. Settings / models / scatter / dead code** | `config/settings.py` (`telegram_bot_token`, `telegram_chat_id`), `orchestrator/jarvis.py` (`surface="telegram"`), `orchestrator/chat_pipeline.py` (`build_telegram_hint` → always `""`), plus single refs in `prompts.py`, `agents.py`, `workflows/daily_briefing.py`, `services/session_manager.py`, `services/workspace_resolver.py`, `tools/server.py`, model comments | Mechanical. **No column migration** — `surface`/`channel` are free-text `String(32)`, so the schema is unchanged; only data is scrubbed (§4). |

## 2. Surface/channel column inventory (migration targets)

`surface`/`channel` are free-text strings, **not** DB enums — no `ALTER TYPE`, no schema migration.
Only **data** is affected:

| Table.column | Holds | Scrub action |
|---|---|---|
| `tool_definitions` (name=`send_telegram`) | the removed tool | **DELETE** row |
| `conversations.surface` == `'telegram'` | telegram chat sessions | **DELETE** rows (+ cascade messages — verify FK in §3) |
| `messages.surface` == `'telegram'` (`conversations.py:60`) | telegram messages | **DELETE** rows (directly, if not FK-cascaded) |
| `notifications.channel` == `'telegram'` (`models/notifications.py:20`) | undeliverable notifications | **DELETE** rows |
| `users.surface` == `'telegram'` (`models/users.py:87`, default `'web'`) | a user's preferred surface | **UPDATE → `'web'`** — **never delete the user row** |
| Redis `surface_registry` telegram keys | live surface presence | clear at deploy (runtime, not SQL); TTL'd so they also expire |

> `ui_state.surface_id` / `surface_type` are the A2UI component layer — a different concept, **not**
> in scope.

## 3. Phase 0 — discovery (resolve before deleting)

1. **Bot entry point.** Find how/whether `TelegramInterface` is started. `run.py` shows only a comment;
   no `src/` instantiation. Confirm whether `--bot` is wired at all (it may be dead already, making
   §5.1 trivial) or started via a path the grep missed.
2. **FK cascade.** Confirm whether deleting a `conversations` row cascades to `messages`. If yes, the
   migration deletes parents only; if no, delete both explicitly (children first).
3. **Registry validation.** Confirm `validate_registry()` won't fail at boot for a capability
   (`internal.send_telegram`) that's removed from the catalog but still referenced by a stale row
   before the migration runs (ordering: migration in the same release as the code removal).

## 4. The cleanup migration (Alembic, its own commit, destructive)

A single Alembic revision, `upgrade()` only meaningful (no `downgrade` data restore — the data is
gone):

```
DELETE FROM tool_definitions WHERE name = 'send_telegram';
DELETE FROM messages       WHERE surface = 'telegram';   -- or rely on FK cascade
DELETE FROM conversations  WHERE surface = 'telegram';
DELETE FROM notifications  WHERE channel = 'telegram';
UPDATE users SET surface = 'web' WHERE surface = 'telegram';
```

Plus a one-shot runtime step (deploy script, not SQL) to clear Redis `surface_registry` telegram keys.

**This permanently deletes user conversation history and notifications tied to Telegram.** Acceptable
per owner decision #2 (pre-release product, surface being retired), but it is its own reviewed commit,
separate from the code-removal commits, and tested on a DB copy first.

## 5. Removal sequence (leaf-first; each step independently testable + revertable)

1. **Interface** — delete `telegram.py` + its (discovered) startup wiring; drop `--bot` flag/docs.
2. **Tool + capability** — remove `send_telegram` from `communication_server.py`/`catalog.py`/
   `schemas.py` and `internal.send_telegram` from `capabilities.py`. Update the
   registration-integrity tests' expected tool/capability set.
3. **Delivery** — strip the telegram branch from `notifier.py` + `surface_registry.py`.
4. **Settings + scatter + dead code** — drop `telegram_bot_token`/`telegram_chat_id`,
   `surface="telegram"` literals, and `build_telegram_hint` (delete the function + its two call
   sites in `jarvis.py`, which become `telegram_hint = ""` → inline-removable).
5. **Migration** (§4) — separate destructive commit.
6. **Tests** — delete telegram-only test files; update mixed ones (the ~20 referencing files).
7. **Frontend** — the single reference.

## 6. Test strategy

- **Registration-integrity test** (the `intelligence`/communication tool-name snapshot) updates to the
  new tool set minus `send_telegram` — this is the canary that proves the tool is fully gone.
- **Notifier tests** — drop telegram rate-limit/delivery cases; assert telegram is no longer a valid
  delivery surface.
- **Migration test** — on a seeded copy: assert telegram rows are gone, `users.surface` telegram→web,
  and non-telegram rows untouched.
- Full non-e2e suite green after each phase.

## 7. Risks & rollback

- **Destructive migration (§4).** Irreversible data loss by design. Mitigation: separate reviewed
  commit; dry-run row counts on a copy; the code removal can ship a release before the migration if
  staging confidence is wanted (the orphan rows are harmless until then).
- **Boot-time registry validation.** A removed capability still referenced by a stale row could trip
  `validate_registry()`. Mitigation: ship migration + code removal in the same release; `JARVIS_SKIP_
  REGISTRY_VALIDATION` is the escape hatch.
- **Hidden bot consumers.** If `--bot` is wired somewhere the grep missed, deleting the interface
  breaks startup. Mitigation: Phase 0 discovery resolves this first.
- **Rollback:** code-removal phases revert cleanly; the migration does **not** (data is gone) — which
  is why it's last and separately gated.

## 8. Interaction with the chat-pipeline fold

Land this first. It removes one batch caller (4 remain: WS default-dispatch, WS execute-insight,
scheduler meeting_prep ×3 → wait, meeting_prep is 3 call sites = batch keeps **5** callers total) and
deletes `build_telegram_hint` + the `surface=="telegram"` branch, so the fold's §5 #1 (intentional
conversational-vs-structured prompt split) no longer carries a Telegram-specific length hint.

## 9. Effort

M. Mostly mechanical deletion; the genuine-thought parts are Phase 0 discovery (bot entry, FK
cascade), the destructive migration, and updating ~20 test files. Recommend the plan-first cadence:
Phase 0 → approve → execute leaf-first → migration last.
