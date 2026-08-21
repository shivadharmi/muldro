# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Muldro

Muldro is a **Personal AI Operating System** for founders. It is NOT a chatbot — it is an OS with a core loop: Perceive → Understand → Update Model → Plan → Act → Communicate.

## Architecture

Multi-agent hub-and-spoke: a central `MuldroOrchestrator` (`backend/src/orchestrator/muldro.py`) routes to 6 sub-agents on the autonomous path, and to a single plan-scoped `lead` on the chat path. The model layer is **provider-configurable** (Anthropic by default; OpenAI, Google and local Ollama are in the catalog) — see "Model / thinking params". Capability-based routing: Planner produces `PlanOutput` with steps, `CapabilityResolver` maps each step's capability to the appropriate agent. Internal FastMCP servers wrap the intelligence layer; external MCP servers provide connectors — all run **on demand with no Docker dependency**: GitHub and Atlassian as remote HTTP MCP servers, Google Workspace as an on-demand local `uvx` process managed by `LocalMCPProcessManager` (`backend/src/integrations/local_process_manager.py`), and stdio servers (Slack, Notion, Playwright) via `npx`. MCP sessions are **turn-scoped** via `TurnScope` (`backend/src/integrations/turn_scope.py`) and torn down at turn end; the scheduler's `run_health_tick` idle reaper is the safety net.

```
User <-> Next.js Frontend (A2UI)
              |
         MuldroOrchestrator (provider-configurable model layer)
         Chat turn      -> ONE plan-scoped lead
         Autonomous run -> Perceiver, Librarian, Planner,
                           Executor, Presenter, Persona
              |
         derive_lead_scope (plan capabilities → the lead's authority)   [chat]
         CapabilityResolver (step.capability → agent)                   [autonomous]
              |
         trust_gate (per capability) → permission_gate (per action)
         verdicts: allow | interrupt | prepare
              |
         Tool Layer: FastMCP (intelligence + communication) + external MCP servers
              |
         Intelligence Backend (Postgres + Redis)
         EventProcessor, WorldModel, MemoryService, Planner,
         Governor, Executor, Presenter, Audit, DLQ
```

**Key paths:**
- Orchestrator + agents: `backend/src/orchestrator/` (muldro.py, agents.py, hooks.py, prompts.py, tracing.py, budget.py, perception_runner.py, connector_poller.py, recovery.py, intent_classifier.py, api_circuit_breaker.py, capability_summary.py, services.py)
- Deep runtime (the single execution engine): `backend/src/deep_runtime/` (agent_builder.py, model_factory.py, confirmation.py, stream_adapter.py, middleware/) + the model layer `backend/src/llm/` (model_factory.py, utility.py) + the provider capability map `backend/src/config/` (model_catalog.py, capability_map.py)
- Services (business logic): `backend/src/services/` — planner, governor, executor, presenter, memory_service, world_model, event_processor, capability_resolver, risk_assessor, trust_engine, etc.
- Tool layer: `backend/src/tools/` (catalog.py, schemas.py, validation.py, intelligence_server.py, communication_server.py, server.py)
- Runtime contracts: `backend/src/contracts/` (PlanOutput, PlanStep, CapabilityGap, PolicyDecision, SurfaceUpdate, InsightSurfaceData, StepResult, ToolCallRequest, DomainEvent, WorkspaceSurfacePush) — neutral layer both api and services import downward from
- A2UI component system: `backend/src/ui/` (contracts.py, renderer.py)
- A2UI surface builder: `backend/src/services/surface_builder.py` (SurfaceService) + `surface_detail_builders.py`
- API routes: `backend/src/api/` — all `/v1/` prefixed
- SQLAlchemy models: `backend/src/models/` — all workspace-scoped
- Frontend: `frontend/src/` (Next.js + A2UI renderer + chat split-pane)
- Infra: `infra/` (Terraform for AWS) + `docker-compose.yml` (local dev)

## Commands

### Backend (run from `backend/`)

```bash
# Infrastructure (Postgres, Redis, MinIO, Qdrant, Neo4j)
docker compose up -d

# Run backend API server (port 8000)
source .venv/bin/activate
python run.py

# Run with background worker (StreamConsumer + Scheduler)
python run.py --worker

# Tests
pytest tests/ -v                          # all tests
pytest tests/test_planner.py -v           # single file
pytest tests/test_planner.py::test_name -v  # single test
pytest tests/ -v -k "planner"             # keyword filter

# Lint and format
ruff check src/ tests/
ruff format src/ tests/
ruff check src/ tests/ --fix              # auto-fix

# Database migrations (from backend/)
alembic revision --autogenerate -m "description"
alembic upgrade head
```

### Frontend (run from `frontend/`)

```bash
npm install
npm run dev     # dev server (port 3000)
npm run build   # production build
npm run lint    # eslint
```

## Configuration

All backend settings via env vars with `MULDRO_` prefix (pydantic-settings in `src/config/settings.py`). Key vars: `MULDRO_DATABASE_URL`, `MULDRO_REDIS_URL`, `MULDRO_ANTHROPIC_API_KEY`, `MULDRO_OPENAI_API_KEY`, `MULDRO_LOG_JSON`, `MULDRO_DAILY_TOKEN_BUDGET_USD`, `MULDRO_EMBEDDING_MODEL`, `MULDRO_RERANKER_MODEL`, `MULDRO_RERANKER_ENABLED`, `MULDRO_SKIP_REGISTRY_VALIDATION`, `MULDRO_SKIP_GATEWAY_VALIDATION` (startup aborts when the OpenConnector gateway is unconfigured — this is the escape hatch). Provider API keys are an **env fallback**: UI-entered keys live encrypted in `provider_credentials`, and `ollama` is keyless (`KEYLESS_PROVIDERS`), authenticating by `base_url` alone. Embeddings and reranking run locally via fastembed (ONNX, no external API). Uses `.env` file.

## Coding Standards

**Binding rules live in [docs/engineering-standards.md](docs/engineering-standards.md)** —
architecture (one-way deps, frozen god objects, typed boundary contracts), OOP/pattern usage,
file size caps (200–400 target / 800 hard cap Python, 400 components, enforced via pre-commit),
refactoring process (characterization tests, structure/behavior commit separation), A2UI
side-effect rule, and OSS hygiene. Read it before structural changes. Summary below.

### Python

- **ruff**: line-length 100, target py312, rules: E, F, I, N, W
- **Async everywhere**: all service methods and route handlers are async. Use `asyncpg` for DB, `httpx` for HTTP.
- **Types**: type hints everywhere. Pydantic for API schemas. SQLAlchemy mapped types for models.
- **Naming**: snake_case. Prefixed IDs with ULID (e.g., `evt_`, `plan_`, `exec_`, `mem_`, `apr_`).
- **Imports**: absolute from `src.` prefix. Ruff handles sorting.
- **Errors**: `HTTPException` for HTTP errors. Always use Pydantic response models, never bare dicts.
- **Tests**: pytest + pytest-asyncio (asyncio_mode = "auto"). Test files mirror `src/` structure. Use `make_mock_settings()` from `tests/conftest.py`. Mock Anthropic client via `@patch("src.orchestrator.muldro.get_anthropic_client")`.

### Frontend (React/Next.js)

- **Hooks order**: NEVER place hooks after conditional returns (`if (!x) return`). All `useState`, `useEffect`, `useCallback`, etc. must be called unconditionally at the top of the component. Move early returns below all hook calls.
- **No side effects during render**: Never assign `window.location.href` or mutate refs (`ref.current = ...`) directly in the component body. Use `useEffect` for side effects like navigation and ref updates.
- **No synchronous setState in effects**: Avoid calling `setState` synchronously in `useEffect` bodies. Instead, derive state from props/params (compute during render) or use lazy `useState` initializers for values from `localStorage`/URL params.
- **Lazy state initialization**: For state derived from `localStorage` or other sync browser APIs, use `useState(() => getValueFromStorage())` instead of `useState(null)` + `useEffect(() => setState(...))`.
- **Router navigation**: Use Next.js `useRouter().replace()` inside `useEffect` for redirects, never `window.location.href`.

### Database

- Always use Alembic for migrations. String IDs with type prefix + ULID. JSONB for flexible data; proper typed columns for indexed fields.

### API

- All endpoints `/v1/` prefixed. REST-first with proper HTTP methods.

## Agent Boundaries (do not violate)

| Agent | Tier | Role | Write Scope |
|-------|------|------|-------------|
| Perceiver | balanced | Gather information from any source — email, calendar, Slack, GitHub, web, internal knowledge (read-only) | normalized_events |
| Librarian | balanced | Extract entities, update world model, store memories | entities, relationships, memories |
| Planner | reasoning | Produce capability-based plans (structured PlanOutput JSON) via PLANNER_PROMPT_V2 | plans, plan_tasks, goal memories |
| Executor | balanced | Execute approved plans via tools, scoped per step (reads context first; offered only the current step's capability tools, not the full write union) | task_runs, task_steps |
| Presenter | balanced | Generate user-facing text output | briefings, A2UI surfaces (via SurfaceService + renderer.py) |
| Persona | fast | Learn and store preferences (batched every 10th scheduler tick, min 5 interactions) | memories (preference type) |

**Only Planner decides intent.** The table above is the **autonomous** path's cast: only the Executor performs external actions (scoped to the step's capability) and only Presenter talks to the user. A **chat** turn routes to none of them — it builds one synthetic `lead` (`orchestrator/lead_builder.py`, not a registry row) scoped to the plan's capability union, and that lead acts and answers for itself. **Every external write is gated at action time** — `trust_gate` (TrustEngine, per capability) on the autonomous path, `permission_gate` (per action) on chat — and with no human on the turn a gated write is *prepared* for review rather than executed.

Tiers are **provider-neutral** (`reasoning` / `balanced` / `fast`, `AGENT_MODEL_TIERS` in `orchestrator/agents.py`). Which model backs a tier is DB data (`ModelBinding`, per-workspace overridable via `PUT /v1/model-config`); the seeded defaults are Claude Opus / Sonnet / Haiku, and OpenAI, Gemini and local Ollama are in `config/model_catalog.py`.

*The Governor is not a routed cognitive agent — its deterministic policy service (`services/governor.py`) and audit-only pre-tool hook (`hooks.py::governor_pre_tool_hook`, always `allowed: True` except disabled tools) remain as non-agent machinery.*

## Capability-Based Routing

The Planner produces a `PlanOutput` with ordered `PlanStep` entries. Each step has a `capability` field (e.g., `email.send`, `knowledge.search`, `system.respond`). What happens next differs by path:

- **Chat path — no per-step agent routing.** One lead runs per turn. `derive_lead_scope` (`src/orchestrator/lead_builder.py`) folds the plan's steps into a single `capability_scope` — the *union* of each step's authority — and the lead discovers its own tools inside that scope. A read-only plan yields a read-only lead; a write plan grants only that plan's write capabilities, never the Executor's full write union. `resolve_plan_routing` (`src/orchestrator/chat_pipeline.py`) is all that remains of the old *agent* pre-resolution: a pure filter selecting the steps the *user* must act on, which are reported rather than executed. One residue of per-step **execution** does survive: `system.*` steps are run deterministically per step by `system_capability_handler` before the lead starts, each emitting a `SystemStepResult`. That is not agent routing, but it is not the lead either.
- **Autonomous path — still per-step.** `GraphExecutor` / `DagRunner` route each step through `StepRunner`, which calls `CapabilityResolver.resolve_for_step` to scope that step's tool offering.

`CapabilityResolver` survives for `resolve_for_step` / `capabilities_for_step` (used by the autonomous path and by `derive_lead_scope`); `classify_capability_agent` survives for `runtime_projection`. The capability→agent table below therefore describes **agent ownership of capabilities**, not chat-turn routing:

| Capability Prefix | Owning agent |
|------------------|-------|
| `reason.*`, `respond.*`, `system.respond` | Presenter |
| `knowledge.*` | Librarian |
| `email.read/list/search`, `calendar.read`, any read capability | Perceiver |
| Write capabilities (`email.send`, `calendar.create`, etc.) | Executor |

**Key files:** `src/services/capability_resolver.py` (resolve, resolve_for_step, capabilities_for_step, is_read_capability, is_write_capability), `src/orchestrator/lead_builder.py` (derive_lead_scope, build_chat_lead), `src/orchestrator/capability_summary.py` (generate_capability_summary — compact XML for the Planner prompt)

## Agentic vs Scripted Execution

All steps use **agentic execution**: the agent runs on the deep runtime (a LangGraph agent loop built by `build_deep_agent`), discovers available tools, and autonomously decides which to call.

Multi-step plans trigger GraphExecutor for DAG management (dependencies, checkpointing, resume, TrustEngine approval gates), but each step within the DAG is executed **through the deep runtime** — the routed agent discovers tools autonomously per step. GraphExecutor is a **durable DAG wrapper around the deep runtime** (`build_deep_agent` per step), not a separate execution mode.

**Do not** hardcode tool-calling sequences in Python handlers. Let agents discover tools via the deep runtime's agent loop, which handles tool discovery, multi-turn reasoning, error recovery, and audit hooks automatically.

## Unified Tool Registry

Tool identity lives in 2 files: `src/tools/catalog.py` (definitions) + `src/tools/intelligence_server.py` (implementations). All tools are served through MCP — no native connectors. Internal tools + external tool seeds.

**Adding tools:**
- New internal tool: edit `catalog.py` (add `InternalToolDef`) + `schemas.py` (add Pydantic input model) + `intelligence_server.py` (add MCP function)
- New external tool seed: edit `catalog.py` (add `ExternalToolSeed`)
- Unknown MCP tools: auto-registered on discovery with `capability=None` (invisible until admin maps capability)

**Dispatch:** One `ToolRegistry.get_tool()` lookup → match on `backend`:
- `internal_mcp` → `_call_internal_tool()` with `server_prefix` from registry
- `external_mcp` → `call_mcp_tool()` with real MCP name (no normalization)
- `composite` → `_call_composite_tool()` (e.g., `web_search`)

The `_special` value is a `server` (not a `backend`): tools with `backend="internal_mcp"` and `server="_special"` (e.g., `report_governor_verdict`) are intercepted before the backend match and returned as-is (input passed through, no MCP call).

**Authorization:** `SubAgent.can_use_tool()` does one registry lookup for capability, checks against agent's `capability_scope`. No normalizer chain.

**Approval policy:** Handled by TrustEngine (`src/services/trust_engine.py`) — a deterministic 4×4 matrix (trust_level × risk_level) in GraphExecutor, not Governor. Governor hooks are now audit-only.

**Startup:** `seed_defaults()` reads from `INTERNAL_TOOLS` + `EXTERNAL_TOOL_SEEDS` in catalog.py → upserts into `tool_definitions` table. `validate_registry()` runs startup cross-checks. `MULDRO_SKIP_REGISTRY_VALIDATION=true` disables validation in emergencies. `initialize_mcp_bridge()` registers server configs only — **no eager tool discovery at startup**. Tool schemas are durable in the DB (`ToolDefinition.input_schema`) and lazily (re)discovered per server on first agent build via `discover_and_persist` / `discover_missing_schemas`. A startup preflight (`backend/src/integrations/runtime_preflight.py`) warns if `uvx`/`npx` are missing from the host.

**Key files:**
- Catalog: `src/tools/catalog.py` (InternalToolDef, ExternalToolSeed, INTERNAL_TOOLS, EXTERNAL_TOOL_SEEDS)
- Schemas: `src/tools/schemas.py` (Pydantic input models for internal tools, TOOL_INPUT_MODELS registry)
- Validation: `src/tools/validation.py` (startup cross-checks)
- Registry: `src/services/tool_registry.py` (ToolRegistry — DB CRUD + seed from catalog)
- Capabilities: `src/integrations/capabilities.py` (CAPABILITY_CATALOG, CapabilityFamily — taxonomy only)

## Agent Prompt Architecture

System prompts (`src/orchestrator/prompts.py`):
- `MULDRO_SOUL_CORE` — shared by the 6 agents **and by the chat lead**: identity plus the behavioural laws, and nothing else. It carries **no agent roster and no division of labour**, because everything in it must be true for every reader — and the lead is in no roster while owning its whole turn. Each role prompt states its own boundary in the second person, which is the only form a model can act on. Its rules do cover what a **gate** can do: allow, pause, or *stage* — and that a staged action has not happened yet, which the runtime's `status="success"` PREPARE ToolMessage would otherwise let a model report as done.
- `PLANNER_PROMPT_V2` — 7-step capability-based decomposition engine (replaces decision classification)
- `PERCEIVER_PROMPT` — 7-step read-only methodology with JSON output (findings, synthesis, gaps, confidence)
- `LIBRARIAN_PROMPT`, `EXECUTOR_PROMPT`, `PRESENTER_PROMPT`, `PERSONA_PROMPT` — agent-specific roles

Only the Planner sees `PLANNER_PROMPT_V2`. Other agents receive `MULDRO_SOUL_CORE` + their role prompt. The Planner also receives a ~200-token capability summary (via `generate_capability_summary()`) instead of 15-20K raw tool schemas.

**Reasoning depth** is set by the tier binding's `effort` (`reasoning`=high, `balanced`=medium, `fast`=low), translated per provider by `config/capability_map.py`. `AGENT_THINKING` in `orchestrator/agents.py` still carries per-agent `budget_tokens`, but only `thinking.enabled` reaches model construction (`deep_runtime/model_factory.py`) — the numbers no longer size the budget.

## Intent Classification

Fast intent classification (utility `fast` tier — Haiku by default) is extracted into `src/orchestrator/intent_classifier.py`:
- `classify_intent()` — one `complete_text(tier="haiku")` call (mapped onto the resolver's `fast` tier), returns `(intent, confidence, sources)`
- `intent_to_plan()` — synthesizes lightweight PlanOutput from fast intents (replaces `intent_to_decision`)
- `extract_plan()` — parses structured JSON from Planner response text (replaces `extract_decision`)
- `_match_read_capability()` — keyword-to-capability mapping for fast-path single reads
- Constants: `FAST_INTENTS`, `INTENT_CONFIDENCE_THRESHOLD` (0.7), `VALID_PERCEPTION_SOURCES`

Fast intents (`greeting`, `chitchat`, `simple_question`, `data_fetch`, `status_query`, `approval_response`, `direct_answer`, `single_read`, `memory_operation`, `acknowledgment`) skip the Planner entirely and produce lightweight PlanOutput via `intent_to_plan()`.

## Data Flow

**Autonomous path:** Perceiver → EventProcessor (normalize, score, dedup, DLQ on failure) → Librarian (entities, memories) → Planner (PlanOutput with capability steps) → TrustEngine (approval gate per step) → Executor (execute via GraphExecutor, per-step capability scope via `resolve_for_step`) → Presenter (deliver via A2UI / web)

**Chat path:** intent → Planner (or fast-path plan) → ONE lead scoped to the plan's capability union → `permission_gate` at each write (allow / interrupt / prepare) → the lead's own reply. No per-step routing, no Presenter step.

**Perception signal flow:** Scheduler → PerceptionPolicyService (circuit breaker, rate limiting) → Perceiver → RelevanceAssessor (tier routing: act/alert/brief/silent) → Notifier (priority-scored delivery with hold-for-briefing)

## A2UI System (Agent-to-UI)

A2UI is the dynamic interface generation layer. Backend agents produce typed component trees that the frontend renders via a recursive React dispatcher.

**Backend pipeline:**
```
SurfaceService (surface_builder.py) or _push_workspace_surface (muldro.py)
  → uses renderer.py builders: card(), heading(), text(), badge(), button(), alert(), etc.
  → produces A2UISurface with populated children[]
  → delivered via: GET /v1/workspace/surfaces (REST) or muldro:a2ui:{user_id} (Redis → WebSocket)
  → persisted to ui_surfaces table (24h TTL)
  → live execution updates via SurfaceUpdate (emission points in graph_executor.py)
```

**Frontend pipeline:**
```
fetchWorkspaceSurfaces() or useMuldroWs hook (surface_update message type)
  → A2UISurface objects with children[]
  → useSurfaceStore (Zustand) — single store, updateSurface() for live merges
  → A2UIRenderer (renderer.tsx) — switch dispatcher
  → React components in components/a2ui/components/
  → ExecutionSurface, InsightSurface, InlineApprovalCard (new surface types)
```

**Key files:**
- Contracts: `src/ui/contracts.py` (A2UIComponent, A2UISurface, ComponentType enum)
- Builders: `src/ui/renderer.py` (builder functions: card, text, button, table, metric, etc.)
- Surface builder: `src/services/surface_builder.py` (SurfaceService — builds workspace surfaces from DB)
- Surface details: `src/services/surface_detail_builders/` (package — per-kind tab builders + a `(kind, tab_id)` registry)
- WS surface push: `src/orchestrator/muldro.py` `_push_workspace_surface()` + `_push_insight_surface()`
- Notifier: `src/services/notifier.py` (priority-scored delivery with rate limiting + hold-for-briefing)
- Frontend renderer: `frontend/src/components/a2ui/renderer.tsx`
- Frontend store: `frontend/src/stores/surface-store.ts` (single Zustand store)
- Execution surface: `frontend/src/components/a2ui/components/execution-surface.tsx` (phase-aware live renderer)
- Insight surface: `frontend/src/components/a2ui/components/insight-surface.tsx` (proactive insights with dismiss)
- Inline approval: `frontend/src/components/a2ui/components/inline-approval.tsx` (risk, trust, approve/edit/reject)
- Workspace: `frontend/src/app/page.tsx` → `workspace-canvas.tsx` (pure A2UIRenderer grid)
- Chat: `frontend/src/app/chat/page.tsx` → split-pane layout (chat left, surfaces right)

**Surface kinds:** run, summary, briefing, alert, recommendation, proactive_insight, `prepared_work` (the standing review queue for actions staged on turns with no human present), message (system/agent-managed) + legacy `plan` (still produced by `derive_surface_kind`) and `approval` (demoted to an inline run-surface detail tab). See the `SurfaceKind` Literal in `src/ui/contracts.py` for the authoritative set.

**Capability → Surface mapping** (in `_push_workspace_surface`): derives surface kind from plan capabilities.

**Live execution surfaces:** `SurfaceUpdate` contract (`src/contracts/__init__.py`, the neutral contracts layer — NOT `src/ui/contracts.py`, which holds the A2UI component tree) with phases: plan_ready → executing → approval_needed → completed/failed. This phase machine is **autonomous-path only** (emitted from `graph_executor.py`/`dag_runner.py`/`trust_gate.py` via `execution_surface_emitter.py`); the deep chat path emits none. Frontend `StepList` shows status icons (○ ◉ ✓ ✗ ⚠ 👤), plus the verification-nuance icons `✓?` (`completed_unverified` — sent but read-back-unconfirmed) and `⚠` (`partially_completed` — read-back contradicted). Those two DB statuses now pass through `step_status_to_ui` un-collapsed; the server-side `ui/units.py` step-icon map and the frontend `step-presentation.tsx` map both render them.

**Proactive insight surfaces:** `InsightSurfaceData` contract with signal summary, relevance reasoning, goals, suggested actions. Delivered via `_push_insight_surface()`. Dismissal tracked by `EngagementService` (3+ dismissals: penalty, 5+: suppressed). API: `POST /v1/insights/{surface_id}/dismiss`.

**API:** `GET /v1/workspace/surfaces` — unified endpoint returning pre-built A2UI surfaces. `POST /v1/insights/{surface_id}/dismiss` — dismiss insight. WebSocket `execute_insight` action bridges proposal→execution.

**Do not:** create surfaces with empty `children[]`. Always use `renderer.py` builders to populate component trees. Do not create client-side surface conversion (e.g., `approvalToSurface()`). Do not use `useSurfaceState` hook (deleted — use `useSurfaceStore` only).

## Trust Infrastructure & Approval

The autonomous path's deterministic approval gate is `TrustEngine` (`src/services/trust_engine.py`). It is not the only gate — see "One runtime, gated at action-time" below for how it composes with `permission_gate`:
- **RiskAssessor** (`src/services/risk_assessor.py`): risk assessment on the utility `fast` tier (`complete_text(tier="haiku")`, Haiku by default) with Redis-cached 24h TTL. Returns `RiskAssessment` (risk_level, reasoning, reversible, blast_radius).
- **TrustState** model (`src/models/trust_state.py`): Per-workspace, per-capability trust tracking (approved/rejected counts, trust_level, cooldown).
- **TrustEngine.evaluate()**: 4×4 matrix (trust_level × risk_level) → `PolicyDecision` (approval_required, auto_execute_notify, auto_execute_silent, blocked).
- **Trust graduation**: 3 approved → learning, 10 approved (<10% reject) → trusted, 25 approved (<5% reject) → autonomous. Where `permission_gate` is also installed (chat and the `process_message` batch entry) graduation does **not** reach past it — it sits inner of `trust_gate` and never consults trust, so an irreversible, external/public, or high-risk write is still staged at the `autonomous` level. On **GraphExecutor DAG steps** no `permission_gate` is installed, but the step's capability is pre-approved **only when the DAG gate actually cleared it** — a graduated capability still reaches the inner `trust_gate`'s irreversible-union override on any verdict that wanted a human. Graduation there speeds up what trust already covers; it does not silence an irreversible write. Note also what does **not** feed graduation: a PREPARED step records no approval (nothing was reviewed and nothing necessarily executed), and exempt reads / `system.*` actions are never evaluated at all. See "One runtime, gated at action-time" below.
- **Trust demotion**: Rejection applies cooldowns (72h/48h/24h) with demotion ladder.
- **Per-call cost attribution**: the `budget` middleware (`deep_runtime/middleware/budget.py`) records a `TokenUsage` span per model call (`trigger="chat"`) via a LangChain `after_model` hook. Per-*tool* token splitting is intentionally not carried over (it was analytics-only).
- **Trust API**: endpoints in `routes_trust.py` (dashboard, detail, ceiling, reset, time-policies GET+PUT).
- **Frontend Trust tab**: the Trust tab inside the Settings popup modal — grouped-by-family display, progress bars, ceiling dropdown, reset.
- **Risk assessment fails closed**: when the RiskAssessor LLM/JSON call fails, it returns `risk_level="high"` (not `medium`). `high` maps to `approval_required` at *every* trust level including `autonomous`, so an assessment outage can never silently auto-execute a write. Both fallback sites (`risk_assessor.py`, `graph_executor._assess_step_risk`) agree on this.

**One runtime, gated at action-time — two surfaces, two gate middlewares:**

All surfaces (chat, perception, autonomous) execute on the **single deep runtime** (`src/deep_runtime/` — a LangGraph/Deep-Agents graph built by `build_deep_agent`). This is the *only* runtime; the legacy `agent_loop` engine and its runtime-selection control plane are deleted. Muldro tools are inert schema shells (**tools-are-schemas / execution-is-central**): a central `muldro_tool_dispatcher` (`wrap_tool_call`) routes every execution through `ToolExecutor.execute_tool`. Policy is enforced by a fixed middleware chain wrapping that dispatcher (outer→inner): `capability_scope → governor_audit → unavailable_server → trust_gate → [permission_gate] → write_lock → [read_back] → repair_cap → dispatcher`. Treat `src/deep_runtime/` as the source of truth for its internals; the rebuild's step history lives in the local (untracked/gitignored) `docs/superpowers/plans/` planning trail, not here and not in the repo.

**Two independent facts travel with every chat turn, and neither derives from the other** (`src/deep_runtime/confirmation.py`):

- **`permission_mode`** (`bypass` | `ask` | `auto`) — *which* writes need a human. `bypass` never interrupts; `ask` confirms every write; `auto` confirms only irreversible, external-or-public, or high-risk writes, failing closed when risk is unknown. In *every* mode the four internal `system.*` action capabilities (`set_goal`, `set_instruction`, `schedule_reminder`, `add_to_brief`) are exempt — they are the user's own instructions to Muldro, not outbound writes.
- **`presence`** (`present` | `absent`) — *whether* a human is reachable on this turn. Precisely: `presence = caller_presence AND a durable checkpointer exists`, because an interrupt with nothing to resume from is not a reachable human. That second conjunct is an infrastructure fact, so the `can_pause` conflation is **narrowed and made fail-safe (downgrade-only), not eliminated** — which is exactly why it must not be widened again.

`presence` may only ever **downgrade** authority, never grant it: `bypass` + absent → `auto`, because "do not interrupt me" is only meaningful when there is a *me*. An unknown or blank mode fails closed to `ask`. The whole policy is the pure, exhaustively-tested `resolve_effective_permission_mode`. `presence` replaced the transport boolean `can_pause`, which had been silently acting as an authority input — do not reintroduce that conflation. **`bypass` is transitional**: it is fenced to a present, workspace-entitled user, and nothing new should be built on it.

**A write gate has three outcomes, not two: allow, interrupt, and PREPARE.** When a write needs a human and `presence` is `absent`, both write gates record it as an `Approval` (`approval_type="prepared_action"`) carrying the redacted payload plus a **snapshot of the acting agent's `capability_scope`**, then return a `status="success"` ToolMessage so the turn finishes everything else — a turn with three writes reports *"I did these two and prepared this one."* `status="success"` is load-bearing: `stream_adapter` maps `status="error"` onto the frozen `blocked` SSE frame, which would stop the lead at the first prepared write. Prepared work is discoverable in the `prepared_work` review queue surface — the **only** place an item can be acted on — and via the briefing, into whose context the Presenter injects one pointer line (LLM-mediated, so not a guaranteed literal). It is never announced per item, and there is no dedicated notification type.

- **Chat path** (`muldro.py` `process_message` / `process_message_stream`): **one shape, one lead per turn**, scoped to the plan's capability union (`derive_lead_scope`) and discovering its own tools. There is no per-step agent routing and no Presenter step — the lead's own reply is the turn's reply. The turn normally carries `authorization_source = DIRECT_USER_REQUEST`, under which `trust_gate` (TrustEngine) stays **dormant** — the user's message *is* the turn's authorization — and **`permission_gate`** (`src/deep_runtime/middleware/permission_gate.py`) is the chat gate, installed whenever the effective mode is `ask` or `auto`. Writes are additionally held by the always-on `capability_scope` + `write_lock` middlewares.
- **Autonomous path** (`graph_executor.py`; all scheduler/perception-triggered runs): persisted as DB `Plan`s and driven per-step by `GraphExecutor` / `DagRunner`, which run each step *through the deep runtime* with `authorization_source = AUTONOMOUS`. **TrustEngine's 4×4 matrix gates every step that is not exempt**, enforced at two layers — the DAG-step `TrustGate` (`dag_runner.py`) and the deep `trust_gate` middleware (`trust_engine.evaluate`); `pre_approved_capabilities` short-circuits the inner gate so a step the DAG gate *cleared* is not double-prompted, and is deliberately withheld when it did not. Reads and internal `system.*` actions skip the DAG gate entirely. A verdict that wants a human on a run with none present PREPAREs rather than pausing — see "Execution State Machine" below.
- **Non-chat callers of `process_message` declare their real provenance.** The scheduler's dispatch actions and the WebSocket unknown-action fallback pass `authorization_source = AUTONOMOUS` rather than wearing the founder's identity to get past the gate — which *activates* `trust_gate` for those turns. They also run `presence="absent"`, so an `approval_required` verdict becomes PREPARE rather than an interrupt into a void.
- **Trust and permission answer different questions, and trust does not suppress permission — *where both are installed*.** `trust_gate` asks a per-**capability** question ("has the founder approved this capability enough times to stop asking?"); `permission_gate` asks a per-**action** one ("is *this* write irreversible or externally visible?"). `trust_gate` is **outer**, and its auto-execute verdict returns `await handler(request)` — it falls **through** to `permission_gate`, which decides on `mode × assessment` alone and **never consults trust**. Letting accrued trust suppress the second would let twenty-five approved self-scoped sends silently authorise a send to a brand-new external counterparty.
- **Which gates are actually installed differs by entry point — do not generalise across them.** There are three shapes:
  - **User-typed chat** — `DIRECT_USER_REQUEST` keeps `trust_gate` dormant; `permission_gate` is the gate.
  - **`process_message` batch** (scheduler dispatch actions, WS action fallback) — declares `AUTONOMOUS`, so `trust_gate` wakes **on the lead leg**, and `permission_mode` defaults to `auto`, so `permission_gate` is installed too. **Both** gates run, and the composition above holds in full: an irreversible / external-or-public / high-risk write is staged at **every** trust level including `autonomous`.
  - **GraphExecutor DAG steps** (`run_autonomous_deep_step`) — pass **no** `permission_mode`, so **`permission_gate` is not installed**. The DAG-level `TrustGate` (`services/trust_gate.py`) is a bare `TrustEngine.evaluate` with **no** irreversible override, so it is *not* the last line of defence on this path — **`pre_approved_capabilities` is now conditional, and that is what carries the invariant.** `dag_runner` forwards `{step.capability}` **only on an auto-execute verdict** (a gate ran and said yes). On an `approval_required` verdict with `presence="absent"` — every autonomous run — it forwards the **empty set** and lets the step run anyway, so the deep `trust_gate` evaluates the **real tool call** and PREPAREs it (replayable payload + capability-scope snapshot) into the `prepared_work` queue while the run carries on. A DAG *step* can never be staged that way: it is an agentic unit with no recorded tool call, which is why `prepared_actions.py` states that "a prepared action has no run and no step". **Do not restore an unconditional `pre_approved_capabilities`** — it short-circuits the inner gate before its irreversible-union override, which is precisely how a graduated capability could execute an irreversible write unreviewed.
- **Confirmation replays a recorded payload; it never re-runs an agent.** `src/services/prepared_actions.py` executes the exact tool call the founder reviewed, checked against the `capability_scope` snapshot taken at prepare time — routing it back through `GraphExecutor` would re-*derive* the action instead. It fails closed on every way the recorded action could fail to be the reviewed one — missing tool name, unknown tool, no capability, registry drift, out-of-scope capability, missing snapshot, truncated payload, unreadable payload — and is exactly-once via the idempotency ledger keyed on the approval id.
- **Capability-scope (the always-on compensating control):** `src/deep_runtime/middleware/capability_scope.py` is the **outermost** guard (installed first by `build_deep_agent`), enforcing each agent's `capability_scope` at tool-execution time via one `ToolRegistry.get_tool` lookup (fail-closed for known capabilities; `build_deep_agent` refuses to compile a write-capable agent without it). It enforces the agent's *own* scope, never the offered tool list, and remains the real boundary until the platform JWT mint is re-keyed per action.
- **Latent enhancement (not yet implemented):** if a chat turn's write was triggered by *perception-sourced* content rather than the user's literal words, forcing `permission_gate` to confirm it regardless of `bypass` would be defensible. Tracked, not built.

**Key files:** `src/services/risk_assessor.py`, `src/services/trust_engine.py`, `src/models/trust_state.py` (TrustState + TrustCeiling), `src/api/routes_trust.py`, `src/deep_runtime/confirmation.py`, `src/services/prepared_actions.py`

## Execution State Machine

`detected → planned → policy_checked → approved → executing → completed/failed`

TaskRun statuses: `pending, running, paused, awaiting_approval, awaiting_input, completed, failed, cancelled, blocked, partially_completed, archived, timed_out`

TaskStep statuses: `pending, ready, running, completed, failed, skipped, waiting_approval, awaiting_input, blocked, timed_out`

State transitions are enforced by `src/services/execution_state.py` — never mutate status directly, use `transition_run()` / `transition_step()`. Retry: `failed → pending`.

**DAG-level approval gate in GraphExecutor** (`dag_runner.py`): per step → `auto_execute_notify` executes + notifies, `auto_execute_silent` executes silently, `approval_required` **prepares** (see below). Governor hooks are audit-only (`hooks.py` always returns `allowed: True` except for blocked tools). `auto_execute_*` does **not** fall through to `permission_gate` on this path — `run_autonomous_deep_step` passes no `permission_mode`, so the gate is never installed (`agent_invoker.py`: `permission_gate_chain` is empty unless the mode is `ask`/`auto`).

Two rules make the DAG gate *live* rather than a deadlock, and both mirror `permission_gate` rather than inventing a second policy:

- **The gate is not consulted for reads or internal `system.*` actions.** `dag_runner` bypasses `SYSTEM_ACTION_CAPABILITIES` and `is_read_only_capability` before risk assessment, against the same predicates `permission_gate` uses, so the two cannot drift. Without this, `first_use` × `risk=none` → `approval_required` meant a bare `email.list` needed a human — and since trust graduates *only* through approvals, autonomous perception could never bootstrap out of it. Every capability starts at `first_use`, and both `first_use` and `learning` require approval at **every** risk level, so this is a property of the matrix, not of any one capability.
- **`approval_required` with nobody present PREPAREs; it never freezes the run.** `presence` reaches `DagRunner` (default `absent` — every GraphExecutor run is autonomous) and selects between `create_approval_and_pause` and running the step un-pre-approved so the inner gate stages the real tool call. Freezing was the worst of the three outcomes: the run stopped dead, the approval expired unanswered at its 24h deadline, and `heartbeat._expire_approvals` cancelled the whole run at 0/N steps. **A run must never park on a human who is not there.** Pinned by `tests/test_autonomous_gate_liveness.py`.

**InteractionLog** (`src/models/interaction_log.py`): Lightweight audit record for simple interactions (replaces TaskRun for non-execution flows).

**Eviction**: `EvictionService` (`src/services/eviction_service.py`) — 90-day retention with cascade cleanup (vector store + graph engine).

## Runtime Resilience

The deep runtime is a LangGraph graph over `langchain-anthropic`, so several behaviors the legacy `agent_loop` hand-rolled are now provided by that stack or deliberately dropped — the notes below reflect the *current* deep runtime, not the retired loop.

- **Run-level timeout**: background runs are capped via `asyncio.wait_for` in `graph_executor.py` (`run.timeout_seconds or 600`s); user-initiated chat runs are uncapped. There is no per-tool timeout on the deep path (the legacy 60s per-tool ceiling was not carried over).
- **API retry / backoff**: delegated to `langchain-anthropic`'s client — the runtime no longer wraps the Anthropic API in a Muldro-owned RateLimit backoff loop.
- **Tool error signaling**: a failed tool returns a `ToolMessage(status="error")` — set in `muldro_tool_dispatcher.py` when the result carries `error`/`blocked`, and by the `capability_scope` / `permission_gate` / `write_lock` middlewares on refusal. `stream_adapter.py` maps `status="error"` to the frozen `blocked` SSE frame so the client knows the call failed.
- **Model / thinking params**: built once per agent tier at model construction. `config/model_catalog.py` holds the capability FACTS per `(provider, model_id)` — `thinking_style`, whether the model accepts `temperature`, prompt-cache support — and `config/capability_map.py` translates neutral inputs into provider-specific kwargs keyed on `thinking_style`, **dropping** any kwarg the model would reject so a caller never 400s. Adaptive-thinking models drop `temperature` and the legacy `thinking:{type:"enabled"}` shape; `thinking_style="none"` (e.g. local Ollama models) gets neither thinking nor effort. Which model backs a tier is DB data (`ModelBinding`, per-workspace overridable via `PUT /v1/model-config`); the capability facts are versioned code. There is no mid-loop "disable thinking and retry" fallback.
- **Background task tracking**: `_spawn_background()` replaces bare `asyncio.create_task()`. Tasks are tracked in `_background_tasks` set with done-callback cleanup. `shutdown()` awaits pending tasks.
- **Perception idempotency**: `pending_run=False` is set atomically BEFORE running perception cycles, preventing the next scheduler tick from double-picking the same source.
- **Perception circuit breaker**: `PerceptionPolicyService` (`src/services/perception_policy.py`) with error classification (transient: 6 failures, permanent: 1 failure), starvation prevention (30-min ceiling), exponential backoff with jitter.
- **DLQ**: `DeadLetterService` (`src/services/dead_letter.py`) for failed operations. Scheduler retries via `_tick_dlq_retry()`. Auto-exhaustion after max_attempts.
- **EventBus init race**: `asyncio.Lock()` with double-check pattern guards lazy `_ensure_event_bus()` initialization.
- **JSON parsing protection**: `_draft_action` and `_summarize_action` in GraphExecutor catch `JSONDecodeError` with graceful fallback.
- **Budget hydration**: BudgetTracker in-memory counter hydrates from DB on day change (survives restarts). `record_from_span()` as single source of truth.
- **Input validation**: `process_message` and `process_message_stream` reject empty `user_id`, `workspace_id`, or `message` before starting a trace.
- **Notification rate limiting**: Per-surface hourly caps (web:15, slack:8, email:3) via Redis INCR. Priority scoring with hold-for-briefing (score < 0.6).
- **Memory expiration**: Scheduler `_tick_memory_expiration()` enforces TTL with Qdrant cascade deletes. Stability decay: 0.02/day, +0.1 on access.
- **Execution timeout**: Background runs capped at 600s, user-initiated unlimited.

## Background Tasks

The `SchedulerLoop` (`src/services/scheduler.py`) runs a `_tick_background_tasks()` method every 30s that picks up `TaskRun` records with `status="pending"` and `source="background"`, executes them via `GraphExecutor`, and notifies the user on completion.

## Conversation Context

`_load_conversation_history` loads up to 20 messages (8000 chars) including `metadata_` column. Assistant messages are annotated with their decision type (e.g., `Assistant [create_task]: ...`), giving downstream agents execution lineage. When history overflows, older messages are summarized on the utility `fast` tier (`_summarize_history` in `orchestrator/context_assembler.py`) and prepended as `[Earlier conversation summary]`. Most recent 5 messages are kept verbatim.

## Intelligence Loop (Soul)

The system learns from execution outcomes and synthesizes across perception sources:

- **Outcome learning** (`_learn_from_outcome`): After each run completes, checks for linked approval decisions. Approved/rejected actions are stored as preference memories. Failed plans are stored as task_context memories (30-day TTL). These flow into future context packs via the preference system.
- **Cross-source synthesis**: When 2+ perception sources have new events in the same scheduler tick, triggers a Planner synthesis call to identify cross-cutting insights. Throttled to once per 30 minutes, budget-aware.
- **Explicit preference injection**: `ContextBuilder.build()` fetches ALL active preferences via `get_user_preferences()` and merges with semantic matches, ensuring preferences always influence decisions even when they don't match the current query semantically.
- **Context enrichment**: `_assemble_context()` builds a full ContextPack with entities, memories, preferences, graph relationships (Neo4j), related runs, procedures, artifacts, goals, constraints, and risks. Rendered as structured sections in the agent system prompt.

## Multi-Tenant Workspace Isolation

All data tables are scoped by `workspace_id` (NOT NULL FK to `workspaces`). Only 5 tables are user-level: `users`, `workspaces`, `workspace_members`, `sessions`, `magic_links`. Global table: `agents`.

- API routes: resolve workspace via `get_current_workspace_id()` dependency (reads from session, zero queries)
- Background services: resolve via `resolve_workspace_id(db, user_id)` helper (queries WorkspaceMember)
- No default users — every function requires explicit `user_id` from auth context

## Git

- Do NOT add `Co-Authored-By` lines to commit messages
- Follow conventional commits: `<type>: <description>`

## Documentation Maintenance

**Code is the source of truth.** Docs (including this file) capture durable architecture, design intent, and invariants — not an inventory of the codebase.

- **Never record volatile counts** — file/router/model/migration/test/tool counts, line numbers as identity, or exhaustive file lists. They rot within days and mislead agents into false precision. Name the directory and let the reader inspect it.
- **Document the durable, not the incidental** — layering and one-way deps, boundary contracts, invariants, state machines, agent roles, and the *why* behind decisions. Skip anything trivially re-derivable from the code.
- **Update docs only when an architectural fact changes** — a component added/removed, a contract/invariant/dependency changed, a concept renamed. Routine edits (adding a file, tool, migration, or test) require **no** doc change. When in doubt, leave the docs: a smaller doc that is correct beats a larger one that drifts.

See [docs/engineering-standards.md](docs/engineering-standards.md) for the full standard.

## Common Mistakes

### Contracts & Routing
- Do not let the planner output free-form text — always structured PlanOutput JSON with steps and capabilities
- Do not reference `PlannerOutput` or its 19 decision types — replaced by `PlanOutput` (capability-based steps, no decision field)
- Do not reference `RouteResolver`, `DEFAULT_ROUTES`, or `agent_routes` table — deleted. Capability-based routing via `CapabilityResolver` replaced decision-type routing
- Do not reference `observer` or `researcher` agents — merged into `perceiver`
- Do not reference `MULDRO_DECISION_FRAMEWORK`, `MULDRO_SOUL`, `OBSERVER_PROMPT`, `RESEARCHER_PROMPT` — deleted. Use `MULDRO_SOUL_CORE` + `PLANNER_PROMPT_V2` / `PERCEIVER_PROMPT`
- Do not import `intent_to_decision`/`extract_decision` — renamed to `intent_to_plan`/`extract_plan` in `intent_classifier.py`

### Approval & Trust
- Do not use Governor as an approval gate — it is audit-only. The gates are `trust_gate` (TrustEngine, per capability, autonomous path) and `permission_gate` (per action, chat). Do not collapse them into one: trust is evidence about a *capability*, not about an *action*
- Do not add a TrustEngine gate to a **user-typed** chat turn — under `authorization_source=DIRECT_USER_REQUEST` TrustEngine stays **dormant** by design (the user's message = authorization). Action-time write confirmation there is `permission_gate`'s job (per `permission_mode`); capability-scope enforcement lives in the `capability_scope` deep-runtime middleware. The qualifier matters: `process_message` is *also* the batch entry point for scheduler dispatch and the WS action fallback, and those callers declare `AUTONOMOUS`, which **wakes** `trust_gate` for their turns. Route provenance through `authorization_source`; do not hard-wire a gate on or off by entry point. See "One runtime, gated at action-time" above
- Do not reintroduce a transport flag as an authority input. `can_pause` conflated "can this turn interrupt?" with "how should this write be gated?" — `presence` names the first fact explicitly and `permission_mode` owns the second
- Do not route a prepared action's confirmation through `GraphExecutor` / `run_step_via_deep_agent` — an agent would re-derive the action instead of executing the one the founder reviewed
- Do not re-derive an agent's `capability_scope` at confirmation time — the snapshot on the Approval is the authority the turn actually held, and a since-widened scope must not authorise it retroactively
- Do not give a prepared Approval `artifact_refs["chat"]` — that flag routes it to `/v1/muldro/chat/resume`, and a prepared action has no thread to resume
- Do not "correct" a prepared write's ToolMessage to `status="error"` — `stream_adapter` maps that onto the frozen `blocked` SSE frame, which would stop the lead at the first prepared write. A prepared action is staged work, not a failure
- Do not claim a gate composition without naming the entry point. `permission_gate` is installed only when the turn carries a `permission_mode` — true for chat and for `process_message` batch turns, **false** for GraphExecutor DAG steps. "Trust never suppresses permission" is a statement about the first two only
- Do not reference `settings.deep_single_lead`, `can_pause`, `presenter_skip`, `capability_resolver.route_step`, or `chat_pipeline`'s presenter prompt builders — deleted with the legacy chat arm
- Do not rename an SSE event string while tidying. Two names cross: the `PlanReady` CoreEvent maps to SSE `"plan"`, while `PlanModeStepSkipped` maps to SSE `"plan_ready"`. `tests/test_core_events.py` pins the backend mapping including the literals, but nothing pins the **frontend** switch — so a coordinated backend-plus-test rename still breaks the UI silently
- Do not let an autonomous run park on a human who is not there. `approval_required` + `presence="absent"` must PREPARE (stage the real tool call, continue the run), never `create_approval_and_pause` — a frozen run's approval expires unanswered and the run is cancelled at 0/N steps. This is why `DagRunner` takes `presence`
- Do not gate reads or internal `system.*` actions on the DAG path, and do not hand-roll a second predicate for "safe". Use `is_read_only_capability` + `SYSTEM_ACTION_CAPABILITIES`, the same ones `permission_gate` uses. Gating a read means perception cannot SEE, and since trust graduates only through approvals it can never bootstrap out
- Do not restore an unconditional `pre_approved_capabilities={step.capability}` in `step_runner`. It is the DAG saying "a gate already cleared this"; passing it on a verdict that wanted a human short-circuits the inner `trust_gate` before its irreversible-union override
- Do not count a PREPARED step as an approval (`record_auto_execution_outcome`). Nothing was reviewed and nothing necessarily executed; counting it graduates a capability on unreviewed writes, which at `autonomous` then execute silently
- Do not make the RiskAssessor fail open — its failure default is `risk_level="high"` (forces approval). Do not "simplify" it back to `medium`
- Do not reference `ApprovalPolicyEngine`, `TrustScore` model, or `ApprovalPolicy` model — deleted. Use `TrustEngine` + `TrustState` + `TrustCeiling`
- Do not create tool-level approvals without `run_id` and `artifact_refs` — the approval resume path needs these

### Execution & State
- Do not mutate TaskRun/TaskStep status directly — use `transition_run()` / `transition_step()`
- Do not bypass the deep runtime for step execution — GraphExecutor delegates to the deep runtime (`build_deep_agent`) per step
- Do not use bare `asyncio.create_task()` in muldro.py — use `self._spawn_background()` for lifecycle tracking

### Data & Services
- Do not use bare `db = db_factory()` — always `async with db_factory() as db:` + `await db.commit()`
- Do not skip `workspace_id` when calling `_assemble_context()` or `ContextBuilder.build()` — preferences and related runs are workspace-scoped
- Do not hardcode user IDs — resolve from auth context
- Do not store secrets in memory or model context

### Tools & MCP
- Do not add tool definitions to multiple files — use `catalog.py` only (`InternalToolDef` or `ExternalToolSeed`)
- Do not add internal MCP tools without adding them to ALL three places: `schemas.py` (input model), `catalog.py` (tool def), `intelligence_server.py` (implementation)
- Do not hardcode tool names in agent prompts — agents discover tools via the MCP tool list
- Do not normalize MCP tool names — use real names everywhere
- Do not hardcode tool-calling sequences — let agents discover tools autonomously
- Do not use deleted modules: `tool_normalizer.py`, `tool_policy.py`, `tool_schemas.py`, `route_resolver.py`, `route_analytics.py`
- Do not use `TOOL_TO_CAPABILITY`, `_DEFAULT_TOOLS`, `CANONICAL_ALIASES`, `_NATIVE_TOOL_MAP` — all deleted
- Do not assume external MCP servers run in Docker — they run on-demand as host processes (remote HTTP for GitHub/Atlassian, `uvx` for Google Workspace, `npx` for others); the `google-workspace-mcp` Docker image and service were removed
- Do not assume MCP tool schemas are discovered at startup — `initialize_mcp_bridge()` registers configs only; schemas are lazily fetched on first agent build and persisted in `ToolDefinition.input_schema`

### A2UI & Frontend
- Do not push A2UI surfaces with empty `children[]` — use `renderer.py` builders
- Do not create client-side surface conversion functions — surfaces built server-side by `SurfaceService`
- Do not use `useSurfaceState` hook — deleted. Use `useSurfaceStore` (Zustand)
- Do not import from `src/ui/views.py` — deleted. Use `renderer.py` builders + `SurfaceService`

### Streaming & Handlers
- Do not add handlers only to `process_message` — always wire into BOTH `process_message` and `process_message_stream`
- Do not give agents write capabilities without corresponding read capabilities (read-before-write principle)
- Do not over-engineer — Postgres + Redis + Qdrant is the core stack
