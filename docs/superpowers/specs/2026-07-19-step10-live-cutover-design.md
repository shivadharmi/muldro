# Step-10 — Live Cutover Design (permission-model, all-three-deep, staging-validated)

> **Status:** DESIGN (2026-07-19), brainstormed + grounded (4 verification scouts, verify-don't-trust).
> This is **Step 10** of the first-principles rebuild — the ONLY live/irreversible step. It supersedes
> the SHAPE of the pre-pivot `docs/superpowers/plans/2026-07-10-step10d-coordinated-live-cutover.md`
> (its Part-A chat tasks were superseded by the Session-2 permission-model pivot; its Part-B R0→R6
> runbook skeleton survives). On branch `rebuild/first-principles` (321 commits ahead of an untouched
> `main`, NEVER pushed). Implement via superpowers:writing-plans → subagent-driven-development.

## 1. What this is

Activate the deep runtime + chat permission model in production, across **all three surfaces**
(chat, perception, autonomous), validated by a **local full-stack end-to-end test on deep before
merge** — NOT by in-prod shadow-compare. Direct flip. Every irreversible gate (push, merge, prod
deploy) is a separate STOP-and-ASK sign-off.

**Explicitly dropped from the pre-pivot plan (user decision 2026-07-19):**
- Shadow-compare rework, divergence comparison, and the multi-week shadow-driven clean-week holds.
  Rationale: shadow-compare's job was catching deep-vs-legacy regressions *in prod without a human*.
  It is replaced by **direct validation in a local full-stack environment** (a human drives the real
  flows on deep before prod sees them). It is also **misaligned** with the permission-model chat path
  (`ShadowRunner.maybe_run_shadow` keys off static `settings.runtime`, ignores
  `effective_runtime("chat")`/`deep_single_lead`/`permission_mode`; authoritative-side `write_intents`
  is a stubbed `frozenset()`), so reworking it would itself be a build — not worth it when the human-
  in-the-loop permission gate (`ask` confirms every write, `auto` confirms risky writes) is the real
  safety net for the interactive path.

## 2. Verified prerequisite state (verify-don't-trust, by symbol)

MEMORY's "10A/10B/10C DONE" is **largely accurate**. Confirmed wired (file:line in the scout maps):

| Prerequisite | State |
|---|---|
| Chat approval round-trip (ask/auto interrupt→approve→resume) | ✅ Fully wired end-to-end. `streamChat` sends `permission_mode` (P3), `permission_gate.interrupt()` → `approval_needed` SSE → in-chat `InlineApprovalCard` → `POST /v1/jarvis/chat/resume` → `resume_deep_lead` (fail-closed gate re-install + atomic CAS flip). `/approve`+`/reject` 409 chat approvals. |
| Durable `AsyncPostgresSaver` checkpointer (API) | ✅ Prod-ready, gated on `settings.runtime=="deep"` **at boot** (`app.py:76`), real psycopg3 pool. |
| `capability_scope` middleware | ✅ Live, fail-closed RAISE (`agent_builder.py:120`). |
| Bypass entitlement + per-ws default + tenant isolation | ✅ Enforced; `thread_id=c:{ws}:{ULID}` collision-free + resume ws-assertion (10A closed the spec's "Step-10 BLOCKING substrate fail-open"). Resume IDOR-safe. |
| Effective-runtime gate (per-surface, 4-tier, fail-safe) + auto-rollback watcher + escape hatch | ✅ Exist, wired into scheduler; `runtime_gate.effective_runtime`, `RuntimeRollbackTickMixin`, `routes_admin_runtime` (rejects `target="deep"`). |
| Autonomous deep engine (10C: lease, reconcile, ledger, durable) | ✅ `run_autonomous_deep_step`, `acquire_run_lease`, `reconcile_run_from_events`; `deep_step_runner` injected at `run_health_tick.py:224` + `background_tasks_tick.py:98`; gated `effective_runtime("autonomous")=="deep"` AND injected non-None. |

**Baseline (verified at design time):** backend 3688 passed / 18 skipped, single alembic head
`1a2770a28c39`, ruff clean; frontend 124 tests + build clean; `main..HEAD` = 321 commits,
`HEAD..main` = 0 (main undiverged, merge-base = main tip `31ce42b`).

### 2.1 The gap that reopened scope: B6 unbuilt

`call_agent` (non-stream, `agent_invoker.py:1663`) has **no `runtime=="deep"` branch** — it does not
take a runtime param and unconditionally runs legacy `agent_loop`. Its callers are the **entire
perception path** (`perception_runner.py:143/277/446` — Perceiver + Librarian) and `generate_briefing`
(`jarvis.py:864`). So **perception is legacy by construction**; `JARVIS_RUNTIME=deep` is a silent no-op
for it (nothing reads `effective_runtime("perception")`). Making perception genuinely deep **requires
building B6** — this design includes that build (Phase A).

## 3. Phases

### Phase A — Build perception-deep (B6)
Add a `runtime == "deep"` branch to `call_agent` (non-stream) mirroring `call_agent_stream`'s deep
branch, so Perceiver + Librarian + `generate_briefing` run on `build_deep_agent` when
`effective_runtime("perception")=="deep"`. Dormant + byte-neutral on legacy.
- **Scope discipline:** run Librarian *as a deep agent* through the new branch. **DEFER** the
  librarian→middleware collapse, presenter-inline, and agent-row-drop (the old "B5/B7") — out of scope.
- **Ground first:** a read-only scout maps exactly what B6 touches in the **post-pivot** code
  (memory's B5/B6 framing predates the single-lead pivot; do not trust it). Confirm: does `call_agent`'s
  deep branch need `permission_mode`/gating? (Perception is read-heavy + autonomous-origin — likely
  `authorization_source=autonomous`, gated by construction, NOT the chat permission gate.)
- Subagent-driven, negative-control tested, per-task spec+security+quality review. Full gate green,
  ZERO migrations.

### Phase B — Activation config (reversible)
Enable deep via **environment variables**, NOT `settings.py` default changes (keeps the 3688-test suite
byte-neutral; rollback = env change + restart):
`JARVIS_RUNTIME=deep`, `JARVIS_DEEP_SINGLE_LEAD=true`, `JARVIS_CHAT_PLANLESS=true`. Per-ws default
`permission_mode` stays raw-default **`auto`** (bypass needs an entitlement no workspace has;
provisioning is DB-only).
- **Load-bearing verify:** confirm `run.py --worker` builds + injects the durable `AsyncPostgresSaver`
  into the invoker the scheduler uses for `run_autonomous_deep_step` (app.py does it for the API path;
  the worker path must be confirmed or autonomous durable-resume has no durable substrate).

### Phase C — R0 whole-branch holistic review (pre-merge)
Independent opus review of the **entire `main..HEAD` 0→10 diff** (not per-step) + security-reviewer on
the cross-path write-lock, checkpointer tenant-binding, and capability-scope-outer/dispatcher-inner
invariant. Re-run both gates from a clean checkout; confirm single head `1a2770a28c39` drift-free.
**SHIP with no open CRITICAL/HIGH before Phase E.**

### Phase D — Local full-stack e2e on deep (the validation gate)
Deploy the **branch** locally: docker infra (up) + `python run.py --worker` with the deep env + real
Anthropic key + `npm run dev`. Drive every flow (Playwright + HTTP/WS):
- **Chat:** `bypass` (ungated single-lead); `ask`/`auto` (interrupt → in-chat card → approve →
  **resume**; and **reject** does NOT fire the write); planless reroute; `write_todos` surfacing;
  per-ws default resolution.
- **Perception:** Perceiver + Librarian run deep (B6); entities/memories extracted; briefing fires
  once (no double).
- **Autonomous (hardest, SCRIPTED per user):** seed a multi-step plan with a write step → run through
  GraphExecutor on deep → TrustEngine gate → approve → verify **exactly-once via the idempotency
  ledger** + durable resume after interrupt + single-flight lease held.
- **Untouched:** confirm legacy default paths still behave (byte-neutral proof).

Green here is the merge gate.

### Phase E — Merge (irreversible gates #1–2)
`--no-ff` merge preserves the 321-commit history (squash would destroy the SHA archaeology the memory
references; main undiverged → zero conflicts). Push branch → open PR (CI `ci.yml` must pass) → merge.
**R1 CLAUDE.md rewrite AT merge:** the "Two execution paths" / "Deep Agents runtime" sections — chat is
now gated **at action time** by `permission_mode` (§7 of the permission-model spec supersedes "chat is
ungated by design; never add a gate"). Do NOT delete legacy-path docs (still live as rollback fallback).

### Phase F — Prod deploy + arm safety net (irreversible gate #3)
`ssh -i ~/.ssh/jarvis-key.pem ubuntu@65.2.19.78 ; sudo /opt/jarvis/infra/scripts/deploy.sh main` with
the deep env. Prod smoke on each surface (a chat turn, a perception tick, an autonomous run report the
deep runtime). **Rollback net (no shadow-compare):**
- **Escape hatch:** `POST /v1/admin/runtime/override → legacy` (admin-token gated) per surface or `all`.
- **Env rollback:** `JARVIS_RUNTIME=legacy` + restart (the durable git-free lever).
- **Auto-rollback watcher stays armed** — live `double_fire` emitter on the autonomous idempotency
  wrapper is the highest-stakes signal. (The other 4 metrics have no live emitters yet — documented
  limitation; the human + escape hatch are the primary net.)
Document the manual rollback runbook.

## 4. Deferred out of this cutover (recorded, not gaps)
- **R5 agent-row-drop (6→4 migration)** + librarian/presenter agent-collapse — keep **zero migrations**;
  stranded agent rows are harmless (governor/operator precedent). Rides a later track.
- **R6 retire escape hatch** — the hatch stays as the standing safety net.
- **Shadow-compare rework** — dropped (§1).
- **Cross-process auto-rollback watcher (Prometheus HTTP API)** + live emitters for the 4 dormant
  metrics — later ops hardening; the escape hatch + human are the net for now.

## 5. Gate ladder (each = STOP-and-ASK, never batched)
1. **Push branch** to origin.
2. **Open PR / merge to main** (after CI green + R0 SHIP).
3. **Prod deploy** (deploy.sh main with deep env).
4. Escape hatch + watcher armed (state, not a flip).

Phase-D flag flips are **local** (reversible). The prod flip *is* the deploy env (folds into gate #3).

## 6. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Autonomous unattended double-fire on deep | Idempotency ledger (exactly-once, live `double_fire` emitter) + single-flight lease + scripted e2e proof (Phase D) before any prod flip |
| Worker lacks durable checkpointer → autonomous resume broken | Phase-B load-bearing verify of `run.py --worker` checkpointer injection |
| B6 build regresses perception (extraction fidelity, double briefing) | Dormant/byte-neutral build + negative controls + Phase-D perception e2e |
| Merge integrates 321 commits | main undiverged (zero conflicts) + R0 holistic review + `--no-ff` (reversible via revert until prod-confirmed) |
| Rollback net thinner without shadow-compare | Local e2e is the pre-prod gate; escape hatch + env rollback + `double_fire` watcher in prod; interactive chat is human-confirmed in ask/auto |
| CLAUDE.md invariant drift | Rewrite AT merge (R1), doc-policy compliant |

## 7. Open items for the plan (resolve during writing-plans / grounding)
1. **B6 grounding** — exact touch-set of `call_agent` deep branch in post-pivot code; gating/auth-source
   for perception (autonomous vs chat gate); whether `generate_briefing`'s presenter call rides B6 or
   stays legacy.
2. **Worker checkpointer wiring** — does `run.py --worker` construct + inject the durable checkpointer?
3. **Autonomous e2e script** — the concrete seeded-plan scenario + exactly-once assertion (ledger row
   count) + interrupt/resume drive.
4. **CLAUDE.md rewrite content** — the exact two-execution-paths + permission-model + rollback-runbook
   edit (drafted at Phase E, not before).
5. **`generate_briefing` presenter caller** — confirm whether it blocks any later row-drop (informational;
   row-drop is deferred regardless).
