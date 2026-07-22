# Agentic Redesign — Design Spec

**Date:** 2026-06-23 · **Status:** Approved shape; ready for implementation plan
**SHAPE SUPERSEDED 2026-06-28** by [`2026-06-28-first-principles-rebuild-design.md`](./2026-06-28-first-principles-rebuild-design.md):
the lead/registry topology, the "workflow" concept (§15), and the gating model are re-based there
(workflow-as-concept and Operator-as-agent are deleted; the gate unifies via a deterministic
`authorization_source`). The tenancy / custom-agent / per-fingerprint-trust controls here are carried
forward.
**Amended 2026-06-24** (§15): adds **dynamic research agents** (Tier-3) + **emergent workflows**
(chat-only); resolves §14 q4 & q8. See §15.
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
- **Dynamic research agents (Tier-3)** and **emergent workflows** extend this architecture — see §15.

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
  `silent` **return without planning** — reading/observing never produces a run. The act-tier turn
  executes a **single bounded gated action** (optionally one read-only research spawn) — **never a
  multi-step workflow** (§15.4).
- Keep the queue/consume plumbing (background tick) as async fulfillment for act-tier work.
- **Re-home `perception_policy` cadence** for non-act tiers (a cheap policy step or deterministic
  defaults) — this is the one real downstream that depended on the Planner being called every cycle.
- **Cross-source synthesis (resolved, §15.4):** produces a **briefing/insight only** — it never
  starts a workflow (workflows are chat-only), closing the tier-less planning loophole.

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
| `backend/src/workflows/*` (`workflow_registry`, `research_agent`, `inbox_triage`, `meeting_prep`, `daily_briefing`, `context`) — hardcoded `WorkflowStep` sequences (banned by CLAUDE.md) | emergent chat-only workflows on `durable_graph` (§15.3); recurring cases → lean scheduled prompts | moderate |

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
10. **Perception cadence regression (MEDIUM)** — re-home `perception_policy` for non-act tiers.
    (Cross-source synthesis is no longer a planning loophole: it produces a briefing/insight only —
    never a workflow — per §15.4.)
11. **Free-form ephemeral agent reopens the deleted `task` surface (CRITICAL)** — mitigated by
    registry-anchored read-only templates with shaped prompts but locked tools (§15.2).
12. **Agent-bomb / no tree-level cost cap (HIGH)** — mitigated by pre-spawn budget admission +
    per-run spawn/concurrency/width caps; workflows chat-only (attended) (§15.5).
13. **Fan-out resume double side-effect (HIGH)** — mitigated by per-`(workspace, step)` idempotency
    ledger + write-as-own-node + `durability="sync"` (§15.6).

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
11. A dynamic research agent cannot be built with a write-class tool or a `delegate` tool (read-only
    leaf; a free-form/LLM-authored agent spec is rejected) (§15.2).
12. A workflow exceeding the per-run spawn / fan-out-width cap is refused (or degraded to cheaper
    models) **before** spawning, not after spend (§15.5).
13. A fan-out workflow killed after one sibling write does **not** re-fire that write on resume
    (per-`(workspace, step)` idempotency ledger) (§15.6).
14. Perception (any tier) cannot start a multi-step workflow; the act tier executes at most a single
    bounded gated action; cross-source synthesis never starts a workflow (§15.4).

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
- **Step G — Dynamic agents & emergent workflows** (§15). After Step D (data-model collapse) + Step E
  (perception act-tier). Tier-3 read-only research agents (chat + act-tier single-spawn); emergent
  **chat-only** workflows on `durable_graph` (Send/reducer fan-in + presenter report); cost-admission
  caps (degrade-then-refuse); per-`(workspace, step)` fan-out idempotency ledger; delete
  `backend/src/workflows/*`. Step-0 probes (ContextVar-per-child, `interrupt()` in `wrap_tool_call`)
  precede the fan-out work.

---

## 14. Open questions (resolve in Step 0 / writing-plans)

1. Does `delegate` stay LLM-routed (closed enum) or fall back to a deterministic resolver for the
   write→operator hop specifically? (Lead chooses among the workspace's agents; the *enum* keeps it
   safe. Confirm this satisfies "pure agentic" for you.)
2. Do autonomous (`durable_graph`) steps become delegate-routed too, or stay operator-pinned? Today
   the autonomous executor hardcodes operator.
3. Is the lead agent's scope strictly read-only with only `delegate`+`respond`, and is that
   startup-asserted? (Assumed yes.)
4. **Resolved (§15.8):** keep a goal/steps decomposition summary in the Run projection for the final
   report + history (no full `plan_output_json` tree required).
5. Do `RuntimeEvent` rows survive as their own projection table or fold into LangGraph events?
6. Concrete delegation depth bound (1 vs bounded-2) and the visited-set representation.
7. `perception_policy` cadence for non-act tiers: cheap policy step (extra LLM call) vs deterministic
   defaults?
8. **Resolved (§15.4):** cross-source synthesis produces a briefing/insight only — it never starts a
   workflow (workflows are chat-only).
9. Can a `wrap_tool_call` raise `interrupt()` from inside a tool wrapper, or must `trust_interrupt`
   move to a dedicated node? (Affects how a delegated child carries the gate. Step-0 probe.)
10. What replaces the `PlanReady` SSE `plan_dict` for the existing Next.js renderer (frontend rebuild
    is out of scope)?

---

## 15. Dynamic agents & emergent workflows (amendment 2026-06-24)

Adds two user-requested capabilities — runtime **dynamic research agents** and multi-step
**workflows** — resolved against every invariant above. Grounded by a second research + adversarial
red-team pass (12 holes, all `holds=false` as first sketched; the controls here are what make them
hold). The four shape decisions (D1–D4) were taken with the user.

### 15.1 Two primitives, not one
These are **opposite halves** of "ultracode" and are specced separately:
- **Dynamic agents = model-driven dynamic dispatch** (Claude Code's `Agent`/Task tool): the lead
  decides at runtime to spawn a short-lived research/exploration worker. Non-deterministic.
- **Workflows = deterministic orchestration** (Claude Code's `Workflow` tool): coded multi-step
  control flow (goal-per-step, fan-out → synthesize → forward → report); only the work *inside* a
  step is model-powered.

They **compose** (a workflow step may contain a dynamic spawn) but are distinct mechanisms.

### 15.2 Feature 1 — Tier-3 dynamic research agents
A new **third agent tier** beside built-in seeds (Tier-1) and custom agents (Tier-2): **ephemeral
research/exploration agents**, spawned at runtime.

**Decision D1 — shaped prompt, locked tools.** The lead authors the child's task/instructions freely
(flexibility); the child's **tool surface is fixed read-only** (security). The two axes are decoupled
— the instruction text is untrusted-but-harmless because it can only direct *reads*. (The free-form
"LLM also picks the tool union" variant is rejected; see H1/H2.)

Required controls (each red-team-confirmed):
- **Registry-anchored kind.** Tier-3 is one (or a few) reserved seed *kind(s)* (e.g. `_research`), so
  `delegate(agent_name)` still validates against the **closed per-workspace enum** (§5#5). A
  free-form / LLM-authored agent spec is **rejected** — that is the deleted `task` surface (H1, CRIT).
- **Read-class only.** Tool union restricted to the read set of `ROLE_ALLOWED_CLASSES`, drawn only
  from **already-fingerprint-approved** servers (§4.1). Build-time filtered + per-call
  `capability_scope` re-applied for the ephemeral role + workspace, fail-closed. No `mutate`/`destroy`
  ever (H2, CRIT). Any surfaced write hands off to the gated operator/custom agent.
- **Leaf.** No `delegate` tool; startup-asserted that no Tier-3 (or any non-lead) agent carries
  `delegate` or a spawn-like attached tool. Preserves depth-1, kills the recursion multiplier (H3).
- **Tenant from parent closure** only — never an LLM arg; fail closed if unresolvable (§5#4, H10).
- **One-shot (v1).** Return-final-only, not resumable — resumability adds Run-projection surface;
  deferred (judgment call; revisit if long exploration is needed).

Net: a Tier-3 agent is "a custom agent minus the DB row" and inherits the **same** §6.1/§6.2/§6.3
controls, but is strictly read-only — so it is never less safe than a custom agent.

### 15.3 Feature 2 — emergent workflows (a verb, not a noun)
**Decision D2 — emergent only.** There is **no `Workflow`/`WorkflowTemplate` entity.** A workflow is
*what on-demand decomposition looks like* when work is multi-step, assembled entirely from parts the
spec already carries forward:

| "Workflow" concept | Built from |
|---|---|
| step has a goal | a todo item (on-demand decomposition, §4) |
| step spins up agents | `delegate` to a Tier-3 research / operator / custom agent (§5) |
| fan-out → synthesize | LangGraph **Send + reducer + synthesize node** on `durable_graph` (resume does **not** re-run completed siblings via `pending-writes`) |
| forward to next step | `{task_id}.output.field` resolution (§5.1#6) |
| final report | the `respond → presenter` special route (§4) |

- **One repeatability mechanism: schedules.** Recurring cases (briefing, triage, meeting-prep) are
  **scheduled prompts** the lead decomposes — not a workflow store. `backend/src/workflows/*` (the
  banned hardcoded-`WorkflowStep` sequences) is **deleted** (§10).
- Re-apply the acyclicity/dependency validation `PlanOutput`'s `model_validator` did, re-homed on the
  todo list / Run projection. A pre-computed declarative step-DAG is **forbidden** — that is the
  deleted `Plan`/`PlanTask` reincarnated (H5).

**Decision D3 — chat-only.** Workflows run **only on the user-initiated chat path** (a human is
present and can interrupt). This dissolves the worst red-team holes, all of which were on the
*unattended* path (H6 agent-bomb, H7 plan-everything, H8 synthesis loophole, the unattended half of
H9).

### 15.4 Where each capability may run (the path matrix)

| Path | Dynamic agents | Workflows | Writes |
|---|---|---|---|
| **Chat** (user-initiated) | yes — full, incl. wide fan-out + loop-until-dry | **yes** | gated per §6.3 (custom) / ungated built-in operator |
| **Perception `act` tier** | at most **one** read-only research spawn to inform one action | **no** | single bounded action, TrustEngine-gated |
| Perception `briefing`/`push`/`silent` | no | no | surface only — never a run |
| **Scheduled** runs | no fan-out — lean single-turn | **no** | gated |
| **Cross-source synthesis** | no | **no** | briefing/insight only — never a workflow |

**Decision D4 — lean act tier survives.** The perception `act` tier still executes a **single
bounded, gated action** (one lead-agent turn, gated writes, optionally one read-only research spawn)
— but **never a multi-step workflow**.

### 15.5 Cost / spawn admission control (agent-bomb, H6 HIGH)
`BudgetTracker` is **reactive** (pauses at 95% *after* spend) with no pre-spawn projection and no
spawn cap — unacceptable for multi-tenant fan-out. Required:
- **Pre-spawn budget admission**: project a fan-out's cost against remaining daily budget.
  **Degrade-then-refuse** — first downgrade non-critical children to a cheaper model (per-child
  `model` override → Haiku/Sonnet); hard-refuse to start/expand only if still over (judgment call).
- **Per-run total-spawn cap** + **max-concurrency** (LangGraph `max_concurrency`) + **max-fan-out
  width** + **loop-until-dry round ceiling** (chat-only). Concrete numbers fixed in the plan.
- Depth-1 leaf (15.2) kills the recursion multiplier; the shared **process-global** Anthropic breaker
  is **not** reset per child (H12).

### 15.6 Fan-out durability & idempotency (H9 HIGH, H10 CRIT, H12)
Durable resume is durable **replay** (at-least-once), so a fan-out where one child wrote and a
sibling failed will **re-fire** that write on resume without protection. Required:
- Each external write = its **own minimal node** behind an **idempotency ledger keyed
  `(workspace_id, step_id)`** (unique per child) that short-circuits replay (§5.1#1).
- `durability="sync"` for irreversible-write nodes; the **`trust_interrupt`/approval node is separate
  from the send node** so interrupt-replay can't re-send.
- **Tenant per child** from the parent closure; **strict `(workspace_id, name)` tool resolution**
  (§6.2); **Step-0 probe** that `turn_scope`'s ContextVar propagates into each concurrent child
  `astream` (MCP sessions ref-counted/torn down) (H10).
- **Idempotent cost rollup** across all children and resume segments (§5.1#7, H12).

### 15.7 Write gating inside workflow steps (H4 CRITICAL)
Claude Code auto-approves writes inside steps. Jarvis **rejects that posture wholesale**: every write
step is a **`trust_interrupt` suspension** through TrustEngine mid-run; a delegated child inside a
step inherits the parent step's full middleware **including `trust_interrupt`** (§5#3). There is no
"saving is authorization" gap because nothing is saved (emergent only); each step's `agent_name` is
re-validated against the caller's workspace registry at run time, and custom-agent steps never inherit
the built-in-operator chat-path exemption (H11).

### 15.8 Run-projection additions (extends §8)
The Run projection must additionally model:
- the **N-children → 1-synthesis fan-in** shape (new — today `PlanTask` is one-agent-per-step);
- **per-step `goal`/`success_criteria`**;
- **per-child `idempotency_key`** keyed `(workspace_id, step_id)` (15.6);
- **ephemeral-agent attribution** (Tier-3 kind-id + parent linkage — no stable registry name);
- **idempotent cross-child cost rollup**;
- a **goal/steps decomposition summary** retained for the final report + history (resolves §14 q4).

### 15.9 Decisions locked + red-team holes addressed

| Decision | Value |
|---|---|
| D1 dynamic-agent definition | shaped prompt, **locked read-only tools**, registry-anchored kind |
| D2 workflow object | **emergent only** (no entity; `src/workflows/*` deleted) |
| D3 workflow path | **chat-only** |
| D4 act tier | **lean act tier survives** (single bounded gated action, no workflow) |

| Hole | Sev | Control | Where |
|---|---|---|---|
| H1 free-form reopens `task` | CRIT | registry-anchored read-only template | 15.2 |
| H2 ephemeral write escalation | CRIT | read-class-only, fingerprint-approved | 15.2 |
| H3 ephemeral re-delegation | HIGH | leaf + startup-assert | 15.2 |
| H4 step write bypasses TrustEngine | CRIT | `trust_interrupt` per write step | 15.7 |
| H5 workflow resurrects deleted DAG | HIGH | emergent pattern, no Plan schema | 15.3 |
| H6 agent-bomb | HIGH | pre-spawn admission + caps; chat-only | 15.4/15.5 |
| H7 perception plan-everything | HIGH | workflows chat-only; act-tier no workflow | 15.4 |
| H8 cross-source synthesis loophole | HIGH | synthesis → surface only, never workflow | 15.4 |
| H9 fan-out resume double-write | HIGH | per-`(ws, step)` idempotency ledger | 15.6 |
| H10 tenant leak across fan-out | CRIT | closure tenant + strict resolution + probe | 15.6 |
| H11 unsafe authored step | HIGH | moot (emergent); run-time agent re-validation | 15.7 |
| H12 breaker/cost per child | MED | shared breaker + idempotent rollup | 15.5/15.6 |

### 15.10 Open items for the plan / Step-0
- Concrete cap numbers: per-run spawn cap, `max_concurrency`, max fan-out width, loop-until-dry round
  ceiling.
- Step-0 probes: ContextVar propagation per concurrent child `astream` (H10); whether
  `wrap_tool_call` can raise `interrupt()` or `trust_interrupt` must be a dedicated node (§14 q9 —
  affects how a fan-out child carries the gate).
- Whether the synthesize node gets its own advisory verification verdict (§5.1#4) or inherits the
  run-level one.
- Deferred: Tier-3 resumability; recurring *heavy* (fan-out) scheduled work.
