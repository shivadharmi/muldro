# Activation-Gate Ledger — Deep-Runtime Cutover (Step-7 close, 2026-07-08)

> **Purpose.** The first-principles rebuild deferred the autonomous/deep runtime cutover to LAST
> (spec Step 10), so a large body of **dormant-but-proven** machinery has accumulated behind
> unflipped flags across Steps 0–7. This ledger collects every carried gate into ONE authoritative,
> categorized checklist so the cutover is a finite, auditable list — not archaeology across ten
> memory blocks. Produced at Step-7 close (7A+7B1+7B2+7C shipped) per the 2026-07-08 decision.

> **CRITICAL FRAMING — nothing here is a LIVE risk today.** The default runtime is `legacy`
> (`JARVIS_RUNTIME=legacy`), and the deep path is further gated by off-by-default flags
> (`deep_inline_format`, `deep_delegates_enabled`, `deep_readback_enabled`). There is **no live
> gated producer** on the deep path. Every item below is either (a) dormant deep machinery behind a
> not-yet-flipped flag, or (b) a safe-not-broken quality carry. The only historically-LIVE
> safety gaps — the session-poisoning SAVEPOINTs on the autonomous path — were **already closed**
> (Phase-1 CF-1/CF-2, 7A P0). "Dormant-but-proven" (tests + a documented activation path) is NOT
> the "dead-wiring disease" this rebuild set out to cure.

> **How to use.** Category A (SECURITY/SAFETY) MUST land before flipping the deep runtime live.
> Category B (CUTOVER-MECHANICAL) is the coordinated Step-10 flip, done together with shadow-compare
> + auto-rollback. Category C (QUALITY) is opportunistic — safe to carry, land when convenient.
> Each item cites its originating step for traceability. This is a living doc: update as items land.

---

## Category A — SECURITY / SAFETY (land BEFORE flipping the deep runtime live)

These, if missed at activation, become actual vulnerabilities or correctness holes on the live deep
runtime.

> **STEP 10A LANDED — 2026-07-10** (subagent-driven, TDD, 13 commits `b431487..97beba7` on
> `rebuild/first-principles`, NOT pushed; base `5919530`). All Category-A items below are DONE except
> A2's real `read_fn` (invariant LOCKED in 10A; the real per-connector seam rides B4). **Byte-neutral on
> the live `legacy` path** — final holistic review verified: default `JARVIS_RUNTIME=legacy` + all
> `deep_*` off + new `write_lock_require_redis=False` → NO live behavior change, incl. the LIVE A3
> autonomous path. **ZERO migrations** (single head `1a2770a28c39`), ruff clean, 3329 non-e2e passed /
> 18 skipped. Every load-bearing guard has a mutation-proven negative control (18/18 RED→GREEN,
> holistic-reproduced). Reviews: A6 + A1 = independent opus + security (SAFE 6/6 each); A3 = SHIP; A4 =
> SHIP-WITH-FIXES (I-1 applied); the automated commit-review caught the A1 static-delimiter escape →
> hardened to a per-request nonce. Per-gate activation carries recorded below (for 10B–10D / B3–B4). NO
> CLAUDE.md edit (dormant deep internals; the two-execution-paths doc rewrite is 10D at merge).

- [x] **A1 — Critique prompt-injection hardening.** *(7B2 gate e.)* **DONE `9c47ca4` + `0641bd1`.** Added an
  untrusted-data / never-obey clause to `_CRITIQUE_SYSTEM_PROMPT_TEMPLATE` + fenced the summary in a
  **per-request random-nonce** delimiter (`<delegate_summary_{token}>`, `secrets.token_hex(8)`) — the
  static tag was escapable (automated-review finding). Security review SAFE (6/6): break-out is
  impossible-by-construction (summary materialized before the secret nonce), parse path fails CLOSED on
  the write branch. **B3 carries:** (R1) the critique is a probabilistic Haiku judge — before any
  *write*-delegate ships, pair it with deterministic checks (TrustEngine/RiskAssessor), don't make it the
  sole gate; (R2) the verdict cache is global-not-workspace-scoped (`sha256(summary)[:24]`; value
  content-derived, no tenant data, failed verdicts never cached) — optionally tenant-scope the key.
  File: `src/deep_runtime/middleware/governor_delegate_critique.py`.
- [ ] **A2 — Real per-connector read-back `read_fn` + the unservable denylist.** *(7C gate a / Step-3 CF#1.)*
  **Invariant LOCKED in 10A `97beba7`** (test-only `test_readback_readfn_none_invariant.py`: `read_fn=None`
  → UNVERIFIED, never CONTRADICTED, for every registered post-condition cap + the middleware wiring;
  mutation-proven). **Real `read_fn` still → B4:** wire it through the dispatcher's `execute_tool` **and
  reproduce `_READBACK_UNSERVABLE_CAPABILITIES`** (`step_runner.py:38` = `{"calendar.get"}`) so the lone
  mock-only post-condition `calendar.create` (backed by `query_freebusy`) cannot false-CONTRADICT.
- [x] **A3 — Deep write-lock fail-open under a Redis outage.** *(6C #2.)* **DONE `2781735` + `210a4a5`.**
  Hardened with an opt-in `write_lock_require_redis` flag (default False = today's fail-OPEN): when True a
  WRITE is REFUSED (canonical blocked shape) instead of executing unlocked when Redis is unavailable, on
  BOTH the deep middleware and the autonomous `step_runner`. The outer gate now builds the wrapper under
  `require_redis` even with a None redis client (`_should_build_write_lock_wrapper`) so the fail-closed
  branch is REACHABLE on the autonomous path (else dead code). Review SHIP; byte-neutral when off
  (LIVE-path verified). **Residual (documented, prod-unreachable):** if BOTH the redis client AND
  tool_registry are None, no wrapper is built (can't classify) → unlocked; a *runtime* Redis outage
  already fails-closed via the uncaught `redis.set` connection error. Files: `write_lock.py`,
  `step_runner.py`, `settings.py`, `services/contention.py`.
- [x] **A4 — `_build_delegate_subagents` error-path hardening.** *(7B2 gate d.)* **DONE `0b5ded5` + `02577bb`.**
  All raw `MODEL_TIER_IDS[...]` subscripts → `.get(tier, MODEL_TIER_IDS["sonnet"])` (a MODEL ID, not the
  tier NAME — review I-1 fix) at all 4 sites (incl. `model_factory.py:42` lead build); delegate build body
  wrapped in `try/except → return []`. Review SHIP-WITH-FIXES (I-1 applied). Accepted (M-2/M-3): the broad
  `except Exception` can mask a programming error as degrade (logged `exc_info=True`); docstring aligned.
  File: `src/orchestrator/agent_invoker.py`, `src/deep_runtime/model_factory.py`.
- [x] **A5 — GP-disable process-global scope re-audit.** *(7B2 gate c.)* **DONE `c0b4dd6`.** Audited +
  ACCEPTED sign-off in the `disable_general_purpose_subagent` docstring (key-scoped to one model id; only
  drops the ambient GP `task` child; dormant + idempotent). Added a `general_purpose_disabled`
  context-manager with **restore-not-pop** teardown (captures the prior profile so a pre-existing built-in
  survives the undo — the 7B2 pop-poisons lesson). File: `src/deep_runtime/delegates.py`.
- [x] **A6 — Multi-tenant checkpointer/Store workspace-binding.** *(Step-1 / Step-10 blocking.)* **DONE
  `800d293` + `751cac2`.** New `thread_identity.py`: `make_thread_id(ws)="c:{ws}:{ulid}"` (58 chars, fits
  `Approval.thread_id` String(64)) + `workspace_of_thread_id` (fail-closed None on missing/colonless/None —
  never raises, for the 10C nullable-column reuse). Chat mint bound + `resume_deep_turn` asserts the
  embedded ws (defense-in-depth on the `approval.workspace_id` IDOR guard, same no-leak envelope). Security
  review SAFE (6/6). **10C carries:** (1) re-apply the `workspace_of_thread_id != workspace_id` assertion at
  the autonomous GraphExecutor resume seam — the resume-side guard lives ONLY in `resume_deep_turn`; (2)
  autonomous approvals historically carry NULL `thread_id` → mint them with `make_thread_id` from the
  scheduler/`resolve_workspace_id` auth context; (3) the 58/64 budget is exact — re-verify if 10C adds a
  further prefix; old `chat_`-format thread_ids are fail-closed refused at the 10D flip (safe — never live).
  There is NO LangGraph `Store` in the codebase; the only binding surface is `thread_id`.
- [x] **A7 — Contended-blocked shape reconciliation.** *(6C #3.)* **DONE `535e4b5`.** New
  `src/services/contention.py` (NOT `deep_runtime/` — that would be an upward services→deep_runtime dep):
  pure `blocked_body(error)` + `CONTENDED_MESSAGE` / `WRITE_LOCK_UNAVAILABLE_MESSAGE`. Deep wraps it in a
  `ToolMessage(status="error")`, autonomous returns the bare dict — both from one source (the contended
  AND the A3 fail-closed shape). Byte-identical strings (pure refactor). Parity locked by two complementary
  tests (paths-agree + value-pinned).
- [x] **NEW-1 — Checkpoint-reaper decided-approval sweep is workspace-agnostic.** *(Step-10A grounding.)*
  **DONE `04a74c7`.** `sweep_decided_approval_checkpoints` gained an optional `workspace_id` filter
  (default None = today's global sweep, byte-neutral for the sole scheduler caller) + an A6-leveraged
  consistency guard: never reap a thread whose embedded ws disagrees with its approval's `workspace_id`.
  Preserves the `decided − pending` per-thread guard. File: `src/deep_runtime/checkpoint_reaper.py`.
- [x] **NEW-2 — Assert `capability_scope` is always installed (build-time, fail-closed).** *(Step-10A
  grounding.)* **DONE `b431487` (test-only).** The build-time guard already existed at
  `agent_builder.py:114-124`; 10A regression-locked it with a mutation-proving test + added the
  guard-POSITION delta (asserts the scope guard is OUTERMOST — index 0 in the `create_deep_agent`
  middleware list; verified langchain-1.3.10 "first = outermost"; teeth via a reorder mutation).

## Category B — CUTOVER-MECHANICAL (the coordinated Step-10 flip)

Done together, behind shadow-compare (READ-ONLY outputs only, never shadow-run writes) + auto-rollback
gates + a 1-production-clean-week escape hatch (spec Step 10).

> **STEP 10B BUILT — 2026-07-10** (subagent-driven + TDD, 10 commits `10ac80b..cd5e6ad` on
> `rebuild/first-principles`, off `main`, NOT pushed/merged; base `360cfc2`). The cutover-control-plane
> SCAFFOLDING named in this Category-B header is now built + DORMANT — **NO B-item below is checked off**
> (10B builds the "behind shadow-compare + auto-rollback + escape hatch" machinery; the flips themselves
> are 10D). Byte-neutral on the live `legacy` path, **ZERO migrations** (head `1a2770a28c39` unchanged),
> ruff clean, **3388 passed / 18 skipped** (18 = optional `fakeredis`-gated tests, NOT infra-down; = 3406/0
> with fakeredis present). Holistic **opus = SHIP** (re-ran gate + independently reproduced ALL 11
> load-bearing negative controls RED→restore→GREEN; write-suppression airtight + gate fail-safe + admin-gate
> fail-closed all SAFE-with-evidence; tree clean). What's built:
> - **5 rollback metrics EXIST** (`metrics_service.py`: `double_fire`[surface,kind] + `verification_false_negative` + `double_prompt` + `ungated_perception_write` + `shadow_divergence`[kind]); only `double_fire` has a LIVE emitter today (autonomous idempotency wrapper `:81/:84`, byte-neutral counter inc); the other 4 are defined-dormant (emitters ride 10C/10D).
> - **Shadow-compare harness PROVEN + wired at CHAT (default OFF).** Phase-0 offline spike verdict = **PROVEN** (`spikes/step10b_shadow/`, throwaway — write NEVER reaches real dispatch; suppression composes with a realistic loop). `ShadowToolExecutor` (write-suppress fail-closed on unknown cap) + `DivergenceComparator` (pure) + `ShadowRunner.maybe_run_shadow` (sampled+async+isolated+throwaway-session) + additive `run_shadow_turn` + additive `_build_deep_agent_for(execute_tool=)` injection seam (the LOAD-BEARING teeth: a deep-shadow write via the REAL build path records 0 real dispatches). `shadow_sample_rate=0.0` default → NO spawn (byte-neutral). Autonomous shadow = 10C adds a caller; harness kept runtime-agnostic. Authoritative-side `write_intents` capture deferred to 10D. Comparator does NOT diff `SurfaceUpdate` phases (B12 boundary).
> - **Per-surface effective-runtime GATE live-resolvable.** `runtime_gate.effective_runtime(surface)` resolves manual-override → auto-breaker → enable-key → static `settings.runtime`; `runtime_breaker.py` owns the Redis keyspace (`jarvis:runtime:{override,breaker,enabled}:{surface}`). Chat seam (`call_agent_stream`) now resolves runtime ONCE via the gate (jit/metric/fork). **FAIL-SAFE:** the only path to `"deep"` is a successful enable-key read; any Redis error / `redis=None` → static `legacy`, never accidental `deep`. Resolve-once memo. **Kill-switch storage = ZERO-migration Redis override** (RESOLVED per this ledger; a Redis outage falls back to static `legacy` = fail-safe; no DB table → preserves "B7 is the ONLY Step-10 migration").
> - **One-directional auto-rollback WATCHER armed (dormant while all-legacy).** `RuntimeRollbackTickMixin._tick_runtime_rollback` (scheduler, ~30s) watches only surfaces CURRENTLY `deep` (`if rt != "deep": continue` = the byte-neutral gate) → on a mapped-signal delta breach trips ONLY that surface's breaker to `legacy` (never writes `enabled=deep`, never `clear` — re-enable is MANUAL, anti-flap). Signal→surface: double_fire→autonomous, verification_fn→flipped, double_prompt→chat, ungated_perception_write→perception+autonomous, shadow_divergence→all-deep. **LIMITATION (10D/ops hardening):** `prometheus_client` counters are process-LOCAL → the in-process watcher only sees its own process's counts (prod watcher querying the Prometheus HTTP API is 10D).
> - **Escape hatch PRESENT (admin-gated, legacy-only).** `POST/DELETE /v1/admin/runtime/override` (`routes_admin_runtime.py`) + `runtime_breaker.set/clear_manual_override` (surface or `"all"`). Security-hardened: override restricted to `"legacy"` ONLY (`_VALID_TARGETS=("legacy",)` — a `"deep"` request 400s; forcing `deep` would outrank a tripped breaker + defeat the watcher) + fail-closed admin-token gate (`require_admin`, `X-Admin-Token` via `hmac.compare_digest`, route DISABLED when `JARVIS_ADMIN_API_TOKEN` unset). `# TODO(10D): retire escape hatch` marker on the route.
> - **10D pre-activation carries (before ANY surface flips to `rate>0` / `enabled=deep`):** (1) shadow runs commit real `TokenUsage` with `trigger="chat"` + spend real budget → switch to `trigger="shadow"` + implement the budget gate (`ShadowRunner._budget` already threaded, TODO in place); (2) `deep_readback_enabled=True` (dormant) would fire `record_confirmed_outcome` trust-increment for a SUPPRESSED shadow write → suppress/verify; (3) reads execute for real (documented live-reads design); (4) suppression correctness rests on `CAPABILITY_CATALOG.read_only` accuracy → audit before treating as a hard boundary; (5) shadow is deliberately DELEGATE-FREE (test-locked) — if shadow delegate-fidelity is ever wanted, thread the injected shadow executor into `build_read_only_delegate` FIRST (it wires the REAL executor). Cleanup carry: M5 assembly-preamble dedup between `call_agent_stream` + `run_shadow_turn`.
>
> **B1 (flip) + B12 (native-stream→`surface_update` adapter) remain 10D** (the shadow comparator's decision-capture touches the same phase surface as B12 but stops at the decision, not the transport). No CLAUDE.md edit (dormant/observability machinery; the two-execution-paths + rollback-runbook doc rewrite is 10D at merge).

- [ ] **B1 — Flip `JARVIS_RUNTIME=deep`.** The master switch (default `legacy` today).
- [ ] **B2 — Flip `deep_inline_format=True` + lead-scope the Presenter voice.** *(7B1 P4.)* The
  `PRESENTER_VOICE` augmentation is agent-agnostic today (every deep agent gets it when on) → scope
  it to the lead only at activation. File: `src/orchestrator/agent_invoker.py` `_augment_system_blocks_for_inline`.
- [ ] **B3 — Flip `deep_delegates_enabled=True` + build the LIVE lead→delegate routing.** *(7B2.)*
  The `subagents=` scaffolding + per-child gated middleware exist; the live single-lead routing
  decision (when the lead delegates to a read-only worker) is NOT built.
- [ ] **B4 — Flip `deep_readback_enabled=True`** *(7C)* — depends on A2 (real read_fn). Also make the
  conscious call on **B4a: read-back holds the write lock** — read_back sits INNER of write_lock, so a
  real read_fn + the trust-increment's fresh session execute while the cross-path lock is held
  (atomic write+verify, arguably correct; harmless now with read_fn=None). *(7C gate d / holistic obs-b.)*
- [ ] **B5 — Librarian live activation.** *(7B1 P3.)* Add a runtime-agnostic `chat_processor` branch
  that, on `runtime=deep`, drops the presenter step (`chat_processor.py:583`) + the InteractionLearner
  spawn (`:621`) and flips librarian middleware `active=True` (else it double-fires with the still-live
  runtime-agnostic `InteractionLearner`).
- [ ] **B6 — Perception-path Perceiver → deep.** *(7B2 gate b.)* The deep branch is currently ONLY in
  `call_agent_stream` (chat); `call_agent` (perception/briefing) stays legacy until this.
- [ ] **B7 — Agent-count reduction (6→N) + Presenter/Librarian agent-row-drop migration.** ONLY after
  live activation (`seed_defaults` creates/updates, never deletes → needs a data migration, like the
  7A governor-drop / 6C operator-drop).
- [ ] **B8 — decision_type modified/approved on the deep gate.** *(7C gate c.)* 7C records "approved";
  capture the modified/approved distinction from the interrupt verdict for accurate trust graduation.
- [ ] **B9 — AsyncPostgresSaver autonomous wiring + `durability="sync"` + single-flight lease +
  reconcile-from-event-log EXECUTION.** *(Step-1 spike green; Step-5 made RuntimeEvent recordable with
  `seq`; Step-10 reconciles.)* The autonomous durable-resume cutover proper.
  - **10C: MACHINERY BUILT (dormant — no flip).** All 3-of-4 net-new B9 pieces landed behind the
    effective-runtime gate keyed `"autonomous"` (byte-neutral, ZERO migrations): (a) worker-side
    `AsyncPostgresSaver` → `AgentInvoker.checkpointer_provider` + `deep_step_runner` wired into the
    scheduler's `GraphExecutor` (**P2** `a5df7b5`); (b) single-flight Redis `SET NX PX` lease
    `autonomous_lease.acquire_run_lease` wrapping `execute_run`/`resume_run` on the deep path (**P3**
    `6da358c`); (c) reconcile-from-event-log `run_reconcile.reconcile_run_from_events` (substrate-agnostic,
    UP-ONLY, never regresses terminal-success) replacing the WARN in `_resume_run_body` on the deep path
    (**P4** `a0f5cb7`); (d) `durability="sync"` + the ledger-in-deep BUILD (`run_autonomous_deep_step`,
    the exactly-once linchpin — the ledger, NOT thread-id stability, is the guarantee) (**P1** `b1b4446`/
    `48dd672`). **Design note:** per-step thread is fresh-minted + reaped on completion; run-level durable
    resume is via P4's reconcile + DAG re-pick + the ledger (more robust for the 10D cross-substrate drain
    than per-step checkpoint resume). **Flip / live durable-resume = 10D.**
- [ ] **B10 — Checkpoint reaper / TTL cleanup before deep is default.** *(6B CF-4; 6C added
  `checkpoint_reaper.py` reap-on-completion + decided-approval sweep — confirm it's sufficient at scale.)*
  - **10C: autonomous reaper BUILT (dormant).** `run_autonomous_deep_step` reaps its per-step durable
    checkpoints on completion (mirrors the chat `resume_deep_turn` reap; no-op on MemorySaver → dormant-
    safe); the tick's decided-approval sweep remains the substrate-agnostic backstop (covers chat + the
    rare Branch-C within-step-expansion approvals) (**P5** `779b85b`). **Carry (10D):** a process-crash
    orphan of a pre-approved autonomous step thread (no Approval, fresh-mint) is a documented rare
    limitation — a proper age-based checkpoint-table sweep is 10D; also confirm reaper sufficiency at scale.
- [ ] **B11 — Flip `deep_context_jit=True` + slim the AUTONOMOUS path + live quality-validate.** *(Step 8.)*
  - **10C: B11-auto slim BUILT (dormant).** The 3 autonomous `ContextBuilder.build` callers slim to the
    JIT core under `deep_context_jit` + effective-runtime `"autonomous"`==deep (short-circuits on the flag
    FIRST → no Redis GET on the default path → byte-neutral); render contract verified empirically (the
    persisted pack carries `entities`; `build(jit=True)` retains them via `_fetch_core_entities` → the
    plan/summary detail tab renders non-empty) (**P6** `82628af`). **Flip + LIVE quality-validate
    (slim-core + JIT retrieval doesn't regress autonomous agent output — a behavior change, not byte-
    provable) = 10D.**
  Step 8 landed the slim/JIT pack DORMANT on the deep CHAT path only (`deep_context_jit` default off,
  scoped to `JIT_ENABLED_AGENTS={planner,perceiver,librarian}`; Presenter/Executor stay eager). At
  activation: (a) flip the flag; (b) slim the autonomous callers (`step_graph_store.py:67` /
  `step_runner.py:427` / `graph_executor.py:449`) whose slimmed pack persists to
  `TaskRunDetail.context_pack` → **validate surface rendering** (`surface_detail_builders/plan.py:87`,
  `summary.py:103`) on slimmer packs; (c) LIVE-validate that slim-core + JIT retrieval doesn't regress
  agent output (the reduction can't be proven byte-identical — it's a behavior change on the retrieved
  runtime). Consider the Presenter-only read-scope relaxation if its output degrades on the slim core.
- [ ] **B12 — Native-stream → `surface_update` translation adapter.** *(Step 9, Fork 1.)* The only
  producer of `SurfaceUpdate` phases is the autonomous DAG (`graph_executor`/`dag_runner` →
  `execution_surface_emitter.py`), legacy until this cutover. A deep run has no phases to translate
  until the autonomous path itself runs on the deep runtime — so the adapter is built HERE, not in
  Step 9 (Step 9 deliberately built nothing for it — no source phases, no consumer). Also the full
  **"one interrupt approval event"** backend-contract unification spanning WS `ApprovalContext`
  (phase machine) + the deep `approval_needed` frame (6B) converges here, when the phase machine is
  reworked. **Step 9 (P3) did ONLY the safe subset** — retired the dead `approval.edit` no-op + dropped
  the legacy `approval`-kind badge shim (3 reps → 2). The **renderer unification** ("one
  `InlineApprovalCard` for both live transports") was DEFERRED here too: `InlineApprovalCard` consumes
  a rich typed `ApprovalContext` the persisted REST/detail path does not carry, so unifying it needs
  this unified contract first (forcing it in Step 9 = speculative glue on the highest-risk surface).

## Category C — QUALITY CARRIES (safe-not-broken; opportunistic)

- [ ] **C1 — Governor SERVICE/tool-layer liveness sweep.** *(7A.)* `report_governor_verdict` /
  `evaluate_policy` / `approve_action` / `get_plan_details` tools + `internal.*` caps + the `_special`
  backend + `services/governor.py` + the audit hook `governor_pre_tool_hook` are orphaned-but-harmless
  (validation-clean — `validate_registry` only checks the reverse). Decide keep-or-cull.
- [ ] **C2 — Reconciliation attr_key-scoping + entity name/email resolution.** *(Step-4 CF #2/#3.)*
  Reconciliation bumps ALL current facts of the resolved entity, not just the touched attr_key
  (abstention-only, acceptable); `_resolve_entity_id` honors only explicit `entity_id`.
- [ ] **C3 — `resolve_inflight_on_resume` ledger wiring.** *(Step-3 CF#3.)* Not wired into
  `idempotency/wrapper.py` (lacks a read_fn seam + persisted in_flight output + escalation emitter;
  fail-closed default unchanged).
- [ ] **C4 — Unwired supersede-event constants.** `ENTITY_FACT_SUPERSEDED` / `MEMORY_SUPERSEDED`
  declared-but-unwired. *(Step-4/carry.)*
- [ ] **C5 — Deep deferred-recheck loop.** *(7C defers.)* Deep has no `TaskStep` / `completed_unverified`
  persistence surface the autonomous tick keys off; a deep equivalent needs a net-new surface (would be
  a migration — out of the inline shape).
- [ ] **C6 — Interactive compensator EXECUTION + `verification_divergence` frontend UI.** *(Step-3 CF#4.)*
  7C annotates the escalate-first divergence payload; executing the compensator + rendering the divergence
  is a gated-write + frontend follow-up.
- [ ] **C7 — Stale Governor/Observer docstrings.** `routes_chat.py:5`, `perception.py:8`, `tracing.py:5`
  name a long-removed agent chain (predate 7A). Doc/liveness-sweep candidates.
- [ ] **C8 — Prompt-caching live-proof.** *(deferred since 6A — needs an API key.)* If a live smoke shows
  `cache_read==0` on turn 2, add `AnthropicPromptCachingMiddleware` explicitly (real per-turn cost regression
  otherwise). Structural caching is proven (6A.5 T4); the live proof is deferred.
- [ ] **C9 — Latent perf carries.** Native-token key hashing (unbounded `:tok:` length, latent — no spec
  uses it) + any remaining per-call capability-resolver memoization (6C #1 folded the deep-chain lookups;
  audit the autonomous path). *(Step-1/6C carry.)*
- [ ] **C10 — Summarization trigger tuning.** *(Step 8, Fork 2.)* deepagents' `_DeepAgentsSummarizationMiddleware`
  runs at the auto-computed `trigger=("fraction",0.85)` default. If long-session cost telemetry argues for a
  tighter working budget, swap it via `excluded_middleware=["SummarizationMiddleware"]` + a custom-configured
  langchain `SummarizationMiddleware` in `extra_middleware` — needs a SECOND spike proving the swap composes
  with `AnthropicPromptCachingMiddleware`. Also: the live check that the REAL summary `.ainvoke` streams under
  `build_chat_model`'s adaptive-thinking config (P0 proves the ADAPTER offline; the model-streaming half needs
  an API key — pair with C8). **SSE-filter forward-compat (Step 8 P3):** `stream_adapter.py`'s summarization
  skip matches ONLY `lc_source=="summarization"` on `payload[1]` — fail-OPEN if a future langchain/langgraph
  renames the tag or nests metadata differently (leak returns silently). Verified correct vs pinned langchain
  1.3.10 (`summarization.py:833`); re-check at any langchain/deepagents bump.
- [ ] **C11 — Richer graph JIT tool + eager-path micro-perf.** *(Step 8, Fork 3b + E1.)* The slim pack routes
  graph via one-hop Postgres `traverse` (accepted reach downgrade vs eager Neo4j weighted depth-2); add a
  weighted depth-2 graph tool if agents need reach. Eager-path-only waste (relevant only while legacy eager
  lives): the same query is embedded 3×/turn (uncached) and context assembly fires ~15 read-path
  `refresh_stability` PG WRITES; `internal.build_context` (whole-pack tool) sits in no agent's scope
  (orphaned escape hatch); `catalog.py:8` "intelligence: 19 tools" is a stale count (actual 23).
- [ ] **C12 — A2UI → SurfaceKit rename.** *(Step 9, Fork 3.)* Deferred out of Step 9 — pure churn,
  categorically larger blast radius than the prune (every importer, not just the layer), and its
  purpose (free the "A2UI" name for Google A2UI) only bites when the standards layer is built. Land
  it WITH the standards track (`project_week3_standards_adoption` threads the rename through 3A/3B).
- [ ] **C13 — Agentic-UI standards adoption (AG-UI transport + MCP-Apps artifacts).** *(Step 9, Fork 3
  / `project_week3_standards_adoption`.)* Separate larger track: 3A replaces `SurfaceUpdate` + the
  dual SSE/Redis-WS with one AG-UI SSE stream (overlaps the post-Step-10 phase-machine deletion);
  3B FastMCP `ui://` resources + frontend-as-MCP-host. Out of the rebuild's Step-9 scope; sequence
  after the runtime cutover settles. Also folds the spec-explicit **phase-machine deletion** (after
  Step 10) + dropping the two dead phase arms `planning`/`partial` (`contracts/__init__.py:462`).
- [ ] **C14 — Step-9 opportunistic narrative-Markdown carries (holistic nits, safe-not-broken).** (a)
  `PRESENTER_VOICE` fallback guidance (`prompts.py:~636`) still tells the LLM to fall back to a single
  `Text` section "with the content as a markdown string" — but `Text` renders literally; now that a
  `Markdown` component exists, that fallback could route through it (a real Presenter `surface_data`
  behavior change + a golden-hash re-baseline — out of Step-9's scoped 4 backend narrative sites).
  (b) Two shorter insight prose fields left as `Text` for consistency — `build_insight_actions`
  description (`insight.py:71`) and `build_insight_context` goals (`insight.py:107`) — vs the briefing
  action description which now renders Markdown. Align when the insight surface is next touched.

## Remaining rebuild steps (scoped, not "gates")

- [x] **Step 8 — context JIT-hybrid** (spec T7). **DONE + reviewed = SHIP** 2026-07-09 (subagent-driven,
  commits `77ffe31..0ea0bf2` on `rebuild/first-principles`, NOT pushed). Plan
  `docs/superpowers/plans/2026-07-09-step8-context-jit-hybrid.md`. Slim/JIT pack DORMANT behind
  `deep_context_jit=False` + `runtime=="deep"` (legacy + autonomous byte-identical); SSE summarization
  filter live on the deep path; kept the deepagents 85% summarization default; reused Step-4 tools (0 new);
  Presenter/Executor stay eager; graph→JIT one-hop. **NO migration** (head `1a2770a28c39`); 3325 non-e2e
  green. Activation → B11; carries → C10/C11. Execution correction: `tool_options` is LIVE on the
  autonomous path (kept; only dead `artifacts` fetch deleted).
- [x] **Step 9 — A2UI render-payload cleanup** (spec §4.9). **DONE = SHIP** 2026-07-10 (subagent-driven,
  commits `b0e9c0a..6dd6c4d` on `rebuild/first-principles`, NOT pushed). Plan
  `docs/superpowers/plans/2026-07-09-step9-a2ui-layer-split.md` (`1cc90b8`). Grounded by 4 parallel
  extraction passes. LIVE runtime-agnostic UI cleanup (NOT dormant — first such step since Step 6).
  **NO migration** (head `1a2770a28c39` unchanged, drift-free); `A2UI_SCHEMA_VERSION`=1 unbumped
  (Fork 2 TTL-prune); phase machine byte-untouched. Backend 3292 passed / 18 skipped; frontend 100
  passed, lint/build green. Landed: **P1** pruned 13 never-produced ComponentTypes + 5 dead
  SurfaceKinds (+`lists.py`) — the 2-stage review CAUGHT a live regression the census missed
  (`PRESENTER_VOICE` still advertised the deleted kinds; strict `SurfaceSpec.kind` Literal → the LLM
  chat path would silently drop surfaces → fixed `f24f1e4`, re-baselined the 7B1 golden hash + added
  a dead-schema teeth test). **P2** added a `Markdown` ComponentType + builder + `A2UIMarkdown`
  (react-markdown, XSS-safe) rewiring 4 briefing/insight narrative sites. **P3** REDUCED to the safe
  subset — retired the dead `approval.edit` no-op + dropped the legacy `approval`-kind badge shim
  (3 approval reps → 2); **the renderer-unification (`one InlineApprovalCard for both transports`)
  was DEFERRED to B12** (it needs the unified approval contract; `InlineApprovalCard` consumes a rich
  typed `ApprovalContext` the persisted REST path doesn't carry — forcing it now = speculative glue).
  Holistic opus = SHIP-WITH-NITS (nit closed `6dd6c4d`: dropped 5 dead-kind labels from
  `surface-card.tsx`). Carries → C12/C13 (unchanged) + C14 (below).
- [ ] **Step 10 — autonomous-path runtime cutover** (the coordinated flip above + shadow-compare + auto-rollback).
  **SCOPED + DECOMPOSED 2026-07-10** (4 parallel grounding subagents cross-verified every A/B item at
  file:line; forks resolved one-by-one with the user). **Split 4 ways along the build-vs-flip fault line
  — three no-flip, offline/forced-provable build sub-steps, then one live+irreversible closeout:**
  - **10A — Category-A security hardening** *(plan `docs/superpowers/plans/2026-07-10-step10a-security-hardening.md`,
    committed `2c30d17`)*: A1, A3, A4, A5, A6, A7 + NEW-1, NEW-2 (A2 = invariant-guard only; real `read_fn`
    → B4). No flip, byte-neutral, ZERO migrations. **DONE = SHIP 2026-07-10** (subagent-driven, TDD, 13
    commits `b431487..97beba7` on `rebuild/first-principles`, NOT pushed). Holistic review verified:
    byte-neutral on live `legacy`, 18/18 negative controls RED→GREEN, 3329 non-e2e passed / 18 skipped,
    ZERO migrations (head `1a2770a28c39`). See the Category-A checklist above for per-gate commits +
    activation carries (10C: A6 resume-seam re-assert + NULL thread_id mint; B3: A1 probabilistic-judge +
    cache-scope; B4: A2 real read_fn). NO CLAUDE.md edit (10D at merge).
  - **10B — Cutover control plane**: the 4 net-new rollback metrics (all NET-NEW — only `AGENT_RUNTIME_CALLS`
    exists; double-fire has a log-only hook `idempotency/wrapper.py:81/84`) + shadow-compare harness
    (live reads + hard-suppressed writes at the single `ToolExecutor.execute_tool` choke-point, sampled +
    async + throwaway session, **spike-first**) + per-surface **effective-runtime gate** (durable manual
    kill-switch + Redis auto-breaker + static `settings.runtime` fallback — `runtime` CANNOT hot-change,
    so this gate is the mechanism) + one-directional auto-rollback watcher + escape hatch. No flip.
  - **10C — Autonomous durable engine — DONE = SHIP 2026-07-11** (subagent-driven, TDD, 12 commits
    `83f9b4c`..`451a3c1`; NO flip, byte-neutral on default `legacy`, ZERO migrations, single head
    `1a2770a28c39`, 3443 non-e2e passed / 18 skipped, ruff clean; NOT pushed/merged). Cut the autonomous
    **step executor** onto `build_deep_agent` (`authorization_source=autonomous`; DAG orchestrator
    `graph_executor`/`dag_runner` STAYS) + B9 (`AsyncPostgresSaver` + `durability="sync"` + single-flight
    **lease** + **reconcile-from-event-log** — 3 of 4 net-new) + B10 autonomous reaper + B11-auto slim, all
    DORMANT behind the effective-runtime gate keyed `"autonomous"`. **Spike-first (all 4 Phase-0 gates
    resolved, independently opus-signed `PHASE_0_SOUND=YES`):** SQ1 **Branch A** CONFIRMED (per-step
    `build_deep_agent` durable resume + ledger exactly-once — plan NOT disproven); SQ2 **Branch C** (deep
    `trust_gate` `pre_approved_capabilities` short-circuit — one line, NO thread_id change, NO
    GraphInterrupt→run-pause bridge; the step-level `dag_runner` gate stays the durable pause); SQ3
    **Branch A** (inline `_finalize_with_verification` seam kept; deep `read_back` dormant — real read-back
    unification stays **B4/10D**); SQ4 **Branch A** (reuse `_build_deep_agent_for` + `AUTONOMOUS`, BUT the
    ledger-in-deep is a genuine BUILD — the deep chain lacked it → double-fire hole closed). **Ledger =
    exactly-once linchpin** (not thread-id stability). **Reviews:** ledger-in-deep + Branch-C gate
    independently opus-reviewed SOUND + security-reviewed SAFE (2 LOW hardenings applied: empty-ws
    fail-closed, `_RUN_TERMINAL_SUCCESS` run guard); P2 A6 ws-binding security-reviewed SAFE; P4 reconcile
    reviewed SOUND; P7 e2e proved all 6 happy-path assertions + 5 negative controls (GREEN→RED→GREEN) with
    NO P1–P6 integration bug. **Flip = 10D.**
  - **10D — Coordinated live cutover** (the ONLY live+irreversible step): final whole-branch review →
    **merge dormant machinery to `main`** (merge-then-flip; byte-identical under default `legacy`) +
    CLAUDE.md two-execution-paths rewrite → incremental flip **chat → perception → autonomous** (each
    armed by 10B's shadow+rollback, 1 production-clean-week hold per surface) → **B7 row-drop migration**
    (6→4 agents: drop Presenter+Librarian AFTER all consuming surfaces flipped, remove `AGENT_PROMPTS`
    keys first; Perceiver stays; the ONLY Step-10 migration — **6→4 requires 10D Task A-8 migrating
    `generate_briefing` (`jarvis.py:553` `call_agent("presenter")`) off the Presenter agent; else descope
    to 6→5 Librarian-only**) → retire escape hatch. **Legacy-code deletion
    is OUT of Step 10** (kept as the auto-rollback fallback; a later post-rebuild cleanup).

  **ALL 4 SUB-PLANS WRITTEN + COMMITTED 2026-07-10** (`2c30d17`..`9dc559d` on `rebuild/first-principles`,
  NOT pushed; grounded by 3 parallel plan-drafting subagents that re-verified anchors @ `a5ab52f`):
  10A `2026-07-10-step10a-security-hardening.md`, 10B `2026-07-10-step10b-cutover-control-plane.md`,
  10C `2026-07-10-step10c-autonomous-durable-engine.md`, 10D `2026-07-10-step10d-coordinated-live-cutover.md`.
  Every anchor is `@ a5ab52f` — RE-VERIFY at execution (10A mutates `agent_invoker.py`/`write_lock.py`/
  `step_runner.py`/`settings.py` + adds `thread_identity.py`; 10B adds `runtime_gate.py`; the plans cross-
  reference these collisions). **SHARPEST OPEN QUESTIONS (surfaced by the drafters, decided at execution):**
  (a) **10C SQ1 is a plan-killer** — Phase-0 spike 0.1 must confirm a per-step `build_deep_agent` thread
  resumes under the outer Python DAG loop (Branch A); if not, Branch B = a `dag_runner` rewrite = a DIFFERENT
  plan ⇒ STOP+escalate. (b) **10C SQ2 (gate reconciliation) is the crux** — `dag_runner` step-level
  `TrustEngine` gate vs the deep tool-call `trust_gate` would DOUBLE-gate (`is_gated_source("autonomous")`);
  recommended hybrid Branch C (step-gate = coarse pre-step pause; deep chain runs but `trust_gate` doesn't
  re-prompt the already-approved step) — spike 0.3 decides. (c) **10B kill-switch = ZERO-migration Redis
  override** (deep is opt-in via a Redis enable-key; a Redis outage falls back to static `legacy` = fail-safe;
  no DB table → preserves "B7 is the ONLY Step-10 migration"). (d) **B7 = single migration after R3** dropping
  BOTH Presenter+Librarian (a stranded unrouted row is harmless per the operator/governor precedent) — vs two
  migrations by timing (Presenter droppable after R2/chat, Librarian after R3/perception). (e) **B12 fold** —
  the native-stream→`surface_update` adapter is built in 10D Part A but first-LIVE at R4 (autonomous is the only
  phase producer; chat-deep emits none); its exercise may fold into 10C. Correction: `SurfaceUpdate.phase` has
  7 arms (5 live + dead `planning`/`partial` = C13 drops); `AGENT_CAPABILITY_SCOPES`/`AGENT_MODEL_TIERS` live in
  `agents.py:28/:16` (not `prompts.py`); the write-lock classifies via pure `is_read_only_capability`
  (`capabilities.py:227`, fail-closed) NOT `is_write_capability`.

  **ALL 4 PLANS REVIEWED + FIXED 2026-07-10** (`e374f96`; 4 parallel adversarial reviewers independently
  re-verified anchors + hunted false-claims-of-fact — SHIP-WITH-FIXES each, all applied). **3 CRITICAL + 2
  IMPORTANT the drafts missed:** (1) **10C P1 — the idempotency ledger is NOT in the deep chain** (`grep`
  empty in `src/deep_runtime/`; deep dispatcher uses RAW `self._tool_executor.execute_tool`; ledger is
  legacy-`step_runner.py:326`-only) → the draft's "already ledger-guarded, confirm" was FALSE → autonomous
  deep writes would DOUBLE-FIRE on every mid-step resume → rewrote P1 to an explicit ledger BUILD
  (`make_idempotent_execute_tool_fn` per-step, stable `identity_key`). (2) **10D B7 — `generate_briefing`
  (`jarvis.py:553`) is a LIVE scheduler-driven Presenter-agent caller** not retired by any surface flip →
  dropping the row strands scheduled briefings → "6→4" needs Task A-8 (migrate off) or descope 6→5.
  (3) **10A NEW-2 — the build-time `capability_scope` guard ALREADY EXISTS** (`agent_builder.py:114-124`,
  tested) → Task 1 reduced to a test-only regression lock (+ the uncovered guard-POSITION delta). (4) **10B
  — the `ShadowToolExecutor` had no injection seam** (`_build_deep_agent_for` hard-wires the real executor)
  → the deep shadow would bypass write-suppression → added an additive `execute_tool=` param + teeth test.
  (5) **10A A3 — undefined `is_write_capability`** → `not is_read_only_capability` + resolve-cap-before-
  fail-closed; A6 `thread_id` shortened (`String(64)` headroom). Anchor sampling found ZERO false file:line
  claims — the CRITICALs were false claims of FACT (grep-disproved), the class the Step-9 census lesson warns of.

---

### Provenance
Compiled from the per-step "carry-forward" / "activation gate" notes in
`~/.claude/.../memory/project_first_principles_rebuild.md` (Steps 0–7C DONE blocks) + the rebuild spec
`docs/superpowers/specs/2026-06-28-first-principles-rebuild-design.md`. Re-verify each item at
`file:line` before acting — anchors rot across steps (the rebuild's recurring lesson).
