# Step 11 — Legacy Runtime Retirement (design)

> **Status:** DESIGN (2026-07-19), brainstormed + grounded (2 verify-don't-trust scouts:
> direct-SDK blast radius + 10B control-plane / worker-MCP map). On branch
> `rebuild/first-principles` (off an untouched `main`, NEVER pushed). This is the **eventual
> end-state** of the first-principles rebuild — retire the legacy runtime entirely so the
> Deep Agents runtime is the ONLY runtime on all three surfaces (chat, perception, autonomous).
> Implement via superpowers:writing-plans → subagent-driven-development.

## 0. Decisions locked in brainstorm (2026-07-19)

- **Shape = B (retire on-branch NOW, ahead of prod).** All deletion happens on the unmerged
  `rebuild/first-principles` branch. Rollback lever = `git revert` of the branch, NOT a runtime
  flag. (Approaches A "prep-now/delete-after-prod-soak" and C "design-only" were declined.)
- **10B control plane = B1 (full collapse).** Runtime *selection* and the entire rollback-to-legacy
  machinery are deleted, not preserved as a thin kill-switch. One runtime ⇒ that machinery is dead
  code by definition.
- **No Bedrock. Pure Claude API via `langchain-anthropic` (`ChatAnthropic`).** All Bedrock machinery
  is removed. The "Bedrock-on-deep gap" is *deleted*, not closed.
- **Session boundary:** everything lands on-branch. **No push / merge / deploy** in this effort.
  Full gate green + independent review at every checkpoint. Subagent-driven; main loop owns
  verify + commit + all hot-file mutation synchronously.

## 1. End-state & scope

**One runtime.** Delete the legacy `agent_loop` execution engine and the raw
`anthropic.AsyncAnthropic` client factory. `build_deep_agent` becomes the *only* way an agent
executes, on every surface. Runtime *selection* collapses: `effective_runtime()` and the entire
10B control plane (runtime breaker, auto-rollback watcher, admin escape hatch, shadow runner) are
deleted.

### In scope
- Delete `src/orchestrator/agent_loop.py` (the legacy execution engine).
- Re-home **all** remaining raw-SDK callers onto a unified LangChain model (`UtilityLLM`).
- Introduce **one** provider-simple LangChain model constructor (`build_langchain_model`) that both
  deep agents and utility calls use.
- **Remove all Bedrock machinery** — `JARVIS_USE_BEDROCK`, `settings.use_bedrock`,
  `AsyncAnthropicBedrock` construction, Bedrock model-ID mapping tables, and the Bedrock arms of
  `resolved_model` / `get_haiku_model`.
- Collapse runtime selection to deep-only; delete the 10B control plane (§ below).
- Fix the worker/MCP dual-loop bug (independent prerequisite for autonomous-deep safety).
- Rewrite the affected docs (CLAUDE.md R1 + the `agent_loop` resilience section).

### Explicitly NOT in scope (unchanged by this)
The Planner, `CapabilityResolver`, capability-based multi-agent routing, `intent_classifier`
**as a component**, TrustEngine / permission-gate / `capability_scope`, A2UI, the memory/world-model
pipelines' *logic*. Deleting `agent_loop` makes the planned/multi-agent chat route **execute on
deep** (each capability-routed agent built via `build_deep_agent`) — it does **not** remove cognitive
routing. `intent_classifier.classify_intent` is **re-homed onto `UtilityLLM`, not deleted**.

Also deferred (recorded, not gaps): the librarian→middleware / presenter-inline collapse and the
6→4 agent-row-drop migration (rides a later track; stranded agent rows are harmless — governor/operator
precedent). Keep **zero migrations** in this effort.

## 2. The unified model layer (one constructor, pure Claude API)

Today two things build models: `deep_runtime/model_factory.build_chat_model(agent)` (deep agents,
direct `ChatAnthropic`) and **13** scattered raw-SDK call sites. This design funnels **both** through
a single constructor:

```
model_factory.build_langchain_model(tier, *, temperature, max_tokens, thinking=None) -> ChatAnthropic
    ChatAnthropic(model=MODEL_TIER_IDS[tier], api_key=settings.anthropic_api_key,
                  max_tokens=max_tokens, **thinking_or_temperature_params)
    # thinking/effort mapping preserved as-is (Anthropic-native):
    #   adaptive models (Opus 4.7/4.8, Fable): thinking={"type":"adaptive","display":"summarized"}
    #                                           + effort=effort_for_budget(budget); temperature omitted
    #   legacy models: thinking={"type":"enabled","budget_tokens":budget<max_tokens} + temperature=1
    #   thinking off:  temperature=agent.temperature (or 0 for utility)

build_chat_model(agent)        -> build_langchain_model(..., thinking=<from agent>)   # deep agents
build_utility_model(tier, ...) -> build_langchain_model(..., thinking=None)           # plain completions
```

`UtilityLLM` is the thin helper the re-homed consumers call:
- `async complete_text(system, user, *, tier, temperature=0, max_tokens) -> str`
- `async complete_json(system, user, *, tier, schema=None, prefill=None, ...) -> dict`
  (uses `.with_structured_output(schema)` when a Pydantic schema exists; otherwise mirrors each
  consumer's current prefill + `json.loads` + fallback so output shape is identical.)

**Why this shape.** The (now-removed) Bedrock gap and the re-homing were the same problem —
"construct a LangChain model." Funneling deep agents *and* utility calls through one constructor
keeps the Anthropic-native thinking/effort mapping in **exactly one place** and means the utility
consumers (all `thinking=None`, plain text/JSON) get a trivial code path. No provider branch, no
`init_chat_model`, no Bedrock verification item.

**API-key plumbing.** `ChatAnthropic` must be passed `api_key=settings.anthropic_api_key`
(`JARVIS_ANTHROPIC_API_KEY`) explicitly — LangChain otherwise reads the unprefixed
`ANTHROPIC_API_KEY` which Jarvis never sets (this was one of the two live-e2e blockers in Step-10;
the constructor enforces it for all callers).

## 3. Re-homing the shared machinery (behavior-preserving)

Of the 13 raw-SDK callers, **only `agent_loop` dies**; the other **12 survive and get re-homed**
onto `UtilityLLM`. All 12 are **non-streaming, non-tool, text-or-JSON** completions — the easy kind.
Grouped for cohesive commits + one characterization test per consumer:

| Group | Consumers (file) | Model tier |
|---|---|---|
| Perception / ingest | `event_processor` scoring, `relevance_assessor`, `world_model` extraction | resolved / Haiku |
| Memory | `memory_service` extraction + preferences, contradiction check | resolved |
| Execution / verify | `step_runner.minimal_claude_action`, `verifier._llm_judge` | resolved |
| Context / presentation | `context_assembler._summarize_history`, `presenter` briefing/meeting-prep | Haiku / resolved |
| Governance (load-bearing) | `risk_assessor.assess_risk`, `governor_delegate_critique` | Haiku |
| Chat routing | `intent_classifier.classify_intent` | Haiku |

**`risk_assessor` is the critical one:** three deep-runtime middlewares (`permission_gate`,
`trust_gate`, `readback`) call it — the surviving path depends on it. `governor_delegate_critique`
already *lives inside* the deep runtime yet still calls the raw client — clearest proof that
"deep path" ≠ "off the raw client."

**Behavior-preserving contract:** same model IDs, same params (temperature/max_tokens), same JSON
parsing + fallbacks. Each consumer's `client.messages.create(...)` + parse becomes a single
`utility_llm.complete_*` call. Characterization test asserts identical output for identical input
(model mocked at the `UtilityLLM` seam).

## 4. Worker/MCP dual-loop fix (independent prerequisite)

**Root cause (confirmed by scout):** the module-global singleton `jarvis_tools = FastMCP(...)`
(`src/tools/server.py`) plus a per-`ToolExecutor` cached `_internal_client`
(`tool_executor.py` — created via `Client(jarvis_tools).__aenter__()` and cached forever). The
in-memory transport binds to whichever event loop enters it first. In `run.py --worker`, API and
worker run in one process on **separate** loops; the worker's later internal-tool call collides →
`got Future attached to a different loop`. This blocks internal-MCP writes from the worker/scheduler
(the exact failure the Step-10 D4 live e2e hit on `update_execution`). It is **orthogonal to runtime
selection** — deleting legacy neither causes nor fixes it.

**Fix:** loop-isolate the internal MCP server/client — each `ToolExecutor` (API and worker) gets its
own FastMCP server instance + client bound to its own loop, rather than sharing the process-global
`jarvis_tools`. Provide a `build_internal_mcp_server()` factory; construct + enter the client on the
loop that owns the `ToolExecutor`. **Dedicated dual-loop regression test** (simulate two loops, assert
an internal tool call succeeds from the second). Highest-risk *mechanical* change ⇒ its own phase.

## 5. What becomes dead code when legacy is gone (deletion inventory)

Confirmed by the control-plane scout (file references are the durable anchors; verify at build):

- **`agent_loop.py`** — the engine. 3 real inbound import edges: `agent_invoker`, `step_runner`,
  `dag_runner`. The **one hard code edge** is `dag_runner` importing `CancellationRequested` *from*
  `agent_loop` → re-home that symbol (to a neutral module) **before** deleting `agent_loop`.
- **`runtime_gate.effective_runtime`** — collapses to the constant `"deep"`, then inlines away; delete
  the 4-tier resolve + cache + every `runtime == "deep"` branch across `agent_invoker`,
  `chat_processor`, `step_runner`, `graph_executor` (lease/drive/reconcile gates + `_should_jit`).
- **`runtime_breaker.py`** — trip/clear/breaker_state/override tiers (only target is `"legacy"`).
- **`scheduler/runtime_rollback_tick.py`** (`RuntimeRollbackTickMixin`) — trips to a runtime that no
  longer exists.
- **`api/routes_admin_runtime.py`** — `_VALID_TARGETS = ("legacy",)`; whole router obsolete.
- **`orchestrator/shadow_runner.py`** + `AgentInvoker.run_shadow_turn` + `ShadowToolExecutor` /
  `_IntentRecordingShadowExecutor` / `DivergenceComparator` wiring — no second runtime to compare.
- **Legacy branches** in `agent_invoker.call_agent_stream` / `call_agent`, `step_runner`
  (`run_step_via_agent_loop`, `minimal_claude_action` fallback chain), `graph_executor`
  (`_run_step_via_agent_loop` facade).
- **Bedrock machinery** — `settings.use_bedrock` + `JARVIS_USE_BEDROCK`, `AsyncAnthropicBedrock`
  branch in `get_anthropic_client`, Bedrock model-ID mappings, `resolved_model` / `get_haiku_model`
  Bedrock arms.
- **`get_anthropic_client` / `close_anthropic_client` / `_anthropic_client` singleton** — the raw
  client factory (`settings.py`); deletable once all 12 consumers are re-homed and `agent_loop` gone.
- **Obsolete settings fields** — `runtime`/`JARVIS_RUNTIME`, `deep_single_lead`, `chat_planless`
  (becomes always-on / inlined — confirm at build), `shadow_sample_rate`, `rollback_*_threshold`.
  Static `settings.runtime` checks at `app.py` (open pool unconditionally), `routes_health.py`,
  `checkpoint_reaper_tick.py`, `run.py` drop their "else legacy" arm.
- **Metrics** — `AGENT_RUNTIME_CALLS` runtime label goes single-valued; the rollback counters' watcher
  consumer dies (counters may stay as observability).
- **Stale doc comments** — `deep_runtime/_thinking.py`, `model_factory.py`,
  `middleware/{unavailable_server,capability_scope}.py` "mirrors agent_loop" references.

## 6. Phase ordering (all on-branch; full gate + independent review each)

1. **Model layer** — add `build_langchain_model` (pure `ChatAnthropic`) + `build_utility_model` +
   `UtilityLLM`; route `build_chat_model` through the new constructor. Additive, dormant, byte-neutral
   on live paths. (No Bedrock branch.)
2. **Re-home shared machinery** — switch the 12 consumers to `UtilityLLM`, group by group,
   behavior-preserving + characterization tests. After this, `agent_loop` is the **only** raw-SDK
   caller left.
3. **Worker/MCP dual-loop fix** — loop-isolated internal MCP + dual-loop test. (Independent; may run
   first.)
4. **Collapse runtime selection + delete 10B + Bedrock** — re-home `CancellationRequested`; inline
   `effective_runtime → "deep"`; delete breaker / watcher / escape-hatch / shadow-runner + all legacy
   branches; strip Bedrock + obsolete settings fields. **Test re-homing is first-class here** (see §7).
5. **Delete `agent_loop` + raw client factory** — remove the module, `get_anthropic_client`, the
   `AsyncAnthropic`/`AsyncAnthropicBedrock` construction; fix stale `deep_runtime/*` comments.
6. **Docs / CLAUDE.md R1** — collapse "two execution paths" → one; rewrite the `agent_loop` resilience
   section; remove legacy / 10B / shadow / Bedrock references. Subsumes Step-10's deferred R1.

Ordering rationale: additive foundation → behavior-preserving re-homing (shrinks the raw-SDK surface
without touching runtime selection) → subtractive deletion (safe because nothing depends on legacy
anymore) → final deletion → docs. Each phase leaves the tree green.

## 7. The biggest hidden cost — test migration (surfaced up front)

The current 3697-test suite is green **because it exercises the legacy path** (tests mock `agent_loop`
and `@patch("src.orchestrator.jarvis.get_anthropic_client")`). Phase 2 is gentle — swap the mock
target per consumer to the `UtilityLLM` seam. But **Phases 4–5 flip the default runtime to deep for
the whole suite**: every test that drove `agent_loop` or patched the raw client must be rewritten to
the deep runtime or retired. That test re-homing is likely the **largest** chunk of Phases 4–5 — not
the deletions themselves. The plan treats it as first-class work, not cleanup. Verify-don't-trust:
a phase is not done until the full non-e2e gate is green from a clean checkout.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Re-homing silently changes a shared helper's output | Behavior-preserving + characterization test per consumer (same in → same out) |
| Worker/MCP fix regresses internal tool calls | Dedicated dual-loop test; isolated phase |
| Collapsing to deep-only breaks the suite en masse | Test re-homing is first-class Phase-4/5 work; full gate green before each phase closes |
| `CancellationRequested` re-home missed → import break at delete | Re-home the symbol in Phase 4 before Phase-5 deletion; grep for residual `agent_loop` imports |
| Prod cutover (later E+F) has no in-band rollback | Accepted under B; rollback = revert branch + redeploy. Documented in the deploy runbook |
| Prod runs on Bedrock today | Deploy-time (Phase-F) item: switch prod to a direct `JARVIS_ANTHROPIC_API_KEY`; no AWS Bedrock access after this. Recorded, not blocking on-branch work |

## 9. Deploy consequence (record for the eventual Phase-F, not this effort)

After this retirement, prod **must** reach `api.anthropic.com` directly with `JARVIS_ANTHROPIC_API_KEY`.
If the current prod deploy runs `JARVIS_USE_BEDROCK=true`, the eventual deploy must provision a direct
Anthropic key and drop the Bedrock env. Confirm prod's current Anthropic access mode before Phase F.

## 10. Open items for the plan (resolve during writing-plans / grounding)

1. **`CancellationRequested` new home** — pick the neutral module (a leaf under `orchestrator/` or a
   small `execution_support`); confirm no other legacy symbol is imported by survivors.
2. **`UtilityLLM` JSON contract** — confirm each of the 12 consumers' exact output parsing (prefill
   `"{"`, `.with_structured_output`, bare `json.loads`) so the helper reproduces it faithfully.
3. **`chat_planless` / `deep_single_lead` fate** — become always-on/inlined vs kept as flags; confirm
   at grounding (they gate the single-lead branch that is now the only branch).
4. **Worker/MCP fix seam** — confirm the `build_internal_mcp_server()` + per-ToolExecutor ownership
   does not break external-MCP bridge init (`_ensure_worker_mcp_bridge`) or turn-scoped teardown.
5. **Test-migration inventory** — enumerate the tests that mock `agent_loop` / `get_anthropic_client`
   (the Phase-4/5 re-home worklist) before starting the collapse.
