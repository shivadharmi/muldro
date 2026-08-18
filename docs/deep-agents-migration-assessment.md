# Deep Agents Migration Assessment & Plan

**Status:** Draft for decision · **Date:** 2026-06-22 · **Author:** architecture audit
**Question:** Should Muldro replace its hand-rolled multi-agent runtime with LangChain's
**Deep Agents** framework (`deepagents`), and if so, how?

> **⚠️ Strategy superseded (2026-06-22):** the team chose a **hard replacement** over the
> strangler-fig. The gradual strategy below (Part E Option 1, Part F phasing, the
> `MULDRO_RUNTIME` flag, the `LoopEvent` adapter) is **superseded** by
> `docs/superpowers/specs/2026-06-22-deep-agents-hard-replacement-design.md` (a local
> planning doc — `docs/superpowers/` is untracked/gitignored and not part of the repo).
> Everything else here remains the source of truth — the audit (Part B), the invariants
> register (§B.7), the constraints (§B.8) — and the **already-built** Phase 0 spike + Phase 1
> `deep_runtime` foundation/middleware (commit `da8c459`) **carry forward** as the foundation
> of the hard replacement.

> **Decisions locked (2026-06-22):**
> 1. **Scope = Full** — LangGraph also replaces `graph_executor.py` + `execution_state.py`
>    durable DAG (Option 1). Not runtime-only.
> 2. **Model layer = Direct Anthropic API** (not Bedrock) — `model="anthropic:claude-opus-4-8"`
>    via `langchain-anthropic` `ChatAnthropic`. This **removes the top risk** (Bedrock/
>    `ChatBedrockConverse` adaptive-thinking) and replaces it with a much smaller
>    "confirm `ChatAnthropic` passes adaptive thinking + effort" check.
> 3. **Appetite = Full migration commitment** — execute the strangler-fig through Phase 5
>    cleanup (assuming Phase 0 confirms the model layer).
>
> Open (my recommendations stand): keep deterministic **capability-based routing**
> (`CapabilityResolver`); LangSmith adoption optional.
>
> **✅ Phase 0 gate PASSED (2026-06-22, live against Opus 4.8).** Both goals confirmed —
> migration is viable, `build_thinking_params` can be deleted. Pinned stack: `deepagents`
> 0.6.11, `langchain` 1.3.10, `langgraph` 1.2.6, `langchain-anthropic` 1.4.6,
> `langchain-core` 1.4.8, `anthropic` 0.111.0 (Python 3.12.8 venv).
> - **G1:** `ChatAnthropic(model="claude-opus-4-8", thinking={"type":"adaptive",
>   "display":"summarized"}, effort="high")` works natively; temperature omitted (None is
>   dropped from the body). Adaptive = model self-decides whether to think → the mid-loop
>   thinking-fallback hack is likely unneeded. Usage on `resp.usage_metadata` +
>   `resp.response_metadata["usage"]` (incl. `thinking_tokens`, cache fields).
> - **G2:** `@wrap_tool_call` → `f(request, handler)`; `request.tool_call` is a dict
>   (`name`/`args`/`id`); return `ToolMessage(tool_call_id=request.tool_call["id"])`
>   WITHOUT calling `handler` to block (fail-closed). `@after_model` → `f(state, runtime)`;
>   usage from `state["messages"][-1].usage_metadata`. `@wrap_model_call` → `f(request,
>   handler) -> ModelResponse` for model-boundary interception.
> - **Test-validity lesson:** capability-scope tests MUST use benign-looking out-of-scope
>   tools — Opus 4.8 self-refuses scary-named calls, so the interceptor never runs and you
>   falsely test the model's conscience instead of your enforcement code.

> **Sourcing note.** The Deep Agents facts below were ground-truthed against the
> authoritative LangChain API reference (Context7 `/websites/reference_langchain` and
> `/websites/langchain_oss`) — the `create_deep_agent` signature, middleware surface,
> backends, and HITL are quoted from those docs. A parallel deep-research web pass
> extracted 25 corroborating claims from primary sources (docs.langchain.com, GitHub,
> PyPI, the LangChain blog) but its adversarial *verification* stage could not run (an
> account session-limit outage made every verifier abstain — recorded as "refuted",
> which is a false negative, not a real refutation). Treat the API-surface facts as
> confirmed; treat the softer "production-readiness" claims as **plausible but
> independently unverified** until the verification pass is re-run.

---

## 0. TL;DR — Recommendation

**Adopt Deep Agents for the agent *runtime*, re-home Muldro policy as LangChain
middleware, and keep the domain layer. Do it as an incremental strangler-fig, not a
big-bang rewrite.**

- Deep Agents (built on LangGraph) is a strong, well-fitting replacement for the
  generic ~60% of `agent_loop.py` / `graph_executor.py`: the multi-round tool loop,
  retry/backoff, circuit breaking, mid-loop thinking fallback, conversation
  summarization, sub-agent delegation, planning (todos), durable checkpoint/resume,
  and human-in-the-loop pause/resume.
- The Muldro-specific ~40% — **capability-scope enforcement**, the **TrustEngine 4×4
  approval gate**, **RiskAssessor (fail-closed)**, **per-tool cost attribution +
  budget**, **ContextPack injection**, **turn-scoped MCP**, **A2UI typed surfaces**,
  **capability-based routing**, **workspace isolation**, and **Bedrock + Opus-4.8
  adaptive-thinking** — is NOT provided by Deep Agents and must be preserved. The good
  news: every one of these maps onto a LangChain **middleware hook**
  (`@wrap_tool_call`, `@wrap_model_call`, `@before_model`, `@after_model`,
  `@dynamic_prompt`, `@before_agent`, `@after_agent`) or onto a custom subagent/backend.
- **Model-layer risk is now LOW (decision: direct Anthropic API).** With
  `model="anthropic:claude-opus-4-8"` via `langchain-anthropic` `ChatAnthropic`, the
  Opus-4.8 adaptive-thinking + effort surface passes through far more directly than the
  rejected Bedrock/`ChatBedrockConverse` path. Phase 0 confirms it rather than gambling
  on it. (If `ChatAnthropic` can't express adaptive thinking/effort, fallback is a thin
  custom `BaseChatModel` wrapping the raw Anthropic client with `build_thinking_params`
  verbatim — small, isolated.)
- **Scope is FULL (decision):** LangGraph replaces `graph_executor.py` +
  `execution_state.py` durable DAG via checkpointer/interrupts, not just the per-step
  `agent_loop`. Full migration through Phase 5.

Estimated effort for full replacement via strangler-fig: **~6–10 focused weeks**, with
the autonomous/GraphExecutor path (durable resume + TrustEngine) as the last and
riskiest phase.

---

## Part A — What Deep Agents actually is

`deepagents` is a LangChain-maintained Python package that sits **between** the
lightweight `langchain.create_agent` and raw LangGraph. It is an *opinionated harness*:
it bundles a curated middleware stack + a detailed system prompt and returns a compiled
LangGraph graph.

### A.1 Entry point

```python
create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,                 # AGENTS.md sources
    permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | BackendFactory | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,  # HITL
    response_format: ... | None = None,              # structured output
    state_schema: type[DeepAgentState] | None = None,
    context_schema: type[ContextT] | None = None,
    checkpointer: Checkpointer | None = None,        # durable resume
    store: BaseStore | None = None,                  # long-term memory
    debug: bool = False, name: str | None = None, cache: BaseCache | None = None,
) -> CompiledStateGraph
```

**Key consequence:** the return value is a LangGraph `CompiledStateGraph`. Adopting
Deep Agents = adopting LangGraph as the execution substrate. You get
`.ainvoke()/.astream()`, checkpointer-based durable execution, `interrupt`/`Command`
HITL, and the LangGraph store "for free" — but you also inherit LangGraph's state model
and its way of doing persistence, streaming, and resume.

### A.2 The four pillars (what makes it "deep")

A deep agent is a tool-calling loop elevated by four things:
1. **Planning tool** — `write_todos` / `TodoListMiddleware`. A context-engineering
   no-op that keeps a long task on-track (it does not execute anything; it just
   maintains a visible todo list in state). *Muldro equivalent: `PlanOutput`/`PlanStep`
   + Planner agent — but Muldro's is a real executable DAG, not a scratchpad.*
2. **Sub-agents** — `SubAgentMiddleware` / `AsyncSubAgentMiddleware`. First-class
   delegation with **isolated context windows**; each subagent has its own
   name/description/system_prompt/model/tools/middleware/permissions. The parent calls
   them as tools. *Muldro equivalent: the 7 SubAgents + CapabilityResolver routing.*
3. **Virtual filesystem** — `FilesystemMiddleware` exposing `ls/read_file/write_file/
   edit_file/glob/grep/execute`, backed by a pluggable **backend**:
   - `StateBackend` — "mock" FS in LangGraph state (default; ephemeral per thread)
   - `FilesystemBackend(root_dir=...)` — real disk
   - `StoreBackend(store=...)` — LangGraph long-term store (persistent across threads)
   - `CompositeBackend(default=..., routes={...})` — route path prefixes to different
     backends (e.g. `/skills/` → store, everything else → sandbox)
   - sandbox backends (Daytona, OpenSandbox) + `LocalShellBackend`
4. **Detailed system prompt** — a long, opinionated harness prompt the agent ships
   with (you append your own via `system_prompt=`).

### A.3 The extension mechanism = middleware (this is the crux)

Everything custom hooks in via **LangChain middleware**. Either subclass
`AgentMiddleware` or use the decorators:

| Decorator | Fires | Muldro policy that belongs here |
|---|---|---|
| `@before_agent` | once before the run | turn-scope setup, budget hydration, ContextPack assembly start |
| `@after_agent` | once after the run | **turn-scoped MCP teardown (`TurnScope`)**, final budget commit |
| `@dynamic_prompt` | builds the system prompt per model call | **ContextPack injection** (entities, memories, preferences, goals) |
| `@before_model` / `@after_model` | around each model call | **per-call token/cost attribution → budget**, metrics |
| `@wrap_model_call` | wraps each model call | **AnthropicCircuitBreaker**, **Bedrock/Opus-4.8 adaptive-thinking params**, model fallback |
| `@wrap_tool_call` | wraps each tool call | **capability-scope enforcement**, **per-tool cost attribution**, **auth_required per-turn breaker**, secret sanitization |
| `@hook_config` | conditional routing | edge-case fallback (Governor-style) |

Built-in middleware you'd get for free (and could delete bespoke code for):
`TodoListMiddleware`, `FilesystemMiddleware`, `SubAgentMiddleware`,
`SummarizationMiddleware` + `SummarizationToolMiddleware`, `SkillsMiddleware`,
`MemoryMiddleware` (AGENTS.md), `HumanInTheLoopMiddleware`, `ModelCallLimitMiddleware`,
`ToolCallLimitMiddleware`, `ToolRetryMiddleware`, `ModelFallbackMiddleware`,
`PIIMiddleware`, `ContextEditingMiddleware`, `ShellToolMiddleware`, `RubricMiddleware`.

### A.4 Human-in-the-loop (maps to TrustEngine — partially)

```python
agent = create_deep_agent(
    model=..., tools=[...],
    interrupt_on={
        "send_email":  {"allowed_decisions": ["approve", "edit", "reject"]},
        "write_file":  True,     # all decisions
        "read_file":   False,    # never interrupt
    },
    checkpointer=AsyncPostgresSaver(...),   # REQUIRED for HITL
)
# Pause surfaces as result.interrupts; resume:
agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config={...})
```

Also: `FilesystemPermission(operations=[...], paths=[...], mode="allow|deny|interrupt")`
for per-path gating. **Important limitation:** `interrupt_on` is keyed on **tool name**
with a *static* policy. Muldro's gate is **dynamic** (a 4×4 `trust_level × risk_level`
matrix evaluated at runtime by `TrustEngine.evaluate()`, with graduation/demotion). So
HITL gives you the *pause/resume plumbing* but **not** the decision logic — that stays a
custom middleware that decides whether to raise an interrupt.

### A.5 Persistence, streaming, provider, MCP

- **Persistence:** `checkpointer` (e.g. `AsyncPostgresSaver` — Muldro already runs
  Postgres) for thread/run durability + resume; `store` (`BaseStore`, Postgres-backed
  available) for long-term cross-thread memory.
- **Streaming:** LangGraph stream modes (`messages`, `updates`, `values`, `custom`) —
  this is what would feed the A2UI `SurfaceUpdate` SSE/WebSocket pipeline.
- **Provider:** model as `"anthropic:claude-…"` string or a `BaseChatModel` instance.
  Bedrock via `langchain-aws` (`ChatBedrockConverse`). Per-subagent model override.
- **MCP:** via `langchain-mcp-adapters` (`MultiServerMCPClient.get_tools()` → LangChain
  tools). Muldro's `mcp_pool` / `turn_scope` could be replaced or wrapped.
- **Observability:** LangSmith tracing/eval/deploy is the native path (replaces Muldro's
  `MuldroTrace`/spans if desired).

---

## Part B — Current Muldro agent architecture (audit)

### B.1 Two execution paths (a load-bearing invariant)

1. **Chat path** (`orchestrator/chat_processor.py`, `muldro.py` `process_message[_stream]`):
   single-step / lightweight plans execute inline via the agent loop with **no
   TrustEngine gate**. This is intentional — the user's message *is* the authorization.
   The compensating control is **tool-time capability-scope enforcement** inside
   `agent_loop`.
2. **Autonomous path** (`services/graph_executor.py`): multi-step / risky plans and all
   scheduler/perception-triggered runs are persisted as DB `Plan`/`TaskRun`s and executed
   through GraphExecutor, where **TrustEngine gates every step**.

> Any migration MUST preserve this split. Naively gating the chat path would
> double-prompt users for actions they just requested; naively un-gating the
> autonomous path would let perception-triggered writes execute unapproved.

### B.2 The agent-runtime contract (`orchestrator/agent_loop.py`)

`agent_loop()` is a hand-rolled async generator over the **Anthropic SDK directly**
(`client.messages.create/stream`). It yields typed `LoopEvent`s
(`LoopAgentStart/Thinking/TextDelta/ToolCall/ToolResult/Done/Error`). What it provides —
the concrete contract a replacement must match:

| # | Behavior | Where | Deep Agents covers? |
|---|---|---|---|
| 1 | Multi-round tool loop (`max_tool_rounds=10`) | loop body | ✅ native |
| 2 | Streaming + non-streaming unified | `stream` flag | ✅ LangGraph stream |
| 3 | **Capability-scope enforcement (fail-closed)** | `_resolve_tool_scope_and_server` | ❌ → `@wrap_tool_call` |
| 4 | Governor pre-tool hook (audit-only) | `governor_pre_tool_hook` | ❌ → `@wrap_tool_call` |
| 5 | Forced `tool_choice` for structured Governor verdict | `GOVERNOR_VERDICT_TOOL` | ◐ → `response_format` / tool_choice |
| 6 | **Per-turn auth_required breaker** (server/provider) | `unavailable_servers/providers` | ❌ → `@wrap_tool_call` |
| 7 | 60s tool timeout | `asyncio.wait_for` | ◐ → middleware / ToolRetry |
| 8 | API retry w/ backoff (RateLimitError) | `_api_call_with_retry` | ✅ native |
| 9 | **Anthropic circuit breaker per model** | `circuit_breaker` | ◐ → `@wrap_model_call` / ModelFallback |
| 10 | **Mid-loop thinking fallback** | `_is_thinking_error`, `_disable_thinking_in_kwargs` | ◐ may be unneeded; else `@wrap_model_call` |
| 11 | **Bedrock/Opus-4.8 adaptive thinking + effort** | `build_thinking_params` | ⚠️ RISK → `@wrap_model_call` / model config |
| 12 | **Per-tool cost attribution** (`trigger=f"tool:{name}"`) | `TokenUsage` insert | ❌ → `@wrap_tool_call` |
| 13 | **Budget recording** | `budget.record_usage` | ❌ → `@after_model`/`@after_agent` |
| 14 | Secret sanitization for trace spans | `_sanitize_for_span` | ❌ → middleware + LangSmith redaction |
| 15 | Trace spans | `MuldroTrace` | ◐ → LangSmith |
| 16 | Cancellation token | `cancel_event` | ◐ → LangGraph cancellation |

Legend: ✅ native · ◐ partial/needs glue · ❌ must be re-homed as middleware · ⚠️ risk.

### B.3 Agents & routing

- `SubAgent` (dataclass): `name, prompt, model_tier, capability_scope: set[str],
  max_tokens, temperature, thinking, edge_case_only`. 7 agents (Perceiver, Librarian,
  Planner, Governor, Operator, Presenter, Persona). Each has an explicit
  **capability scope** (set of capability strings) in `AGENT_CAPABILITY_SCOPES`.
- `CapabilityResolver` + `classify_capability_agent` + `route_step`: maps a plan-step
  `capability` → owning agent (presenter/librarian/perceiver/operator) and resolves a
  capability → concrete enabled tools (workspace-scoped). This is **capability-based
  routing** — Muldro's distinctive alternative to a supervisor picking subagents by
  description.
- Model tiers per agent (Planner=opus, Persona=haiku, rest=sonnet) + per-agent thinking
  budgets + a "cheap mode" that downgrades opus→sonnet and halves thinking.

### B.4 Trust & approval (`trust_engine.py`, `trust_gate.py`, `risk_assessor.py`)

- `TrustEngine.evaluate()` → `PolicyDecision` via a deterministic **4×4 matrix**
  (`trust_level × risk_level`) → one of `auto_execute_notify / auto_execute_silent /
  approval_required / blocked`.
- `TrustGate` (read in full): `assess_step_risk` (**fails closed to `high`** so an
  assessment outage can never auto-execute a write), `create_approval_and_pause`
  (persists approval, `transition_step`/`transition_run`, checkpoints, notifies, emits
  `approval_needed` A2UI surface), trust reinforcement on auto-execute, and a checkpoint
  audit trail so a later verification failure can reverse premature reinforcement.
- Trust graduation (3→learning, 10→trusted, 25→autonomous) + demotion cooldowns.

### B.5 Execution DAG & state machine

- `graph_executor.py` (+ collaborators `StepGraphStore`, `StepRunner`, `TrustGate`,
  `OutcomeLearner`, `DagRunner`): durable DAG that delegates **each step to
  `agent_loop`**, applies the TrustEngine gate, checkpoints, and resumes.
- `execution_state.py`: the state machine — `transition_run()/transition_step()` are the
  ONLY legal way to change status (never direct mutation). Rich status vocab
  (`pending/running/paused/awaiting_approval/awaiting_input/completed/failed/…`).
- Contracts (`contracts/__init__.py`): `PlanOutput`/`PlanStep` are **frozen** Pydantic
  with built-in **DAG cycle detection** + dependency validation; `PolicyDecision`,
  `SurfaceUpdate`/`StepState`/`ApprovalContext` (A2UI live execution), `SpanRecord`.

### B.6 Surrounding domain layer (stays — not Deep Agents' job)

- **Tool/MCP layer** — unified registry (`catalog.py` + `tool_registry.py`),
  dispatch by backend (`internal_mcp`/`external_mcp`/`composite`/`_special`),
  FastMCP internal servers, **turn-scoped MCP sessions** (`turn_scope.py`),
  on-demand `uvx`/`npx` processes (`local_process_manager.py`), lazy schema discovery.
- **Context/memory** — `ContextBuilder.build()` → `ContextPack` (entities, memories,
  preferences, graph relationships via Neo4j, related runs, goals, constraints, risks);
  `MemoryService` (7 memory types, stability decay); `WorldModel`; `EventProcessor`.
- **Perception loop** — `PerceptionPolicyService` (circuit breaker, rate limit),
  `RelevanceAssessor` (act/alert/brief/silent tiers), `Notifier`, `SchedulerLoop`. The
  autonomous trigger surface that runs agents with no user message.
- **A2UI** — typed component trees (`SurfaceService`, `renderer.py`), live
  `SurfaceUpdate` frames, dismissible insight surfaces.
- **Multi-tenant isolation** — every table `workspace_id`-scoped; resolved from auth.

### B.7 Invariants any replacement MUST preserve (acceptance criteria)

1. **Capability-scope is enforced at tool-execution time, fail-closed** (chat path's
   only safety control). — `agent_loop._resolve_tool_scope_and_server`
2. **TrustEngine gates every step on the autonomous path**; chat path stays ungated. —
   `graph_executor` + `trust_gate`
3. **Risk assessment fails closed to `high`** (never silently auto-executes). —
   `risk_assessor`, `trust_gate.assess_step_risk`
4. **Status changes only via `transition_run`/`transition_step`.** — `execution_state`
5. **Turn-scoped MCP teardown** at turn end. — `turn_scope`
6. **`workspace_id` isolation** on every data access.
7. **Per-tool + per-agent cost attribution and daily budget** survive. — `TokenUsage`,
   `BudgetTracker`
8. **PlanOutput stays a validated, acyclic DAG** (or its replacement preserves cycle
   rejection). — `PlanOutput._validate_step_dependencies`
9. **A2UI surfaces never empty**; live execution phases
   (`plan_ready→executing→approval_needed→completed/failed`) keep flowing.
10. **Bedrock + Opus-4.8 adaptive thinking** keeps working (no 400s). —
    `build_thinking_params`
11. **Durable resume** of paused/approved autonomous runs across restarts.

---

### B.8 — Audit-confirmed constraints that break a naive swap (Phase 1/4 design inputs)

The full-audit workflow (9 parallel readers + synthesis) confirmed these as the things
most likely to silently break. They are hard requirements on the new runtime:

1. **`agent_loop` is ONE seam feeding TWO consumers.** Chat (`AgentInvoker.call_agent_stream`)
   and every autonomous DAG step (`StepRunner.run_step_via_agent_loop`) both delegate to
   `agent_loop`. The replacement MUST emit a compatible **event envelope**
   (`LoopEvent`/`LoopToolResult`/the `auth_required` envelope) or all downstream consumers
   break together: CoreEvent translation, `DagRunner` finalize/defer, trust reinforcement,
   per-tool cost accounting, OAuth re-auth deferral. → **Build a LangGraph-stream →
   `LoopEvent` adapter** as the first integration artifact.
2. **`CoreEvent` ↔ frozen SSE dict (`core_event_to_sse`) is consumed by the frontend BY
   KEY.** LangGraph-native events must be adapted back into this exact contract or the
   Next.js renderer + live execution/insight/approval surfaces break. Don't change keys.
3. **Capability=None gating + registry-backed scope (Critical-security).** Discovering MCP
   tools directly via `langchain-mcp-adapters` would BYPASS the `ToolRegistry` capability
   gate — the sole safety net on the ungated chat path. Tools must keep flowing through
   `ToolRegistry`; capability-scope must be re-checked **per call** (CapabilityScopeMiddleware),
   never trusted from the offered list. Newly discovered tools stay `capability=None`
   (invisible/uncallable until an admin maps a capability).
4. **TrustEngine stays EXTERNAL to the runtime (Critical-safety).** Do NOT collapse it into
   deepagents' static `interrupt_on` HITL — that forfeits the 4×4 determinism, fail-closed
   risk, graduation ladder, and durable async resume. The runtime *raises* an interrupt;
   the decision + durable `Approval` + scheduler-driven resume stay in Muldro.
5. **Sequential DAG execution.** `DagRunner` runs ready steps SEQUENTIALLY because the
   `AsyncSession` is not concurrency-safe. LangGraph parallelizes by default — either
   serialize step execution, or give each step its own DB session.
6. **Per-request DB session isolation + DI composition root** (`runtime.py`
   `build_shared`/`request_services`/`attach_session` + `db_factory` provider). The runtime
   must accept `workspace_id` on every entry and build DB-bound services per request.
   Sharing one `AsyncSession` across concurrent requests corrupts transactions (already
   reverted once).
7. **Generator-drain-to-`finally`.** Trace lifecycle + `turn_scope` teardown live in the
   pipeline generator's `finally`. Any adapter over the event stream must exhaust it — an
   early return leaks traces + MCP sessions.
8. **Async, scheduler-driven approval resume** with a fresh `trace_id` per segment
   (`TraceStore` INSERTs, not upserts). The approve HTTP handler must not execute inline.

---

## Part C — Concept mapping (Muldro → Deep Agents)

| Muldro concept | Deep Agents equivalent | Difficulty | Notes |
|---|---|---|---|
| `agent_loop()` multi-round loop | `create_deep_agent` graph (LangGraph) | moderate | core swap; deletes lots of plumbing |
| `SubAgent` + 7 agents | `subagents=[{name,description,system_prompt,model,tools,middleware,permissions}]` | moderate | description-based delegation vs capability routing |
| Capability-based routing (`CapabilityResolver`) | none (supervisor picks subagent by description) | **hard** | re-home as a custom **planning/routing middleware** or keep CapabilityResolver feeding subagent tool lists |
| `PlanOutput`/`PlanStep` executable DAG | `TodoListMiddleware` (scratchpad) **+** LangGraph graph for real DAG | hard | todos ≠ executable DAG; keep PlanOutput, drive a custom graph OR collapse to deep-agent + todos |
| `agent_loop` retry/backoff | native | trivial | delete `_api_call_with_retry` |
| `AnthropicCircuitBreaker` | `@wrap_model_call` / `ModelFallbackMiddleware` | moderate | re-home |
| thinking fallback / `build_thinking_params` | `@wrap_model_call` + model config | **hard/⚠️** | Bedrock+Opus-4.8 adaptive thinking is the top risk |
| Capability-scope enforcement | `@wrap_tool_call` middleware | moderate | port `_resolve_tool_scope_and_server` |
| Governor pre/post hooks (audit) | `@wrap_tool_call` + LangSmith | trivial | mostly audit-only now |
| TrustEngine 4×4 gate | `HumanInTheLoopMiddleware` (plumbing) **+** custom decision middleware | **hard** | HITL gives pause/resume; decision logic stays custom |
| RiskAssessor (fail-closed) | keep as-is, called from gate middleware | trivial | domain logic, stays |
| GraphExecutor durable DAG + resume | LangGraph `checkpointer` + `interrupt`/`Command` | **hard** | biggest persistence/resume rework |
| `execution_state` transitions | LangGraph state + custom status mapping | moderate | keep transition functions or map to graph state |
| Per-tool cost attribution / budget | `@wrap_tool_call` + `@after_model` | moderate | re-home `TokenUsage` writes |
| ContextPack injection | `@dynamic_prompt` / `@before_agent` | moderate | keep ContextBuilder, inject via hook |
| Memory (7 types, Qdrant/Neo4j) | keep services; optionally `StoreBackend`/`MemoryMiddleware` for agent-visible memory | moderate | domain stays |
| Turn-scoped MCP | `@before_agent`/`@after_agent` + keep `turn_scope` | moderate | wrap existing |
| MCP tool loading | `langchain-mcp-adapters` OR keep `mcp_pool` + adapter shim | moderate | choose one |
| A2UI `SurfaceUpdate` SSE | LangGraph stream (`messages`/`custom`) → existing surface pipeline | moderate | re-wire event source |
| Conversation summarization (`_summarize_history`) | `SummarizationMiddleware` | trivial | delete bespoke |
| Tracing (`MuldroTrace`/spans) | LangSmith (optional) | optional | can keep both |
| Bedrock provider | `langchain-aws` `ChatBedrockConverse` | moderate/⚠️ | validate Opus-4.8 path |
| Workspace isolation | pass via `context_schema` / config, enforce in middleware + services | moderate | thread through |
| Perception loop / scheduler | unchanged — becomes a *caller* of the new agent | trivial | domain stays |

---

## Part D — What Deep Agents does NOT give you (Muldro-only, must retain)

1. **Capability-based routing** — Deep Agents delegates to subagents by *description*
   (LLM picks). Muldro routes deterministically by *capability → agent*. Either keep
   `CapabilityResolver` (preferred — it's deterministic and testable) or accept
   description-based delegation (less predictable, simpler).
2. **TrustEngine 4×4 dynamic gate + graduation/demotion** — HITL is static per-tool;
   the trust *decision* is bespoke domain logic.
3. **Fail-closed RiskAssessor** — domain.
4. **Per-tool/per-agent cost attribution + daily USD budget** — `interrupt`-style
   limits exist (`ModelCallLimitMiddleware`) but not Muldro's accounting.
5. **Turn-scoped MCP with on-demand `uvx`/`npx` process lifecycle** — Deep Agents'
   MCP story is `langchain-mcp-adapters`, not Muldro's `LocalMCPProcessManager`/`TurnScope`.
6. **A2UI typed surfaces** — Deep Agents has its own frontend story; Muldro's
   `SurfaceUpdate`/renderer pipeline is bespoke and stays.
7. **World model / 7-type memory / Qdrant+Neo4j TriSearch** — domain intelligence.
8. **Perception/relevance/notification loop** — autonomous trigger surface.
9. **Bedrock + Opus-4.8 adaptive thinking/effort** — must be reproduced through the
   LangChain model abstraction (top technical risk).
10. **Multi-tenant `workspace_id` isolation** — threaded everywhere; must be carried
    through LangGraph config/state.

---

## Part E — Strategic options

### Option 1 — Full replacement, strangler-fig *(recommended)*
LangGraph + deepagents replace both the per-step runtime **and** the durable DAG; Muldro
policy moves into middleware; domain services stay. Migrate path-by-path behind a feature
flag, oldest/safest first.
- **Pros:** deletes the most bespoke code (loop, retry, breaker, summarization, resume,
  HITL plumbing); aligns with a maintained ecosystem; LangSmith observability.
- **Cons:** largest surface; persistence/resume rework; Bedrock/Opus-4.8 risk; two
  runtimes coexist during migration.

### Option 2 — Runtime-only adoption *(fallback)*
deepagents replaces only the per-step `agent_loop` (as a "step runner"); GraphExecutor,
TrustEngine, `execution_state`, and the DB task graph stay.
- **Pros:** ~70% of the code-deletion benefit, ~40% of the risk; keeps the proven
  durable DAG + trust gate; smaller blast radius.
- **Cons:** not "entirely"; you keep maintaining GraphExecutor; two planning models
  (PlanOutput + todos) coexist.

### Option 3 — Big-bang rewrite *(not recommended)*
Rebuild on deepagents in a branch, cut over once. Highest risk for a production system
with live perception, trust, and multi-tenant data. Reject unless the team explicitly
accepts a freeze + long stabilization.

### Option 4 — Status quo *(baseline)*
Keep the hand-rolled runtime. Zero migration risk; you keep owning all the plumbing and
miss ecosystem features (skills, sandboxed FS, LangSmith, summarization).

---

## Part F — Recommended phased plan (Option 1, strangler-fig)

> Process discipline: follow the repo's refactoring standard — **characterization tests
> first** (snapshot current behavior of `agent_loop`, the trust gate, and a few golden
> chat/autonomous runs), then structure-vs-behavior commit separation, then swap behind a
> flag. Each phase has an explicit exit gate; the full suite (currently green) must stay
> green at every phase.

### Phase 0 — Spike & de-risk — ✅ COMPLETE (2026-06-22, both gates PASSED live)
- Stand up `deepagents` + `langgraph` + `langchain-aws` in a throwaway script.
- **Validate the model layer:** Opus 4.8 on **Bedrock** via `ChatBedrockConverse` with
  adaptive thinking + effort — confirm no 400s and that thinking/effort is honored.
  This single result decides whether Option 1 is viable as-is or needs a custom
  `BaseChatModel` wrapper around the Anthropic Bedrock client.
- Prototype one `@wrap_tool_call` middleware doing capability-scope enforcement, and one
  `@wrap_model_call` doing budget/cost capture, to prove the hook surface is sufficient.
- **Exit gate:** Bedrock+Opus-4.8 works through LangChain (or a wrapper is scoped); all
  16 runtime behaviors in §B.2 have an identified home.

### Phase 1 — Middleware library — ✅ per-call middleware library COMPLETE (2026-06-22)

**Design rule discovered (2026-06-22):** only policies that must hook *per call* become
middleware; whole-turn wrappers and once-per-turn prompt assembly stay integration-layer,
because the deep agent is built **per turn** (mirroring `agent_loop`-per-call). This trims
the middleware list to three.

Foundation (✅ done, verified): `src/deep_runtime/` — `model_factory.build_chat_model`
(ports `build_thinking_params` adaptive/legacy split into `ChatAnthropic` kwargs;
`build_thinking_params` to be deleted in Phase 5) + `agent_builder.build_deep_agent(agent,
tools, *, extra_middleware=(), system_prompt=None, name=None) -> CompiledStateGraph`. Deps
pinned in `pyproject.toml`; coexist cleanly with the full app (verified).

Per-call middlewares (each a `make_*_middleware(...)` closure factory; TDD-first;
characterization tests against legacy `agent_loop` behavior) — **building now, in parallel:**
- `capability_scope.make_capability_scope_middleware` (`@wrap_tool_call`, fail-closed) —
  ports `_resolve_tool_scope_and_server`; per-call `ToolRegistry` lookup; **Critical-security
  §B.8#3**. Test uses a benign out-of-scope tool (Phase-0 lesson).
- `budget.make_budget_middleware` (`@after_model`) — ports authoritative
  `BudgetTracker.record_usage`; per-tool `TokenUsage` split deferred to Phase 2 (analytics).
- `unavailable_server.make_unavailable_server_middleware` (`@wrap_tool_call`) — per-turn
  `auth_required` short-circuit + terminal steer.

Deferred / re-homed:
- **`ContextPack` → Phase 2 integration**, NOT a middleware: in the build-per-turn model it
  is `system_prompt = agent.prompt + ContextBuilder.to_prompt(await builder.build(...))`,
  assembled once per turn (legacy `AgentInvoker.build_system_prompt`). Fail-open per section.
- **`turn_scope` → Phase 2 integration wrapper**, NOT a middleware: `async with
  turn_scope(on_close=close_turn_sessions): await agent.ainvoke(...)` (legacy wraps
  `_process_core`). ContextVars propagate into the LangGraph run — verify in Phase 2.
- **Model resilience (`AnthropicCircuitBreaker`) → optional `@wrap_model_call`**, deferrable:
  native LangChain retry + `ModelFallbackMiddleware` cover the common case; adaptive thinking
  is already native (Phase 0), so the mid-loop thinking-fallback hack is dropped.

- **Exit gate ✅ MET (2026-06-22, subagent-driven, 3 parallel):** 27/27 `deep_runtime`
  tests green (10 foundation + 5 capability_scope + 6 budget + 6 unavailable_server); ruff
  check + format clean (14 files); the middleware package + foundation + existing app
  (`muldro`, `graph_executor`) all import together (deps coexist).

### Phase 2 — Chat path on Deep Agents, single agent (1–2 weeks)
- Replace the chat path's per-agent call with a `create_deep_agent` instance per Muldro
  agent (system_prompt = existing role prompt; tools = capability-resolved tools; the
  Phase-1 middleware stack; **no HITL** — chat stays ungated by design).
- Wire LangGraph streaming → existing A2UI `SurfaceUpdate`/SSE.
- Run behind `MULDRO_RUNTIME=deepagents|legacy` flag; shadow-compare outputs.
- **Exit gate:** golden chat runs match legacy on tool selection, capability denials,
  cost accounting, and streamed surfaces; capability-scope still fail-closed.

### Phase 3 — Multi-agent via subagents + routing (1–2 weeks)
- Express the 7 agents as deepagents `subagents`. Decide routing:
  **keep `CapabilityResolver`** to compute each subagent's tool list and to pick the
  subagent for a step (deterministic), rather than free LLM delegation.
- Map `PlanOutput`/`PlanStep` onto the orchestration (Planner still emits `PlanOutput`;
  a thin driver walks the DAG and invokes subagents).
- **Exit gate:** multi-step chat plans route identically to legacy; per-agent model tiers
  + thinking budgets preserved.

### Phase 4 — Autonomous path: durable DAG + TrustEngine on LangGraph (2–3 weeks, riskiest)
- Replace GraphExecutor's DAG mechanics with a LangGraph graph + `AsyncPostgresSaver`
  checkpointer; replace pause/resume with `interrupt`/`Command`.
- Implement `TrustGateMiddleware`: call `TrustEngine.evaluate()` (+ fail-closed
  RiskAssessor); on `approval_required` raise a LangGraph `interrupt` carrying the
  existing `ApprovalContext`; persist approval + emit `approval_needed` surface; on
  resume, map `Command(resume=...)` back to the existing approval-resume path.
- Preserve `transition_run/transition_step` semantics (map graph state → DB status, or
  keep the state machine authoritative and mirror into graph state).
- **Exit gate:** an approval-gated autonomous run pauses, persists, survives a worker
  restart, resumes on approval, reinforces/reverses trust correctly; fail-closed risk
  verified; scheduler/perception triggers unchanged.

### Phase 5 — Cleanup & ecosystem adoption (1 week)
- Delete `agent_loop.py`, `api_circuit_breaker.py`, bespoke retry/summarization, and
  GraphExecutor DAG code superseded by LangGraph.
- Optionally adopt `SummarizationMiddleware`, `SkillsMiddleware`, `StoreBackend` memory,
  LangSmith tracing.
- Update CLAUDE.md + `docs/engineering-standards.md` to the new runtime; refresh the
  "Common Mistakes" section.
- **Exit gate:** legacy flag removed; suite green; docs reflect reality.

---

## Part G — Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| ~~Bedrock + Opus-4.8 adaptive thinking~~ → **Direct Anthropic API** | ✅ **RESOLVED** | Confirmed live in Phase 0: `ChatAnthropic` 1.4.6 drives `claude-opus-4-8` with `thinking={"type":"adaptive"}` + `effort="high"`, no 400. `build_thinking_params` to be deleted. Fallback (custom `BaseChatModel`) no longer needed. |
| TrustEngine 4×4 dynamic gate doesn't fit static `interrupt_on` | High | Custom decision middleware that *raises* HITL interrupts; HITL only provides pause/resume |
| Durable resume semantics differ (LangGraph checkpoint vs `TaskRun`/`execution_state`) | High | Phase 4 isolation; keep state machine authoritative, mirror into graph; restart tests |
| Capability routing lost to description-based delegation | Medium | Keep `CapabilityResolver`; don't rely on LLM subagent selection |
| Two runtimes during migration → drift/double-maintenance | Medium | Feature flag + shadow compare + time-boxed phases |
| LangGraph/LangChain version churn (fast-moving, pre-cutoff knowledge) | Medium | Pin versions; re-run the deep-research **verification** pass once the session quota resets to confirm production-readiness claims |
| MCP lifecycle (`uvx`/`npx`, turn-scope) mismatch with `langchain-mcp-adapters` | Medium | Keep `mcp_pool`/`TurnScope`, expose tools as LangChain `BaseTool` adapters |
| Per-tool cost attribution lost | Medium | `@wrap_tool_call` middleware ports `TokenUsage` writes; characterization test |
| A2UI live frames regress | Medium | Map LangGraph stream → existing `SurfaceUpdate` contract; golden surface tests |
| New heavy deps (langgraph, langchain, langsmith) | Low | Acceptable for the runtime layer; keep domain services dep-light |

---

## Part H — Decisions

1. **Scope of "entirely":** ✅ **RESOLVED → Full (Option 1).** LangGraph replaces
   GraphExecutor + `execution_state` durable DAG, not just the per-step loop.
2. **Bedrock hard requirement?** ✅ **RESOLVED → No, direct Anthropic API.** Top model-
   layer risk removed; Phase 0 confirms `ChatAnthropic` adaptive thinking/effort.
3. **Appetite/timeline:** ✅ **RESOLVED → Full migration commitment** through Phase 5.
4. **Routing philosophy:** open — *recommended: keep deterministic capability-based
   routing (`CapabilityResolver`)*, not LLM description-based delegation.
5. **LangSmith adoption:** open — adopt as tracing/eval backend, or keep `MuldroTrace`?
   *(Non-blocking; can be decided at Phase 5.)*

---

## Appendix — Follow-ups to re-run when the session quota resets (7:20pm IST)

- Re-run the `deep-research` **verification** stage (it abstained under the outage) to
  independently confirm the production-readiness/composability claims.
- Re-run the `muldro-agent-architecture-audit` workflow (all readers were knocked out) to
  get the full structured subsystem maps + completeness critique for the appendix.
- ~~Pull exact pinned versions + confirm the model surface for Opus 4.8.~~ ✅ DONE in
  Phase 0: `deepagents` 0.6.11, `langchain` 1.3.10, `langgraph` 1.2.6,
  `langchain-anthropic` 1.4.6, `langchain-core` 1.4.8, `anthropic` 0.111.0.
  (`langchain-aws`/`ChatBedrockConverse` dropped — direct Anthropic chosen.)
