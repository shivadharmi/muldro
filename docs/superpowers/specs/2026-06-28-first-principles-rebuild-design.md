# First-Principles Rebuild — Design Spec

**Date:** 2026-06-28 · **Status:** Adopted (full, correctness-first). Ready for implementation plan.

**Supersedes the SHAPE of** [`2026-06-22-deep-agents-hard-replacement-design.md`](./2026-06-22-deep-agents-hard-replacement-design.md)
and [`2026-06-23-agentic-redesign-design.md`](./2026-06-23-agentic-redesign-design.md) **where they conflict.**
Those two specs remain valid for the invariants they carry forward (durable execution, fail-closed
authz, TrustEngine decision logic, per-fingerprint trust, workspace isolation, turn-scoped MCP);
this document re-bases the agent topology, gating model, execution/verification engine, data model,
world model, context engineering, and migration order on top of them.

**Grounded by** a 17-agent research + adversarial pass (6 SOTA researchers, an 8-member thesis panel
that steelmanned *and* attacked each rebuild thesis, then synthesize → critique → revise). All 8
theses were upheld **adopt-with-modifications**; the panel also surfaced six controls the naive
design omitted. The modifications and the six controls are normative here, not optional.

---

## 0. Why this exists

The two prior specs are strong on **Safety** (won't do unauthorized writes) and **Recoverability**
(durable resume) but thin on **Correctness** (does the *right* thing, correctly) — the pillar that
matters most for an assistant that *acts on your behalf*. A first-principles teardown (Musk's
algorithm: question every requirement → delete → simplify → accelerate → automate) found the same
disease in three subsystems — **rich schema/vocabulary built ahead of use while the load-bearing
behavior is thin or dead-wired** — and a 6-agent topology that is a hub-and-spoke ghost. This rebuild
**inverts the investment**: cut speculative generality, and make a small set of reliability behaviors
*uniform and enforced* across every execution path.

---

## 1. North star & the four reliability pillars

**Goal:** a reliable personal assistant that takes actions on a founder's behalf, and that is easy to
extend with new features later.

| Pillar | Definition | Prior status | This rebuild |
|---|---|---|---|
| **Correctness** | does the right thing, correctly | weak (verification *advisory*) | **enforced** verify→reconcile→compensate loop |
| **Safety** | won't do unauthorized/harmful things | strong | preserved + unified gate + cross-path write lock |
| **Recoverability** | survives failure without double-acting | partial (no idempotency key per step) | per-step idempotency ledger + compensation registry |
| **Earned trust** | learns you, transparent, asks when unsure | partial | preserved; trust graduation counts only *confirmed* writes |

---

## 2. The headline (one-breath target)

> **One durable lead agent** on the Deep Agents / LangGraph substrate holds read tools **and** gated
> write tools directly, closing **Perceive → Understand → Update-Model → Plan → Act → Verify →
> Reconcile → Compensate** on every world-touching write. Internal cognition (memory extraction,
> presentation, persona, governance) collapses into **middleware / tools / background jobs**; only
> **read-only research / perception workers** keep an isolated context window. Reliability is bought
> by **deletion** and by making a small set of properties **uniform and enforced** — capability-scope,
> write-serialization, idempotency, one gate, risk-gated verification, compensation — **not** by adding
> agents, a confidence dimension that is still a constant, or a "workflow" mode. The data model splits
> by reader: **LangGraph owns hot-path execution truth, an append-only event log is the system of
> record, UI/analytics are lagging projections** — so new features become projections, not columns.

---

## 3. Adopted theses (each with the modification that defines it)

| # | Thesis | Adopted as |
|---|---|---|
| **T1** | Collapse the 6-agent zoo | One lead + read-only research/perception workers; cognition → middleware/tools/jobs. **Preserve model/budget specialization** per tool/job. Ship **with** T2 (verification) — collapsing cognition removes inspection seams, so it requires the read-back loop + a delegate-summary critique or it trades coordination cost for undetectable single-agent error. |
| **T2** | Enforced verification | **Risk-gated** read-back (mandatory only for `reversible=false` OR `blast_radius ≥ external`) as an inline step-completion gate. Three terminal states (adds `completed_unverified`). |
| **T3** | World model as control surface | **Input-quality first**, then expose agent-queryable tools. Reconcile-written confidence is **quarantined to abstention**, never the gate. Build/buy of the temporal-KG engine is **spike-gated** (§4.6). |
| **T4** | Event-log + projections | LangGraph state owns execution truth (read-your-writes); append-only `RuntimeEvent` is system-of-record; UI/analytics/scheduler are projections. **No control-flow read off a lagging projection.** |
| **T5** | No "workflow" concept | Delete `backend/src/workflows/`. Cost-admission, idempotency, gating, verification, write-serialization are **uniform engine properties**. Keep read-only dynamic research agents. Do **not** touch `CapabilityFamily.WORKFLOW` (connector taxonomy — name collision). |
| **T6** | Unified gate / kill Operator | **Kill Operator-as-agent** (it's a routing label, not an engine). Unify the gate **mechanism**, but keep the chat/autonomous **semantics** as a **deterministic categorical** `authorization_source` — *not* a "chat raises trust" scalar (that would destroy "your message = authorization"). |
| **T7** | JIT context | **Hybrid:** lean always-on core **pinned outside the summarizable window** + JIT retrieval tools. Caching is already wired (the audit premise was wrong); **verify** it survives the explicit `middleware=` call shape. |
| **T8** | Keep Deep Agents substrate | Keep it — large deletion benefit, already committed + green. Bind to hard gates: **idempotency ledger before autonomous cutover**, TrustEngine stays a custom `wrap_tool_call` interrupt (not native boolean HITL), escape hatch for one cycle with auto-rollback. |

---

## 4. Target architecture

### 4.1 The lead loop + the collapse
A single Deep Agents lead agent (`create_deep_agent`, direct Anthropic API) is the default path for
the overwhelming majority of turns. It holds read tools, write-capable tools behind **one gate**, and
a `delegate` tool that spawns **flat, depth-1, read-only** workers. Effort-scaling lives in the lead
prompt; track delegate-spawn-rate per turn as a reliability metric.

| Current "agent" | Becomes | Why |
|---|---|---|
| Operator | **deleted** — lead holds write tools behind the gate + write lock | not an engine; a routing label + scope + prompt over the shared loop |
| Presenter | cheap-model formatting **tool** / markdown streaming | single bounded call, lead needs the value inline |
| Persona | background **job** (Haiku, batched) **over the full interaction trace** | not interactive; trace (not summary) per Cognition principle #1 |
| Governor | audit **middleware** on tool calls + a **critique pass** on delegate-returned summaries | cross-cutting policy, not a callable unit. **Degradation mode:** on critique timeout/failure, **fail-closed for writes**, fail-open-annotated ("unreviewed") for read summaries |
| Librarian | turn-scoped, **trace-aware extraction middleware** | post-turn processing, not an isolated loop |
| Perceiver + custom research agents | **stay agents** (the one shape that earns an isolated window) | multi-step, read-only, parallelizable, return ~1–2k summaries; depth-1; never hold write tools |

**Model/budget specialization is preserved** at the tool/job level via Deep Agents per-child model
override (cheap model for persona/presenter; right-sized reasoning for research).

### 4.2 Perception / scheduler origination (the headless lead)
A scheduler or perception `act`-tier tick spawns a **headless lead instance** with no user turn,
whose `authorization_source` is categorically **`autonomous`**. Every write it emits is therefore
gated by construction. The proactive pipeline (Perceiver → EventProcessor → RelevanceAssessor →
Notifier) stays a distinct scheduler-driven flow; the headless lead consumes its `act`-tier output.
The proactive loop is **not** collapsed into chat-only origination.

### 4.3 The one gate (kill Operator; unify mechanism, preserve semantics)
Write tools are gated by **one** `wrap_tool_call` middleware that raises a LangGraph `interrupt`
carrying `ApprovalContext`, regardless of which loop invoked the tool.

- **Hard override:** `reversible=false` OR `blast_radius ∈ {external_single, external_multiple,
  public}` forces approval at every trust level (extends RiskAssessor's fail-closed-to-high to a
  gate-level override). `reversible`/`blast_radius` are computed today but underused — promote them.
- **Deterministic categorical `authorization_source`** (the security-critical fix): the chat turn
  carries a structured intent + extracted action params from the existing `intent_classifier`.
  `authorization_source == direct_user_request` **iff every gated tool-call argument is a subset of
  params derived from the user's literal message string.** Any write whose args were sourced from
  perception / tool output / retrieved content is `!= direct_user_request` **by construction** — this
  makes the prompt-injection threat model *structural*. On `direct_user_request` match → skip
  approval; else evaluate trust × risk. **Not** an LLM judgment; **not** a continuous trust nudge.
- **`confidence` as a gate dimension stays DEFERRED** until calibrated evidence-derived confidence
  exists (today `confidence_score` is a constant 1.0).
- **Prerequisite (latent, not a live hole — verified 2026-06-28):** the **current live path**
  (`agent_loop._resolve_tool_scope_and_server`) already enforces capability scope **fail-closed**, so
  there is no exploitable hole today. But the `deep_runtime` migration scaffold ships
  `agent_builder.py` with `extra_middleware=()` and the `capability_scope` middleware
  (`backend/src/deep_runtime/middleware/capability_scope.py`) is **UNWIRED** — it must be wired
  **before** the Deep Agents path ever serves traffic, or the gap becomes live. Wire it in §6 Step 0.
  (Exact one-line edit + the missing end-to-end test are captured for the plan.)

### 4.4 Cross-path write serialization
"Writes are never parallelized" is **not** enforced by "one gate" (a headless scheduler run and a
live chat turn can both call `email.send`). Before any external write, **every** write path (chat,
headless/autonomous, custom write-capable agents) acquires a **per-`(workspace_id, capability_family)`
lock** via the existing `backend/src/services/locking.py` advisory-lock primitive. This makes single-
threaded writes a real cross-path invariant.

### 4.5 Enforced verification + compensation
**Verification (risk-gated):** every world-touching write declares an expected post-condition and is
verified by **read-back before** the step is marked complete — **mandatory only when `reversible=false`
OR `blast_radius ≥ external`** (one shared irreversibility axis with §4.3). It replaces the by-fiat
`{"status":"completed"}` sites (there are **multiple**: `step_runner.py` run_step:308, LLM-prose
fallback:134, aggregate:249 — a characterization test forbids any write path emitting `completed`
without a passing post-condition or an explicit `completed_unverified` verdict).

**Three terminal states:**
- `completed` — read-back confirmed.
- `partially_completed` — read-back contradicts the expected effect (surfaced).
- `completed_unverified` — **NET-NEW** (only `partially_completed` exists today); no confirming read
  exists / eventually-consistent API / verify budget exhausted. Distinct from failure so correct
  actions are never false-failed. Must thread through transition tables, all
  `transition_run`/`transition_step` callers, `StepList` icons, `_reconcile_plan_status` rollup,
  projections, `outcome_learner`, **and trust graduation** (it does **not** count toward
  `approved_count` — never graduate trust on unconfirmed actions).

Confirming reads are budget-bounded and idempotent on resume; budget exhaustion → `completed_unverified`.
Per-capability **post-condition registry** (deterministic), fallback to `completed_unverified` where
no deterministic check exists. Chat fast-path offers **async** confirmation rather than synchronous
read-back latency.

**Compensation / undo registry (escalate-first):** each write capability declares its compensating
action (delete the draft, cancel the invite). On `partially_completed` for an irreversible write the
engine **escalates to the user** with the exact `artifact_ref` and the observed divergence; the user
decides whether to run the compensator (the compensator is itself gated + idempotent). *(Fork
resolved: escalate-first, not auto-compensate.)* Where no compensator exists, escalate regardless.

### 4.6 World model as control surface (input-quality-first, spike-gated)
Stays Jarvis-owned (Postgres + Neo4j + Qdrant) — the substrate Store provides neither
bi-temporal/confidence/contradiction semantics nor safe tenant isolation. Build order, each gated
behind the prior:
1. Replace `find_entity` ILIKE-on-raw-message with **NER-on-spans + Qdrant entity vectors +
   `Entity.search_vector` FTS**.
2. Replace **binary recency** (0.8/0.2) with continuous `exp(-λ·days_since(last_seen_at))`.
3. **Entity-attribute contradiction handling** reusing `memory_service/contradictions.py` —
   **supersede** (`valid_to`) instead of the silent `{**old, **new}` overwrite.
4. **Evidence-derived confidence** (source reliability × corroboration, age-decayed) — **never**
   LLM-self-reported — rendered in `to_prompt()` with provenance.
5. **Only then** expose `get_entity` / `query_facts(as_of)` / `traverse` / `get_provenance` read
   tools, **each workspace-filtered**.

Post-action reconciliation is owned by the verification loop: a confirmed read-back raises/records a
belief; a divergent one lowers confidence — **fed to abstention/ask-the-user only, never the gate**.

**Build/buy resolved (spike done 2026-06-28): BUILD — hand-roll `valid_to` supersede on the existing
`Entity`/`EntityRelationship` schema + reuse `memory_service/contradictions.py`. Graphiti rejected**,
on two decisive factors: (1) **fail-open tenancy** — Graphiti's `group_ids=None` spans *all* groups
(confirmed in source + getzep/graphiti#838 as intended behavior), an unacceptable default against the
workspace-isolation invariant (prior bleed near-miss); adopting it would mean writing the enforcement
wrapper anyway. (2) **forced `openai` + `posthog` core deps + a second Neo4j driver** in an
Anthropic-only stack. Graphiti's real edge (bi-temporal + LLM contradiction) is the part Jarvis can
hand-roll cheaply, and the hand-rolled version is **fail-closed by construction** via the existing
`NOT NULL workspace_id`. Reassess BUY only if Graphiti makes `group_id` enforcement mandatory and
drops the hard `openai` dependency. (graphiti-core 0.29.2 is otherwise Python-3.12/Neo4j-5.26
compatible — compat was not the blocker; tenancy + deps were.)

### 4.7 Context engineering (JIT-hybrid)
A lean **always-on core** (identity, active preferences via the existing explicit-injection, recent
conversation, compact world-model summary) is **pinned outside the summarizable window** (system-
prompt / cached-prefix region) so `SummarizationMiddleware` can never evict it. Bulky/volatile items
move behind **JIT** world-model/retrieval tools. **Delete** relevance-blind items (related-runs-by-
timestamp, always-empty `tool_options`, trivial constraints/risks). **Enforce a token budget** at
assembly — *(fork resolved: delete the dead `token_estimator`/`MAX_CONTEXT_UTILIZATION`/
`to_prompt_compressed` scaffold and rely on the substrate's `SummarizationMiddleware`, not both)*.
**Preserve** the existing cache discipline (stable cached prefix + volatile suffix — already wired);
**verify** auto-`AnthropicPromptCachingMiddleware` survives the explicit `middleware=` shape (assert
2nd-turn `cache_read_input_tokens > 0`). Cache the pack per `(workspace_id, run)`. Add context/cache
observability.

### 4.8 Data model (one owner per fact)
- **Execution truth** = slim state row + LangGraph `AsyncPostgresSaver` checkpointer. Agent
  control-flow reads (readiness, resume cursor, dependency checks, idempotency lookups) are
  read-your-writes — **never folded from a lagging projection**.
- **`RuntimeEvent`** (append-only, correct lifecycle taxonomy) is the **system of record**.
- **UI / history / analytics** are derived **projections** off `RuntimeEvent`. New features → new
  projections, not new columns. Slim `TaskRun` to executor-transactional fields; extract
  `context_pack` (by-ref + TTL), `policy_decision`, cost rollup.
- **Per-step / per-tool idempotency** = `hash(workspace_id, run_id, step_id, tool, normalized_args)`
  + unique constraint + `attempted`/`confirmed` status. **HARD prerequisite before autonomous
  cutover.** Per-step keys do **not** exist today (only `TaskRun`/`Plan`/`NormalizedEvent` have one).
  A **deterministic-arg normalizer** MUST canonicalize non-deterministic args (timestamps/nonces) or
  the ledger fails open. One irreversible action per node; never combine an auto-execute side effect
  with an approval interrupt in one node. Single-flight resume lease per run (existing SKIP-LOCKED).
  `durability="sync"` on irreversible-write nodes. Promote the "Checkpoint/DB mismatch" warning to
  reconcile-from-event-log. Treat checkpoint/event payloads as untrusted (parameterized SQL, no pickle).

### 4.9 A2UI (layer split, keep declarative)
Adopt LangGraph's native stream as **transport** and delete the hand-rolled `SurfaceUpdate` phase
machine. Keep the declarative typed-component tree as **render payload**. Collapse the **three**
approval representations into **one** interrupt event `{risk, trust_level, allowedDecisions, remember}`.
**Add a `version` field** to `A2UISurface`/`A2UIComponent` now (confirmed absent) with graceful
fallback — also the render path for old-schema surfaces during cutover. **Prune** the 10 dead
component types + the 7 legacy surface-kind shims. Route **narrative** content (briefing body, insight
reasoning) to **markdown** + a small fixed card frame. **Rename** the homegrown layer (e.g.
`SurfaceKit`) to free the "A2UI" name. MCP-Apps iframes only for connector-authored UI, routed
through the same gate.

### 4.10 Multi-tenant isolation (blocking Phase-0)
The substrate's namespace isolation is **fail-open**, and a cross-workspace bleed near-miss already
occurred in this substrate. Every agent-queryable world-model tool, every LangGraph checkpointer
thread, and every projection MUST carry and filter on `workspace_id`. A **blocking test** asserts a
tool call in workspace A cannot return workspace B data through the Store/checkpointer/projection.
This sits alongside `capability_scope` wiring as a Phase-0 gate.

---

## 5. Decisions locked

**The three forks (resolved with the user):**
- **Direction:** adopt the full target, **correctness-first** — the Deep Agents runtime cutover is the
  **last** migration step (after idempotency + verification + world-model + data-split land).
- **World-model KG:** spike **done** → **BUILD** (hand-roll `valid_to` supersede + reuse
  memory-contradiction code). Graphiti rejected: fail-open `group_id` tenancy + forced
  `openai`/`posthog` core deps (§4.6).
- **Failed-verification compensation:** **escalate to the user first** (compensation is itself a
  world-touching action).

**Smaller decisions (recommended + adopted; override on review):**
- Dead context compression/budget scaffold → **deleted**; rely on `SummarizationMiddleware`.
- `completed_unverified` UX → soft **"sent (unconfirmed)"** badge + async confirm when a later read
  succeeds.
- Recurring routines → **implicit** `schedule → plan → engine`; first-class "named routines" later
  (YAGNI).
- Escape hatch → legacy behind `JARVIS_RUNTIME` for **one production-clean week** with auto-rollback.
- Confidence → **abstention/ask-only**; not a gate dimension.
- Persona learning → **sampled subset** of interactions to start (budget), full-trace not summary.
- Custom-agent writes → **gated-until-trusted** (prior decision) **and** must acquire the write lock.

---

## 6. Migration order (correctness-first; runtime swap last)

Each step is independently shippable; lowest-risk / highest-correctness-leverage first.

- **Step 0 — Safety + isolation preconditions (blocking, no behavior change):** wire
  `capability_scope` into `agent_builder.py` (closes the **latent** scaffold gap — the live
  `agent_loop` path already enforces scope; `deep_runtime` must be wired before it serves traffic) +
  the missing end-to-end "out-of-scope blocked through the built agent" test; add the multi-tenant
  isolation test (A cannot read B via Store/checkpointer/projection); add the A2UI `version` field +
  graceful fallback; delete `backend/src/workflows/` + `test_meeting_prep.py`; delete the dead
  context-budget scaffold; add context/cache observability + assert auto-caching survives the
  explicit `middleware=` shape. (Graphiti spike already done → BUILD, §4.6.)
- **Step 1 — Per-step idempotency ledger + deterministic-arg normalizer** (hard prerequisite before
  any autonomous cutover). Acceptance: kill the worker after a write's API call but before checkpoint,
  resume where raw args differ, assert the external effect fired **exactly once**.
- **Step 2 — World-model input quality** (cheap, migration-independent): NER + Qdrant + FTS lookup;
  continuous recency decay.
- **Step 3 — Enforced read-back verification + compensation registry** (reliability core): replace all
  three by-fiat `completed` sites with the inline gate (`completed`/`partially_completed`/
  `completed_unverified`), risk-gated, budget-bounded, idempotent; thread `completed_unverified`
  everywhere incl. trust graduation; add post-condition + compensation registries + the
  characterization test.
- **Step 4 — World-model contradiction + confidence + reconciliation:** supersede (`valid_to`) instead
  of `{**old, **new}` (must land **before** the dual-runtime window so both runtimes share write
  semantics); evidence-derived confidence with provenance; reconciliation feeding abstention only;
  **then** expose workspace-filtered query tools.
- **Step 5 — Data-model split by audience:** `RuntimeEvent` system-of-record; UI/history/analytics
  projections off it; slim `TaskRun`; extract `context_pack`/`policy_decision`/cost rollup.
- **Step 6 — Kill Operator + unified gate + cross-path write lock + fast-path contract:** one interrupt
  middleware with deterministic `authorization_source` + `reversible`/`blast_radius` hard override;
  per-`(workspace, capability)` advisory write lock on all write paths; route mutating fast intents
  (`memory_operation`, `approval_response`) through the gate+lock and give world-model-reading fast
  intents (`data_fetch`, `single_read`, `status_query`) the recall/verification guarantees; lead holds
  write tools; delete `CapabilityResolver`'s write→operator branch. Behind metrics; two-path behavior
  preservable behind a flag for one cycle.
- **Step 7 — Collapse the cognitive agents:** Presenter→tool, Persona→background job over the full
  trace, Governor→audit middleware + delegate critique (with the fail-closed/fail-open degradation),
  Librarian→turn-scoped trace-aware middleware; keep Perceiver + custom read-only research workers,
  model/budget specialization per tool/job. Ship **with** Step 3 verification stable (T1+T2 land
  together).
- **Step 8 — Context JIT-hybrid:** lean pinned core + JIT tools; delete relevance-blind items; adopt
  `SummarizationMiddleware`; verify caching. Land **with** Step 7 (collapsed agents must run on the
  new context model, not the old eager assembly).
- **Step 9 — A2UI layer split:** delete `SurfaceUpdate` phase machine, source live state from the
  substrate stream; one interrupt approval event; prune dead types + legacy shims; narrative →
  markdown; rename the layer.
- **Step 10 — Autonomous-path cutover onto LangGraph durable execution** (one-owner-per-fact
  checkpointer, single-flight lease, `sync` durability on write nodes, reconcile-from-event-log) —
  only after Steps 1, 3, 4, 5 land. Drain in-flight `awaiting_approval`/`paused` runs on the legacy
  runtime; start new runs on the new one. **Shadow-compare READ-ONLY decision outputs only — never
  shadow-run the side-effecting write path.** Define automated **rollback gates** (double-fire rate,
  verification false-negative rate, double-prompt rate, ungated-perception-write rate) that auto-flip
  `JARVIS_RUNTIME` back to legacy on breach. Retire the escape hatch after a clean window.

---

## 7. Risks & mitigations (from the adversarial panel)

| Risk | Mitigation |
|---|---|
| **Double-fire on resume** (LangGraph replay before idempotency → "sent twice") | Step 1 ledger + deterministic-arg normalizer is a hard gate before Step 10; acceptance test resumes with differing raw args |
| **Multi-tenant bleed** (fail-open substrate isolation + prior near-miss) | Blocking Phase-0 isolation test + `workspace_id` on every tool/thread/projection |
| **Inlining heavy cognition** bloats the lead window, removes verification seams | Context-economics bar per role; isolate reads/research; ship collapse **with** verification + delegate critique |
| **False-negative verification / alert fatigue** | Risk-gate read-back to irreversible/external only; `completed_unverified`; budget-exhaustion → `completed_unverified` |
| **Failed verification with no remediation** | Compensation registry (escalate-first) |
| **Destroying "your message = authorization" / leaving the perception-write hole** | Deterministic args-subset-of-user-string predicate; perception-sourced args non-authorizing by construction |
| **Unenforced write serialization across paths** | Per-`(workspace, capability)` advisory lock on all write paths incl. custom agents |
| **Fast-path as a hole through everything** | Fast-path safety contract — mutating intents through gate+lock; reading intents inherit recall/verification |
| **Dual-runtime corruption / shadow-running writes** | Supersede semantics + ledger shared by both runtimes before the window; shadow-compare read-only outputs only; one authoritative runtime per turn; drain paused runs on legacy |
| **Agent reading a stale projection** | Split readers by audience; execution-truth reads stay read-your-writes |
| **`completed_unverified` state-machine blast radius** | Its own step (3); explicit rule it doesn't count toward `approved_count` |
| **Fast-moving-framework coupling / unverified native-feature assumptions** | Escape hatch one cycle + auto-rollback; verify caching survives the explicit `middleware=` shape before relying on it (Graphiti already rejected, §4.6) |
| **Opportunity cost** (runtime swap is reliability-neutral) | Correctness Steps 1–4 sequenced ahead of the cutover |

---

## 8. Open spikes / items for the plan (Step 0)

- ~~Graphiti spike~~ **DONE (2026-06-28)** → BUILD (hand-roll supersede); Graphiti rejected on
  fail-open tenancy + forced `openai`/`posthog` deps (§4.6).
- **Caching probe:** assert auto-`AnthropicPromptCachingMiddleware` survives the explicit `middleware=`
  call shape (2nd-turn `cache_read_input_tokens > 0`).
- **Interrupt probe:** can a `wrap_tool_call` raise `interrupt()` from inside the tool wrapper, or must
  the gate be a dedicated node? (Affects how a delegated child carries the gate.)
- **`PlanReady` SSE contract:** what the existing Next.js renderer needs from the new event stream
  (frontend rebuild out of scope).

---

## 9. Relationship to the prior specs

- **Carried forward unchanged:** durable execution (LangGraph + `AsyncPostgresSaver`), fail-closed
  two-dimensional authz (class + per-fingerprint trust), TrustEngine decision logic (now reached via
  the one gate), RiskAssessor fail-closed-to-high, workspace isolation, turn-scoped MCP, atomic
  re-auth defer, validated status transitions.
- **Re-based by this spec:** agent topology (6 agents → 1 lead + research/perception workers), gating
  (dual-path → one mechanism + deterministic `authorization_source`), execution engine (advisory →
  enforced verify/reconcile/compensate), data model (Run god-object → event-log + projections), world
  model (passive blob → input-quality-first control surface), context (eager → JIT-hybrid), A2UI
  (hand-rolled phase machine → substrate stream + pruned declarative tree), and the **migration order**
  (runtime swap moves to last).
- **Superseded outright:** the standalone "workflow" concept and `backend/src/workflows/`; the
  Operator-as-agent; the eager per-agent `ContextPack` assembly.
