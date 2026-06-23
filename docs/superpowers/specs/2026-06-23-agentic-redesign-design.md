# Agentic Redesign — Design Spec

**Date:** 2026-06-23 · **Status:** Approved shape; ready for implementation plan
**Amends** the routing / planning / data-model parts of
[`2026-06-22-deep-agents-hard-replacement-design.md`](./2026-06-22-deep-agents-hard-replacement-design.md)
(its §2 routing rows, §6 manifest, §7 sequencing). **Carries forward unchanged** from that
spec: `durable_graph` (LangGraph + `AsyncPostgresSaver`), `trust_interrupt`, `run_projection`,
the §4 tool-authorization model (classifier + per-fingerprint trust), the §5/§5.1 invariants and
execution-engine carry-forward, and the §12 native-feature adoption decisions. Grounded by a
blast-radius + adversarial red-team pass (both new safety surfaces failed `holds=false` as first
sketched; the controls below are what make them hold).

---

## 1. Goal & principles

Move Jarvis from an **orchestrator-routed, plan-everything** pipeline to a **pure-agentic system**
where a lead agent acts directly and delegates to subagents only when needed, **planning is a
capability used on demand (not a mandatory stage)**, and **agents are first-class, user-creatable
entities** rather than 7 hardcoded roles. Product is early-stage: existing conventions are not
sacred — but every safety invariant from the hard-replacement spec is preserved.

Principles:
- **Planning is a capability, not a pipeline stage.** The agent acts directly; it plans (a native
  todo list) only for genuinely multi-step work. This single flip collapses the routing layer and
  most of the data model (they existed only because every request had to become a `PlanOutput`).
- **Agents are data, built-ins are seeds.** The existing `agents` registry (already CRUD-backed)
  is the substrate; the 7 cognitive roles are seeded rows; users add workspace-scoped custom agents.
- **Capability = attached MCP servers.** An agent's tool surface is the union of the **MCP servers**
  attached to it (server-granularity, not tool-by-tool), each tool still §4-classified + trust-gated.
- **Delegation never weakens gating.** Agentic (LLM-chosen) delegation is allowed, but a delegated
  child is built with **its own** fail-closed authz and inherits the parent's gating posture.
- **Plan on request, not on everything.** Perception observes and stores; only a high-relevance
  **act** tier escalates to an agent. Reading email is perception, never a run.
- **Preserve every invariant** in the hard-replacement spec (§5/§5.1): fail-closed two-dimensional
  authz, TrustEngine as the sole autonomous gate, risk-fails-closed, workspace isolation, durable
  resume + write-idempotency, validated status transitions, turn-scoped MCP, atomic re-auth defer.

---

## 2. Decisions locked (forks resolved with the user)

| Fork | Decision |
|---|---|
| Routing | **Pure-agentic delegation** over an agent registry; deterministic `CapabilityResolver` step-routing removed. A thin `capability_resolver` survives only as a sentinel table for the 3 special routes (§4). |
| Topology | **Registry of agent definitions** (built-in seeds + user-created); a **lead agent** delegates to them. Roles survive as agents, not as privileged code. |
| Custom agents | **First-class, user-creatable, workspace-scoped.** Capability = **attached MCP servers**. |
| Custom-agent write power | **Write-capable, but gated until trusted** — every custom-agent write is force-gated by TrustEngine **even on the chat path** until that agent+capability graduates (builds the "agent-defined gating" hook now). |
| Planning | **On-demand** (native todo list); no dedicated Planner stage, no `PlanOutput`/`Plan`/`PlanTask`. |
| Data model | **Collapse** `PlanOutput`/`Plan`/`PlanTask`/`TaskRun`/`TaskStep` into the thin `Run` projection fed by LangGraph state. |
| Perception | **Act-tier escalation only** (new 4th tier); other tiers never plan. |

---

## 3. Target architecture

```
turn (chat or perception "act" escalation)
  └ LEAD AGENT (read-only scope: read-class tools + {delegate, respond}; NO mutate/destroy tools)
      ├ acts directly for simple turns (answer / read / recall) — no plan, no delegation
      ├ for multi-step work: maintains a native todo list (scratchpad + progress surface)
      └ delegate(agent_name ∈ closed per-workspace enum, task)   ← the only "routing"
           └ rebuilds the named agent (LEAF: no delegate tool) with:
               model + skills + union(attached MCP servers' tools)
               + capability_scope middleware (class ∈ agent.allowed_classes AND fingerprint approved)
               + trust_interrupt IF parent is gated (autonomous run, OR custom-agent write)
               tenant = parent-bound workspace_id (never an LLM arg)
           → child runs through the SAME gated execution path; returns result to lead
```

- **Lead agent** is a narrow router: read-only scope — holds read-class tools (so it can answer /
  read / recall a simple turn directly without a delegation hop) + `delegate` + `respond`, but **no
  write tools** (so the ungated chat path can't execute a write directly through it; every write
  goes to the operator/custom agent, which is gated). Startup-asserted to carry the
  `capability_scope` guard (hard-replacement Step 1).
- **Delegation is depth-1 by default:** only the lead holds `delegate`; role/custom agents are
  **leaves**. This eliminates cycles and escalation chains structurally (§5). A bounded depth-2 is
  a future option behind a visited-set guard, not in scope now.
- **Built-in agents** (perceiver, librarian, governor, operator, presenter, persona) are reserved
  seed rows — the standalone **planner** role is removed (planning is on-demand, §4). **Custom
  agents** are workspace-scoped rows users create.
- **Autonomous path** (perception act-tier, scheduled): same lead/registry, executed on
  `durable_graph`; `trust_interrupt` attached so every write hits the TrustEngine 4×4 gate.

---

## 4. On-demand planning (replaces the Planner-as-stage)

The dedicated Planner LLM call + `PlanOutput` JSON contract are **removed**. Instead:

- The **lead agent acts/decomposes inline.** `FAST_INTENTS` collapse into ordinary lead-agent
  reasoning (answer directly / read / recall) — `classify_intent` + `intent_to_plan` +
  `extract_plan` disappear.
- For genuinely multi-step work the agent uses the **native todo list** (its scratchpad + the
  user's progress surface) — **not** a routing contract.
- **Preserve** (these were doing real work beyond decomposition):
  - The **3 special routes** become lead-agent/delegation behavior: `system.*` → inline handler
    (no agent); `reason|respond` → presenter (no tools); `perceive` → perceiver (all-read tools).
  - The **`actor=="user"` user-action-block** path (steps a human must do) survives as a render
    path off the todo list / Run projection.
  - The **`perception_policy` side-channel** (adaptive next-check cadence) — re-homed to a cheap
    policy step or deterministic defaults, since the Planner is no longer called every cycle (§7).
  - `classify_intent`'s **`sources` → perception-bump** side effect — re-derived from which read
    tools the lead agent actually invoked.
  - **Acyclic/dependency validation** that `PlanOutput`'s `model_validator` enforced — re-applied
    wherever the todo list / Run projection wires step dependencies.
- **Durable autonomous work** still needs a persisted artifact (the scheduler defers it to a 30s
  tick): the **Run projection** carries `goal`, `success_criteria`, `idempotency_key`, `trigger_type`,
  risk/execution_mode, and dependency wiring directly — **no `Plan`/`PlanTask` intermediary** (§8).

---

## 5. Gated delegation — required controls (red-team: `holds=false` without these)

`delegate` is LLM-chosen (pure-agentic) but **must** carry these controls, each confirmed required:

1. **ToolExecutor is not a gate.** `tool_executor.execute_tool` checks only `tool.enabled` + backend
   match (confirmed, `tool_executor.py:319`) — it performs **no** class/trust/scope check. `delegate`
   is not a registry-dispatched tool; it spawns an agent. **All** of `delegate`'s gating lives in
   (a) build-time tool filtering for the child and (b) the child's per-call `capability_scope`
   (+ `trust_interrupt`). The spec must not imply ToolExecutor gates delegation.
2. **Child authz is the child's, fail-closed.** The `delegate` impl rebuilds the child with
   `make_capability_scope_middleware(agent=child, workspace_id=PARENT_BOUND_ws, db_factory)` and the
   two-dimensional class+per-fingerprint-trust check re-applied for the **child's** role+workspace.
   The Step-1 startup-assert (refuse to build an agent lacking the guard) **covers the build inside
   `delegate`**.
3. **Gating posture is never weakened by delegation.** A child built during a `durable_graph` run
   receives the parent step's full middleware list **including `trust_interrupt`**. Explicit
   invariant: *a delegated child inherits the parent's gating posture.* No delegation crosses from a
   gated context into an ungated child build.
4. **Tenant from the parent closure only.** `delegate`'s input schema **must not** expose
   `workspace_id`/`user_id`; the child build resolves them from the parent's bound context. Fail the
   call closed if the parent workspace can't be resolved (no `'' → None` coercion into a global view).
5. **Depth-1 + closed enum.** Only the lead holds `delegate`; children are leaves (no re-delegation
   → no cycles). `agent_name` is a **closed enum validated against this workspace's registry**, not
   free-form; the routing decision is audited. This is how we keep "agentic" without re-opening the
   LLM-routed-`task`-tool surface §12 deleted.
6. **No upward class escalation.** The lead agent is read-only and holds no write tools, so it
   cannot escalate by calling writes directly; it can only delegate to the (gated) operator/custom
   agents. (If bounded depth-2 is ever enabled, a callee's `allowed_classes` ⊆ caller's, except the
   lead→worker hop which is the intended grant.)
7. **Shared per-turn / process-global resilience state.** Building a child per `delegate` call must
   **not** reset the per-turn `unavailable_server` breaker or the Anthropic circuit breaker — share
   the parent's breaker state / use the §12 process-global singleton. Verify `turn_scope`'s
   ContextVar propagates into each child `astream` so child-opened MCP sessions are reference-counted
   and torn down by the outer `turn_scope` (Step-0 risk).

---

## 6. Custom agents & MCP-server attachment (red-team: `holds=false` without these)

### 6.1 Agent-registry tenancy (the single largest hole today)
The `agents` table is **global** (no `workspace_id`, `name` globally UNIQUE), and
`load_agents_from_db` loads **all** agents into one shared dict routed by bare name — so a custom
agent in workspace A is routable from B, and a custom agent named `operator` can shadow the built-in.
Required:
- Add `workspace_id` (NOT NULL FK, `ondelete CASCADE`) to `agents`; drop `UNIQUE(name)`, add
  `UNIQUE(workspace_id, name)`. Built-ins are a **reserved** namespace (separate global/read-only
  rows or a `builtin` flag).
- `load_as_sub_agents` / `load_agents_from_db` take `workspace_id` and load only *(this workspace's
  custom agents + reserved built-ins)*; the orchestrator holds a **per-workspace** agent set, never
  one shared dict; routing resolves within the caller's workspace.
- **Reserved-name enforcement:** `create_agent` rejects the 7 built-in names, so `seed_defaults`
  force-sync can never overwrite/shadow a custom row and no custom row inherits a built-in's classes.
- Reclassify `agents` in CLAUDE.md from the documented global-table exception to **workspace-scoped**.

### 6.2 Strict workspace-scoped tool resolution
`ToolRegistry.get_tool` resolves `(workspace_id == X) OR (workspace_id IS NULL)` and caches by bare
name (confirmed `tool_registry.py:209-229`) — a global-NULL discovered row satisfies the OR for
**every** workspace, defeating per-(workspace, server, fingerprint) trust. Required:
- **Strict `workspace_id` equality** for discovered/attached tools on the hot-path eligibility check;
  deny if no workspace-scoped row resolves. Built-in seeds may stay global-NULL **only** as a
  reserved read-only namespace.
- Implement §4.9 fully: forbid `workspace_id=None` discovered rows (fail discovery **closed** if the
  workspace can't be resolved — remove the `workspace_id or None` foot-gun); key `get_tool`'s cache
  by `(workspace_id, name)`, not bare name.

### 6.3 Write-capable, gated until trusted (the chosen fork)
The ungated-chat-path rule ("your message = authorization") does **not** safely extend to a
user-defined agent wired to arbitrary third-party tools (you vetted the agent's purpose, not each
tool's behavior). Therefore:
- A custom agent **may** be write-capable, but **every write a custom agent performs is force-gated
  by TrustEngine even on the chat path** — approval per write until that (agent, capability)
  graduates, then it flows. This builds the "agent-defined / source-aware gating" enhancement the
  hard-replacement spec listed as latent. Built-in operator on the chat path stays ungated (user's
  direct message authorizes it); custom agents do not get that exemption.
- **Per-server approval** = one human decision bulk-approving the server's *present* fingerprints
  only (§4.6); schema drift → new fingerprint → re-quarantine.
- **Per-server autonomy cap (§4.8), keyed per server/fingerprint — not per capability.** Build it so
  capability-level graduation from a built-in tool can't promote a newly-attached custom-agent
  `mutate` tool to `auto_execute_*`. (TrustState graduates per `(workspace, capability, risk_level)`
  today — a shared-capability bleed; the per-fingerprint `TrustCeiling` closes it.)
- **Per-fingerprint trust must exist + be consulted** on the hot path with strict workspace scoping
  (`mcp_tool_trust` keyed `(workspace_id, server, fingerprint)`, default `quarantined`) — until it
  does, "custom agents start untrusted" is not actually enforced.

---

## 7. Perception act-tier escalation

Perception currently calls the Planner **every cycle** and queues a plan ([perception_runner.py:436-466](../../../backend/src/orchestrator/perception_runner.py:436)). There is **no `act` tier today** — only push / briefing / silent. Required:
- Add a 4th **`act`** tier to `RelevanceAssessment.notification_tier` + `_determine_tier` (split off
  the high-relevance + urgent band currently mapped to `push`).
- **Gate the planning / escalation path behind `tier == "act"`:** feed the observation summary to a
  lead-agent turn *as its request* ("the observation becomes the request"). `briefing`/`push`/
  `silent` **return without planning** — reading/observing never produces a run.
- Keep the queue/consume plumbing (background tick) as async fulfillment for act-tier work.
- **Re-home `perception_policy` cadence** for non-act tiers (a cheap policy step or deterministic
  defaults) — this is the one real downstream that depended on the Planner being called every cycle.
- **Decide cross-source synthesis gating explicitly** — it plans tier-lessly today and would be the
  loophole that still plans on every multi-source tick.

---

## 8. Data-model collapse — what the `Run` projection must cover

`PlanOutput`/`Plan`/`PlanTask`/`TaskRun`/`TaskStep` collapse into the thin **`Run` projection**
(the only writer of the served run/step record, fed by LangGraph state). It must expose
**everything** the current readers depend on — non-negotiable, or history/A2UI/scheduler/briefings
break:

- **Run identity + lifecycle:** `run_id`, plan-link successor, `user_id`, `workspace_id`, `status`,
  `source` (`background`/`approval_resume`/`plan`/`user_message`), `started_at`, `completed_at`,
  `updated_at` (LangGraph step writes **must** touch this — the stuck-run reaper keys on it),
  `error`, `retry_count`/`max_retries`, `trace_id`, `created_at`.
- **Denormalized token/cost rollup** on the run row (idempotent per segment; §5.1#7).
- **Pause states** preserved + distinct: `awaiting_approval`, `awaiting_reauth`,
  `partially_completed`, step-level `waiting_approval`; `timed_out` distinct from `failed`.
- **Validated status-transition allow-set** (port `RUN_TRANSITIONS`/`STEP_TRANSITIONS`, raise on
  illegal; §5.1#2) — including the load-bearing swallowed `awaiting_reauth→awaiting_reauth`.
- **Ordered step collection:** `step_id`, link, `name`, `capability`, `status`, `input_data` (incl.
  resolved `{task_id}.output.field` refs; §5.1#6), `output_data`, timing, `error`, `retry_count`,
  `artifact_refs`, `depends_on`, `step_order`; **stored delegated/executing agent name** (so
  "which agent is running" needs no re-derivation).
- **Step-status→UI mapping** for every status (or strict-Literal reads 500).
- **`run.checkpoint` JSONB bag split** into explicit fields: `surface_id`, `trace_rollup`,
  `auto_executed` trust trail, `verification` verdict, `awaiting_provider` (§10 risk 2).
- **Durable autonomous artifact:** `goal`, `reasoning`, `success_criteria`, risk/execution_mode,
  `idempotency_key`, `trigger_type`, dependency wiring (for the deferred scheduler tick).
- **Lock-safe queue pickup:** `SELECT … FOR UPDATE SKIP LOCKED` on `(status, source)`; the four
  scheduler ticks (background, health/reaper, dlq-retry, perception-reauth) keep working.
- **Approval linkage:** `Approval` rows by `run_id`+`step_id`+`artifact_refs`; approve→running
  transition + source flip to trigger resume; tool-level approval constructs a fresh background run.
- **Atomic OAuth re-auth defer** (run-level only; §5.1#3); **idempotency ledger** keyed
  `(workspace_id, step_id)` (§5.1#1); **advisory verification + `partially_completed`** (§5.1#4);
  **plan-status reconciliation** or its elimination (§5.1#5, the phantom-briefing guard).
- **Read-model/analytics queries:** recent-runs-by-(user, workspace, status) for ContextBuilder
  "related runs", briefing "active work", `routes_health` counts, the runtime-projection dashboard,
  and the SSE ownership/IDOR gate (run by `run_id`+`user_id`).
- Decide the fate of `plan_output_json` (full decomposition tree vs goal/steps summary) and whether
  `RuntimeEvent` rows survive as their own table or fold into LangGraph events.

---

## 9. Invariants (new + amended)

| Invariant | How preserved |
|---|---|
| **Delegation never weakens gating** | Child rebuilt with its own `capability_scope` + the parent's gating posture (incl. `trust_interrupt` on autonomous); tenant from parent closure; depth-1 leaves (§5) |
| **Agent registry is workspace-scoped** | `agents` gets `workspace_id`; per-workspace loading; reserved built-in names (§6.1) |
| **Custom-agent writes are gated until trusted** | Force `trust_interrupt` on the chat path for custom-agent writes; per-server autonomy cap keyed per fingerprint (§6.3) |
| **Strict tenant tool resolution** | `get_tool` strict `workspace_id` equality for discovered/attached tools; no `None` rows; cache by `(ws, name)` (§6.2) |
| **Lead agent cannot write directly** | Lead is read-only, holds only `delegate`+`respond`; startup-asserted (§3/§5) |
| Plan-on-request, not on everything | Perception `act`-tier gate; non-act tiers never plan (§7) |
| (carried) fail-closed §4 authz, TrustEngine sole autonomous gate, risk-fails-closed, workspace isolation, durable resume + write-idempotency, validated transitions, turn-scoped MCP, atomic re-auth defer | unchanged from hard-replacement §5/§5.1 |

---

## 10. Removal manifest (additions to the hard-replacement §6)

| Component | Replacement | Difficulty |
|---|---|---|
| `intent_classifier` (`classify_intent`/`intent_to_plan`/`extract_plan`, `FAST_INTENTS`) | lead-agent inline reasoning (§4) | deep |
| `chat_pipeline.resolve_plan_routing` + per-step fan-out loop | `delegate` + thin `capability_resolver` sentinel router (§5) | deep |
| `PlanOutput`/`PlanStep`/`CapabilityGap` contracts | lead-agent action + native todo list + Run projection (§4/§8) | deep |
| `Plan` + `PlanTask` models + `PlanStore` + their alembic migrations | Run projection created directly (§8) + DROP migration | deep |
| dedicated `planner` agent + `PLANNER_PROMPT_V2` + `generate_capability_summary` | dropped / re-homed into lead-agent context | moderate |
| `surface_mapping`/`surface_pusher` `PlanOutput` derivation | derive surface kind from executed capabilities / Run projection | moderate |
| `Governor.evaluate_plan`/`evaluate_policy` MCP tool + `system_capability_handler` synthetic PlanTask audit + `routes_approvals` synthetic Plan creation | TrustEngine in `durable_graph` + direct Run/work-item creation + InteractionLog audit | moderate |
| global `agents` table (no `workspace_id`, `UNIQUE(name)`) | workspace-scoped agents + reserved built-ins (§6.1) | deep |
| `TaskRun`/`TaskStep`/`execution_state` | already in hard-replacement §6 (durable_graph + run_projection) | deep |

---

## 11. Risks

1. **`delegate` as a universal escalation primitive (HIGH)** — mitigated by depth-1 leaves +
   child-owned authz + lead-is-read-only + closed enum (§5).
2. **Autonomous trust-gate bypass via delegation (HIGH)** — mitigated by gating-posture inheritance
   (§5#3).
3. **Workspace isolation at the agent layer (HIGH)** — mitigated by §6.1 tenancy.
4. **Fail-closed authz defeated by `ws-OR-NULL` row resolution (HIGH)** — mitigated by §6.2.
5. **Chat-path ungated custom-agent writes (HIGH)** — mitigated by §6.3 (gated-until-trusted).
6. **Graduation bleed across tools via shared capability (HIGH)** — mitigated by per-fingerprint
   `TrustCeiling` (§6.3 / §4.8).
7. **Built-in name squatting (HIGH)** — mitigated by reserved-name enforcement (§6.1).
8. **Per-turn breaker reset / depth DoS (MEDIUM)** — shared breaker state + depth/cycle cap (§5).
9. **Frontend `PlanReady` contract break (MEDIUM)** — the `event_serializer` must emit an equivalent
   payload over the todo list / Run projection (frontend rebuild is out of scope); derive surface
   kind from executed capabilities.
10. **Perception cadence regression (MEDIUM)** — re-home `perception_policy` for non-act tiers; gate
    cross-source synthesis or it becomes the planning loophole (§7).

---

## 12. Testing

Red-team-derived required tests (all must pass before the legacy paths are deleted):
1. A read-only context cannot reach a `mutate`/`destroy` class via `delegate` (escalation blocked).
2. A child built inside `delegate` on the autonomous path still hits the TrustEngine gate.
3. `delegate` rejects `workspace_id`/`user_id` args; the child resolves the parent's tenant only.
4. Depth/cycle cap rejects re-delegation (leaves can't delegate).
5. A custom agent created in workspace A is **not** routable from workspace B.
6. `create_agent` for a reserved built-in name is rejected.
7. An attached server's `mutate` tool does **not** inherit a sibling capability's graduation
   (per-fingerprint `TrustCeiling`).
8. A global-NULL discovered row is **not** selected for a foreign workspace's eligibility check.
9. Only the `act` tier escalates perception to a queued run; non-act tiers never plan; non-act
   cadence does not regress.
10. A custom-agent write on the chat path is gated (approval) until the agent+capability graduates.

---

## 13. Sequencing (interleaves with the hard-replacement §7)

- **Step A — Agent-registry tenancy + reserved names** (DB migration; §6.1). Foundational; unblocks
  custom agents safely. Pairs with hard-replacement Step 1 (authz wiring) and §6.2 strict tool
  resolution.
- **Step B — Lead agent + gated `delegate`** (§3/§5). Built on the hard-replacement Step-1
  `capability_scope` guard; lead is read-only; `delegate` carries all §5 controls. Replaces
  `CapabilityResolver` step-routing + `resolve_plan_routing`.
- **Step C — On-demand planning on the chat path** (§4). Removes `intent_classifier` planning +
  `PlanOutput` on chat; lead acts/decomposes inline; preserve special routes + user-action-block +
  perception-bump. Pairs with hard-replacement Step 3 (chat native). Replace the `PlanReady` SSE
  payload via the `event_serializer`.
- **Step D — Data-model collapse + custom-agent write gating** (§6.3/§8). With hard-replacement
  Step 4 (`durable_graph` + `run_projection`): delete `Plan`/`PlanTask`/`PlanStore`/`PlanOutput`;
  Run projection covers §8; per-fingerprint trust + autonomy cap; force `trust_interrupt` for
  custom-agent writes.
- **Step E — Perception act-tier** (§7). Add the tier; gate planning behind it; re-home cadence;
  decide synthesis gating.
- **Step F — Cleanup** (with hard-replacement Step 5): drop the `planner` agent, `Governor`
  evaluate_plan path, `surface_mapping` PlanOutput derivation; update CLAUDE.md (agents
  workspace-scoped; routing is agentic; planning is on-demand).

---

## 14. Open questions (resolve in Step 0 / writing-plans)

1. Does `delegate` stay LLM-routed (closed enum) or fall back to a deterministic resolver for the
   write→operator hop specifically? (Lead chooses among the workspace's agents; the *enum* keeps it
   safe. Confirm this satisfies "pure agentic" for you.)
2. Do autonomous (`durable_graph`) steps become delegate-routed too, or stay operator-pinned? Today
   the autonomous executor hardcodes operator.
3. Is the lead agent's scope strictly read-only with only `delegate`+`respond`, and is that
   startup-asserted? (Assumed yes.)
4. Does `plan_output_json` have a consumer needing the full tree, or only goal/steps summary?
5. Do `RuntimeEvent` rows survive as their own projection table or fold into LangGraph events?
6. Concrete delegation depth bound (1 vs bounded-2) and the visited-set representation.
7. `perception_policy` cadence for non-act tiers: cheap policy step (extra LLM call) vs deterministic
   defaults?
8. Cross-source synthesis: own act-tier-equivalent gate, or disabled, or always-act?
9. Can a `wrap_tool_call` raise `interrupt()` from inside a tool wrapper, or must `trust_interrupt`
   move to a dedicated node? (Affects how a delegated child carries the gate. Step-0 probe.)
10. What replaces the `PlanReady` SSE `plan_dict` for the existing Next.js renderer (frontend rebuild
    is out of scope)?
