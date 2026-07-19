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

**Behavior-preserving contract (grounding-refined):** the minimal seam is to change **only how each
consumer gets the model's text** — `client.messages.create(...)` → `utility_llm.complete_text(...)` —
and **leave each consumer's existing parse untouched.** All 15 call sites already funnel through the
shared tolerant extractor `src/llm_utils.parse_llm_json(text, default=...)` with a domain-specific
fallback; that stays exactly as-is. Confirmed across all 15: **zero tool-calling, zero streaming**,
**one prefill** (verifier `_llm_judge` prefills `"{"` and re-prepends it — `UtilityLLM.complete_text`
needs an optional `prefill` param), **two `temperature=0`** sites (others omit temperature — the
param must be *unset* vs `0`), **two `system=` block-list shapes** + **one no-system** call
(relevance), and **one text-only** consumer (`_summarize_history`, no parse). `UtilityLLM` reproduces
these knobs; the parse layer is not re-touched. Characterization test per consumer asserts identical
output for identical model text (model mocked at the `UtilityLLM` seam).

## 4. Worker/MCP dual-loop fix (independent prerequisite)

**Root cause (CORRECTED at build — the scout's original transport diagnosis was empirically
disproven; see the build note below).** The collision is in the **DB session factory**, not the
FastMCP transport. Internal MCP tools acquire their session via `_shared._get_db()`
(`src/tools/intelligence_server/_shared.py`), which used the **module-global `_shared._db_factory`**.
That global is set last-writer-wins by `configure_tool_servers()` on BOTH the API thread (per chat
request, `routes_chat.py`) and the worker thread (startup, `run.py`). The engine itself is
`threading.local` (`src/models/database.py` — each thread's asyncpg pool binds to that thread's loop),
but the shared **global pointer** meant a worker background tool call (e.g. `update_execution`) could
run against the API thread's loop-bound engine → `got Future attached to a different loop` (the
Step-10 D4 live-e2e failure). It is **orthogonal to runtime selection** — deleting legacy neither
causes nor fixes it.

**Build note — why the transport diagnosis was wrong.** Three probes: (a) `list_tools()` and (b) a
full `call_tool()` round-trip, each across two concurrent loops on the SHARED global `jarvis_tools`
server, both **succeed** — FastMCP's in-memory transport creates fresh per-connection streams, so
sharing the server object across loops is fine. (c) A real asyncpg session factory bound to loop A,
used from loop B, raises the exact `Future attached to a different loop` error. So the loop-bound
resource is the DB engine reached inside the tool, not the MCP server/client.

**Fix:** resolve the DB factory **per loop**. `_get_db()` uses the thread-local
`get_session_factory()` when no explicit override is configured; `_db_factory` remains a TEST-ONLY
override (tests inject a mock via `configure()`), and the three production `configure_tool_servers`
call sites pass `None`. No per-instance FastMCP server is needed (`build_internal_mcp_server()` was
prototyped then dropped — the transport was never the problem). **Regression test:** two threads, two
concurrent loops, no override, both reach the DB via `_get_db()` (fails RED on the pre-fix
`not configured` guard; the buggy shared-global variant raises the cross-loop `Future` error).

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
- **Obsolete *runtime-selection* settings fields** — `runtime`/`JARVIS_RUNTIME`,
  `shadow_sample_rate`, `rollback_*_threshold`. Static `settings.runtime` checks at `app.py`
  (open pool unconditionally), `routes_health.py`, `checkpoint_reaper_tick.py`, `run.py` drop their
  "else legacy" arm.
- **KEPT — NOT deleted (grounding correction):** `deep_single_lead` and `chat_planless`. The scout
  proved these gate **three distinct live chat *product* shapes** — planless-single-lead
  (`_run_single_lead_planless`), planned-single-lead (`_run_single_lead`), and planned multi-agent
  per-step — which are **orthogonal to runtime**. `deep_single_lead=False` still selects the
  Planner + per-step multi-agent path (now executing on deep). Retiring legacy does NOT collapse
  these; both flags stay. (Only the `effective_chat_runtime()=="deep"` sub-condition inside
  `_resolve_effective_mode` becomes constant-true.)
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

The current 3697-test suite is green **because it exercises the legacy path**. Grounding sized the
migration precisely — **~737 tests across 67 files**, but overwhelmingly mechanical and
**self-distributing across the phases** (the suite never goes broadly red):

| Effort | Files | ~Tests | Lands in |
|---|---|---|---|
| **SWAP-MOCK** — re-point a client mock to the `UtilityLLM` seam | 45 | ~517 | mostly Phase 2 (consumer tests, ~167) + Phase 5 cleanup (~350 defensive stubbers) |
| **REWRITE-TO-DEEP** — drives `agent_loop` / asserts legacy event translation | 12 | ~125 | Phase 4 (per-surface) |
| **DELETE** — 10B / shadow / runtime-selection / Bedrock-only + `test_agent_loop.py` | 10 | ~95 | Phases 4–5 (die with code) |

**Key green-keeping mechanic:** a `@patch("...get_anthropic_client")` stays a harmless no-op as long
as the *symbol* exists, so the ~350 defensive stubbers (that patch the raw client only to avoid real
init while testing execution/trust/orchestrator) do **not** break during re-homing — they need cleanup
only when the factory's *import* is removed in Phase 5. So Phase 2 touches ~167 real consumer tests;
the ~350-test cleanup is a mechanical drop-the-dead-patch pass at the end.

**The genuine-rewrite risk is small and localizable** (Phase 4, per-surface): `test_execution_durability.py`
(23 — cancellation/gauges; the deep runtime has its *own* cancellation model, so these are
*re-established* on deep, not re-pointed), `test_graph_executor.py` (27, mixed),
`test_fix6_orchestrator_error_handling.py` (17 — the legacy `LoopError`→string contract), and the two
per-surface "gate picks legacy vs deep" files (`test_step_runner_deep_executor.py`,
`test_perception_deep_branch.py`). No shared conftest `Loop*` fabricator exists — every fake loop is
file-local (5 copies of a `_fake_loop` idiom), so rewrites don't cascade through one fixture. The one
shared config mutation point is `make_mock_settings` in `conftest.py` (carries `use_bedrock`/
`bedrock_region` — a one-line edit at the Bedrock teardown).

Verify-don't-trust: a phase is not done until the full non-e2e gate is green from a clean checkout.

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

## 10. Open items — RESOLVED by grounding (2026-07-19)

1. **`CancellationRequested` new home → `src/services/execution_support.py`.** It's a bare
   `Exception` subclass (`agent_loop.py:241`), no fields. Users: `dag_runner` (import + 3 `except`
   sites) and `agent_loop` itself (raise `:250`, catch `:926`). `execution_support.py` is the right
   leaf — `dag_runner` already imports it, and it imports only `contracts`/`errors`/`observability`
   (no cycle, does not import `agent_loop`). `agent_loop` re-imports it from the new home until
   `agent_loop` is deleted in Phase 5.
2. **`UtilityLLM` contract → RESOLVED (see §3).** All 15 sites reuse `llm_utils.parse_llm_json`; keep
   each consumer's parse; `UtilityLLM.complete_text` needs `tier`, optional `prefill`, optional
   `system` (string | block-list | none), and unset-vs-0 `temperature`. No tools, no streaming.
3. **`chat_planless` / `deep_single_lead` → KEPT (see §1/§5).** They gate live chat product shapes
   orthogonal to runtime; not inlinable. Only the `effective_chat_runtime()=="deep"` sub-condition
   becomes constant.
4. **Worker/MCP fix seam → per-loop DB factory in `_get_db()` (REVISED at build).** The original
   plan (per-ToolExecutor `build_internal_mcp_server()`) was based on a transport diagnosis that build
   probes disproved — sharing the FastMCP server across loops works fine; the loop-bound resource is
   the DB engine. The actual fix makes `_shared._get_db()` resolve the thread-local
   `get_session_factory()` (no shared global factory); the three production `configure_tool_servers`
   call sites pass `None`. The FastMCP server/`jarvis_tools` global is untouched. See §4 build note.
5. **Test-migration inventory → RESOLVED (see §7).** ~737 tests / 67 files, self-distributing;
   worklist and clusters enumerated.
