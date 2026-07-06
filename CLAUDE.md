# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Jarvis

Jarvis is a **Personal AI Operating System** for founders. It is NOT a chatbot — it is an OS with a core loop: Perceive → Understand → Update Model → Plan → Act → Communicate.

## Architecture

Multi-agent hub-and-spoke: a central `JarvisOrchestrator` (`backend/src/orchestrator/jarvis.py`) routes to 7 sub-agents via Claude API. Capability-based routing: Planner produces `PlanOutput` with steps, `CapabilityResolver` maps each step's capability to the appropriate agent. Internal FastMCP servers wrap the intelligence layer; external MCP servers provide connectors — all run **on demand with no Docker dependency**: GitHub and Atlassian as remote HTTP MCP servers, Google Workspace as an on-demand local `uvx` process managed by `LocalMCPProcessManager` (`backend/src/integrations/local_process_manager.py`), and stdio servers (Slack, Notion, Playwright, Filesystem) via `npx`. MCP sessions are **turn-scoped** via `TurnScope` (`backend/src/integrations/turn_scope.py`) and torn down at turn end; the scheduler's `run_health_tick` idle reaper is the safety net.

```
User <-> Next.js Frontend (A2UI)
              |
         JarvisOrchestrator (Claude API)
         Routes to: Perceiver, Librarian, Planner, Governor,
                    Operator, Presenter, Persona
              |
         CapabilityResolver (step.capability → agent)
         TrustEngine (single approval gate per step)
              |
         Tool Layer: FastMCP (intelligence + communication) + external MCP servers
              |
         Intelligence Backend (Postgres + Redis)
         EventProcessor, WorldModel, MemoryService, Planner,
         Governor, Operator, Presenter, Audit, DLQ
```

**Key paths:**
- Orchestrator + agents: `backend/src/orchestrator/` (jarvis.py, agents.py, agent_loop.py, hooks.py, prompts.py, tracing.py, budget.py, perception.py, recovery.py, intent_classifier.py, api_circuit_breaker.py, capability_summary.py, services.py)
- Services (business logic): `backend/src/services/` — planner, governor, operator, presenter, memory_service, world_model, event_processor, capability_resolver, risk_assessor, trust_engine, etc.
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

All backend settings via env vars with `JARVIS_` prefix (pydantic-settings in `src/config/settings.py`). Key vars: `JARVIS_DATABASE_URL`, `JARVIS_REDIS_URL`, `JARVIS_ANTHROPIC_API_KEY`, `JARVIS_USE_BEDROCK`, `JARVIS_LOG_JSON`, `JARVIS_DAILY_TOKEN_BUDGET_USD`, `JARVIS_RERANKER_MODEL`, `JARVIS_RERANKER_ENABLED`, `JARVIS_SKIP_REGISTRY_VALIDATION`. Uses `.env` file.

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
- **Tests**: pytest + pytest-asyncio (asyncio_mode = "auto"). Test files mirror `src/` structure. Use `make_mock_settings()` from `tests/conftest.py`. Mock Anthropic client via `@patch("src.orchestrator.jarvis.get_anthropic_client")`.

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

| Agent | Model | Role | Write Scope |
|-------|-------|------|-------------|
| Perceiver | Sonnet | Gather information from any source — email, calendar, Slack, GitHub, web, internal knowledge (read-only) | normalized_events |
| Librarian | Sonnet | Extract entities, update world model, store memories | entities, relationships, memories |
| Planner | Opus | Produce capability-based plans (structured PlanOutput JSON) via PLANNER_PROMPT_V2 | plans, plan_tasks, goal memories |
| Governor | Sonnet | Edge-case safety fallback only (audit-only hooks, `edge_case_only=True`). Invoked when: low confidence, unknown capability, conflicting signals | policy decisions |
| Operator | Sonnet | Execute approved plans via tools (reads context first) | task_runs, task_steps |
| Presenter | Sonnet | Generate user-facing text output | briefings, A2UI surfaces (via SurfaceService + renderer.py) |
| Persona | Haiku | Learn and store preferences (batched every 10th scheduler tick, min 5 interactions) | memories (preference type) |

**Only Planner decides intent. Only Operator executes external actions. Only Presenter talks to the user. TrustEngine (not Governor) gates every external write via a single deterministic approval gate in GraphExecutor.**

## Capability-Based Routing

The Planner produces a `PlanOutput` with ordered `PlanStep` entries. Each step has a `capability` field (e.g., `email.send`, `knowledge.search`, `system.respond`). The `CapabilityResolver` (`src/services/capability_resolver.py`) maps capabilities to agents:

| Capability Prefix | Agent |
|------------------|-------|
| `reason.*`, `respond.*`, `system.respond` | Presenter |
| `knowledge.*` | Librarian |
| `email.read/list/search`, `calendar.read`, any read capability | Perceiver |
| Write capabilities (`email.send`, `calendar.create`, etc.) | Operator |

**Key files:** `src/services/capability_resolver.py` (resolve, route_step, is_read_capability, is_write_capability), `src/orchestrator/capability_summary.py` (generate_capability_summary — compact ~200-token XML for Planner prompt)

## Agentic vs Scripted Execution

All steps use **agentic execution**: the agent goes through the agent loop, discovers available tools, and autonomously decides which to call.

Multi-step plans trigger GraphExecutor for DAG management (dependencies, checkpointing, resume, TrustEngine approval gates), but each step within the DAG is executed via the agent loop — the routed agent discovers tools autonomously per step. GraphExecutor is a **durable DAG wrapper around agent_loop**, not a separate execution mode.

**Do not** hardcode tool-calling sequences in Python handlers. Let agents discover tools via the agent loop. The agent loop handles tool discovery, multi-turn reasoning, error recovery, and audit hooks automatically.

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

**Startup:** `seed_defaults()` reads from `INTERNAL_TOOLS` + `EXTERNAL_TOOL_SEEDS` in catalog.py → upserts into `tool_definitions` table. `validate_registry()` runs startup cross-checks. `JARVIS_SKIP_REGISTRY_VALIDATION=true` disables validation in emergencies. `initialize_mcp_bridge()` registers server configs only — **no eager tool discovery at startup**. Tool schemas are durable in the DB (`ToolDefinition.input_schema`) and lazily (re)discovered per server on first agent build via `discover_and_persist` / `discover_missing_schemas`. A startup preflight (`backend/src/integrations/runtime_preflight.py`) warns if `uvx`/`npx` are missing from the host.

**Key files:**
- Catalog: `src/tools/catalog.py` (InternalToolDef, ExternalToolSeed, INTERNAL_TOOLS, EXTERNAL_TOOL_SEEDS)
- Schemas: `src/tools/schemas.py` (Pydantic input models for internal tools, TOOL_INPUT_MODELS registry)
- Validation: `src/tools/validation.py` (startup cross-checks)
- Registry: `src/services/tool_registry.py` (ToolRegistry — DB CRUD + seed from catalog)
- Capabilities: `src/integrations/capabilities.py` (CAPABILITY_CATALOG, CapabilityFamily — taxonomy only)

## Agent Prompt Architecture

System prompts (`src/orchestrator/prompts.py`):
- `JARVIS_SOUL_CORE` — shared by all 7 agents (role, agent table, rules, TrustEngine gates writes)
- `PLANNER_PROMPT_V2` — 7-step capability-based decomposition engine (replaces decision classification)
- `PERCEIVER_PROMPT` — 7-step read-only methodology with JSON output (findings, synthesis, gaps, confidence)
- `GOVERNOR_PROMPT` — Edge-case safety fallback only (audit-only per Spec 2B-i)
- `LIBRARIAN_PROMPT`, `OPERATOR_PROMPT`, `PRESENTER_PROMPT`, `PERSONA_PROMPT` — agent-specific roles

Only the Planner sees `PLANNER_PROMPT_V2`. Other agents receive `JARVIS_SOUL_CORE` + their role prompt. The Planner also receives a ~200-token capability summary (via `generate_capability_summary()`) instead of 15-20K raw tool schemas.

**Thinking budgets** (`src/orchestrator/agents.py`): Planner=8192, Perceiver=6144, Librarian=4096, Presenter=4096, Governor=2048, Operator=2048, Persona=2048.

## Intent Classification

Fast Haiku-based intent classification is extracted into `src/orchestrator/intent_classifier.py`:
- `classify_intent()` — calls Haiku, returns `(intent, confidence, sources)`
- `intent_to_plan()` — synthesizes lightweight PlanOutput from fast intents (replaces `intent_to_decision`)
- `extract_plan()` — parses structured JSON from Planner response text (replaces `extract_decision`)
- `_match_read_capability()` — keyword-to-capability mapping for fast-path single reads
- Constants: `FAST_INTENTS`, `INTENT_CONFIDENCE_THRESHOLD` (0.7), `VALID_PERCEPTION_SOURCES`

Fast intents (`greeting`, `chitchat`, `simple_question`, `data_fetch`, `status_query`, `approval_response`, `direct_answer`, `single_read`, `memory_operation`, `acknowledgment`) skip the Planner entirely and produce lightweight PlanOutput via `intent_to_plan()`.

## Data Flow

Perceiver → EventProcessor (normalize, score, dedup, DLQ on failure) → Librarian (entities, memories) → Planner (PlanOutput with capability steps) → TrustEngine (single approval gate per step) → Operator (execute via GraphExecutor) → Presenter (deliver via A2UI / web)

**Perception signal flow:** Scheduler → PerceptionPolicyService (circuit breaker, rate limiting) → Perceiver → RelevanceAssessor (tier routing: act/alert/brief/silent) → Notifier (priority-scored delivery with hold-for-briefing)

## A2UI System (Agent-to-UI)

A2UI is the dynamic interface generation layer. Backend agents produce typed component trees that the frontend renders via a recursive React dispatcher.

**Backend pipeline:**
```
SurfaceService (surface_builder.py) or _push_workspace_surface (jarvis.py)
  → uses renderer.py builders: card(), heading(), text(), badge(), button(), alert(), etc.
  → produces A2UISurface with populated children[]
  → delivered via: GET /v1/workspace/surfaces (REST) or jarvis:a2ui:{user_id} (Redis → WebSocket)
  → persisted to ui_surfaces table (24h TTL)
  → live execution updates via SurfaceUpdate (emission points in graph_executor.py)
```

**Frontend pipeline:**
```
fetchWorkspaceSurfaces() or useJarvisWs hook (surface_update message type)
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
- Surface details: `src/services/surface_detail_builders.py` (trust context, approval preview, graduation hints)
- WS surface push: `src/orchestrator/jarvis.py` `_push_workspace_surface()` + `_push_insight_surface()`
- Notifier: `src/services/notifier.py` (priority-scored delivery with rate limiting + hold-for-briefing)
- Frontend renderer: `frontend/src/components/a2ui/renderer.tsx`
- Frontend store: `frontend/src/stores/surface-store.ts` (single Zustand store)
- Execution surface: `frontend/src/components/a2ui/components/execution-surface.tsx` (phase-aware live renderer)
- Insight surface: `frontend/src/components/a2ui/components/insight-surface.tsx` (proactive insights with dismiss)
- Inline approval: `frontend/src/components/a2ui/components/inline-approval.tsx` (risk, trust, approve/edit/reject)
- Workspace: `frontend/src/app/page.tsx` → `workspace-canvas.tsx` (pure A2UIRenderer grid)
- Chat: `frontend/src/app/chat/page.tsx` → split-pane layout (chat left, surfaces right)

**Surface kinds:** summary, briefing, plan, checklist, approval, comparison, alert, timeline, table, recommendation, activity, execution, proactive_insight

**Capability → Surface mapping** (in `_push_workspace_surface`): derives surface kind from plan capabilities.

**Live execution surfaces:** `SurfaceUpdate` contract (`contracts.py`) with phases: plan_ready → executing → approval_needed → completed/failed. Multiple emission points in `graph_executor.py`. Frontend `StepList` shows status icons (○ ◉ ✓ ✗ ⚠ 👤).

**Proactive insight surfaces:** `InsightSurfaceData` contract with signal summary, relevance reasoning, goals, suggested actions. Delivered via `_push_insight_surface()`. Dismissal tracked by `EngagementService` (3+ dismissals: penalty, 5+: suppressed). API: `POST /v1/insights/{surface_id}/dismiss`.

**API:** `GET /v1/workspace/surfaces` — unified endpoint returning pre-built A2UI surfaces. `POST /v1/insights/{surface_id}/dismiss` — dismiss insight. WebSocket `execute_insight` action bridges proposal→execution.

**Do not:** create surfaces with empty `children[]`. Always use `renderer.py` builders to populate component trees. Do not create client-side surface conversion (e.g., `approvalToSurface()`). Do not use `useSurfaceState` hook (deleted — use `useSurfaceStore` only).

## Trust Infrastructure & Approval

Single deterministic approval gate via `TrustEngine` (`src/services/trust_engine.py`):
- **RiskAssessor** (`src/services/risk_assessor.py`): Haiku-based risk assessment with Redis-cached 24h TTL. Returns `RiskAssessment` (risk_level, reasoning, reversible, blast_radius).
- **TrustState** model (`src/models/trust_state.py`): Per-workspace, per-capability trust tracking (approved/rejected counts, trust_level, cooldown).
- **TrustEngine.evaluate()**: 4×4 matrix (trust_level × risk_level) → `PolicyDecision` (approval_required, auto_execute_notify, auto_execute_silent, blocked).
- **Trust graduation**: 3 approved → learning, 10 approved (<10% reject) → trusted, 25 approved (<5% reject) → autonomous.
- **Trust demotion**: Rejection applies cooldowns (72h/48h/24h) with demotion ladder.
- **Per-tool cost attribution**: `TokenUsage` with `trigger=f"tool:{tool_name}"` in `agent_loop.py`.
- **Trust API**: endpoints in `routes_trust.py` (dashboard, detail, ceiling, reset, time-policies GET+PUT).
- **Frontend Trust tab**: the Trust tab inside the Settings popup modal — grouped-by-family display, progress bars, ceiling dropdown, reset.
- **Risk assessment fails closed**: when the RiskAssessor LLM/JSON call fails, it returns `risk_level="high"` (not `medium`). `high` maps to `approval_required` at *every* trust level including `autonomous`, so an assessment outage can never silently auto-execute a write. Both fallback sites (`risk_assessor.py`, `graph_executor._assess_step_risk`) agree on this.

**Two execution paths — only the autonomous path is gated (by design):**
- **Chat path** (`jarvis.py` `process_message` / `process_message_stream`): single-step / lightweight plans execute inline via `_call_agent()` with **no TrustEngine gate**. This is intentional — the user's direct chat message *is* the authorization for that turn. Do **not** "add a trust gate to the chat path" as a bugfix; it would double-prompt the user for actions they just requested.
- **Autonomous path** (`graph_executor.py`): multi-step / risky plans (and all scheduler/perception-triggered runs) are persisted as DB `Plan`s and executed through GraphExecutor, where **TrustEngine gates every step**.
- **Compensating control on the chat path (ORCH-P0-1):** `agent_loop._capability_in_scope()` enforces capability-scope at tool-execution time, so even ungated, a chat-routed agent can only call tools within its `capability_scope` (fail-closed for known capabilities). This is what keeps the ungated path safe.
- **Latent enhancement (not yet implemented):** if a chat turn's write step was triggered by *perception-sourced* content rather than the user's literal words, gating it would be defensible. Tracked, not built.
- **Step 6A (runtime swap):** The chat path can run on the Deep Agents runtime via `JARVIS_RUNTIME=deep` (default `legacy` = unchanged agent_loop). This is a per-streaming-call switch in `AgentInvoker.call_agent_stream` that reproduces the SSE contract; the interrupt gate, kill-Operator, and trust relocation land in 6B/6C. No gate fires on the deep path in 6A.

**Key files:** `src/services/risk_assessor.py`, `src/services/trust_engine.py`, `src/models/trust_state.py` (TrustState + TrustCeiling), `src/api/routes_trust.py`

## Execution State Machine

`detected → planned → policy_checked → approved → executing → completed/failed`

TaskRun statuses: `pending, running, paused, awaiting_approval, awaiting_input, completed, failed, cancelled, blocked, partially_completed, archived, timed_out`

TaskStep statuses: `pending, ready, running, completed, failed, skipped, waiting_approval, awaiting_input, blocked, timed_out`

State transitions are enforced by `src/services/execution_state.py` — never mutate status directly, use `transition_run()` / `transition_step()`. Retry: `failed → pending`.

**Single approval gate in GraphExecutor** (`graph_executor.py`): TrustEngine.evaluate() per step → approval_required pauses run, auto_execute_notify executes + notifies, auto_execute_silent executes silently. Governor hooks are now audit-only (`hooks.py` always returns `allowed: True` except for blocked tools).

**InteractionLog** (`src/models/interaction_log.py`): Lightweight audit record for simple interactions (replaces TaskRun for non-execution flows).

**Eviction**: `EvictionService` (`src/services/eviction_service.py`) — 90-day retention with cascade cleanup (vector store + graph engine).

## Runtime Resilience (agent_loop.py)

- **Tool timeout**: 60s via `asyncio.wait_for`. Timed-out tools return `{"error": "...", "timed_out": true}`.
- **API retry**: 3 attempts with exponential backoff (2s→4s→8s, capped 30s) for `anthropic.RateLimitError` only.
- **API circuit breaker**: `AnthropicCircuitBreaker` (`src/orchestrator/api_circuit_breaker.py`) tracks failures per model. CLOSED/OPEN/HALF_OPEN states, 5-failure threshold, 120s cooldown. When OPEN, agent_loop yields LoopError without calling the API.
- **Thinking fallback**: If API rejects thinking blocks, automatically disables thinking mid-loop and retries.
- **Tool error signaling**: Error dicts (`{"error": "..."}`) are flagged with `is_error: true` in tool results so Claude knows the tool failed.
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

`_load_conversation_history` loads up to 20 messages (8000 chars) including `metadata_` column. Assistant messages are annotated with their decision type (e.g., `Assistant [create_task]: ...`), giving downstream agents execution lineage. When history overflows, older messages are summarized via Haiku (`_summarize_history`) and prepended as `[Earlier conversation summary]`. Most recent 5 messages are kept verbatim.

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
- Do not reference `JARVIS_DECISION_FRAMEWORK`, `JARVIS_SOUL`, `OBSERVER_PROMPT`, `RESEARCHER_PROMPT` — deleted. Use `JARVIS_SOUL_CORE` + `PLANNER_PROMPT_V2` / `PERCEIVER_PROMPT`
- Do not import `intent_to_decision`/`extract_decision` — renamed to `intent_to_plan`/`extract_plan` in `intent_classifier.py`

### Approval & Trust
- Do not use Governor as the primary approval gate — TrustEngine in GraphExecutor is the single approval gate. Governor hooks are audit-only
- Do not add a TrustEngine gate to the chat path (`process_message`/`process_message_stream`) — it is ungated **by design** (user's message = authorization); capability-scope enforcement in `agent_loop` is the compensating control. See "Two execution paths" above
- Do not make the RiskAssessor fail open — its failure default is `risk_level="high"` (forces approval). Do not "simplify" it back to `medium`
- Do not reference `ApprovalPolicyEngine`, `TrustScore` model, or `ApprovalPolicy` model — deleted. Use `TrustEngine` + `TrustState` + `TrustCeiling`
- Do not create tool-level approvals without `run_id` and `artifact_refs` — the approval resume path needs these

### Execution & State
- Do not mutate TaskRun/TaskStep status directly — use `transition_run()` / `transition_step()`
- Do not bypass agent loop for step execution — GraphExecutor delegates to agent_loop per step
- Do not use bare `asyncio.create_task()` in jarvis.py — use `self._spawn_background()` for lifecycle tracking

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
