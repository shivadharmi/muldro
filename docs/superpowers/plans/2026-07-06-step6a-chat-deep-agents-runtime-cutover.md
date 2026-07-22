# Step 6A — Chat → Deep Agents Runtime Cutover (foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Deep Agents runtime an *alternative* chat execution path behind a `JARVIS_RUNTIME` flag (default `"legacy"` = zero behavior change), reproducing the frozen SSE streaming contract byte-for-byte, with a `checkpointer` wired through `build_deep_agent` so the 6B gate can later raise `interrupt()`. This is the low-risk **runtime foundation** — the gate (6B), the write lock + kill-Operator + fast-path + trust relocation (6C), and the collapse/context (Steps 7–8) all build on it.

**Architecture:** A single branch seam inside `AgentInvoker.call_agent_stream` (after `agent`/`tools`/`context`/`system_blocks` are resolved) chooses legacy `agent_loop` vs a new `_stream_via_deep_agent(...)` adapter. The adapter compiles the *same* routed `SubAgent` via `build_deep_agent(agent, tools, checkpointer=…)` and translates langgraph stream events back into the exact 7 SSE-shaped dicts `call_agent_stream` emits today, so `agent_event_from_sse` → `core_event_to_sse` → the web client are all unchanged. This is a **per-agent runtime swap**, NOT the single-lead collapse (that rides with kill-Operator in 6C) and NOT the gate (6B).

**Tech Stack:** Python 3.12, deepagents 0.6.11 / langgraph 1.2.6 / langchain-anthropic 1.4.6 / langgraph-checkpoint 4.1.1, async SQLAlchemy, pytest via the repo's custom `pytest_pyfunc_call` asyncio hook (NO pytest-asyncio). LangGraph streaming (`CompiledStateGraph.astream(stream_mode=…)`) is the load-bearing unknown → **spike-gated (Task 0)**.

---

## Infra note (verify at start)

Run all commands from `backend/` via `uv run`:

```bash
docker compose up -d postgres redis qdrant     # from repo root
cd backend
uv sync --all-extras                            # NO pip; plain `uv sync` drops dev extras
uv run alembic upgrade head                     # head c7d3e4f5a6b8 (6A adds NO migrations)
uv run pytest tests/ --ignore=tests/e2e         # baseline: 3110 passed / 18 skipped
```

- **NO pip.** `uv run …` / `uv add …`.
- deepagents/langgraph are installed (Step 1). `tests/deep_runtime/` collects.
- Do NOT edit `backend/` files while a `uvicorn --reload` worker runs.
- **No migrations, no schema, no DB writes in 6A** — this is a runtime seam + streaming adapter + a settings flag. `alembic check` must stay drift-free.
- **API key:** the streaming spike (Task 0) and the adapter tests use a **fake streaming ChatModel** (no API key needed) to prove the *structural* mapping; only the optional live e2e (Task 8) needs `JARVIS_ANTHROPIC_API_KEY`.

---

## Current-state (verified 2026-07-06 against HEAD a973221; spec present-tense is partly stale)

1. **The chat SSE pipeline** (do NOT change its downstream shape): `AgentInvoker.call_agent_stream` (yields dicts) → `core_events.agent_event_from_sse` (dict→typed `CoreEvent`) → `ChatProcessor._process_core` (yields `CoreEvent`s) → `core_events.core_event_to_sse` (CoreEvent→SSE dict) → `routes_chat.py` (wire frames `event: <name>\ndata: <json>`). Batch (`process_message`) folds the same `CoreEvent`s into a `result` dict. **Both stream and batch funnel through `call_agent_stream`** — so branching there covers both.
2. **The 7 dict shapes `call_agent_stream` emits** (from `LoopEvent`s; `agent_invoker.py:135-235`) — the adapter must reproduce these EXACTLY (keys are a frozen contract):
   - `{"event":"agent_start","agent":str,"model":str|None}`
   - `{"event":"thinking","agent":str,"text":str,"is_thinking":bool}`
   - `{"event":"text_delta","agent":str,"text":str}`
   - `{"event":"tool_call","agent":str,"tool":str,"input":dict}`
   - `{"event":"tool_result","agent":str,"tool":str,"result":Any,"blocked":bool,"latency_ms":int}`
   - `{"event":"agent_done","agent":str,"text":str,"input_tokens":int,"output_tokens":int,"cache_creation_tokens":int,"cache_read_tokens":int,"tools_called":list[str]|None,"latency_ms":int,"cost_usd":float}`
   - `{"event":"error","agent":str,"code":str,"message":str,"correlation_id":str}` — **sanitized**: raw upstream error is logged, NEVER emitted (`agent_invoker.py:210-222`).
3. **`build_deep_agent`** (`deep_runtime/agent_builder.py:55-120`, async) installs the capability-scope guard + fail-closed `ValueError`, and calls `create_deep_agent(model=…, tools=…, system_prompt=…, middleware=…, name=…)` — but does **NOT** forward `checkpointer=`/`interrupt_on=` (deepagents 0.6.11 accepts both). Adding `checkpointer=` is the 6A blocker.
4. **`build_chat_model`** (`deep_runtime/model_factory.py:28-61`) already maps a `SubAgent`→`ChatAnthropic` (tier + adaptive/legacy thinking). No new model code needed.
5. **Settings** (`config/settings.py`): `class Settings(BaseSettings)` with `env_prefix="JARVIS_"`. New fields are plain annotated class attributes with a default; read via `get_settings().<field>`.
6. **`create_deep_agent`/`build_deep_agent` have ZERO live importers** — the whole `deep_runtime` package is dormant. 6A gives it its first live caller (behind the flag).
7. **The branch seam** (`agent_invoker.py:135-182`): `call_agent_stream` resolves `agent = self._agents.get(agent_name)`, `model`, `tools = _resolve_tools(...)`, `context_block = _context.assemble_context(...)`, `system_blocks = build_system_prompt(...)`, then `async for evt in agent_loop(..., stream=True)`. 6A branches here.
8. **Prompt shape divergence:** the chat path builds Anthropic **multi-block** `system_blocks` (soul + role + context, with `cache_control` ephemeral markers); `build_deep_agent` takes a flat `system_prompt: str`. 6A flattens the blocks' `.text` into one string for the lead (cache-control is an Anthropic-API optimization the langchain path handles differently — the spike re-checks auto-caching survives).
9. **OPEN (spike-gated):** LangGraph token streaming is **unproven** — all repo spikes used `.ainvoke`. `CompiledStateGraph.astream(stream_mode=…)` supports `values/updates/checkpoints/tasks/debug/messages/custom`. Reproducing `text_delta`/`thinking`/`tool_call`/`tool_result`/`agent_done`(+telemetry) is Task 0's job.

---

## Design decisions

- **D1 — 6A is a per-agent runtime swap, not the single-lead collapse.** Behind `JARVIS_RUNTIME="deep"`, the *already-routed* chat agent (perceiver/librarian/operator/presenter/planner) runs on `build_deep_agent` instead of `agent_loop`. Orchestration (planner→route→per-step→presenter) is UNCHANGED. Operator still exists (killed in 6C). This bounds blast radius and lets the runtime land "once."
- **D2 — One seam covers stream + batch.** Branch inside `call_agent_stream` only. `process_message` (batch) consumes `call_agent_stream` transitively, so no separate batch branch.
- **D3 — The 7 SSE dict shapes are the contract.** The adapter's acceptance test asserts the deep path emits dicts with identical keys/types to the legacy path. Downstream (`agent_event_from_sse`, `core_event_to_sse`, `routes_chat`) is UNTOUCHED.
- **D4 — `checkpointer=` lands in 6A but no gate fires; the durable choice is 6B's (do NOT pre-decide here).** 6A forwards `checkpointer` to `create_deep_agent` so 6B can raise `interrupt()`. In 6A no interrupt fires, so the checkpointer is inert — a per-turn `MemorySaver` is a sufficient *placeholder*. **Open flag for 6B:** an in-process `MemorySaver` will NOT survive a chat approval that resolves via a separate REST round-trip (`routes_approvals` runs in a different request/process), so 6B must choose a checkpointer that spans the approval wait (AsyncPostgresSaver from the Step-1 spike, or a Redis-backed saver) keyed by a stable per-turn `thread_id`. 6A only proves the param threads through and picks the `thread_id` scheme; it does not lock the saver.
- **D5 — Default `"legacy"` = zero behavior change.** The deep path is dormant until an operator flips `JARVIS_RUNTIME=deep`. This is the "two-path behavior preservable behind a flag" the spec requires.
- **D6 — Telemetry via `custom` stream if needed.** `cost_usd`/`blocked`/`latency_ms` are Jarvis-computed, not langgraph-native. The spike (Task 0) decides: extract from `AIMessage.usage_metadata` + wall-clock timing in the adapter, or emit via a middleware `get_stream_writer()` `custom` event. The plan pins the *contract* (the 7 dicts); the spike pins the *mechanism*.
- **D7 — Error sanitization preserved.** The deep path catches all exceptions, logs the raw error, and emits ONLY `{"event":"error","agent","code","message","correlation_id"}` with generic `code`/`message` — reusing the existing `_GENERIC_CODE`/`_GENERIC_MESSAGE`/correlation-id helper from `agent_invoker`.
- **D8 — Prompt flattening is lossy; caching is a spike gate, not an assumption.** `system_blocks` → `"\n\n".join(b["text"] …)` drops the explicit `cache_control` ephemeral markers on the soul/role blocks. langchain-anthropic manages prompt caching itself, but losing the markers is a real per-turn **cost regression** if caching doesn't re-engage — so Task 0 (point 6) must verify it, and the DECISION line records `caching <confirmed|AT-RISK>`. If AT-RISK, escalate before the cutover ships (do not silently accept a cache loss).

---

## In-flight posture

- Branch `rebuild/first-principles`, HEAD `a973221`. Do NOT push/merge to main.
- Per-task commit (conventional-commit, no `Co-Authored-By`).
- **Task 0 is a spike (decision gate).** If it proves the streaming mapping infeasible with `messages`+`updates`+`custom`, STOP and escalate — the adapter (Task 4) depends on it. Do not fake the adapter.
- Tasks 1–3 are independent and deterministic (checkpointer param, settings field, prompt flattener). Task 4 (adapter) is gated by Task 0. Task 5 (seam) depends on 1+4. Tasks 6–8 are verification.
- Full gate after each task: `uv run pytest tests/ --ignore=tests/e2e`.

---

## File structure

| File | Change | Task |
|---|---|---|
| `backend/spikes/deep_stream/probe.py` + `docs/superpowers/spikes/2026-07-06-langgraph-stream-to-sse.md` | **Create** — streaming-mapping spike + decision doc | 0 |
| `backend/src/deep_runtime/agent_builder.py` | Add `checkpointer=` param, forward to `create_deep_agent` | 1 |
| `backend/tests/deep_runtime/test_agent_builder.py` | Test checkpointer forwarding | 1 |
| `backend/src/config/settings.py` | Add `runtime: str = "legacy"` | 2 |
| `backend/tests/test_settings_runtime_flag.py` | **Create** — flag default + override test | 2 |
| `backend/src/deep_runtime/prompt_bridge.py` | **Create** — `flatten_system_blocks()` | 3 |
| `backend/tests/deep_runtime/test_prompt_bridge.py` | **Create** — flatten test | 3 |
| `backend/src/deep_runtime/stream_adapter.py` | **Create** — `stream_deep_agent_events()` (langgraph stream → 7 SSE dicts) | 4 |
| `backend/tests/deep_runtime/test_stream_adapter.py` | **Create** — contract test vs the 7 shapes (fake model) | 4 |
| `backend/src/orchestrator/agent_invoker.py` | Branch `call_agent_stream` on `get_settings().runtime` | 5 |
| `backend/tests/test_agent_invoker_runtime_branch.py` | **Create** — flag routes legacy vs deep; default legacy unchanged | 5 |
| `backend/tests/test_chat_deep_runtime_parity.py` | **Create** — deep path emits the frozen SSE shapes end-to-end (fake model) | 6 |

---

## Task 0 (SPIKE, decision gate): prove langgraph `astream` → the 7 SSE dicts

**Files:**
- Create: `backend/spikes/deep_stream/probe.py`
- Create: `docs/superpowers/spikes/2026-07-06-langgraph-stream-to-sse.md`

**Goal:** Prove a compiled Deep Agent can stream token-by-token and that the stream can be reconstructed into the 7 frozen dict shapes (agent_start, text_delta, thinking, tool_call, tool_result, agent_done+telemetry, error) — using a **fake streaming ChatModel** (no API key). Decide the `stream_mode` mechanism.

- [ ] **Step 1: Write a runnable probe** at `backend/spikes/deep_stream/probe.py` that:
  1. Builds a fake streaming chat model **scripted for two turns** (turn 1: a couple of text chunks + a `tool_call` to a trivial `echo` tool; turn 2: a final text chunk) so `create_deep_agent` actually executes the tool then re-invokes the model. Try `langchain_core.language_models.fake_chat_models.GenericFakeChatModel`/`FakeMessagesListChatModel` first (verify with `uv run python -c "import langchain_core.language_models.fake_chat_models as m; print([n for n in dir(m)])"`), **but** a model that can't emit `tool_calls` inside a streamed `AIMessageChunk` won't drive the agent — expect to write a small custom `BaseChatModel` subclass whose `_astream` yields scripted `AIMessageChunk`s (with `tool_call_chunks` on turn 1 and `usage_metadata` on the terminal chunk).
  2. Compiles a deep agent: `create_deep_agent(model=<fake>, tools=[echo], checkpointer=MemorySaver())`.
  3. Streams it three ways and prints the raw events: `astream(input, config, stream_mode="messages")`, `stream_mode="updates"`, and `stream_mode=["messages","updates"]`.
  4. Attempts to reconstruct each of the 7 dict shapes from the stream, printing which mode yields which. For `agent_done` telemetry, read `AIMessage.usage_metadata` off the final message (the fake model sets it); compute `latency_ms` via `time.monotonic()` around the stream and `cost_usd` via the existing cost helper (grep `cost` in `agent_loop.py`/`tracing.py` for the token→USD formula — reuse it, do not re-derive).
  5. **`blocked` mapping:** compile a SECOND deep agent WITH a `db_factory` + a `capability_scope` that does NOT include the tool's capability, so the Step-0 capability-scope guard denies `echo` (returns a `ToolMessage(status="error")` without running it). Confirm that denial is observable in the stream (a tool result marked error/blocked) so the adapter can set `tool_result.blocked=True`. If the guard's denial is NOT distinguishable in the stream from a normal tool result, record that as a required `custom`-writer item.
  6. **Prompt-caching check (cost-regression guard for D8):** confirm that flattening `system_blocks` → one `system_prompt` string still engages Anthropic prompt caching on the deep path. With the fake model this is structural only (assert the system prompt reaches the model unsplit); note in the decision doc that the *real* cache-hit validation belongs in the live smoke (Task 8) — and flag it LOUDLY if caching cannot be confirmed, since losing the soul/role cache is a real per-turn cost regression.

- [ ] **Step 2: Run it**

Run: `uv run python spikes/deep_stream/probe.py`
Expected: prints, for each of the 7 target shapes, the source `stream_mode` and the reconstructed dict. If `messages`+`updates` cannot yield `tool_result.blocked`/`latency_ms`/`cost_usd`, additionally prototype a `stream_mode="custom"` path: a tiny middleware `@wrap_tool_call` that calls `get_stream_writer()(...)` to emit a Jarvis-shaped custom event, and confirm `astream(stream_mode="custom")` surfaces it.

- [ ] **Step 3: Write the decision doc** `docs/superpowers/spikes/2026-07-06-langgraph-stream-to-sse.md` capturing, verbatim from the run:
  - The exact `stream_mode` (or combination) that reconstructs each of the 7 shapes.
  - The precise object types yielded (e.g. `(AIMessageChunk, metadata)` for `messages`) and how to discriminate a text chunk vs a thinking/reasoning chunk (`is_thinking`) — langchain-anthropic emits reasoning content blocks; document how they appear in a chunk.
  - Whether telemetry (`usage_metadata`, cost, latency) is reconstructable in the adapter (D6) or needs a `custom` writer.
  - How a capability-scope **guard denial** surfaces in the stream (→ `tool_result.blocked=True`), per Step-1 point 5.
  - The **prompt-caching** finding (Step-1 point 6): does the flattened `system_prompt` still engage caching, or is this a cost regression to escalate?
  - A **DECISION line**: "Adapter uses `stream_mode=<...>`; telemetry via `<usage_metadata|custom>`; blocked via `<...>`; caching `<confirmed|AT-RISK>`." This is what Task 4 implements.

- [ ] **Step 4: Commit**

```bash
git add backend/spikes/deep_stream/probe.py docs/superpowers/spikes/2026-07-06-langgraph-stream-to-sse.md
git commit -m "spike(rebuild): prove langgraph astream → the 7 chat SSE dict shapes (Step 6A Task 0)"
```

> **Gate:** if no `stream_mode` combination reconstructs the 7 shapes (esp. token-level `text_delta` and the `tool_call`/`tool_result` pairing), report BLOCKED with the probe output — Task 4 cannot proceed and 6A's approach needs rethinking (e.g. `astream_events(version="v2")`).

---

## Task 1: add `checkpointer=` to `build_deep_agent`

**Files:**
- Modify: `backend/src/deep_runtime/agent_builder.py:55-120`
- Test: `backend/tests/deep_runtime/test_agent_builder.py`

- [ ] **Step 1: Write the failing test** — append to `backend/tests/deep_runtime/test_agent_builder.py`:

```python
async def test_build_deep_agent_forwards_checkpointer():
    """build_deep_agent must forward a checkpointer to create_deep_agent so the 6B gate
    can raise interrupt() (which requires a checkpointer + thread_id)."""
    from unittest.mock import patch

    from langgraph.checkpoint.memory import MemorySaver

    from src.deep_runtime import agent_builder
    from src.orchestrator.agents import SubAgent

    saver = MemorySaver()
    # EMPTY capability_scope on purpose: with no capabilities, _has_write_capability_in_scope
    # returns False, so build_deep_agent does NOT hit the fail-closed "refuse write agent
    # without a guard" raise even when db_factory is None — letting the test reach (patched)
    # create_deep_agent to assert checkpointer forwarding. (The write-agent refusal is tested
    # separately by the existing test_build_deep_agent_refuses_write_agent_without_scope_middleware.)
    probe_agent = SubAgent(
        name="probe",
        prompt="p",
        model_tier="sonnet",
        capability_scope=set(),
        temperature=0.0,
        max_tokens=1024,
    )

    with patch.object(agent_builder, "create_deep_agent") as mock_create:
        await agent_builder.build_deep_agent(
            probe_agent, tools=[], workspace_id="ws", db_factory=None, checkpointer=saver
        )
    assert mock_create.call_args.kwargs["checkpointer"] is saver
```

(Confirm `SubAgent`'s exact constructor fields first — read `src/orchestrator/agents.py` for the dataclass; adjust the kwargs to match. If `SubAgent` rejects an empty `capability_scope`, instead pass a non-None `db_factory` stub — `db_factory=MagicMock()` — so the scope-guard middleware installs (`has_scope_mw=True`) and short-circuits the raise; verify `make_capability_scope_middleware` accepts the stub at construction, since it only uses `db_factory` at tool-call time.)

- [ ] **Step 2: Run it → FAIL**

Run: `uv run pytest tests/deep_runtime/test_agent_builder.py::test_build_deep_agent_forwards_checkpointer -v`
Expected: FAIL — `build_deep_agent` has no `checkpointer` param (TypeError) or `create_deep_agent` isn't called with it.

- [ ] **Step 3: Implement** — in `agent_builder.py`, add the param to the signature and forward it:

```python
async def build_deep_agent(
    agent: SubAgent,
    tools: list[Any],
    *,
    workspace_id: str = "",
    db_factory=None,
    extra_middleware: Sequence[Any] = (),
    system_prompt: str | None = None,
    name: str | None = None,
    checkpointer=None,
) -> CompiledStateGraph:
```

and in the `create_deep_agent(...)` call, add `checkpointer=checkpointer,`:

```python
    return create_deep_agent(
        model=build_chat_model(agent),
        tools=tools,
        system_prompt=system_prompt or agent.prompt,
        middleware=middleware,
        name=name or agent.name,
        checkpointer=checkpointer,
    )
```

(Update the docstring to mention the new param. Leave the guard/ValueError logic unchanged.)

- [ ] **Step 4: Run it → PASS**, then the deep_runtime suite: `uv run pytest tests/deep_runtime/ -v`. Expected all pass.

- [ ] **Step 5: Full gate + ruff**: `uv run pytest tests/ --ignore=tests/e2e` (3110 passed + your new test); `uv run ruff check src/deep_runtime/agent_builder.py tests/deep_runtime/test_agent_builder.py`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/agent_builder.py backend/tests/deep_runtime/test_agent_builder.py
git commit -m "feat(rebuild): build_deep_agent forwards checkpointer to create_deep_agent (Step 6A)"
```

---

## Task 2: add the `JARVIS_RUNTIME` settings flag

**Files:**
- Modify: `backend/src/config/settings.py`
- Test: `backend/tests/test_settings_runtime_flag.py` (create)

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_settings_runtime_flag.py`:

```python
"""Step 6A: JARVIS_RUNTIME selects the chat execution runtime. Default 'legacy' = the
agent_loop path (zero behavior change); 'deep' = the Deep Agents lead path."""

import os
from unittest.mock import patch

from src.config.settings import Settings


def test_runtime_defaults_to_legacy():
    s = Settings(_env_file=None)
    assert s.runtime == "legacy"


def test_runtime_reads_jarvis_runtime_env():
    with patch.dict(os.environ, {"JARVIS_RUNTIME": "deep"}):
        s = Settings(_env_file=None)
        assert s.runtime == "deep"
```

- [ ] **Step 2: Run it → FAIL** (`AttributeError: 'Settings' object has no attribute 'runtime'`).

Run: `uv run pytest tests/test_settings_runtime_flag.py -v`

- [ ] **Step 3: Implement** — add to `Settings` (near the other feature flags, e.g. after `skip_registry_validation`):

```python
    # Chat execution runtime: "legacy" (agent_loop) | "deep" (Deep Agents lead).
    # Default legacy so the Deep Agents path is dormant until explicitly enabled.
    runtime: str = "legacy"  # JARVIS_RUNTIME
```

- [ ] **Step 4: Run it → PASS.**

- [ ] **Step 5: Full gate + ruff**: `uv run pytest tests/ --ignore=tests/e2e`; `uv run ruff check src/config/settings.py tests/test_settings_runtime_flag.py`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/config/settings.py backend/tests/test_settings_runtime_flag.py
git commit -m "feat(rebuild): JARVIS_RUNTIME flag (legacy|deep) for chat runtime selection (Step 6A)"
```

---

## Task 3: the prompt bridge (`system_blocks` → flat `system_prompt`)

**Files:**
- Create: `backend/src/deep_runtime/prompt_bridge.py`
- Test: `backend/tests/deep_runtime/test_prompt_bridge.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/deep_runtime/test_prompt_bridge.py`:

```python
"""Step 6A: flatten the chat path's Anthropic multi-block system prompt into the single
system_prompt string build_deep_agent expects, preserving text order and dropping
cache_control markers (langchain-anthropic manages caching itself)."""

from src.deep_runtime.prompt_bridge import flatten_system_blocks


def test_flatten_joins_text_blocks_in_order():
    blocks = [
        {"type": "text", "text": "SOUL"},
        {"type": "text", "text": "ROLE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "CONTEXT"},
    ]
    assert flatten_system_blocks(blocks) == "SOUL\n\nROLE\n\nCONTEXT"


def test_flatten_accepts_plain_string():
    assert flatten_system_blocks("already a string") == "already a string"


def test_flatten_ignores_non_text_blocks_and_empty():
    blocks = [{"type": "text", "text": "A"}, {"type": "image"}, {"type": "text", "text": ""}]
    assert flatten_system_blocks(blocks) == "A"
```

- [ ] **Step 2: Run it → FAIL** (module doesn't exist).

Run: `uv run pytest tests/deep_runtime/test_prompt_bridge.py -v`

- [ ] **Step 3: Implement** — create `backend/src/deep_runtime/prompt_bridge.py`:

```python
"""Bridge the chat path's Anthropic multi-block system prompt to the flat system_prompt
string build_deep_agent / create_deep_agent expects (Step 6A)."""

from typing import Any


def flatten_system_blocks(system_blocks: Any) -> str:
    """Join the ``text`` of Anthropic system content blocks into one string.

    The chat path builds ``system_blocks`` as a list of ``{"type": "text", "text": ...}``
    dicts (some with ``cache_control`` ephemeral markers). Deep Agents takes a flat
    ``system_prompt`` str, and langchain-anthropic manages prompt caching itself, so the
    cache_control markers are dropped. A plain string is returned unchanged.
    """
    if isinstance(system_blocks, str):
        return system_blocks
    parts = [
        b["text"]
        for b in system_blocks
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run it → PASS.**

- [ ] **Step 5: Full gate + ruff** on the two files.

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/prompt_bridge.py backend/tests/deep_runtime/test_prompt_bridge.py
git commit -m "feat(rebuild): prompt bridge flattens system_blocks for the deep runtime (Step 6A)"
```

---

## Task 4 (spike-gated): the stream adapter — langgraph events → the 7 SSE dicts

**Files:**
- Create: `backend/src/deep_runtime/stream_adapter.py`
- Test: `backend/tests/deep_runtime/test_stream_adapter.py`

> **Prerequisite:** Task 0's DECISION line (the `stream_mode` + telemetry mechanism). Implement the internal mapping per the spike; the *contract* below is fixed regardless.

- [ ] **Step 1: Write the failing contract test** — create `backend/tests/deep_runtime/test_stream_adapter.py`. It builds a deep agent over a **fake streaming model** (mirror the Task-0 probe's fake model + `echo` tool + `MemorySaver`), runs `stream_deep_agent_events(agent, input, config, agent_name="operator")`, collects the yielded dicts, and asserts the shape contract:

```python
"""Step 6A: stream_deep_agent_events must reproduce the exact SSE dict shapes that
AgentInvoker.call_agent_stream emits from LoopEvents, so agent_event_from_sse still types
them and the frozen web contract is preserved."""

# (imports: the fake streaming model + echo tool from spikes/deep_stream, MemorySaver,
#  create_deep_agent, and stream_deep_agent_events)

_ALLOWED_EVENTS = {
    "agent_start", "thinking", "text_delta", "tool_call",
    "tool_result", "agent_done", "error",
}
_REQUIRED_KEYS = {
    "agent_start": {"event", "agent", "model"},
    "thinking": {"event", "agent", "text", "is_thinking"},
    "text_delta": {"event", "agent", "text"},
    "tool_call": {"event", "agent", "tool", "input"},
    "tool_result": {"event", "agent", "tool", "result", "blocked", "latency_ms"},
    "agent_done": {
        "event", "agent", "text", "input_tokens", "output_tokens",
        "cache_creation_tokens", "cache_read_tokens", "tools_called",
        "latency_ms", "cost_usd",
    },
    "error": {"event", "agent", "code", "message", "correlation_id"},
}


async def test_adapter_emits_frozen_sse_shapes():
    frames = [f async for f in _run_adapter_over_fake_stream()]  # helper builds+streams
    assert frames, "adapter yielded nothing"
    # every frame is a known event with at least the required keys, agent stamped through
    for f in frames:
        assert f["event"] in _ALLOWED_EVENTS
        assert _REQUIRED_KEYS[f["event"]] <= set(f.keys())
        assert f["agent"] == "operator"
    # the fake stream produced at least one text_delta, one tool_call+tool_result pair,
    # and exactly one terminal agent_done
    kinds = [f["event"] for f in frames]
    assert "text_delta" in kinds
    assert kinds.count("tool_call") == kinds.count("tool_result") >= 1
    assert kinds.count("agent_done") == 1
    # every frame must round-trip through agent_event_from_sse without becoming a raw
    # pass-through error (proves downstream typing still recognizes them)
    from src.orchestrator.core_events import agent_event_from_sse
    for f in frames:
        typed = agent_event_from_sse(f, agent="operator")
        assert typed is not None


async def test_adapter_sanitizes_errors():
    """A raised model/tool error must surface ONLY as a sanitized error frame — never the
    raw exception text."""
    frames = [f async for f in _run_adapter_over_raising_stream("boom-secret-detail")]
    err = [f for f in frames if f["event"] == "error"]
    assert err, "no error frame emitted"
    for f in err:
        assert "boom-secret-detail" not in f["message"]
        assert _REQUIRED_KEYS["error"] <= set(f.keys())
```

(Write the `_run_adapter_over_fake_stream` / `_run_adapter_over_raising_stream` helpers in the test module, reusing the Task-0 fake-model construction. Verify `agent_event_from_sse`'s exact signature — `core_events.py:240-281` — and the sanitized-error helper names in `agent_invoker.py:210-222` so the adapter reuses them.)

- [ ] **Step 2: Run it → FAIL** (module doesn't exist).

- [ ] **Step 3: Implement `stream_deep_agent_events`** per Task 0's DECISION. Skeleton (fill the mapping from the spike):

```python
"""Step 6A: translate a compiled Deep Agent's langgraph stream into the exact SSE dict
shapes AgentInvoker.call_agent_stream emits, so the frozen web streaming contract holds
across the runtime cutover. The stream_mode + telemetry mechanism is fixed by the
2026-07-06-langgraph-stream-to-sse spike."""

import logging
import time
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


async def stream_deep_agent_events(
    agent: Any,          # CompiledStateGraph from build_deep_agent
    graph_input: Any,    # {"messages": [{"role": "user", "content": message}]}
    config: dict,        # {"configurable": {"thread_id": ...}}
    *,
    agent_name: str,
    model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Yield the 7 frozen SSE-shaped dicts (agent_start/text_delta/thinking/tool_call/
    tool_result/agent_done/error) — key-identical to call_agent_stream's LoopEvent path."""
    start = time.monotonic()
    yield {"event": "agent_start", "agent": agent_name, "model": model}
    try:
        # --- per the spike DECISION: e.g. stream_mode=["messages","updates"] ---
        async for chunk in agent.astream(graph_input, config, stream_mode=[...]):
            # map each chunk → text_delta / thinking / tool_call / tool_result dicts,
            # stamping "agent": agent_name. (See the spike doc for the exact chunk types
            # and the is_thinking discrimination.)
            ...
        # terminal telemetry (usage_metadata + cost + latency), per spike DECISION:
        yield {
            "event": "agent_done",
            "agent": agent_name,
            "text": ...,
            "input_tokens": ...,
            "output_tokens": ...,
            "cache_creation_tokens": ...,
            "cache_read_tokens": ...,
            "tools_called": ...,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "cost_usd": ...,
        }
    except Exception:
        # Sanitized error — reuse agent_invoker's generic code/message + a correlation id.
        # NEVER emit the raw exception (logged only).
        from src.orchestrator.agent_invoker import sanitized_error_frame  # or inline the helper
        logger.error("[deep_runtime] %s stream failed", agent_name, exc_info=True)
        yield sanitized_error_frame(agent_name)
```

(If `agent_invoker` has no reusable public sanitizer, extract the `_GENERIC_CODE`/`_GENERIC_MESSAGE`/`correlation_id` logic from `agent_invoker.py:210-222` into a small shared helper and call it from both — do this as part of this task, keeping the legacy behavior byte-identical.)

- [ ] **Step 4: Run it → PASS.** Iterate the mapping against the fake stream until all contract assertions hold. Do NOT loosen the test to fit a partial mapping — if a shape can't be produced, return to Task 0.

- [ ] **Step 5: Full gate + ruff** on the two files.

- [ ] **Step 6: Commit**

```bash
git add backend/src/deep_runtime/stream_adapter.py backend/tests/deep_runtime/test_stream_adapter.py
git commit -m "feat(rebuild): stream adapter maps deep-agent stream to the frozen chat SSE shapes (Step 6A)"
```

---

## Task 5: branch `call_agent_stream` on `JARVIS_RUNTIME`

**Files:**
- Modify: `backend/src/orchestrator/agent_invoker.py:135-235`
- Test: `backend/tests/test_agent_invoker_runtime_branch.py` (create)

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_agent_invoker_runtime_branch.py`. It builds an `AgentInvoker` (mock settings via `make_mock_settings`, patch `get_anthropic_client`), and asserts:
  1. With `settings.runtime == "legacy"` (default), `call_agent_stream` invokes `agent_loop` (patch `src.orchestrator.agent_invoker.agent_loop` and assert it was awaited; `stream_deep_agent_events` was NOT).
  2. With `settings.runtime == "deep"`, it calls `build_deep_agent` + `stream_deep_agent_events` (patch both) and NOT `agent_loop`, yielding the adapter's dicts through.

```python
"""Step 6A: call_agent_stream routes to agent_loop (legacy) or the deep-agent adapter
based on JARVIS_RUNTIME, without changing the yielded SSE dict shapes."""

# (imports + a helper to build AgentInvoker with make_mock_settings and a stub _agents
#  containing a read-only 'perceiver' SubAgent, tools_override=[] to skip tool resolution)

async def test_legacy_runtime_uses_agent_loop():
    from unittest.mock import AsyncMock, patch
    inv = _make_invoker(runtime="legacy")
    async def _fake_loop(**kw):
        from src.orchestrator.agent_loop import LoopDone
        yield LoopDone(agent="perceiver", text="ok")
    with patch("src.orchestrator.agent_invoker.agent_loop", _fake_loop) as _, \
         patch("src.orchestrator.agent_invoker.stream_deep_agent_events") as mock_deep:
        frames = [f async for f in inv.call_agent_stream("perceiver", message="hi",
                    user_id="u", workspace_id="ws", tools_override=[])]
    assert any(f["event"] == "agent_done" for f in frames)
    mock_deep.assert_not_called()


async def test_deep_runtime_uses_adapter():
    from unittest.mock import AsyncMock, patch
    inv = _make_invoker(runtime="deep")
    async def _fake_adapter(*a, **k):
        yield {"event": "agent_done", "agent": "perceiver", "text": "ok",
               "input_tokens": 1, "output_tokens": 1, "cache_creation_tokens": 0,
               "cache_read_tokens": 0, "tools_called": [], "latency_ms": 1, "cost_usd": 0.0}
    with patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()) as mock_build, \
         patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_adapter), \
         patch("src.orchestrator.agent_invoker.agent_loop") as mock_loop:
        frames = [f async for f in inv.call_agent_stream("perceiver", message="hi",
                    user_id="u", workspace_id="ws", tools_override=[])]
    assert any(f["event"] == "agent_done" for f in frames)
    mock_loop.assert_not_called()
    mock_build.assert_awaited()
```

(Adjust `_make_invoker` to the real `AgentInvoker.__init__` signature — read `agent_invoker.py` for its constructor deps; stub `_agents` with a minimal read-only `SubAgent` so the write-agent guard doesn't fire.)

- [ ] **Step 2: Run it → FAIL** (no branch exists; `deep` still calls `agent_loop`).

- [ ] **Step 3: Implement the branch** in `call_agent_stream`, after `system_blocks` is built and before the `agent_loop` loop. Import at top: `from src.config.settings import get_settings`, `from src.deep_runtime.agent_builder import build_deep_agent`, `from src.deep_runtime.stream_adapter import stream_deep_agent_events`, `from src.deep_runtime.prompt_bridge import flatten_system_blocks`, `from ulid import ULID` (thread_id), `from langgraph.checkpoint.memory import MemorySaver`. Then:

```python
        if get_settings().runtime == "deep":
            deep_agent = await build_deep_agent(
                agent,
                tools,
                workspace_id=workspace_id,
                db_factory=self._db_factory,
                system_prompt=flatten_system_blocks(system_blocks),
                checkpointer=MemorySaver(),
            )
            config = {"configurable": {"thread_id": f"chat_{ULID()}"}}
            graph_input = {"messages": [{"role": "user", "content": message}]}
            async for frame in stream_deep_agent_events(
                deep_agent, graph_input, config, agent_name=agent_name, model=model
            ):
                yield frame
            return
        # else: legacy agent_loop path (unchanged below)
```

(Place this so the existing `async for evt in agent_loop(...)` block is the `else`. Keep the legacy path byte-identical.)

- [ ] **Step 4: Run it → PASS.**

- [ ] **Step 5: Full gate + ruff.** Confirm NO existing chat/agent_invoker test regressed under the default (`legacy`) — the default path must be untouched.

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/agent_invoker.py backend/tests/test_agent_invoker_runtime_branch.py
git commit -m "feat(rebuild): call_agent_stream branches to the deep runtime behind JARVIS_RUNTIME (Step 6A)"
```

---

## Task 6: end-to-end SSE-shape parity (fake model, deep runtime)

**Files:**
- Test: `backend/tests/test_chat_deep_runtime_parity.py` (create)

- [ ] **Step 1a (PRIMARY — the reliable guarantee): seam-boundary round-trip.** For every frame the adapter emits over the Task-0 fake stream, assert the full downstream chain does not drop or choke on it: `typed = agent_event_from_sse(frame, agent="operator")` is not None, and `core_event_to_sse(typed)` returns a dict whose `"event"` name is in the frozen set from the current-state table (or `None` for the intentionally stream-dropped types). This proves the deep path's frames survive the exact pipeline the web client consumes, WITHOUT needing a real API or wiring a fake model through `build_chat_model`. This is the load-bearing test.

- [ ] **Step 1b (OPTIONAL integration): full `_process_core` turn.** If a fake model can be wired through `build_chat_model`→`create_deep_agent` cheaply, additionally drive one read turn through `_process_core` with `settings.runtime="deep"` and assert the SSE `event:` names match the legacy path for the same turn. If wiring proves invasive, SKIP this and rely on 1a — do not spend a task budget forcing a brittle integration harness.

- [ ] **Step 2: Run it → PASS.** (No production change; if it fails, the failure reveals a real seam/contract gap — fix the adapter/branch, not the test.)

- [ ] **Step 3: Full gate + ruff. Commit**

```bash
git add backend/tests/test_chat_deep_runtime_parity.py
git commit -m "test(rebuild): chat deep-runtime emits the frozen SSE shapes end-to-end (Step 6A)"
```

---

## Task 7: metrics + docs

**Files:**
- Modify: wherever runtime metrics live (grep `MetricsService` / prometheus counters) + `CLAUDE.md`

- [ ] **Step 1:** Add a counter/label distinguishing `runtime="legacy"` vs `"deep"` on chat turns (find the existing chat/turn metric; add a `runtime` label read from `get_settings().runtime`). Test the label is emitted.
- [ ] **Step 2:** Update `CLAUDE.md`'s "Two execution paths" + agent sections with a one-line note: chat can run on the Deep Agents runtime behind `JARVIS_RUNTIME=deep` (foundation; gate/kill-Operator land in 6B/6C). Correct the stale "multi-step chat → GraphExecutor" phrasing (chat executes inline; only scheduler-picked runs use GraphExecutor).
- [ ] **Step 3:** Full gate + ruff. Commit `chore(rebuild): JARVIS_RUNTIME metric label + doc note (Step 6A)`.

---

## Task 8 (OPTIONAL, live): real-model smoke behind the flag

**Files:** `backend/spikes/deep_stream/live_smoke.py` (create; not a pytest test)

- [ ] Only if `JARVIS_ANTHROPIC_API_KEY` is set. A runnable script that sets `JARVIS_RUNTIME=deep`, sends one real read message through `call_agent_stream("perceiver", …)`, and prints the frames — validating real `usage_metadata`/`cost_usd`/token counts flow through (the fake-model tests only prove structure). Document the output; do NOT gate CI on it. Commit under `spikes/`.

---

## Self-review checklist (run before dispatching implementers)

1. **Spec coverage:** 6A = checkpointer param (T1) + `JARVIS_RUNTIME` (T2) + prompt bridge (T3) + stream adapter (T0 spike → T4) + branch seam (T5) + parity (T6) + metrics/docs (T7). The gate (6B), write lock/kill-Operator/fast-path/trust-relocation (6C), and cognitive-agent collapse (Step 7) are explicitly OUT. ✅
2. **Placeholder scan:** Task 4's adapter body is intentionally spike-gated (the *contract* is fully pinned; the internal `stream_mode` mapping is the spike's deliverable) — this is a legitimate spike-first task, not a placeholder. All other tasks have complete verbatim code. ✅
3. **Type/name consistency:** the 7 dict shapes match `call_agent_stream`'s output exactly (verified against `agent_invoker.py:135-235`); `flatten_system_blocks`, `stream_deep_agent_events`, `checkpointer=` names are consistent across tasks. ✅
4. **No migrations / no DB writes** → `alembic check` stays drift-free. Default `runtime="legacy"` → zero behavior change until flipped. ✅

---

## Review strategy (for the executor)

- **Task 0 (spike)** — a single combined review confirming the decision doc's mapping is real (re-run the probe) before Task 4 builds on it.
- **Task 5 (branch seam)** — the one blast-radius task (touches the live chat entry) → **2-stage parallel review** (spec + quality) on the frozen commit; the quality reviewer must confirm the default `legacy` path is byte-unchanged and no existing chat test regresses.
- **Tasks 1/2/3/4/6/7** — single combined review each.
- **Final holistic review**: full gate green, `alembic check` drift-free, `JARVIS_RUNTIME=legacy` proven behavior-neutral, and the deep path proven to emit the frozen SSE shapes (adapter contract test is a real guard — confirm it fails if a shape key is dropped).
