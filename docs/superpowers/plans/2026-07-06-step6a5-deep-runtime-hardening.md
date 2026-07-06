# Step 6A.5 — Deep Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Deep Agents chat runtime (behind `JARVIS_RUNTIME=deep`) actually **functional, cost-safe, and resume-ready** by resolving the three Step-6A carry-forwards: (1) give it real, typed LangChain tools that execute through Jarvis's policy layer, (2) restore soul/role prompt caching, and (3) swap the inert per-call `MemorySaver` for a durable `AsyncPostgresSaver` wired once at app lifespan.

**Architecture:** Three independent hardening threads that meet at the one existing seam (`AgentInvoker.call_agent_stream`'s `runtime=="deep"` branch). (1) A **tool bridge** wraps each Jarvis registry tool as a `StructuredTool` whose async coroutine routes through `ToolExecutor.execute_tool` (all dispatch/cost/turn-scope preserved), plus a `wrap_tool_call` **normalizer** middleware that flips recoverable `{"error"|"blocked"}` results to `ToolMessage(status="error")`. (2) A **structured `SystemMessage`** replaces the flattened system string so the `cache_control` breakpoint between soul+role and volatile context is preserved. (3) A durable **`AsyncPostgresSaver`** over a dedicated small psycopg3 pool, built at lifespan and injected via a `checkpointer_provider` callable. This is the runtime FOUNDATION beneath 6B (the interrupt gate); 6A.5 leaves the checkpointer durable-but-inert.

**Tech Stack:** Python 3.12; deepagents 0.6.11 / langgraph 1.2.6 / langchain 1.3.10 / langchain-core 1.4.8 / langchain-anthropic 1.4.6 / langgraph-checkpoint-postgres 3.1.0 (psycopg 3.3.4 + psycopg-pool 3.3.1, already installed); async SQLAlchemy over asyncpg; pytest via the repo's custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio).

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

- **NO pip.** `uv run …` / `uv add …`. No new dependencies are required (all libraries above are already installed — confirm with `uv pip show langgraph-checkpoint-postgres psycopg psycopg-pool langchain-core`).
- Do NOT edit `backend/` files while a `uvicorn --reload` worker runs.
- **No migrations, no alembic changes.** `AsyncPostgresSaver.setup()` creates its own 4 checkpoint tables (`checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) at runtime; `alembic/env.py`'s `_include_object` filter already excludes exactly those 4 (verified), so `alembic check` stays drift-free even after `setup()` runs. A persisted `thread_id` column (6B) is explicitly OUT of scope.
- **API key:** all pytest tests use fake models / a real local Postgres — no API key. Only the OPTIONAL live smoke (Task 9) needs `JARVIS_ANTHROPIC_API_KEY`.
- **Default stays `legacy`.** Every change is gated behind `runtime=="deep"` or is inert on the legacy path. `JARVIS_RUNTIME=legacy` must remain byte-behavior-identical.

---

## Current-state (verified 2026-07-06 against HEAD 4105d55 + installed source)

1. **The deep seam** (`agent_invoker.py:171-192`): after `system_blocks = self.build_system_prompt(...)`, `if self._settings.runtime == "deep":` builds `await build_deep_agent(agent, tools, workspace_id=…, db_factory=self._db_factory, system_prompt=flatten_system_blocks(system_blocks), checkpointer=MemorySaver())`, then `config = {"configurable":{"thread_id": generate_id("chat")}}`, `graph_input = {"messages":[{"role":"user","content":message}]}`, then `async for frame in stream_deep_agent_events(deep_agent, graph_input, config, agent_name=agent_name, model=model): yield frame; return`. `tools` here is `self._resolve_tools(...)` output = **Anthropic tool-schema dicts** (cache_control-tagged).
2. **Tool dicts are silently unwired.** `create_deep_agent` → langchain `create_agent` **partitions tools by Python type** (`langchain/agents/factory.py:1029-1031`): `dict`s → "provider built-in tools" (no executor, skipped even for validation), only `BaseTool`/`Callable` reach the `ToolNode`. So Jarvis's dicts never execute. The fix must emit real `StructuredTool`s.
3. **`ToolExecutor.execute_tool(self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = "") -> dict`** (`tool_executor.py:306`) is the one dispatch chokepoint — enforces registry lookup, `enabled`, backend match, `tool.started/completed/failed` events, per-tool cost. Returns dicts: success payload, `{"error": ...}`, or `{"error": ..., "blocked": True}`. **Never raises** for a recoverable failure.
4. **`ToolExecutor.get_tools_for_agent(self, agent, workspace_id="") -> list[dict]`** (`tool_executor.py:131`) already does all the registry + capability-scope filtering and returns clean `{"name","description","input_schema"}` dicts (+ lazy schema discovery). The bridge is a pure transform over this output.
5. **`StructuredTool` (langchain-core 1.4.8)** accepts a **raw JSON-Schema `dict` as `args_schema`** (annotation `type[BaseModel] | type[pydantic.v1.BaseModel] | dict[str, Any]` — confirmed) — no pydantic conversion needed. A `coroutine`-only tool (no `func`) is valid for the async deep path. Import: `from langchain_core.tools import StructuredTool`.
6. **Error signaling caveat (research-verified):** a coroutine that returns an error dict is wrapped by `ToolNode` as `ToolMessage(status="success")` — the error signal is lost, and `stream_adapter.py:159` would compute `blocked=False`. Returning a `ToolMessage(status="error")` *from the coroutine* leaves a placeholder `tool_call_id`, and `InjectedToolCallId` does not work with a dict `args_schema`. The correct place to normalize is a **`wrap_tool_call` middleware** (it has `request.tool_call["id"]` + `["name"]`), returning `ToolMessage(status="error", tool_call_id=…, name=…)` — a returned `ToolMessage` continues the loop; only an unhandled raise aborts it (langgraph `_default_handle_tool_errors` re-raises non-`ToolInvocationError`).
7. **`wrap_tool_call` pattern** (`deep_runtime/middleware/capability_scope.py`): `from langchain.agents.middleware import AgentMiddleware, wrap_tool_call`; `@wrap_tool_call async def guard(request, handler): ... return ToolMessage(content=…, tool_call_id=request.tool_call["id"], status="error")`. The scope guard returns `status="error"` on denial and short-circuits (never calls `handler`).
8. **`build_deep_agent`** (`deep_runtime/agent_builder.py:55-120`, async) builds `middleware = []`, appends the scope guard when `db_factory is not None`, `middleware.extend(extra_middleware)`, runs the fail-closed write-agent guard, then `return create_deep_agent(model=build_chat_model(agent), tools=tools, system_prompt=system_prompt or agent.prompt, middleware=middleware, name=name or agent.name, checkpointer=checkpointer)`. Signature has `system_prompt: str | None = None`.
9. **deepagents auto-injects `AnthropicPromptCachingMiddleware`** unconditionally in the main stack **after** user middleware (`deepagents/graph.py:798`) — so the 6A spike's "add the caching middleware" fallback is already handled; the middleware puts ONE `cache_control` breakpoint at the end of the system message. The real regression is that `flatten_system_blocks` merges the volatile per-turn context into the same prefix as the stable soul+role, so that single end breakpoint caches the volatile context too → **turn-2 cache miss on soul+role**.
10. **`build_system_prompt`** (`agent_invoker.py:83-109`) emits `[{"type":"text","text": soul+"\n\n--- YOUR ROLE ---\n"+role, "cache_control":{"type":"ephemeral"}}]` and, when `context`, appends `{"type":"text","text": context}` (no cache_control). This is the two-block layout the deep path must preserve.
11. **`flatten_system_blocks`** (`deep_runtime/prompt_bridge.py`) joins the block `.text` with `"\n\n"` → a flat `str`, dropping the `cache_control` markers. **langchain-anthropic 1.4.6 honors `cache_control` on message content blocks** (`langchain_anthropic/chat_models.py:463-471`), and `create_deep_agent(system_prompt=…)` accepts `str | SystemMessage` and preserves the caller's blocks — so emitting a structured `SystemMessage` restores the breakpoint.
12. **`AgentInvoker.__init__`** takes `db_factory_provider` (a callable resolved live via the `_db_factory` property) — a `checkpointer_provider` callable follows the same DI pattern. `AgentInvoker` is constructed in `jarvis.py:125` (JarvisOrchestrator is the composition root). The FastAPI lifespan is `api/app.py:53` (`app.state.*`).
13. **`AsyncPostgresSaver` (langgraph-checkpoint-postgres 3.1.0)**: `from_conn_string` is an `@asynccontextmanager` that **closes its connection on exit** (correct for the spike, wrong for a server). For a long-lived saver, pass a psycopg3 `AsyncConnectionPool` to `AsyncPostgresSaver(pool)` and `await saver.setup()` once. The saver serializes DB ops behind one `asyncio.Lock`, so a small pool (`max_size=4`) suffices. Conn string is **psycopg3** `postgresql://…` (strip the app's `+asyncpg`). The green spike `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md` proved durable resume; build on it.

---

## Design decisions

- **D1 — Tool bridge wraps `execute_tool`, not `langchain-mcp-adapters`.** Per-tool `StructuredTool` over the one `execute_tool` chokepoint preserves capability-scope, per-tool cost attribution (`TokenUsage(trigger=f"tool:{name}")`), turn-scoped MCP, and composite tools (`web_search`). `langchain-mcp-adapters` would open a parallel MCP client that bypasses all of that + needs a new dep — rejected. The bridge is a pure transform over `get_tools_for_agent`'s output.
- **D2 — Error normalization is a middleware, not coroutine logic.** A `wrap_tool_call` normalizer flips `{"error"|"blocked"}` results to `ToolMessage(status="error", id, name)` so the frozen `blocked ← status=="error"` mapping holds and the turn isn't aborted. Installed AFTER the capability_scope guard (denials already `status="error"` pass through). This is mandatory, not optional — without it, `blocked`/error results silently read as success.
- **D3 — Structured `SystemMessage` restores the cache breakpoint.** `prompt_bridge` gains `build_system_message()` that preserves the two-block layout (soul+role block keeps `cache_control`; context block after). deepagents' auto-injected caching middleware + langchain-anthropic honoring block-level `cache_control` do the rest. `flatten_system_blocks` is retained (deprecated) for any string caller. Do NOT add `AnthropicPromptCachingMiddleware` manually — it's already there.
- **D4 — `AsyncPostgresSaver` over a dedicated lifespan pool.** Not Redis (Jarvis's `redis:7-alpine` lacks the RedisJSON/RediSearch modules the langgraph Redis saver requires). Constructed once at lifespan (gated on `runtime=="deep"`), a small psycopg3 pool separate from the app's asyncpg pool, injected via a `checkpointer_provider` callable. Durable-but-inert in 6A.5 (no interrupt fires).
- **D5 — The seam wraps the already-resolved tool list.** The bridge reads only `name`/`description`/`input_schema` from each dict and ignores extra keys (incl. `cache_control`), so the seam can wrap the existing `tools` (from `_resolve_tools`) with no re-fetch — the stray `cache_control` key is harmless because it's never passed to `StructuredTool`.
- **D6 — Checkpointer is inert; the gate is 6B.** 6A.5 only makes the saver durable and threads it. The `interrupt()` gate, the stable persisted `thread_id`, the `routes_approvals` resume (`ainvoke(Command(resume=…), config)`), and the SSE-on-resume contract (notify-only vs re-stream) are all **6B-owned** — do NOT build them here.

---

## In-flight posture

- Branch `rebuild/first-principles`, HEAD `4105d55`. Do NOT push/merge to main.
- Per-task commit (conventional-commit, no `Co-Authored-By`).
- No spike/decision-gate: the three approaches were empirically verified during research (installed-source inspection + live `ToolNode` runs). Tasks 1-7 build; Task 8 is the end-to-end guard; Task 9 is the optional live proof.
- Full gate after each task: `uv run pytest tests/ --ignore=tests/e2e`.
- Commit the plan doc BEFORE dispatching implementers (Step-2 orphan lesson); do NOT let a reviewer's `git stash --include-untracked` orphan untracked files.

---

## File structure

| File | Change | Task |
|---|---|---|
| `backend/src/deep_runtime/middleware/tool_result_normalizer.py` | **Create** — `wrap_tool_call` error/blocked → `ToolMessage(status="error")` | 1 |
| `backend/tests/deep_runtime/test_tool_result_normalizer.py` | **Create** — normalizer flips error dicts, passes success through | 1 |
| `backend/src/deep_runtime/tool_bridge.py` | **Create** — `build_langchain_tools()` (Jarvis defs → `StructuredTool`s over `execute_tool`) | 2 |
| `backend/tests/deep_runtime/test_tool_bridge.py` | **Create** — wraps defs, coroutine routes to `execute_tool`, args_schema is the JSON schema | 2 |
| `backend/src/deep_runtime/agent_builder.py` | Install normalizer after scope guard; widen `system_prompt` type to `str \| SystemMessage \| None` | 3 |
| `backend/tests/deep_runtime/test_agent_builder.py` | Append: normalizer installed; `SystemMessage` system_prompt accepted | 3 |
| `backend/src/deep_runtime/prompt_bridge.py` | Add `build_system_message()` (structured `SystemMessage`, preserves cache_control) | 4 |
| `backend/tests/deep_runtime/test_prompt_bridge.py` | Append: 2-block `SystemMessage`, cache_control only on block 0, string passthrough | 4 |
| `backend/src/deep_runtime/checkpointer.py` | **Create** — `build_async_postgres_saver()` (psycopg3 pool + `AsyncPostgresSaver` + `setup()`) | 5 |
| `backend/tests/deep_runtime/test_checkpointer.py` | **Create** — real-DB: build saver, put/get a checkpoint, close pool | 5 |
| `backend/src/orchestrator/agent_invoker.py` | Deep branch: bridge tools + `SystemMessage` + `checkpointer_provider`; add the param | 6 |
| `backend/tests/test_agent_invoker_deep_hardening.py` | **Create** — deep branch calls bridge/SystemMessage/provider; legacy unchanged | 6 |
| `backend/src/orchestrator/jarvis.py` + `backend/src/api/app.py` | Lifespan builds the saver (gated); orchestrator threads the provider | 7 |
| `backend/tests/test_deep_checkpointer_wiring.py` | **Create** — provider returns the saver when set, None otherwise | 7 |
| `backend/tests/test_deep_runtime_tool_execution.py` | **Create** — end-to-end: bridged tool executes + blocked mapping via fake model | 8 |
| `backend/spikes/deep_stream/live_hardening_smoke.py` | **Create (OPTIONAL, live)** — 2-turn cache_read>0 + real tool call | 9 |
| `CLAUDE.md` | Doc note: deep runtime now tool-executes + caches + durable checkpointer | 10 |

---

## Task 1: the tool-result normalizer middleware

**Files:**
- Create: `backend/src/deep_runtime/middleware/tool_result_normalizer.py`
- Test: `backend/tests/deep_runtime/test_tool_result_normalizer.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/deep_runtime/test_tool_result_normalizer.py`. Drive the normalizer through a real langgraph `ToolNode` (mirror how `tests/deep_runtime/test_capability_scope.py` exercises middleware — read it first for the exact harness) so the test pins the ACTUAL `ToolMessage.content` serialization `ToolNode` produces for a dict return. The test builds a deep agent (or a minimal `create_agent`) with (a) a tool whose coroutine returns `{"error":"boom","blocked":True}` and (b) a tool that returns `{"ok":1}`, installs `make_tool_result_normalizer()`, runs one turn that calls both, and asserts the error tool's `ToolMessage.status == "error"` (with the right `tool_call_id`/`name`) while the success tool's stays `status != "error"`.

```python
"""Step 6A.5: the tool_result_normalizer flips recoverable {"error"|"blocked"} tool
results to ToolMessage(status="error") so the deep stream adapter's blocked<-status=="error"
mapping holds, without aborting the turn."""

# (imports: create_deep_agent / create_agent, MemorySaver, the Task-0/Task-4 fake
#  streaming model + a StructuredTool that returns an error dict and one that succeeds,
#  make_tool_result_normalizer, ToolMessage — verify the fake-model harness in
#  tests/deep_runtime/test_stream_adapter.py and the middleware harness in
#  tests/deep_runtime/test_capability_scope.py before writing.)

async def test_normalizer_flips_error_dict_to_status_error():
    frames = await _run_turn_over([_error_tool, _ok_tool])  # helper: build+run, return ToolMessages
    tool_msgs = {m.name: m for m in frames if isinstance(m, ToolMessage)}
    assert tool_msgs["boom_tool"].status == "error"
    assert tool_msgs["boom_tool"].tool_call_id  # a real id, not a placeholder
    assert tool_msgs["ok_tool"].status != "error"
```

- [ ] **Step 2: Run it → FAIL** (module doesn't exist). `uv run pytest tests/deep_runtime/test_tool_result_normalizer.py -v`.

- [ ] **Step 3: Implement** — create `backend/src/deep_runtime/middleware/tool_result_normalizer.py`:

```python
"""Normalize Jarvis error/blocked tool payloads to ToolMessage(status="error") on the
deep runtime (Step 6A.5).

Jarvis tools (wrapped as StructuredTools over ToolExecutor.execute_tool) return plain
dicts — including recoverable failures shaped {"error": ...} or {"error": ..., "blocked":
True}. Under langgraph's ToolNode a non-raising coroutine result is wrapped as
ToolMessage(status="success"), so the error signal is lost and the deep stream adapter
would compute blocked=False. This wrap_tool_call middleware inspects the result and, when
the payload carries an error/blocked marker, re-emits ToolMessage(status="error") with the
correct tool_call_id + name — preserving the frozen blocked<-status=="error" mapping
without aborting the turn (a returned ToolMessage continues the loop; only an unhandled
raise aborts). Install AFTER the capability_scope guard so scope denials (already
status="error") pass through untouched.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


def _payload_dict(content: Any) -> dict | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def make_tool_result_normalizer() -> AgentMiddleware:
    """Build a middleware that marks recoverable Jarvis error/blocked results as errors."""

    @wrap_tool_call
    async def tool_result_normalizer(request, handler):
        msg = await handler(request)
        if not isinstance(msg, ToolMessage) or msg.status == "error":
            return msg
        payload = _payload_dict(msg.content)
        if isinstance(payload, dict) and (payload.get("error") or payload.get("blocked")):
            return ToolMessage(
                content=msg.content,
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )
        return msg

    return tool_result_normalizer
```

> **NOTE for the implementer:** `_payload_dict` handles both a dict and a JSON string. If the Task-1 test reveals `ToolNode` serializes the dict some other way (e.g. Python `str(dict)`), adjust `_payload_dict` to match the OBSERVED content — do NOT loosen the test. Research ran this exact shape through a real `ToolNode` and the JSON path worked; confirm.

- [ ] **Step 4: Run it → PASS.** Then `uv run pytest tests/deep_runtime/ -v`.

- [ ] **Step 5: Full gate + ruff** on the two files.

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/middleware/tool_result_normalizer.py backend/tests/deep_runtime/test_tool_result_normalizer.py
git commit -m "feat(rebuild): tool_result_normalizer flips deep-runtime error/blocked results to status=error (Step 6A.5)"
```

---

## Task 2: the tool bridge (Jarvis defs → LangChain `StructuredTool`s)

**Files:**
- Create: `backend/src/deep_runtime/tool_bridge.py`
- Test: `backend/tests/deep_runtime/test_tool_bridge.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/deep_runtime/test_tool_bridge.py`:

```python
"""Step 6A.5: build_langchain_tools wraps Jarvis tool defs as StructuredTools whose async
coroutine routes to ToolExecutor.execute_tool, preserving name/description/JSON-schema and
the (closure-captured, never-LLM-supplied) user_id/workspace_id."""

from langchain_core.tools import StructuredTool

from src.deep_runtime.tool_bridge import build_langchain_tools


async def test_wraps_defs_and_routes_to_execute_tool():
    calls = []

    async def fake_execute_tool(name, tool_input, user_id, workspace_id):
        calls.append((name, tool_input, user_id, workspace_id))
        return {"ok": True, "echoed": tool_input}

    defs = [
        {"name": "search", "description": "Search knowledge.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                          "required": ["query"]}},
        # extra keys (cache_control) must be ignored:
        {"name": "list_x", "description": "List X.", "input_schema": {"type": "object"},
         "cache_control": {"type": "ephemeral"}},
    ]
    tools = build_langchain_tools(defs, execute_tool=fake_execute_tool, user_id="u", workspace_id="ws")

    assert all(isinstance(t, StructuredTool) for t in tools)
    assert [t.name for t in tools] == ["search", "list_x"]
    # args_schema is the raw JSON schema dict (no pydantic conversion)
    assert tools[0].args_schema == defs[0]["input_schema"]
    # invoking the coroutine dispatches through execute_tool with closure context
    result = await tools[0].ainvoke({"query": "hi"})
    assert result == {"ok": True, "echoed": {"query": "hi"}}
    assert calls == [("search", {"query": "hi"}, "u", "ws")]


def test_empty_defs_returns_empty():
    assert build_langchain_tools([], execute_tool=None, user_id="u", workspace_id="ws") == []
```

(Confirm `StructuredTool.ainvoke` returns the coroutine's dict as-is for `response_format="content"` — if langchain-core 1.4.8 stringifies it, assert on the stringified form and note it; do NOT loosen the routing assertion on `calls`.)

- [ ] **Step 2: Run it → FAIL** (module doesn't exist).

- [ ] **Step 3: Implement** — create `backend/src/deep_runtime/tool_bridge.py`:

```python
"""Bridge Jarvis registry tools to LangChain StructuredTools for the deep runtime
(Step 6A.5).

langchain's create_agent (under create_deep_agent) classifies dict tools as provider
*built-in* tools with no executor, so Jarvis's Anthropic-schema dicts never run. This
module converts each Jarvis tool def ({"name","description","input_schema"}) into a
StructuredTool whose async coroutine routes through ToolExecutor.execute_tool — preserving
every Jarvis policy layer (capability-scope, per-tool cost attribution, turn-scoped MCP,
composite tools) because execution still flows through the one execute_tool chokepoint.
Recoverable error/blocked results are marked status="error" by the tool_result_normalizer
middleware, not here. user_id / workspace_id are captured in the closure — never
LLM-supplied — matching ToolExecutor's internal-input enrichment contract.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import StructuredTool

ExecuteToolFn = Callable[[str, dict, str, str], Awaitable[dict]]


def build_langchain_tools(
    tool_defs: list[dict],
    *,
    execute_tool: ExecuteToolFn,
    user_id: str,
    workspace_id: str,
) -> list[StructuredTool]:
    """Wrap each Jarvis tool def as a StructuredTool routing to execute_tool.

    ``tool_defs`` are ``{"name","description","input_schema"}`` dicts (extra keys such as
    ``cache_control`` are ignored). ``args_schema`` takes the raw JSON Schema — langchain-core
    1.4.8 accepts a dict, so no pydantic model is built. Only ``coroutine`` is wired; the
    deep path is async-only.
    """
    tools: list[StructuredTool] = []
    for d in tool_defs:
        name = d["name"]
        schema = d.get("input_schema") or {"type": "object", "properties": {}}
        tools.append(
            StructuredTool(
                name=name,
                description=d.get("description") or name,
                args_schema=schema,
                coroutine=_make_coroutine(name, execute_tool, user_id, workspace_id),
            )
        )
    return tools


def _make_coroutine(
    name: str, execute_tool: ExecuteToolFn, user_id: str, workspace_id: str
) -> Callable[..., Awaitable[dict]]:
    async def _run(**kwargs: Any) -> dict:
        # kwargs are the LLM-provided args only; execute_tool injects any context args
        # downstream. user_id/workspace_id come from the closure, never the model.
        return await execute_tool(name, kwargs, user_id, workspace_id)

    return _run
```

- [ ] **Step 4: Run it → PASS.** Then `uv run pytest tests/deep_runtime/ -v`.

- [ ] **Step 5: Full gate + ruff** on the two files.

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/tool_bridge.py backend/tests/deep_runtime/test_tool_bridge.py
git commit -m "feat(rebuild): tool_bridge wraps Jarvis tools as LangChain StructuredTools over execute_tool (Step 6A.5)"
```

---

## Task 3: install the normalizer + accept `SystemMessage` in `build_deep_agent`

**Files:**
- Modify: `backend/src/deep_runtime/agent_builder.py`
- Test: `backend/tests/deep_runtime/test_agent_builder.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/deep_runtime/test_agent_builder.py`:

```python
async def test_build_deep_agent_installs_tool_result_normalizer():
    """Every deep agent gets the tool_result_normalizer middleware (unconditional)."""
    from unittest.mock import patch

    from src.deep_runtime import agent_builder
    from src.orchestrator.agents import SubAgent

    probe = SubAgent(name="probe", prompt="p", model_tier="sonnet",
                     capability_scope=set(), temperature=0.0, max_tokens=1024)
    with patch.object(agent_builder, "create_deep_agent") as mock_create:
        await agent_builder.build_deep_agent(probe, tools=[], workspace_id="ws", db_factory=None)
    mws = mock_create.call_args.kwargs["middleware"]
    # the normalizer callable is present (match by __name__ or type, mirroring the
    # capability_scope detection style already in agent_builder)
    assert any(getattr(mw, "name", None) == "tool_result_normalizer"
               or type(mw).__name__ == "tool_result_normalizer" for mw in mws)


async def test_build_deep_agent_accepts_system_message():
    """system_prompt may be a SystemMessage (structured, cache_control-bearing)."""
    from unittest.mock import patch

    from langchain_core.messages import SystemMessage

    from src.deep_runtime import agent_builder
    from src.orchestrator.agents import SubAgent

    sm = SystemMessage(content=[{"type": "text", "text": "SOUL", "cache_control": {"type": "ephemeral"}}])
    probe = SubAgent(name="probe", prompt="p", model_tier="sonnet",
                     capability_scope=set(), temperature=0.0, max_tokens=1024)
    with patch.object(agent_builder, "create_deep_agent") as mock_create:
        await agent_builder.build_deep_agent(probe, tools=[], workspace_id="ws",
                                             db_factory=None, system_prompt=sm)
    assert mock_create.call_args.kwargs["system_prompt"] is sm
```

- [ ] **Step 2: Run them → FAIL** (normalizer not installed; type annotation aside, the first test fails).

- [ ] **Step 3: Implement** — in `agent_builder.py`:
  1. Add the import: `from src.deep_runtime.middleware.tool_result_normalizer import make_tool_result_normalizer` and `from langchain_core.messages import SystemMessage`.
  2. Widen the signature: `system_prompt: str | SystemMessage | None = None`. Update the docstring one line.
  3. After `middleware.extend(extra_middleware)` and BEFORE the `has_scope_mw` write-agent guard, append the normalizer unconditionally:

```python
    # Normalize recoverable Jarvis error/blocked tool results to status="error" so the
    # deep stream adapter's blocked<-status=="error" mapping holds (runs after the scope
    # guard, which already emits status="error" on denial).
    middleware.append(make_tool_result_normalizer())
```

  (Leave the fail-closed write-agent `ValueError` guard and the `create_deep_agent(...)` call otherwise unchanged; `system_prompt or agent.prompt` still works for a `SystemMessage` since a non-empty `SystemMessage` is truthy.)

- [ ] **Step 4: Run them → PASS.** Then `uv run pytest tests/deep_runtime/test_agent_builder.py -v` (existing checkpointer + write-agent-refusal tests must still pass).

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/agent_builder.py backend/tests/deep_runtime/test_agent_builder.py
git commit -m "feat(rebuild): build_deep_agent installs tool_result_normalizer + accepts SystemMessage prompt (Step 6A.5)"
```

---

## Task 4: `build_system_message` — structured prompt that preserves the cache breakpoint

**Files:**
- Modify: `backend/src/deep_runtime/prompt_bridge.py`
- Test: `backend/tests/deep_runtime/test_prompt_bridge.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/deep_runtime/test_prompt_bridge.py`:

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
    assert sm.content == blocks            # blocks preserved verbatim (incl. cache_control)
    assert sm.content[0].get("cache_control") == {"type": "ephemeral"}  # breakpoint on soul+role
    assert "cache_control" not in sm.content[1]                          # not on volatile context


def test_build_system_message_wraps_plain_string():
    from langchain_core.messages import SystemMessage

    from src.deep_runtime.prompt_bridge import build_system_message

    sm = build_system_message("just a string")
    assert isinstance(sm, SystemMessage)
    assert sm.content == "just a string"


def test_build_system_message_drops_empty_and_non_text_blocks():
    from src.deep_runtime.prompt_bridge import build_system_message

    blocks = [{"type": "text", "text": "A"}, {"type": "image"}, {"type": "text", "text": ""}]
    sm = build_system_message(blocks)
    assert sm.content == [{"type": "text", "text": "A"}]
```

- [ ] **Step 2: Run them → FAIL** (`build_system_message` doesn't exist).

- [ ] **Step 3: Implement** — add to `backend/src/deep_runtime/prompt_bridge.py` (keep `flatten_system_blocks` as-is; add the import `from langchain_core.messages import SystemMessage`):

```python
def build_system_message(system_blocks: Any) -> SystemMessage:
    """Build a SystemMessage preserving the chat path's two-block cache layout.

    The legacy chat path emits ``[{soul+role, cache_control: ephemeral}, {context}]``.
    Flattening to one string (``flatten_system_blocks``) merged the volatile per-turn
    context into the same cache prefix as the stable soul+role, so caching thrashed on the
    second turn. Emitting a ``SystemMessage`` whose content is the structured text blocks
    (``cache_control`` preserved on the soul+role block only) restores the legacy cache
    breakpoint: langchain-anthropic honors ``cache_control`` on content blocks, and
    ``create_deep_agent`` preserves the caller's blocks. A plain string is wrapped as-is.
    """
    if isinstance(system_blocks, str):
        return SystemMessage(content=system_blocks)
    blocks = [
        b
        for b in system_blocks
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return SystemMessage(content=blocks)
```

- [ ] **Step 4: Run them → PASS.**

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/prompt_bridge.py backend/tests/deep_runtime/test_prompt_bridge.py
git commit -m "feat(rebuild): prompt bridge build_system_message preserves the cache breakpoint (Step 6A.5)"
```

---

## Task 5: the durable checkpointer builder module

**Files:**
- Create: `backend/src/deep_runtime/checkpointer.py`
- Test: `backend/tests/deep_runtime/test_checkpointer.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/deep_runtime/test_checkpointer.py`. This is a REAL-DB test — use the repo's self-contained real-DB pattern (`_db_reachable`/NullPool-style guard; read an existing real-DB test like `tests/deep_runtime/test_capability_scope.py` or a `store`/entity_facts real-DB test for the exact skip-if-unreachable idiom). It builds the saver, writes + reads one checkpoint via the langgraph API, and closes the pool.

```python
"""Step 6A.5: build_async_postgres_saver returns a durable AsyncPostgresSaver over a
dedicated psycopg3 pool; setup() is idempotent and a checkpoint round-trips."""

# (imports: build_async_postgres_saver, a DB-reachability guard, settings/database_url from
#  make_mock_settings or the real test DSN used by other real-DB tests)

async def test_saver_round_trips_a_checkpoint():
    if not _db_reachable():
        return  # skip when Postgres isn't up (mirrors the repo's real-DB skip idiom)
    saver, pool = await build_async_postgres_saver(_TEST_DSN)  # postgresql+asyncpg://... accepted
    try:
        cfg = {"configurable": {"thread_id": "t-6a5-test", "checkpoint_ns": ""}}
        # put a checkpoint then read it back (use the AsyncPostgresSaver API surface the
        # 2026-06-28 spike used: aput / aget_tuple — verify exact method names against the
        # installed source before finalizing)
        assert await saver.aget_tuple(cfg) is None  # empty to start
        # ... aput a minimal checkpoint, then assert aget_tuple(cfg) is not None ...
    finally:
        await pool.close()
```

(READ `backend/spikes/postgres_saver/probe.py` first — it already exercises `AsyncPostgresSaver` put/get against live Postgres; reuse its exact API calls + serializer expectations. Keep the test minimal: prove build + setup + one round-trip + clean close.)

- [ ] **Step 2: Run it → FAIL** (module doesn't exist).

- [ ] **Step 3: Implement** — create `backend/src/deep_runtime/checkpointer.py`:

```python
"""Durable langgraph checkpointer for the deep runtime (Step 6A.5).

Builds an AsyncPostgresSaver over a dedicated small psycopg3 connection pool, intended to
be constructed once at app lifespan. This replaces the per-call in-process MemorySaver so
a future 6B interrupt() can pause a chat turn and resume after a separate approval
round-trip. In 6A.5 no interrupt fires, so the saver is durable-but-inert. The saver's
psycopg3 pool is separate from the app's asyncpg pool (different drivers, same Postgres);
it is small because AsyncPostgresSaver serializes DB ops behind one asyncio.Lock.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


def to_psycopg_dsn(database_url: str) -> str:
    """Convert a SQLAlchemy async DSN to a plain psycopg3 DSN (strip the +asyncpg driver)."""
    return database_url.replace("+asyncpg", "", 1)


async def build_async_postgres_saver(
    database_url: str,
) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Open a small psycopg3 pool + an AsyncPostgresSaver over it; run setup() once.

    Returns ``(saver, pool)``. The caller owns lifecycle and MUST ``await pool.close()`` on
    shutdown. Construct inside the running event loop (AsyncPostgresSaver builds an
    asyncio.Lock at init).
    """
    pool = AsyncConnectionPool(
        to_psycopg_dsn(database_url),
        min_size=1,
        max_size=4,
        open=False,  # psycopg-pool 3.2+: do not open in __init__
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # idempotent: CREATE TABLE IF NOT EXISTS for the 4 checkpoint tables
    logger.info("[deep_runtime] durable AsyncPostgresSaver initialized")
    return saver, pool
```

- [ ] **Step 4: Run it → PASS** (with Postgres up). Confirm `uv run alembic check` still reports "No new upgrade operations detected" AFTER the test ran `setup()` (the 4 checkpoint tables are excluded by `env.py`).

- [ ] **Step 5: Full gate + ruff.**

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/checkpointer.py backend/tests/deep_runtime/test_checkpointer.py
git commit -m "feat(rebuild): durable AsyncPostgresSaver builder for the deep runtime (Step 6A.5)"
```

---

## Task 6 (BLAST-RADIUS): rewire the deep seam — bridged tools + SystemMessage + checkpointer provider

**Files:**
- Modify: `backend/src/orchestrator/agent_invoker.py` (the `runtime=="deep"` branch + `__init__`)
- Test: `backend/tests/test_agent_invoker_deep_hardening.py` (create)

> This is the one blast-radius task (touches the live chat entry). The default `legacy` path must stay byte-behavior-identical. **2-stage PARALLEL review** (spec + quality) on the frozen commit.

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_agent_invoker_deep_hardening.py`. Build an `AgentInvoker` (reuse the `_make_invoker` mock pattern from `tests/test_agent_invoker_runtime_branch.py` — read it), and assert that under `runtime="deep"` the branch: (a) wraps tools via `build_langchain_tools` (patch it, assert called with the resolved tool list + `user_id`/`workspace_id`), (b) passes a `SystemMessage` (via `build_system_message`) as `system_prompt` to `build_deep_agent` (patch `build_deep_agent`, assert the kwarg is a `SystemMessage`), and (c) uses `self._checkpointer_provider()` for the checkpointer (inject a sentinel saver via the provider, assert it's forwarded; default provider → `MemorySaver`). Also assert `runtime="legacy"` still routes to `agent_loop` unchanged.

```python
"""Step 6A.5: the deep branch bridges tools to StructuredTools, passes a structured
SystemMessage, and uses the injected durable checkpointer — legacy path unchanged."""

async def test_deep_branch_bridges_tools_and_uses_systemmessage_and_provider():
    from unittest.mock import AsyncMock, patch

    from langchain_core.messages import SystemMessage

    sentinel_saver = object()
    inv = _make_invoker(runtime="deep", checkpointer_provider=lambda: sentinel_saver)

    async def _fake_adapter(*a, **k):
        yield {"event": "agent_done", "agent": "perceiver", "text": "ok",
               "input_tokens": 1, "output_tokens": 1, "cache_creation_tokens": 0,
               "cache_read_tokens": 0, "tools_called": [], "latency_ms": 1, "cost_usd": 0.0}

    with patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()) as mock_build, \
         patch("src.orchestrator.agent_invoker.build_langchain_tools", return_value=["T"]) as mock_bridge, \
         patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter):
        frames = [f async for f in inv.call_agent_stream("perceiver", message="hi",
                    user_id="u", workspace_id="ws", tools_override=[])]

    assert any(f["event"] == "agent_done" for f in frames)
    mock_bridge.assert_called_once()
    kw = mock_build.call_args.kwargs
    assert isinstance(kw["system_prompt"], SystemMessage)
    assert kw["checkpointer"] is sentinel_saver
```

(Adjust `_make_invoker` to accept and inject `checkpointer_provider`.)

- [ ] **Step 2: Run it → FAIL.**

- [ ] **Step 3: Implement.**
  1. Top-of-file imports: `from src.deep_runtime.tool_bridge import build_langchain_tools`, `from src.deep_runtime.prompt_bridge import build_system_message` (alongside the existing `flatten_system_blocks` import — keep it or replace per usage).
  2. `__init__`: add `checkpointer_provider=None` (last param); store `self._checkpointer_provider = checkpointer_provider or (lambda: None)`. Docstring one line.
  3. Rewrite the deep branch body (currently `agent_invoker.py:171-192`) to:

```python
        if self._settings.runtime == "deep":
            # Step 6A.5: run the routed chat agent on the Deep Agents runtime with REAL
            # typed tools (bridged through execute_tool — all Jarvis policy preserved), a
            # structured SystemMessage that keeps the soul/role cache breakpoint, and the
            # durable checkpointer (falls back to an in-process saver until wired at lifespan).
            from langgraph.checkpoint.memory import MemorySaver

            lc_tools = build_langchain_tools(
                tools, execute_tool=self._tool_executor.execute_tool,
                user_id=user_id, workspace_id=workspace_id,
            )
            deep_agent = await build_deep_agent(
                agent,
                lc_tools,
                workspace_id=workspace_id,
                db_factory=self._db_factory,
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

  (Keep the legacy `agent_loop` block below byte-identical. `tools` is the already-resolved list from `self._resolve_tools(...)`; the bridge ignores the `cache_control` key.)

- [ ] **Step 4: Run it → PASS.**

- [ ] **Step 5: Full gate + ruff.** Confirm NO existing chat/invoker test regressed under the default `legacy` path: `uv run pytest tests/ -k "invoker or chat or agent_loop or runtime_branch" --ignore=tests/e2e -v`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/agent_invoker.py backend/tests/test_agent_invoker_deep_hardening.py
git commit -m "feat(rebuild): deep seam uses bridged tools + SystemMessage + durable checkpointer provider (Step 6A.5)"
```

---

## Task 7: wire the durable checkpointer at app lifespan + through the orchestrator

**Files:**
- Modify: `backend/src/api/app.py` (lifespan construction, gated) + `backend/src/orchestrator/jarvis.py` (thread the provider to `AgentInvoker`)
- Test: `backend/tests/test_deep_checkpointer_wiring.py` (create)

> The saver is INERT in 6A.5 (no interrupt fires). This task just makes the seam's provider return a durable saver instead of `None` (→ `MemorySaver`), and only when `runtime=="deep"`.

- [ ] **Step 1: Trace the construction path FIRST.** Read `backend/src/api/app.py` lifespan (`:53`), how `JarvisOrchestrator` is constructed and reaches `app.state` (grep `JarvisOrchestrator(` and how `routes_chat` / `app.state` obtain the orchestrator), and `jarvis.py:125` where `AgentInvoker(...)` is built. Decide the minimal thread: app lifespan builds `(saver, pool)` when `settings.runtime == "deep"`, stores `app.state.deep_checkpointer = saver` (+ `app.state.deep_checkpointer_pool = pool`), and closes the pool on shutdown; the orchestrator is given a `checkpointer_provider` that reads it. If the orchestrator is built lazily/per-process without `app` in scope, pass the provider through `JarvisOrchestrator.__init__` (default `None`) and set it where the orchestrator is created with access to `app.state`.

- [ ] **Step 2: Write the failing test** — create `backend/tests/test_deep_checkpointer_wiring.py`:

```python
"""Step 6A.5: JarvisOrchestrator threads a checkpointer_provider to AgentInvoker; it
returns the injected saver, or None (→ MemorySaver at the seam) by default."""

def test_orchestrator_threads_checkpointer_provider():
    # build a JarvisOrchestrator with a provider returning a sentinel; assert the
    # AgentInvoker's provider returns it (read jarvis.py for the exact ctor deps + how to
    # build it in a test — mirror tests/test_jarvis*.py setup with make_mock_settings +
    # @patch get_anthropic_client).
    sentinel = object()
    orch = _make_orchestrator(checkpointer_provider=lambda: sentinel)
    assert orch._invoker._checkpointer_provider() is sentinel


def test_default_provider_returns_none():
    orch = _make_orchestrator()  # no provider
    assert orch._invoker._checkpointer_provider() is None
```

- [ ] **Step 3: Run it → FAIL.**

- [ ] **Step 4: Implement.**
  1. `jarvis.py`: add `checkpointer_provider=None` to `JarvisOrchestrator.__init__`; pass it into `AgentInvoker(..., checkpointer_provider=checkpointer_provider)` at `:125`.
  2. `api/app.py` lifespan: after Redis init, when `settings.runtime == "deep"`, build the saver and expose it:

```python
        app.state.deep_checkpointer = None
        app.state.deep_checkpointer_pool = None
        if settings.runtime == "deep":
            from src.deep_runtime.checkpointer import build_async_postgres_saver

            saver, pool = await build_async_postgres_saver(settings.database_url)
            app.state.deep_checkpointer = saver
            app.state.deep_checkpointer_pool = pool
```

  and on shutdown, `if app.state.deep_checkpointer_pool: await app.state.deep_checkpointer_pool.close()`.
  3. Where the orchestrator is constructed with `app.state` in scope, pass `checkpointer_provider=lambda: getattr(app.state, "deep_checkpointer", None)`. (If the orchestrator is a module-level singleton built without `app`, thread the provider from the same place `app.state` is read in `routes_chat` — follow the existing pattern; do NOT introduce a global.)

- [ ] **Step 5: Run it → PASS.** Full gate + ruff. Sanity: with default `runtime="legacy"`, the lifespan builds NO saver (no psycopg3 pool opened) — confirm the app still starts and legacy tests are unaffected.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/app.py backend/src/orchestrator/jarvis.py backend/tests/test_deep_checkpointer_wiring.py
git commit -m "feat(rebuild): wire durable deep-runtime checkpointer at lifespan (gated on runtime=deep) (Step 6A.5)"
```

---

## Task 8: end-to-end deep-path tool execution (fake model)

**Files:**
- Test: `backend/tests/test_deep_runtime_tool_execution.py` (create)

> Closes the gap the research flagged: prove a bridged tool actually EXECUTES through a compiled `create_deep_agent` and that the frozen `blocked` mapping holds — without a real API (fake streaming model + `MemorySaver`).

- [ ] **Step 1: Write the test.** Reuse the Task-0/Task-4 fake streaming model (from `tests/deep_runtime/test_stream_adapter.py`) but script it to call TWO bridged tools: one whose `execute_tool` stub returns `{"ok": 1}` and one that returns `{"error": "nope", "blocked": True}`. Build the agent via `build_deep_agent` (so the scope guard + normalizer are installed) with a `capability_scope` that permits both, run through `stream_deep_agent_events`, and assert: the OK tool's `tool_result` frame has `blocked=False` with `result` reflecting `{"ok":1}`; the error tool's `tool_result` frame has `blocked=True`; both `tool_result.tool` names are recovered; `tool_call` count == `tool_result` count.

```python
"""Step 6A.5: a bridged Jarvis tool executes through a compiled deep agent; recoverable
errors surface as tool_result.blocked=True via the normalizer, and the turn continues."""

async def test_bridged_tools_execute_and_blocked_maps_through_the_adapter():
    frames = [f async for f in _run_deep_turn_with_two_bridged_tools()]  # helper builds+streams
    results = {f["tool"]: f for f in frames if f["event"] == "tool_result"}
    assert results["ok_tool"]["blocked"] is False
    assert results["err_tool"]["blocked"] is True
    kinds = [f["event"] for f in frames]
    assert kinds.count("tool_call") == kinds.count("tool_result") >= 2
```

(This test needs the fake model scripted to emit `tool_call_chunks` for both tools then a final turn — mirror the Task-0 probe's two-turn script, extended to two tool calls. If wiring a fake model through `build_chat_model` is too invasive, build the agent directly with `create_deep_agent(model=<fake>, tools=<bridged>, middleware=[scope_guard, normalizer], checkpointer=MemorySaver())` and stream via `stream_deep_agent_events` — the goal is proving execution + blocked mapping end-to-end, not the exact builder path.)

- [ ] **Step 2: Run it → PASS** (fix the adapter/bridge/normalizer, NOT the test, if it fails — a failure is a real integration gap).

- [ ] **Step 3: Full gate + ruff. Commit**

```bash
git add backend/tests/test_deep_runtime_tool_execution.py
git commit -m "test(rebuild): deep-path bridged tools execute + blocked maps end-to-end (Step 6A.5)"
```

---

## Task 9 (OPTIONAL, live): caching + real-tool smoke behind the flag

**Files:** `backend/spikes/deep_stream/live_hardening_smoke.py` (create; not a pytest test)

- [ ] Only if `JARVIS_ANTHROPIC_API_KEY` is set. A runnable script that sets `JARVIS_RUNTIME=deep`, sends TWO real read messages on ONE `thread_id` through `call_agent_stream("perceiver", …)`, and prints the `agent_done` telemetry for both turns. Assert/observe: turn-1 `cache_creation_tokens > 0` and **turn-2 `cache_read_tokens > 0`** (the deferred caching proof — confirms the structured `SystemMessage` engages caching; if `cache_read == 0`, ESCALATE per D3, especially for the Planner whose soul+role may sit below the Opus 4096 min in isolation), and that a real bridged tool call executes and returns a real result. Document output; do NOT gate CI on it. Commit under `spikes/`.

---

## Task 10: docs note

**Files:** `CLAUDE.md`

- [ ] **Step 1:** Update the "Two execution paths" / Step-6A note: the Deep Agents runtime (`JARVIS_RUNTIME=deep`) now (a) executes tools via a Jarvis→LangChain **tool bridge** through `execute_tool` (all policy preserved), (b) preserves soul/role **prompt caching** via a structured `SystemMessage`, and (c) uses a **durable `AsyncPostgresSaver`** (inert until the 6B interrupt gate). Keep it factual, no volatile counts. Note the still-6B-owned items: the interrupt gate, a persisted stable `thread_id`, the `routes_approvals` resume, and the SSE-on-resume contract.
- [ ] **Step 2:** Full gate + ruff. Commit `chore(rebuild): doc note — deep runtime tool-execution + caching + durable checkpointer (Step 6A.5)`.

---

## Review strategy (for the executor)

- **Tasks 1/2/3/4/5/8/10** — single combined review each (spec + quality).
- **Task 6 (deep seam)** — the blast-radius task → **2-stage PARALLEL review** (spec + quality) on the frozen commit; the quality reviewer must confirm the default `legacy` path is byte-behavior-unchanged and no existing chat/invoker test regresses, and that the bridge/SystemMessage/provider are wired exactly.
- **Task 7 (lifespan wiring)** — combined review, but the reviewer must confirm: (a) NO psycopg3 pool is opened when `runtime="legacy"` (gated), (b) the pool is closed on shutdown, (c) `alembic check` stays drift-free, (d) no global/singleton smell in the provider threading.
- **Final holistic review (opus):** full gate green, `alembic check` drift-free (no migrations), `JARVIS_RUNTIME=legacy` behavior-neutral, and — the load-bearing new guarantee — a bridged tool **actually executes** through the deep path with the `blocked` mapping intact (Task 8 is a real guard: confirm it fails if the normalizer is removed or the bridge emits dicts). Confirm the three carry-forwards are genuinely closed and nothing 6B-owned (interrupt/gate/persisted thread_id/resume) leaked in.

---

## Self-review checklist (run before dispatching implementers)

1. **Spec coverage:** carry-forward #1 (tool bridge) = T1 (normalizer) + T2 (bridge) + T3 (install) + T6 (seam) + T8 (e2e). #2 (caching) = T4 (SystemMessage) + T6 (seam uses it) + T9 (live proof). #3 (durable checkpointer) = T5 (builder) + T6 (seam provider) + T7 (lifespan). Docs = T10. ✅
2. **Placeholder scan:** all new modules have complete verbatim code. The two flagged empirical unknowns — `ToolNode`'s exact `ToolMessage.content` serialization (T1) and `AsyncPostgresSaver`'s put/get method names (T5) — are pinned by their tests against installed source, not left as TODOs. ✅
3. **Type/name consistency:** `build_langchain_tools`, `make_tool_result_normalizer`, `build_system_message`, `build_async_postgres_saver`, `checkpointer_provider` are used identically across tasks. The seam passes `build_system_message(system_blocks)` (a `SystemMessage`) to `build_deep_agent(system_prompt=…)` whose type was widened in T3. ✅
4. **No migrations / no DB writes to app tables** → `alembic check` stays drift-free (checkpoint tables excluded). Default `runtime="legacy"` → every change inert until flipped. ✅
5. **Scope discipline:** the `interrupt()` gate, persisted `thread_id`, `routes_approvals` resume, and SSE-on-resume contract are explicitly OUT (6B). Only the durable saver + wiring lands. ✅
