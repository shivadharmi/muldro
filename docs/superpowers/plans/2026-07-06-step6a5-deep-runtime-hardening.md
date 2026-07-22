# Step 6A.5 — Deep Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Deep Agents chat runtime (behind `JARVIS_RUNTIME=deep`) actually **functional, cost-safe, and resume-ready** by resolving the three Step-6A carry-forwards: (1) execute Jarvis tools through a **central `wrap_tool_call` dispatcher** (tools are schema-only shells; one middleware routes every Jarvis tool call through `ToolExecutor.execute_tool` and normalizes errors — mirroring the legacy `agent_loop`'s "tools are schemas, execution is central" model), (2) restore soul/role prompt caching, and (3) swap the inert per-call `MemorySaver` for a durable `AsyncPostgresSaver` wired once at app lifespan. It also removes the now-unneeded external Filesystem MCP (which collided with deepagents' built-in file tools).

**Architecture:** A single central **`jarvis_tool_dispatcher`** (`wrap_tool_call`) middleware intercepts every tool call: it falls through to the real handler for deepagents' own built-in tools (todos/filesystem/subagent — they must run their own bodies) and, for a Jarvis tool, dispatches through the one `execute_tool` chokepoint (all cost/turn-scope/events/composite preserved) and returns a synthesized `ToolMessage(status=…)` — so the Jarvis tool objects are inert **schema shells** the model sees but never executes. Capability-scope enforcement stays a **separate outer middleware** (security is not merged into dispatch), fixed to exempt the built-ins. A structured `SystemMessage` preserves the soul/role cache breakpoint, and a durable `AsyncPostgresSaver` (built at lifespan) replaces the per-call saver. This is the runtime FOUNDATION beneath 6B (the interrupt gate); 6A.5 leaves the checkpointer durable-but-inert.

**Tech Stack:** Python 3.12; deepagents 0.6.11 / langgraph 1.2.6 / langchain 1.3.10 / langchain-core 1.4.8 / langchain-anthropic 1.4.6 / langgraph-checkpoint-postgres 3.1.0 (psycopg + psycopg-pool already installed); async SQLAlchemy over asyncpg; pytest via the repo's custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio).

---

## Infra note (verify at start)

Run all commands from `backend/` via `uv run`:

```bash
docker compose up -d postgres redis qdrant     # from repo root
cd backend
uv sync --all-extras                            # NO pip; plain `uv sync` drops dev extras
uv run alembic upgrade head                     # head c7d3e4f5a6b8 (6A.5 adds NO migrations)
uv run pytest tests/ --ignore=tests/e2e         # baseline: 3123 passed / 18 skipped (after Step 6A)
```

- **NO pip.** No new dependencies (all libraries above are installed — confirm `uv pip show langgraph-checkpoint-postgres psycopg psycopg-pool langchain-core`).
- Do NOT edit `backend/` files while a `uvicorn --reload` worker runs.
- **No migrations.** `AsyncPostgresSaver.setup()` creates its own 4 checkpoint tables at runtime; `alembic/env.py`'s `_include_object` filter already excludes exactly those 4, so `alembic check` stays drift-free. Task 0 (Filesystem MCP removal) touches only seed/catalog/scope data (re-seeded on restart), not schema — still no migration.
- **API key:** all pytest tests use fake models / a local Postgres — no API key. Only the OPTIONAL live smoke (Task 10) needs `JARVIS_ANTHROPIC_API_KEY`.
- **Default stays `legacy`.** Every deep-path change is gated on `runtime=="deep"` or inert on the legacy path. `JARVIS_RUNTIME=legacy` must remain byte-behavior-identical. (Task 0 changes the tool catalog for BOTH paths — it removes a connector, which is a deliberate product change, not a runtime behavior change.)

---

## Current-state (verified 2026-07-06 against HEAD 4105d55 + installed source + two runnable research proofs)

1. **The deep seam** (`agent_invoker.py:171-192`): after `system_blocks = self.build_system_prompt(...)`, `if self._settings.runtime == "deep":` builds `await build_deep_agent(agent, tools, workspace_id=…, db_factory=self._db_factory, system_prompt=flatten_system_blocks(system_blocks), checkpointer=MemorySaver())`, then streams via `stream_deep_agent_events(...)`. `tools` = `self._resolve_tools(...)` output = **Anthropic tool-schema dicts**.
2. **Dict tools are silently unwired.** langchain `create_agent` (under `create_deep_agent`) partitions `tools` by Python type (`langchain/agents/factory.py:1029-1054`): `dict`s → "provider built-in tools" (no executor), only `BaseTool`/`Callable` reach the single `ToolNode`. Jarvis's dicts never execute.
3. **`wrap_tool_call` can BE the executor (research-proven, live).** `create_agent` collects all middleware `awrap_tool_call` hooks and chains them onto ONE `ToolNode` (`factory.py:1007-1045`). `ToolCallRequest` (`langgraph/prebuilt/tool_node.py:133`) carries `tool_call` (dict with `name`, `args`, `id`), `tool` (may be `None`), `state`, `runtime`. The real tool body runs ONLY if the middleware calls `handler(request)`; a middleware that RETURNS a `ToolMessage` without calling `handler` never runs the tool. Proof: `backend/spikes/deep_stream/central_dispatcher_proof.py` (offline) builds a real `create_deep_agent` with a schema-shell tool whose body raises + one dispatcher middleware, and confirms the shell body never runs, `execute_tool` is dispatched with the recovered name+args, the synthesized `ToolMessage` reaches the model, and `write_todos` (built-in) is NOT hijacked (fall-through works). There is NO `before_tool`/`after_tool` hook and NO injectable custom `ToolNode` — `wrap_tool_call` is the one idiomatic interception point (LangChain docs bless it for runtime-registered tools).
4. **`ToolExecutor.execute_tool(tool_name, tool_input, user_id, workspace_id="") -> dict`** (`tool_executor.py:306`) is the one dispatch chokepoint (registry lookup, `enabled`, backend match, `tool.started/completed/failed` events, per-tool cost, composite tools). Returns dicts: success payload, `{"error": ...}`, or `{"error": ..., "blocked": True}`. **Never raises** for a recoverable failure. It does NOT enforce capability-scope (that's `get_tools_for_agent` filtering + the `capability_scope` middleware).
5. **`get_tools_for_agent(agent, workspace_id) -> list[dict]`** (`tool_executor.py:131`) does the capability-scope filtering + lazy discovery and returns `{"name","description","input_schema"}` dicts. The shells are built from this (via `_resolve_tools` which also cache-tags — the shell builder ignores the `cache_control` key).
6. **deepagents auto-installs built-in tools that MUST run their own bodies.** A compiled `create_deep_agent` has `tools_by_name` including **`write_todos, ls, read_file, write_file, edit_file, glob, grep, execute, task`** (contributed by `TodoListMiddleware`/`FilesystemMiddleware`/`SubAgentMiddleware`, which are in deepagents' `_REQUIRED_MIDDLEWARE` and cannot be dropped). A blanket dispatcher would hijack them and `capability_scope` (below) would deny them — both must exempt this reserved set.
7. **`capability_scope` currently DENIES every built-in (latent 6A bug).** `deep_runtime/middleware/capability_scope.py` `_is_in_scope` returns `False` when `ToolRegistry.get_tool()` is `None`; every deepagents built-in is unknown to the Jarvis registry → denied. Confirmed in source. Must be fixed to exempt the reserved built-in set.
8. **`read_file`/`write_file`/`edit_file` NAME COLLISION.** Jarvis's external Filesystem MCP is seeded with those exact names (`catalog.py:493,496,497`, verified) — colliding with deepagents' built-ins. **Decision: remove the external Filesystem MCP entirely** (not needed) → the collision disappears at the source (Task 0). Footprint: `catalog.py:491-505` (14 seeds), `seed_installations.py:23,140-144` (npx `@modelcontextprotocol/server-filesystem` install), `capabilities.py:36,171-176` (`FILESYSTEM` family + 6 caps), `agents.py:60-62` (perceiver `filesystem.read/list/search`), `settings.py:174-176` (`filesystem_mcp_root`), comments in `session_pool.py:853`/`provider_map.py:84`.
9. **`StructuredTool` (langchain-core 1.4.8)** accepts a **raw JSON-Schema `dict` as `args_schema`** (verified) — no pydantic. The model-facing schema is derived from `name` + `args_schema`; with a `wrap_tool_call` present the factory SKIPS arg validation, so a shell whose `coroutine` raises is fine (it's never called). Import: `from langchain_core.tools import StructuredTool`.
10. **Middleware order** (`create_deep_agent`, `deepagents/graph.py:751-814`): base stack (Todo/Filesystem/SubAgent/Summarization/PatchToolCalls) → **your `middleware=`/`extra_middleware`** → tail (profile, `AnthropicPromptCachingMiddleware`, Memory, HITL). Within the tool-wrap chain, **first-in-list = outermost** (`factory.py:605`). `build_deep_agent` appends the `capability_scope` guard first (when `db_factory`), then `extra_middleware` → so `capability_scope` (outer) wraps the `jarvis_tool_dispatcher` (inner). deepagents' own `FilesystemMiddleware.awrap_tool_call` is a large-result post-processor sitting OUTER of both (benign).
11. **6B interrupt composes cleanly (research-verified, live).** `HumanInTheLoopMiddleware` gates via `after_model` + `interrupt()` — BEFORE the tool node — so a short-circuiting `wrap_tool_call` dispatcher cannot pre-empt it. Verified end-to-end with a real interrupt+`Command(resume=…)`: at pause the dispatcher never ran; on resume it fired and short-circuited, shell body never ran. **6B rule:** gate writes via deepagents `interrupt_on=`/`HumanInTheLoopMiddleware` (after_model), NOT a second short-circuiting `wrap_tool_call`. Do NOT wrap `execute_tool` in a blanket `try/except Exception` (it would swallow `GraphInterrupt`). — This is a 6B concern; noted so 6A.5 doesn't foreclose it.
12. **Caching regression** (unchanged from the 6A carry-forward): `flatten_system_blocks` merges the volatile per-turn context into the same prefix as stable soul+role, so deepagents' auto-injected `AnthropicPromptCachingMiddleware` (one end-of-system breakpoint) caches the volatile context too → turn-2 cache miss. Fix: a structured `SystemMessage` preserving the two-block layout (langchain-anthropic 1.4.6 honors block-level `cache_control`; `create_deep_agent` accepts `str | SystemMessage`).
13. **Durable checkpointer**: `AsyncPostgresSaver` (langgraph-checkpoint-postgres 3.1.0) over a dedicated small psycopg3 `AsyncConnectionPool`, built once at lifespan (`from_conn_string` closes its conn on exit — wrong for a server). psycopg3 DSN = strip `+asyncpg`. Green spike: `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md`. `AgentInvoker` (built in `jarvis.py:125`) already takes a `db_factory_provider` callable — a `checkpointer_provider` follows the same DI pattern. Lifespan = `api/app.py:53`.
14. **Parallel tool batches** (research note): `ToolNode` runs a batch via `asyncio.gather` — the dispatcher fires once PER call, concurrently. So N `execute_tool` calls run concurrently within a turn, sharing one turn-scoped MCP session. The plan does NOT change this vs the per-tool approach, but Task 8 must not assume serial execution; and concurrency-safety of `TurnScope`/`ToolExecutor` under a parallel batch is a pre-existing property (flag, don't fix, in 6A.5).

---

## Design decisions

- **D0 — Remove the external Filesystem MCP.** Not needed; its `read_file`/`write_file`/`edit_file` seeds collide with deepagents' built-ins. Removing it (catalog seeds + install config + capabilities + agent scope + setting) eliminates the collision at the source and shrinks the connector surface. (Task 0.)
- **D1 — Central `jarvis_tool_dispatcher` (one `wrap_tool_call`), tools are schema shells.** One middleware routes every Jarvis tool call through `execute_tool` and normalizes `{"error"|"blocked"}` → `ToolMessage(status="error")`. Jarvis tool objects are inert `StructuredTool` shells whose body RAISES (a tripwire — the dispatcher must intercept before the body runs). This mirrors `agent_loop`'s manual dispatch and folds the separate "normalizer" middleware away.
- **D2 — Built-in fall-through via a reserved name set.** The dispatcher AND `capability_scope` exempt `DEEPAGENTS_BUILTIN_NAMES` (fall through to the real handler) so deepagents' own tools run their bodies. The set is version-pinned + guarded by a drift test that compiles a real agent and asserts the set matches its built-in tool names — a deepagents upgrade that changes the built-ins fails loudly.
- **D3 — Capability-scope stays a SEPARATE outer middleware.** Security is not merged into the dispatcher (defense-in-depth; the scope guard is already proven). It's fixed only to exempt the built-ins. Order: `capability_scope` (outer) → `jarvis_tool_dispatcher` (inner).
- **D4 — The dispatcher is built in the SEAM, passed via `extra_middleware`.** It needs `execute_tool` + the turn's `user_id`/`workspace_id`, which `build_deep_agent` doesn't have but the seam does. So `build_deep_agent` barely changes (just widen `system_prompt` to accept a `SystemMessage`); the seam constructs the dispatcher and shells.
- **D5 — Structured `SystemMessage` restores the cache breakpoint** (unchanged from the 6A carry-forward). `prompt_bridge.build_system_message()` preserves the two-block layout; deepagents' auto-injected caching middleware + langchain-anthropic block-level `cache_control` do the rest. Do NOT add `AnthropicPromptCachingMiddleware` manually.
- **D6 — `AsyncPostgresSaver` over a dedicated lifespan pool** (unchanged). Not Redis (Jarvis's `redis:7-alpine` lacks the modules the langgraph Redis saver needs). Injected via a `checkpointer_provider` callable; durable-but-inert in 6A.5.
- **D7 — 6B is out of scope.** The `interrupt()` gate, persisted stable `thread_id`, `routes_approvals` resume, and SSE-on-resume contract are all 6B-owned. 6A.5 only makes tools execute, caching engage, and the checkpointer durable — and must not foreclose the 6B interrupt composition (D11 current-state).

---

## In-flight posture

- Branch `rebuild/first-principles`, HEAD `4105d55` (plan committed `b9447ed`). Do NOT push/merge to main.
- Per-task commit (conventional-commit, no `Co-Authored-By`).
- The central-dispatcher design + 6B-interrupt composition + built-in fall-through were empirically verified (installed-source + two live proofs incl. `central_dispatcher_proof.py`).
- Full gate after each task: `uv run pytest tests/ --ignore=tests/e2e`. Task 0 will shift the baseline (removes filesystem-tool tests + tools) — record the new baseline after Task 0.
- Commit the plan doc BEFORE dispatching implementers (done); don't let a reviewer's `git stash --include-untracked` orphan untracked files.

---

## File structure

| File | Change | Task |
|---|---|---|
| `backend/src/tools/catalog.py`, `seed_installations.py`, `integrations/capabilities.py`, `orchestrator/agents.py`, `config/settings.py` (+ comment refs) | **Modify** — remove the external Filesystem MCP (seeds, install, caps, scope, setting) | 0 |
| `backend/tests/…` (registry/catalog/validation/agent-scope tests referencing filesystem) | **Modify** — drop filesystem assertions; add "no filesystem tools" guard | 0 |
| `backend/src/deep_runtime/builtins.py` | **Create** — `DEEPAGENTS_BUILTIN_NAMES` reserved set | 1 |
| `backend/src/deep_runtime/middleware/capability_scope.py` | **Modify** — exempt `DEEPAGENTS_BUILTIN_NAMES` (fall through) | 1 |
| `backend/tests/deep_runtime/test_builtins.py` + `test_capability_scope.py` | **Create/append** — built-ins exempt; drift-guard vs a compiled agent | 1 |
| `backend/src/deep_runtime/middleware/jarvis_tool_dispatcher.py` | **Create** — central `wrap_tool_call` dispatcher (fall-through + execute_tool + normalize) | 2 |
| `backend/tests/deep_runtime/test_jarvis_tool_dispatcher.py` | **Create** — dispatches Jarvis tool, falls through built-in, flips error→status=error | 2 |
| `backend/src/deep_runtime/tool_bridge.py` | **Create** — `build_tool_shells()` (StructuredTool shells w/ raising body) | 3 |
| `backend/tests/deep_runtime/test_tool_bridge.py` | **Create** — shells advertise name+schema; body raises if executed | 3 |
| `backend/src/deep_runtime/prompt_bridge.py` + `agent_builder.py` | **Modify** — `build_system_message()`; widen `build_deep_agent` `system_prompt` to `str \| SystemMessage \| None` | 4 |
| `backend/tests/deep_runtime/test_prompt_bridge.py` + `test_agent_builder.py` | **Append** — 2-block SystemMessage; SystemMessage prompt accepted | 4 |
| `backend/src/deep_runtime/checkpointer.py` | **Create** — `build_async_postgres_saver()` | 5 |
| `backend/tests/deep_runtime/test_checkpointer.py` | **Create** — real-DB round-trip | 5 |
| `backend/src/orchestrator/agent_invoker.py` | **Modify** — deep branch: shells + dispatcher (extra_middleware) + SystemMessage + checkpointer_provider; add the param | 6 |
| `backend/tests/test_agent_invoker_deep_hardening.py` | **Create** — deep branch wires shells/dispatcher/SystemMessage/provider; legacy unchanged | 6 |
| `backend/src/orchestrator/jarvis.py` + `backend/src/api/app.py` | **Modify** — lifespan builds the saver (gated); orchestrator threads the provider | 7 |
| `backend/tests/test_deep_checkpointer_wiring.py` | **Create** — provider returns the saver when set, None otherwise | 7 |
| `backend/tests/test_deep_runtime_tool_execution.py` | **Create** — end-to-end: Jarvis tool executes via dispatcher, built-in falls through, blocked maps | 8 |
| `backend/spikes/deep_stream/live_hardening_smoke.py` | **Create (OPTIONAL, live)** — 2-turn cache_read>0 + real tool call | 9 |
| `CLAUDE.md` | Doc note: deep runtime tool-executes (central dispatcher) + caches + durable checkpointer; Filesystem MCP removed | 10 |

---

## Task 0: remove the external Filesystem MCP

**Files:** `backend/src/tools/catalog.py`, `backend/src/integrations/seed_installations.py`, `backend/src/integrations/capabilities.py`, `backend/src/orchestrator/agents.py`, `backend/src/config/settings.py` (+ comment refs in `session_pool.py`/`provider_map.py`); tests referencing filesystem.

- [ ] **Step 1: Write the failing/guard test** — add a test (e.g. `backend/tests/test_filesystem_mcp_removed.py`) asserting the Filesystem MCP is gone:

```python
"""Step 6A.5 Task 0: the external Filesystem MCP is removed — no filesystem tool seeds,
no filesystem.* capabilities, no filesystem in any agent scope, no filesystem install."""

def test_no_filesystem_tool_seeds():
    from src.tools.catalog import EXTERNAL_TOOL_SEEDS
    names = {s.name for s in EXTERNAL_TOOL_SEEDS}
    for n in ("read_file", "write_file", "edit_file", "list_directory", "directory_tree",
              "search_files", "move_file", "create_directory", "read_text_file"):
        assert n not in names, f"{n} still seeded"
    assert not any((getattr(s, "server", "") == "filesystem") for s in EXTERNAL_TOOL_SEEDS)


def test_no_filesystem_capabilities():
    from src.integrations.capabilities import CAPABILITY_CATALOG
    assert not any(c.startswith("filesystem.") for c in CAPABILITY_CATALOG)


def test_no_filesystem_in_agent_scope():
    from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES
    for scope in AGENT_CAPABILITY_SCOPES.values():
        assert not any(c.startswith("filesystem.") for c in scope)
```

(Read `catalog.py` for `_ext`'s `server` field name + the exact `EXTERNAL_TOOL_SEEDS`/`CAPABILITY_CATALOG`/`AGENT_CAPABILITY_SCOPES` symbol names before finalizing; adjust the assertions to the real shapes.)

- [ ] **Step 2: Run it → FAIL** (filesystem still present).

- [ ] **Step 3: Remove.** Delete: the 14 `_ext(...)` filesystem seeds (`catalog.py:491-505`); the filesystem install block (`seed_installations.py:140-144`) + `_filesystem_mcp_root` (`:23`) if now unused; the `FILESYSTEM` family + 6 `filesystem.*` entries (`capabilities.py:36,171-176`); the 3 `filesystem.*` lines in perceiver's scope (`agents.py:60-62`); `filesystem_mcp_root` (`settings.py:174-176`); update the no-auth-server comments/maps in `session_pool.py:853` / `provider_map.py:84` (drop "filesystem" from the examples/mapping — verify `filesystem` isn't a load-bearing key there, only an example). Grep `grep -rn "filesystem" src/ | grep -v test` to confirm no live reference remains.

- [ ] **Step 4: Run it → PASS.** Then `uv run python -c "from src.tools.validation import validate_registry"`-style startup check (find the real `validate_registry` entrypoint) to confirm no orphaned capability references. Confirm the seed-sync functions still import (`ToolRegistry.seed_defaults`, `AgentRegistry.seed_defaults`).

- [ ] **Step 5: Full gate + ruff.** Fix/remove any test that asserted on filesystem tools/capabilities. **Record the new baseline count** (was 3123 passed; Task 0 removes filesystem tests + tools → a new lower number — note it for subsequent tasks).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(rebuild): remove the external Filesystem MCP (unused; collided with deepagents built-ins) (Step 6A.5)"
```

---

## Task 1: reserved built-in set + fix `capability_scope` to exempt built-ins

**Files:**
- Create: `backend/src/deep_runtime/builtins.py`
- Modify: `backend/src/deep_runtime/middleware/capability_scope.py`
- Test: `backend/tests/deep_runtime/test_builtins.py` (create) + `test_capability_scope.py` (append)

- [ ] **Step 1: Write the failing tests.**
  - `test_builtins.py` — a **drift-guard** that compiles a real deep agent and asserts the constant matches its built-in tool names:

```python
"""Step 6A.5: DEEPAGENTS_BUILTIN_NAMES must track the tools deepagents auto-installs, so a
deepagents upgrade that changes them fails loudly instead of silently mis-gating."""

async def test_builtins_match_a_compiled_agent():
    # build a minimal create_deep_agent with NO extra tools + a fake model, introspect
    # its compiled tools_by_name, and assert DEEPAGENTS_BUILTIN_NAMES == those names.
    # (read tests/deep_runtime/test_stream_adapter.py for the fake model + how to compile;
    #  the compiled graph exposes tools_by_name on its tool node — verify the exact access.)
    from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
    builtin = _compiled_builtin_tool_names()  # helper: names of the auto-installed tools
    assert DEEPAGENTS_BUILTIN_NAMES == builtin
```

  - `test_capability_scope.py` (append) — a built-in name is exempt (allowed) even with an empty/narrow scope; a genuinely out-of-scope Jarvis tool is still denied:

```python
async def test_capability_scope_exempts_builtins():
    # the guard falls through (allows) for a DEEPAGENTS_BUILTIN_NAMES tool without a
    # registry lookup, but still denies an unknown non-builtin (fail-closed).
    ...  # drive the guard middleware over a "write_todos" call and an out-of-scope call
```

- [ ] **Step 2: Run them → FAIL.**

- [ ] **Step 3: Implement.**
  - Create `backend/src/deep_runtime/builtins.py`:

```python
"""Reserved deepagents built-in tool names (Step 6A.5).

deepagents' create_deep_agent auto-installs a scaffolding toolset (todos, filesystem,
subagent) via required middleware Jarvis cannot drop. These are NOT Jarvis registry tools:
they must run their own bodies and must NOT be capability-gated or routed through
ToolExecutor.execute_tool. Both the capability_scope guard and the jarvis_tool_dispatcher
exempt these names (fall through to the real handler).

Version-pinned to deepagents 0.6.11; test_builtins_match_a_compiled_agent asserts this set
equals a freshly-compiled agent's built-in tool names, so a deepagents upgrade that changes
the built-ins fails loudly.
"""

DEEPAGENTS_BUILTIN_NAMES: frozenset[str] = frozenset(
    {"write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute", "task"}
)
```

  (Verify the EXACT set against a compiled agent in Step 1's test; adjust the literal to match — do NOT guess.)
  - In `capability_scope.py`, import the set and exempt it at the top of `capability_scope_guard` (before `_is_in_scope`):

```python
    from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES  # module-level import preferred

    @wrap_tool_call
    async def capability_scope_guard(request, handler):
        tool_name = request.tool_call["name"]
        # deepagents' own scaffolding tools are harness infrastructure, not Jarvis
        # capability-gated tools — let them run their real bodies.
        if tool_name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)
        if await _is_in_scope(tool_name, agent, workspace_id, db_factory):
            return await handler(request)
        # ... existing denial (ToolMessage status=error) unchanged ...
```

- [ ] **Step 4: Run them → PASS.** Then `uv run pytest tests/deep_runtime/test_capability_scope.py -v` (existing denial tests must still pass).

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/builtins.py backend/src/deep_runtime/middleware/capability_scope.py backend/tests/deep_runtime/test_builtins.py backend/tests/deep_runtime/test_capability_scope.py
git commit -m "feat(rebuild): capability_scope exempts deepagents built-ins via a drift-guarded reserved set (Step 6A.5)"
```

---

## Task 2: the central `jarvis_tool_dispatcher` middleware

**Files:**
- Create: `backend/src/deep_runtime/middleware/jarvis_tool_dispatcher.py`
- Test: `backend/tests/deep_runtime/test_jarvis_tool_dispatcher.py`

- [ ] **Step 1: Write the failing test** — drive the dispatcher through a real `ToolNode`/compiled agent (mirror `test_capability_scope.py` + `central_dispatcher_proof.py` in `spikes/deep_stream/`). Assert: a Jarvis tool call is dispatched to a fake `execute_tool` (shell body never runs), a `{"error"|"blocked"}` result becomes `ToolMessage(status="error")` with the right id/name, and a `write_todos` (built-in) call falls through to `handler`.

```python
"""Step 6A.5: jarvis_tool_dispatcher routes Jarvis tool calls through execute_tool
(short-circuiting the shell), normalizes error/blocked to status="error", and falls through
for deepagents built-ins."""

async def test_dispatches_jarvis_tool_and_normalizes_error():
    calls = []
    async def fake_execute_tool(name, args, uid, ws):
        calls.append((name, args)); return {"error": "nope", "blocked": True}
    # run one turn over [a Jarvis shell "search", the built-in "write_todos"] with the
    # dispatcher; assert the search ToolMessage.status == "error" (name/id correct),
    # execute_tool was called with search's args, and write_todos ran its real body.
    ...
```

- [ ] **Step 2: Run it → FAIL.**

- [ ] **Step 3: Implement** — create `backend/src/deep_runtime/middleware/jarvis_tool_dispatcher.py`:

```python
"""Central Jarvis tool-execution dispatcher for the deep runtime (Step 6A.5).

Mirrors the legacy agent_loop's "tools are schemas, execution is central" model on the deep
path. Jarvis tools are registered with create_deep_agent as inert schema shells
(tool_bridge.build_tool_shells); this ONE wrap_tool_call middleware intercepts every tool
call and, for a Jarvis tool, dispatches through ToolExecutor.execute_tool WITHOUT invoking
the shell body, then normalizes {"error"|"blocked"} results to ToolMessage(status="error")
so the frozen blocked<-status=="error" SSE mapping holds. It falls through to the real
handler for deepagents' own built-in tools (they must run their own bodies). Capability-scope
enforcement stays a SEPARATE outer middleware — security is not merged into dispatch.

Do NOT wrap execute_tool in a blanket try/except: a future 6B interrupt path must be able to
raise GraphInterrupt through this layer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES

logger = logging.getLogger(__name__)

ExecuteToolFn = Callable[[str, dict, str, str], Awaitable[dict]]


def make_jarvis_tool_dispatcher(
    *, execute_tool: ExecuteToolFn, user_id: str, workspace_id: str
) -> AgentMiddleware:
    """Build the central tool-execution dispatcher for one turn.

    ``user_id``/``workspace_id`` are captured in the closure — never LLM-supplied.
    """

    @wrap_tool_call
    async def jarvis_tool_dispatcher(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)  # deepagents' own tool — run its real body
        args = request.tool_call.get("args") or {}
        result = await execute_tool(name, args, user_id, workspace_id)
        blocked = isinstance(result, dict) and bool(result.get("error") or result.get("blocked"))
        content = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error" if blocked else "success",
        )

    return jarvis_tool_dispatcher
```

- [ ] **Step 4: Run it → PASS.** Then `uv run pytest tests/deep_runtime/ -v`.

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/middleware/jarvis_tool_dispatcher.py backend/tests/deep_runtime/test_jarvis_tool_dispatcher.py
git commit -m "feat(rebuild): central jarvis_tool_dispatcher routes deep-path tools through execute_tool (Step 6A.5)"
```

---

## Task 3: tool bridge → inert schema shells

**Files:**
- Create: `backend/src/deep_runtime/tool_bridge.py`
- Test: `backend/tests/deep_runtime/test_tool_bridge.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/deep_runtime/test_tool_bridge.py`:

```python
"""Step 6A.5: build_tool_shells produces StructuredTool schema shells (name + JSON args
schema) whose body RAISES — the jarvis_tool_dispatcher must intercept before it runs."""

import pytest
from langchain_core.tools import StructuredTool

from src.deep_runtime.tool_bridge import build_tool_shells


def test_shells_advertise_name_and_schema():
    defs = [
        {"name": "search", "description": "Search.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}},
        {"name": "list_x", "description": "List.", "input_schema": {"type": "object"},
         "cache_control": {"type": "ephemeral"}},  # extra key ignored
    ]
    shells = build_tool_shells(defs)
    assert all(isinstance(s, StructuredTool) for s in shells)
    assert [s.name for s in shells] == ["search", "list_x"]
    assert shells[0].args_schema == defs[0]["input_schema"]


async def test_shell_body_raises_if_executed():
    shells = build_tool_shells([{"name": "search", "description": "s", "input_schema": {"type": "object"}}])
    with pytest.raises(AssertionError):
        await shells[0].ainvoke({})  # the dispatcher normally prevents this
```

- [ ] **Step 2: Run it → FAIL** (module doesn't exist).

- [ ] **Step 3: Implement** — create `backend/src/deep_runtime/tool_bridge.py`:

```python
"""Build inert schema-shell LangChain tools for the deep runtime (Step 6A.5).

Jarvis tools execute centrally via the jarvis_tool_dispatcher middleware, not per-tool. But
create_deep_agent still needs BaseTool objects so the model SEES each tool's name + args
schema. This builds one StructuredTool per Jarvis tool def whose coroutine is a TRIPWIRE
that raises if ever executed — the dispatcher short-circuits every Jarvis tool call, so the
shell body must never run. Extra keys on the def (e.g. cache_control) are ignored; only
name/description/input_schema are used.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool


def build_tool_shells(tool_defs: list[dict]) -> list[StructuredTool]:
    """One inert StructuredTool shell per Jarvis tool def."""
    return [_shell(d) for d in tool_defs]


def _shell(d: dict) -> StructuredTool:
    name = d["name"]

    async def _tripwire(**_kwargs: Any) -> Any:
        raise AssertionError(
            f"deep-runtime tool shell '{name}' executed — the jarvis_tool_dispatcher "
            "middleware must intercept every Jarvis tool call before the shell body runs."
        )

    return StructuredTool(
        name=name,
        description=d.get("description") or name,
        args_schema=d.get("input_schema") or {"type": "object", "properties": {}},
        coroutine=_tripwire,
    )
```

- [ ] **Step 4: Run it → PASS.**

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/tool_bridge.py backend/tests/deep_runtime/test_tool_bridge.py
git commit -m "feat(rebuild): tool_bridge builds inert schema shells for the central dispatcher (Step 6A.5)"
```

---

## Task 4: `build_system_message` (caching) + widen `build_deep_agent` prompt type

**Files:**
- Modify: `backend/src/deep_runtime/prompt_bridge.py`, `backend/src/deep_runtime/agent_builder.py`
- Test: `backend/tests/deep_runtime/test_prompt_bridge.py` + `test_agent_builder.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `test_prompt_bridge.py`:

```python
def test_build_system_message_preserves_two_block_cache_layout():
    from langchain_core.messages import SystemMessage
    from src.deep_runtime.prompt_bridge import build_system_message

    blocks = [
        {"type": "text", "text": "SOUL+ROLE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "CONTEXT"},
    ]
    sm = build_system_message(blocks)
    assert isinstance(sm, SystemMessage)
    assert sm.content == blocks
    assert sm.content[0].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in sm.content[1]


def test_build_system_message_wraps_plain_string():
    from langchain_core.messages import SystemMessage
    from src.deep_runtime.prompt_bridge import build_system_message
    assert build_system_message("s").content == "s"
    assert isinstance(build_system_message("s"), SystemMessage)


def test_build_system_message_drops_empty_and_non_text():
    from src.deep_runtime.prompt_bridge import build_system_message
    blocks = [{"type": "text", "text": "A"}, {"type": "image"}, {"type": "text", "text": ""}]
    assert build_system_message(blocks).content == [{"type": "text", "text": "A"}]
```

  and append to `test_agent_builder.py`:

```python
async def test_build_deep_agent_accepts_system_message():
    from unittest.mock import patch
    from langchain_core.messages import SystemMessage
    from src.deep_runtime import agent_builder
    from src.orchestrator.agents import SubAgent

    sm = SystemMessage(content=[{"type": "text", "text": "SOUL", "cache_control": {"type": "ephemeral"}}])
    probe = SubAgent(name="probe", prompt="p", model_tier="sonnet", capability_scope=set(),
                     temperature=0.0, max_tokens=1024)
    with patch.object(agent_builder, "create_deep_agent") as mock_create:
        await agent_builder.build_deep_agent(probe, tools=[], workspace_id="ws",
                                             db_factory=None, system_prompt=sm)
    assert mock_create.call_args.kwargs["system_prompt"] is sm
```

- [ ] **Step 2: Run them → FAIL.**

- [ ] **Step 3: Implement.**
  - `prompt_bridge.py` (keep `flatten_system_blocks`; add import `from langchain_core.messages import SystemMessage`):

```python
def build_system_message(system_blocks: Any) -> SystemMessage:
    """Build a SystemMessage preserving the chat path's two-block cache layout.

    Legacy emits [{soul+role, cache_control: ephemeral}, {context}]. Flattening merged the
    volatile context into the same cache prefix as the stable soul+role, thrashing turn-2
    caching. A structured SystemMessage (cache_control on the soul+role block only) restores
    the breakpoint: langchain-anthropic honors block-level cache_control and create_deep_agent
    preserves the caller's blocks. A plain string is wrapped as-is.
    """
    if isinstance(system_blocks, str):
        return SystemMessage(content=system_blocks)
    blocks = [
        b for b in system_blocks
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return SystemMessage(content=blocks)
```

  - `agent_builder.py`: widen the signature `system_prompt: str | SystemMessage | None = None` (import `from langchain_core.messages import SystemMessage`), update the docstring one line. `system_prompt or agent.prompt` still works (a non-empty `SystemMessage` is truthy). No other change.

- [ ] **Step 4: Run them → PASS.** Then `uv run pytest tests/deep_runtime/test_agent_builder.py -v` (existing checkpointer/write-guard tests still pass).

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/prompt_bridge.py backend/src/deep_runtime/agent_builder.py backend/tests/deep_runtime/test_prompt_bridge.py backend/tests/deep_runtime/test_agent_builder.py
git commit -m "feat(rebuild): build_system_message preserves the cache breakpoint; build_deep_agent accepts SystemMessage (Step 6A.5)"
```

---

## Task 5: the durable checkpointer builder module

**Files:**
- Create: `backend/src/deep_runtime/checkpointer.py`
- Test: `backend/tests/deep_runtime/test_checkpointer.py`

- [ ] **Step 1: Write the failing test** — real-DB test using the repo's self-contained real-DB idiom (skip if Postgres unreachable). Read `backend/spikes/postgres_saver/probe.py` for the exact `AsyncPostgresSaver` put/get API; build the saver, round-trip one checkpoint, close the pool.

```python
"""Step 6A.5: build_async_postgres_saver returns a durable AsyncPostgresSaver over a
dedicated psycopg3 pool; setup() is idempotent and a checkpoint round-trips."""

async def test_saver_round_trips_a_checkpoint():
    if not _db_reachable():
        return
    saver, pool = await build_async_postgres_saver(_TEST_DSN)  # +asyncpg DSN accepted (stripped inside)
    try:
        cfg = {"configurable": {"thread_id": "t-6a5", "checkpoint_ns": ""}}
        assert await saver.aget_tuple(cfg) is None
        # ... aput a minimal checkpoint (per the probe) then assert aget_tuple(cfg) is not None ...
    finally:
        await pool.close()
```

- [ ] **Step 2: Run it → FAIL.**

- [ ] **Step 3: Implement** — create `backend/src/deep_runtime/checkpointer.py`:

```python
"""Durable langgraph checkpointer for the deep runtime (Step 6A.5).

Builds an AsyncPostgresSaver over a dedicated small psycopg3 connection pool, constructed
once at app lifespan. Replaces the per-call MemorySaver so a future 6B interrupt() can pause
a chat turn and resume after a separate approval round-trip. In 6A.5 no interrupt fires, so
the saver is durable-but-inert. The psycopg3 pool is separate from the app's asyncpg pool
(different drivers, same Postgres); it is small because AsyncPostgresSaver serializes DB ops
behind one asyncio.Lock.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


def to_psycopg_dsn(database_url: str) -> str:
    """SQLAlchemy async DSN → plain psycopg3 DSN (strip the +asyncpg driver)."""
    return database_url.replace("+asyncpg", "", 1)


async def build_async_postgres_saver(
    database_url: str,
) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Open a small psycopg3 pool + an AsyncPostgresSaver over it; run setup() once.

    Returns ``(saver, pool)``. The caller MUST ``await pool.close()`` on shutdown. Construct
    inside the running event loop (AsyncPostgresSaver builds an asyncio.Lock at init).
    """
    pool = AsyncConnectionPool(
        to_psycopg_dsn(database_url),
        min_size=1,
        max_size=4,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    logger.info("[deep_runtime] durable AsyncPostgresSaver initialized")
    return saver, pool
```

- [ ] **Step 4: Run it → PASS** (Postgres up). Confirm `uv run alembic check` still drift-free after `setup()` ran.

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/checkpointer.py backend/tests/deep_runtime/test_checkpointer.py
git commit -m "feat(rebuild): durable AsyncPostgresSaver builder for the deep runtime (Step 6A.5)"
```

---

## Task 6 (BLAST-RADIUS): rewire the deep seam — shells + dispatcher + SystemMessage + checkpointer provider

**Files:**
- Modify: `backend/src/orchestrator/agent_invoker.py` (the `runtime=="deep"` branch + `__init__`)
- Test: `backend/tests/test_agent_invoker_deep_hardening.py` (create)

> The one blast-radius task (live chat entry). Default `legacy` path must stay byte-behavior-identical. **2-stage PARALLEL review** (spec + quality) on the frozen commit.

- [ ] **Step 1: Write the failing test** — build an `AgentInvoker` (reuse `_make_invoker` from `tests/test_agent_invoker_runtime_branch.py`; add `checkpointer_provider` injection). Assert under `runtime="deep"`: (a) tools become shells via `build_tool_shells` (patch it; assert called with the resolved tool list), (b) a `jarvis_tool_dispatcher` is passed to `build_deep_agent` via `extra_middleware` (patch `make_jarvis_tool_dispatcher` → sentinel; assert it's in the `extra_middleware` kwarg), (c) `system_prompt` is a `SystemMessage`, (d) the checkpointer comes from `self._checkpointer_provider()`. And `runtime="legacy"` still routes to `agent_loop`.

```python
async def test_deep_branch_uses_shells_dispatcher_systemmessage_and_provider():
    from unittest.mock import AsyncMock, patch
    from langchain_core.messages import SystemMessage

    sentinel_saver, sentinel_mw = object(), object()
    inv = _make_invoker(runtime="deep", checkpointer_provider=lambda: sentinel_saver)

    async def _fake_adapter(*a, **k):
        yield {"event": "agent_done", "agent": "perceiver", "text": "ok", "input_tokens": 1,
               "output_tokens": 1, "cache_creation_tokens": 0, "cache_read_tokens": 0,
               "tools_called": [], "latency_ms": 1, "cost_usd": 0.0}

    with patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()) as mock_build, \
         patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]) as mock_shells, \
         patch("src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher", return_value=sentinel_mw), \
         patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter):
        frames = [f async for f in inv.call_agent_stream("perceiver", message="hi",
                    user_id="u", workspace_id="ws", tools_override=[])]

    assert any(f["event"] == "agent_done" for f in frames)
    mock_shells.assert_called_once()
    kw = mock_build.call_args.kwargs
    assert kw["tools"] == ["SHELL"] or mock_build.call_args.args[1] == ["SHELL"]
    assert sentinel_mw in tuple(kw["extra_middleware"])
    assert isinstance(kw["system_prompt"], SystemMessage)
    assert kw["checkpointer"] is sentinel_saver
```

(Adjust arg-vs-kwarg access to how the seam calls `build_deep_agent`.)

- [ ] **Step 2: Run it → FAIL.**

- [ ] **Step 3: Implement.**
  1. Top-of-file imports: `from src.deep_runtime.tool_bridge import build_tool_shells`, `from src.deep_runtime.prompt_bridge import build_system_message`, `from src.deep_runtime.middleware.jarvis_tool_dispatcher import make_jarvis_tool_dispatcher`.
  2. `__init__`: add `checkpointer_provider=None` (last param); `self._checkpointer_provider = checkpointer_provider or (lambda: None)`. Docstring one line.
  3. Replace the deep branch body with:

```python
        if self._settings.runtime == "deep":
            # Step 6A.5: run the routed chat agent on the Deep Agents runtime. Jarvis tools
            # are inert schema shells; the jarvis_tool_dispatcher middleware centrally routes
            # each call through execute_tool (capability_scope stays a separate outer guard).
            # A structured SystemMessage keeps the soul/role cache breakpoint; the durable
            # checkpointer falls back to an in-process saver until wired at lifespan.
            from langgraph.checkpoint.memory import MemorySaver

            shells = build_tool_shells(tools)
            dispatcher = make_jarvis_tool_dispatcher(
                execute_tool=self._tool_executor.execute_tool,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            deep_agent = await build_deep_agent(
                agent,
                shells,
                workspace_id=workspace_id,
                db_factory=self._db_factory,
                extra_middleware=(dispatcher,),
                system_prompt=build_system_message(system_blocks),
                checkpointer=self._checkpointer_provider() or MemorySaver(),
            )
            config = {"configurable": {"thread_id": generate_id("chat")}}
            graph_input = {"messages": [{"role": "user", "content": message}]}
            async for frame in stream_deep_agent_events(
                deep_agent, graph_input, config, agent_name=agent_name, model=model
            ):
                yield frame
            return
```

  (Keep the legacy `agent_loop` block below byte-identical. `tools` is the resolved list; `build_tool_shells` ignores the `cache_control` key.)

- [ ] **Step 4: Run it → PASS.**

- [ ] **Step 5: Full gate + ruff.** Confirm NO existing chat/invoker test regressed under `legacy`: `uv run pytest tests/ -k "invoker or chat or agent_loop or runtime_branch" --ignore=tests/e2e -v`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/agent_invoker.py backend/tests/test_agent_invoker_deep_hardening.py
git commit -m "feat(rebuild): deep seam uses schema shells + central dispatcher + SystemMessage + durable checkpointer (Step 6A.5)"
```

---

## Task 7: wire the durable checkpointer at app lifespan + through the orchestrator

**Files:**
- Modify: `backend/src/api/app.py` (lifespan, gated) + `backend/src/orchestrator/jarvis.py` (thread the provider)
- Test: `backend/tests/test_deep_checkpointer_wiring.py` (create)

> Inert in 6A.5 (no interrupt fires). Makes the seam's provider return a durable saver instead of `None` (→ `MemorySaver`), only when `runtime=="deep"`.

- [ ] **Step 1: Trace the construction path FIRST.** Read `api/app.py` lifespan (`:53`), how `JarvisOrchestrator` is built and reaches `app.state` (grep `JarvisOrchestrator(` + how `routes_chat`/`app.state` obtain it), and `jarvis.py:125` (`AgentInvoker(...)`). Decide the minimal thread: lifespan builds `(saver, pool)` when `settings.runtime == "deep"`, stores on `app.state`, closes the pool on shutdown; the orchestrator gets a `checkpointer_provider` reading it.

- [ ] **Step 2: Write the failing test** — create `backend/tests/test_deep_checkpointer_wiring.py`:

```python
"""Step 6A.5: JarvisOrchestrator threads a checkpointer_provider to AgentInvoker; it returns
the injected saver, or None (→ MemorySaver at the seam) by default."""

def test_orchestrator_threads_checkpointer_provider():
    sentinel = object()
    orch = _make_orchestrator(checkpointer_provider=lambda: sentinel)  # mirror tests/test_jarvis*.py
    assert orch._invoker._checkpointer_provider() is sentinel


def test_default_provider_returns_none():
    assert _make_orchestrator()._invoker._checkpointer_provider() is None
```

- [ ] **Step 3: Run it → FAIL.**

- [ ] **Step 4: Implement.**
  1. `jarvis.py`: add `checkpointer_provider=None` to `JarvisOrchestrator.__init__`; pass into `AgentInvoker(..., checkpointer_provider=checkpointer_provider)` at `:125`.
  2. `api/app.py` lifespan (after Redis init):

```python
        app.state.deep_checkpointer = None
        app.state.deep_checkpointer_pool = None
        if settings.runtime == "deep":
            from src.deep_runtime.checkpointer import build_async_postgres_saver

            saver, pool = await build_async_postgres_saver(settings.database_url)
            app.state.deep_checkpointer = saver
            app.state.deep_checkpointer_pool = pool
```

  and on shutdown: `if app.state.deep_checkpointer_pool: await app.state.deep_checkpointer_pool.close()`.
  3. Where the orchestrator is constructed with `app.state` in scope, pass `checkpointer_provider=lambda: getattr(app.state, "deep_checkpointer", None)` (follow the existing pattern; no globals).

- [ ] **Step 5: Run it → PASS.** Full gate + ruff. Sanity: with default `runtime="legacy"`, NO psycopg3 pool is opened — confirm the app still starts and legacy tests are unaffected.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/app.py backend/src/orchestrator/jarvis.py backend/tests/test_deep_checkpointer_wiring.py
git commit -m "feat(rebuild): wire durable deep-runtime checkpointer at lifespan (gated on runtime=deep) (Step 6A.5)"
```

---

## Task 8: end-to-end deep-path tool execution (fake model)

**Files:**
- Test: `backend/tests/test_deep_runtime_tool_execution.py` (create)

> The load-bearing guard: prove a Jarvis tool actually EXECUTES through a compiled `create_deep_agent` via the central dispatcher, a deepagents built-in still runs its own body, and the frozen `blocked` mapping holds — no real API (fake streaming model + `MemorySaver`).

- [ ] **Step 1: Write the test.** Build a deep agent via `build_deep_agent` (so `capability_scope` [fixed] is installed) with `extra_middleware=(dispatcher,)`, schema shells for two Jarvis tools + a `capability_scope` that permits them, and a fake streaming model scripted to call: (a) a Jarvis tool whose `execute_tool` stub returns `{"ok":1}`, (b) a Jarvis tool whose stub returns `{"error":"nope","blocked":True}`, (c) the built-in `write_todos`. Stream via `stream_deep_agent_events` and assert: the OK tool's `tool_result` frame has `blocked=False`; the error tool's has `blocked=True`; the shells' bodies never ran (no `AssertionError`); `write_todos` ran its real body (todos in state); `tool_call` count == `tool_result` count.

```python
"""Step 6A.5: bridged Jarvis tools execute via the central dispatcher through a compiled deep
agent; recoverable errors surface as tool_result.blocked=True; deepagents built-ins still run."""

async def test_jarvis_tools_execute_via_dispatcher_and_builtin_falls_through():
    frames = [f async for f in _run_deep_turn()]  # helper builds+streams (fake model, 3 tool calls)
    results = {f["tool"]: f for f in frames if f["event"] == "tool_result"}
    assert results["ok_tool"]["blocked"] is False
    assert results["err_tool"]["blocked"] is True
    kinds = [f["event"] for f in frames]
    assert kinds.count("tool_call") == kinds.count("tool_result") >= 2
```

(Mirror `central_dispatcher_proof.py` in `spikes/deep_stream/` for the fake-model + build wiring — it already demonstrates the shell/dispatcher/built-in interplay; adapt it into a pytest test over the streaming adapter, not just `ainvoke`. A failure is a real integration gap — fix the code, not the test.)

- [ ] **Step 2: Run it → PASS.**

- [ ] **Step 3: Full gate + ruff. Commit**

```bash
git add backend/tests/test_deep_runtime_tool_execution.py
git commit -m "test(rebuild): deep-path Jarvis tools execute via the central dispatcher end-to-end (Step 6A.5)"
```

---

## Task 9 (OPTIONAL, live): caching + real-tool smoke behind the flag

**Files:** `backend/spikes/deep_stream/live_hardening_smoke.py` (create; not a pytest test)

- [ ] Only if `JARVIS_ANTHROPIC_API_KEY` is set. Set `JARVIS_RUNTIME=deep`; send TWO real read messages on ONE `thread_id` through `call_agent_stream("perceiver", …)`; print `agent_done` telemetry for both turns. Observe: turn-1 `cache_creation_tokens > 0` and **turn-2 `cache_read_tokens > 0`** (the deferred caching proof — the structured `SystemMessage` engages caching; if `cache_read == 0`, ESCALATE, esp. for the Planner whose soul+role may sit below the Opus 4096 min in isolation), and a real Jarvis tool call executes through the dispatcher and returns a real result. Document output; do NOT gate CI on it. Commit under `spikes/`.

---

## Task 10: docs note

**Files:** `CLAUDE.md`

- [ ] **Step 1:** Update the "Two execution paths" / Step-6A note: `JARVIS_RUNTIME=deep` now (a) executes Jarvis tools via a **central `jarvis_tool_dispatcher` middleware** through `execute_tool` (tools are inert schema shells; `capability_scope` stays a separate guard, both exempt deepagents built-ins), (b) preserves soul/role **prompt caching** via a structured `SystemMessage`, (c) uses a **durable `AsyncPostgresSaver`** (inert until the 6B interrupt gate). Note the external **Filesystem MCP was removed**. Keep it factual, no volatile counts. Note the still-6B-owned items: the interrupt gate, a persisted stable `thread_id`, the `routes_approvals` resume, the SSE-on-resume contract.
- [ ] **Step 2:** Full gate + ruff. Commit `chore(rebuild): doc note — deep runtime central tool dispatch + caching + durable checkpointer; Filesystem MCP removed (Step 6A.5)`.

---

## Review strategy (for the executor)

- **Task 0 (Filesystem MCP removal)** — combined review; reviewer confirms NO live reference to filesystem tools/caps/scope remains, `validate_registry` passes, seed-sync imports, and the baseline shift is only the removed filesystem tests.
- **Tasks 1/2/3/4/5/8/10** — single combined review each (spec + quality).
- **Task 6 (deep seam)** — the blast-radius task → **2-stage PARALLEL review** (spec + quality) on the frozen commit; the quality reviewer confirms the default `legacy` path is byte-behavior-unchanged, no chat/invoker test regresses, and shells/dispatcher(via extra_middleware)/SystemMessage/provider are wired exactly (dispatcher inner, capability_scope outer).
- **Task 7 (lifespan wiring)** — combined review; confirm NO psycopg3 pool opens when `runtime="legacy"`, the pool closes on shutdown, `alembic check` drift-free, no global/singleton smell.
- **Final holistic review (opus):** full gate green, `alembic check` drift-free (no migrations), `JARVIS_RUNTIME=legacy` behavior-neutral, and — the load-bearing new guarantee — a Jarvis tool **actually executes** through the central dispatcher with the `blocked` mapping intact AND a deepagents built-in still runs its own body (Task 8 is a real guard: confirm it fails if the dispatcher is removed or a shell is allowed to execute). Confirm the three carry-forwards are closed, the Filesystem MCP is gone, and nothing 6B-owned (interrupt/gate/persisted thread_id/resume) leaked in.

---

## Self-review checklist (run before dispatching implementers)

1. **Spec coverage:** Filesystem removal = T0. Carry-forward #1 (central tool execution) = T1 (built-ins + capability_scope fix) + T2 (dispatcher) + T3 (shells) + T6 (seam) + T8 (e2e). #2 (caching) = T4 (SystemMessage) + T6 + T9 (live). #3 (durable checkpointer) = T5 (builder) + T6 (provider) + T7 (lifespan). Docs = T10. ✅
2. **Placeholder scan:** all new modules have complete verbatim code. The empirical unknowns — the exact `DEEPAGENTS_BUILTIN_NAMES` set (T1 drift test), `ToolNode`'s `ToolMessage.content` serialization (T2 test), `AsyncPostgresSaver` put/get names (T5 test), the orchestrator construction path (T7 Step 1) — are pinned by their tests/step against installed source, not left as TODOs. ✅
3. **Type/name consistency:** `build_tool_shells`, `make_jarvis_tool_dispatcher`, `DEEPAGENTS_BUILTIN_NAMES`, `build_system_message`, `build_async_postgres_saver`, `checkpointer_provider` used identically across tasks. The seam passes `shells` (positional `tools`) + `extra_middleware=(dispatcher,)` + `build_system_message(system_blocks)` to `build_deep_agent`, whose `system_prompt` type was widened in T4; `capability_scope` (T1) + dispatcher compose as outer/inner. ✅
4. **No migrations** → `alembic check` drift-free. Default `runtime="legacy"` → deep changes inert; T0 is a deliberate connector removal (re-seeded on restart), not a runtime behavior change. ✅
5. **Scope discipline:** the `interrupt()` gate, persisted `thread_id`, `routes_approvals` resume, and SSE-on-resume contract are OUT (6B); the dispatcher deliberately avoids a blanket `try/except` so 6B can raise `GraphInterrupt` through it. ✅
6. **Security:** capability-scope stays a SEPARATE outer middleware (not merged into dispatch); its built-in exemption is drift-guarded so a deepagents upgrade can't silently widen it. ✅
