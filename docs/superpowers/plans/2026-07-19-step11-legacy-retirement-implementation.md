# Step 11 — Legacy Runtime Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking.

**Goal:** Retire the legacy `agent_loop` runtime entirely so the Deep Agents runtime is the ONLY
runtime on all three surfaces (chat, perception, autonomous); re-home every raw-Anthropic-SDK caller
onto a unified LangChain model; remove all Bedrock machinery; collapse runtime selection + the 10B
control plane to one path.

**Architecture:** Subtractive-first. Introduce a leaf LangChain model layer (`src/llm/`), re-home the
12 shared-SDK consumers onto it behavior-preservingly, fix the worker/MCP dual-loop artifact, then
collapse runtime selection to deep-only and delete `agent_loop` + the raw client factory + the 10B
control plane + Bedrock. Everything lands on-branch `rebuild/first-principles`; rollback = `git revert`.

**Tech Stack:** Python 3.12, `langchain-anthropic` (`ChatAnthropic`, 1.4.6), `langchain-core` messages,
FastMCP internal tools, pytest (custom `asyncio.run` hook — NO pytest-asyncio), ruff.

**Spec:** `docs/superpowers/specs/2026-07-19-step11-legacy-retirement-design.md` (read §0 decisions +
§10 resolved open items first).

**Process invariants (every phase):**
- Full non-e2e gate green from a clean run before a phase closes: `uv run pytest tests/ --ignore=tests/e2e -q`
- `ruff check src/ tests/` + `ruff format src/ tests/` clean.
- Single alembic head `1a2770a28c39` unchanged (ZERO migrations this whole effort).
- Commit with `git commit -F <file>` (zsh backtick gotcha). NO `Co-Authored-By`.
- Main loop owns verify + commit + hot-file mutation synchronously; delegate grounding + parallel reviews.
- **NO push / merge / deploy.** STOP-and-ASK at any irreversible gate.

---

## Phase roadmap

| Phase | Title | Detail level | Gate |
|---|---|---|---|
| **1** | Unified LangChain model layer (`src/llm/`) | **Full (below)** | additive/dormant, byte-neutral |
| 2 | Re-home 12 shared-SDK consumers onto `UtilityLLM` | Task-level roadmap → JIT step-detail | behavior-preserving + characterization |
| 3 | Worker/MCP dual-loop fix (per-ToolExecutor internal server) | Task-level roadmap → JIT step-detail | dual-loop regression test |
| 4 | Collapse runtime selection + delete 10B (per-surface: chat→perception→autonomous) | Task-level roadmap → JIT step-detail | per-surface gate green |
| 5 | Delete `agent_loop` + raw client factory + Bedrock | Task-level roadmap → JIT step-detail | dead-patch cleanup, grep clean |
| 6 | Docs / CLAUDE.md R1 | Task-level roadmap → JIT step-detail | doc-policy compliant |

**Why Phase 1 is fully detailed and 2–6 are roadmapped:** the rebuild uses a plan-per-step cadence —
each phase's exact step-code is grounded against live code immediately before it executes (the
consumers' exact bodies, the test files' exact mock targets). Writing all ~737 test-migration steps
now would be speculative and rot. Phase 1 is the foundation, fully specifiable today, and everything
downstream depends on it — so it is written in full. Each later phase gets its own detailed plan doc
(same directory, `-phaseN` suffix) produced JIT before execution.

---

## PHASE 1 — Unified LangChain model layer (`src/llm/`)

**What & why:** Create a leaf package `src/llm/` that both `services/*` and `deep_runtime/*` import
downward (it must sit below both — putting it under `deep_runtime/` would force a `services →
deep_runtime` upward dependency). It holds ONE low-level constructor `build_langchain_model` (extracted
from the logic already in `deep_runtime/model_factory.build_chat_model`), a `build_utility_model`
(thinking-free, tier-based) for plain completions, and a `complete_text` seam the 12 consumers call in
Phase 2. Refactor `build_chat_model` to delegate to the shared constructor, proven byte-equivalent by
the existing `tests/deep_runtime/test_model_factory.py` still passing. Pure Claude API — no Bedrock.

**File structure:**
- Create: `src/llm/__init__.py`
- Create: `src/llm/model_factory.py` — `build_langchain_model`, `build_utility_model`, `_resolve_utility_model_id`
- Create: `src/llm/utility.py` — `complete_text` (the consumer seam) + message assembly
- Modify: `src/deep_runtime/model_factory.py` — `build_chat_model` delegates to `build_langchain_model`
- Test: `tests/llm/__init__.py`, `tests/llm/test_model_factory.py`, `tests/llm/test_utility.py`

**LangChain attribute facts (from `tests/deep_runtime/test_model_factory.py`, langchain-anthropic 1.4.6):**
`.model` = model id (no `.model_name`); `.thinking` = dict|None; `.effort` = str|None; `.temperature`
= float|None (None dropped from request body); `.max_tokens` = int.

---

### Task 1: Low-level `build_langchain_model` constructor

**Files:**
- Create: `src/llm/__init__.py` (empty)
- Create: `src/llm/model_factory.py`
- Test: `tests/llm/__init__.py` (empty), `tests/llm/test_model_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_model_factory.py
"""Unit tests for src.llm.model_factory — no live API calls; inspect ChatAnthropic attrs."""
from __future__ import annotations

from src.llm.model_factory import build_langchain_model


def test_plain_model_only_model_and_max_tokens():
    m = build_langchain_model("claude-haiku-4-5-20251001", max_tokens=256)
    assert m.model == "claude-haiku-4-5-20251001"
    assert m.max_tokens == 256
    assert m.thinking is None
    assert m.effort is None
    assert m.temperature is None  # unset → omitted from request body


def test_temperature_forwarded_when_set():
    m = build_langchain_model("claude-sonnet-4-6", max_tokens=512, temperature=0.0)
    assert m.temperature == 0.0


def test_thinking_and_effort_forwarded():
    m = build_langchain_model(
        "claude-opus-4-8",
        max_tokens=8192,
        thinking={"type": "adaptive", "display": "summarized"},
        effort="high",
    )
    assert m.thinking == {"type": "adaptive", "display": "summarized"}
    assert m.effort == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/llm/test_model_factory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.llm'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/llm/model_factory.py
"""Provider-simple LangChain model construction (pure Claude API, no Bedrock).

The SINGLE place that builds a ``ChatAnthropic``. Both the deep-agent path
(``deep_runtime.model_factory.build_chat_model``) and utility completions
(``build_utility_model`` → ``src.llm.utility.complete_text``) funnel through
``build_langchain_model`` so the api-key + param surface lives in one leaf,
importable downward by both services and the deep runtime.
"""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from src.config.models import MODEL_TIERS
from src.config.settings import get_settings


def build_langchain_model(
    model_id: str,
    *,
    max_tokens: int,
    temperature: float | None = None,
    thinking: dict | None = None,
    effort: str | None = None,
) -> ChatAnthropic:
    """Construct a direct-Anthropic ``ChatAnthropic``.

    Only non-None optional params are forwarded (a None temperature/thinking/effort
    is omitted from the request body). The api key is passed explicitly because
    LangChain otherwise reads the unprefixed ``ANTHROPIC_API_KEY`` which Jarvis never
    sets (it uses ``JARVIS_ANTHROPIC_API_KEY`` → ``settings.anthropic_api_key``).
    """
    kwargs: dict = {"model": model_id, "max_tokens": max_tokens}
    api_key = get_settings().anthropic_api_key
    if api_key:
        kwargs["api_key"] = api_key
    if thinking is not None:
        kwargs["thinking"] = thinking
    if effort is not None:
        kwargs["effort"] = effort
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/llm/test_model_factory.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/llm/__init__.py src/llm/model_factory.py tests/llm/__init__.py tests/llm/test_model_factory.py
git commit -F <msgfile>   # feat(step11): src.llm.build_langchain_model — single pure-Claude constructor
```

---

### Task 2: `build_utility_model` (tier-based, thinking-free)

**Files:**
- Modify: `src/llm/model_factory.py`
- Test: `tests/llm/test_model_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/llm/test_model_factory.py
from src.llm.model_factory import build_utility_model


def test_utility_haiku_tier_direct_id():
    m = build_utility_model("haiku", max_tokens=256)
    assert m.model == "claude-haiku-4-5-20251001"
    assert m.max_tokens == 256
    assert m.thinking is None  # utility calls never think


def test_utility_resolved_tier_uses_settings_anthropic_model(monkeypatch):
    # "resolved" tier honors the configured direct model (JARVIS_ANTHROPIC_MODEL override).
    from src.config import settings as settings_mod
    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("JARVIS_ANTHROPIC_MODEL", "claude-sonnet-4-6")
    m = build_utility_model("resolved", max_tokens=512, temperature=0.0)
    assert m.model == "claude-sonnet-4-6"
    assert m.temperature == 0.0
    settings_mod.get_settings.cache_clear()
```

> NOTE at build time: confirm the actual `get_settings` cache-reset idiom used elsewhere in the suite
> (`make_mock_settings` in `tests/conftest.py`); if `get_settings` is not `lru_cache`, drop the
> `cache_clear()` calls and use the conftest settings helper. This is the one build-time verification.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/llm/test_model_factory.py::test_utility_haiku_tier_direct_id -q`
Expected: FAIL — `ImportError: cannot import name 'build_utility_model'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/llm/model_factory.py

def _resolve_utility_model_id(tier: str) -> str:
    """Map a utility tier to a DIRECT Anthropic model id (no Bedrock).

    - ``"haiku"`` → the Haiku tier id.
    - anything else (``"resolved"``/``"sonnet"``) → the configured direct model
      (``settings.anthropic_model``), preserving the ``JARVIS_ANTHROPIC_MODEL`` override
      that the raw-SDK consumers honored via ``resolved_model``.
    """
    if tier == "haiku":
        return MODEL_TIERS["haiku"]
    return get_settings().anthropic_model


def build_utility_model(
    tier: str, *, max_tokens: int, temperature: float | None = None
) -> ChatAnthropic:
    """Build a thinking-free ``ChatAnthropic`` for a plain utility completion."""
    return build_langchain_model(
        _resolve_utility_model_id(tier), max_tokens=max_tokens, temperature=temperature
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/llm/test_model_factory.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/llm/model_factory.py tests/llm/test_model_factory.py
git commit -F <msgfile>   # feat(step11): build_utility_model — tier→direct-id, thinking-free
```

---

### Task 3: `complete_text` — the consumer seam

**Files:**
- Create: `src/llm/utility.py`
- Test: `tests/llm/test_utility.py`

**Contract (from spec §3 / grounding):** consumers need `tier`, optional `prefill` (only verifier),
optional `system` (str | content-block list | None — relevance passes none), unset-vs-`0` temperature,
and raw text back (each consumer keeps its own `parse_llm_json` + fallback). No tools, no streaming.

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_utility.py
"""Unit tests for src.llm.utility.complete_text — ChatAnthropic.ainvoke is mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.llm.utility import complete_text


def _mock_model(return_text: str):
    model = AsyncMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content=return_text))
    return model


async def test_complete_text_returns_model_content():
    model = _mock_model('{"ok": true}')
    with patch("src.llm.utility.build_utility_model", return_value=model):
        out = await complete_text(system="sys", user="hello", tier="haiku", max_tokens=256)
    assert out == '{"ok": true}'
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[0], SystemMessage) and msgs[0].content == "sys"
    assert isinstance(msgs[1], HumanMessage) and msgs[1].content == "hello"


async def test_complete_text_omits_system_when_none():
    model = _mock_model("summary text")
    with patch("src.llm.utility.build_utility_model", return_value=model):
        out = await complete_text(system=None, user="u", tier="haiku", max_tokens=300)
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[0], HumanMessage)  # no SystemMessage
    assert out == "summary text"


async def test_complete_text_appends_prefill_as_assistant():
    model = _mock_model('"passed": true}')  # continuation after the "{" prefill
    with patch("src.llm.utility.build_utility_model", return_value=model):
        await complete_text(system="s", user="u", tier="resolved", max_tokens=256, prefill="{")
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[-1], AIMessage) and msgs[-1].content == "{"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/llm/test_utility.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.llm.utility'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/llm/utility.py
"""UtilityLLM — the single seam for plain (non-streaming, non-tool) LLM completions.

The 12 shared-machinery consumers (risk_assessor, relevance_assessor, event_processor,
world_model, memory extraction, verifier, presenter, context summarize, intent_classifier,
governor critique) call ``complete_text`` instead of the raw Anthropic client. Each keeps its
OWN ``llm_utils.parse_llm_json`` + domain fallback — this seam only fetches text.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.llm.model_factory import build_utility_model


async def complete_text(
    *,
    system: str | list | None,
    user: str,
    tier: str,
    max_tokens: int,
    temperature: float | None = None,
    prefill: str | None = None,
) -> str:
    """Run one plain completion and return the assistant's text.

    - ``system``: plain string, a list of content blocks, or ``None`` (omitted).
    - ``prefill``: optional assistant partial (e.g. ``"{"``); the returned text is the
      CONTINUATION (does not include the prefill) — callers re-prepend if needed,
      matching the raw-SDK prefill behavior.
    """
    model = build_utility_model(tier, max_tokens=max_tokens, temperature=temperature)
    messages: list = []
    if system is not None:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=user))
    if prefill is not None:
        messages.append(AIMessage(content=prefill))
    response = await model.ainvoke(messages)
    content = response.content
    if isinstance(content, str):
        return content
    # Defensive: a block list (utility calls have no thinking, so this is rare) — join text blocks.
    return "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/llm/test_utility.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/llm/utility.py tests/llm/test_utility.py
git commit -F <msgfile>   # feat(step11): UtilityLLM.complete_text — the shared-consumer seam
```

---

### Task 4: Refactor deep `build_chat_model` to delegate (byte-equivalent)

**Files:**
- Modify: `src/deep_runtime/model_factory.py:29-79` (`build_chat_model` body)
- Test: `tests/deep_runtime/test_model_factory.py` (EXISTING — must still pass unchanged = the equivalence proof)

**Goal:** `build_chat_model` keeps computing the thinking/effort/temperature branches (its logic is
correct and tested), but hands the final `ChatAnthropic` construction to `build_langchain_model`,
removing the duplicated api-key handling. The 7 existing tests are the equivalence oracle.

- [ ] **Step 1: Run the existing tests to capture the green baseline**

Run: `cd backend && uv run pytest tests/deep_runtime/test_model_factory.py -q`
Expected: PASS (7 passed) — this is the behavior we must preserve exactly.

- [ ] **Step 2: Refactor `build_chat_model` to delegate**

Replace the body of `build_chat_model` (keep the docstring) so it computes the params then calls
`build_langchain_model`:

```python
# src/deep_runtime/model_factory.py  (imports: add)
from src.llm.model_factory import build_langchain_model

# ... inside build_chat_model, replace the kwargs-assembly + `return ChatAnthropic(**kwargs)` with:
    model_id = MODEL_TIER_IDS.get(agent.model_tier, MODEL_TIER_IDS["sonnet"])
    is_adaptive = requires_adaptive_thinking(model_id)
    thinking_cfg = agent.thinking

    thinking: dict | None = None
    effort: str | None = None
    temperature: float | None = None

    if thinking_cfg.enabled:
        if is_adaptive:
            thinking = {"type": "adaptive", "display": "summarized"}
            effort = effort_for_budget(thinking_cfg.budget_tokens)
        else:
            budget = thinking_cfg.budget_tokens
            if budget >= agent.max_tokens:
                budget = agent.max_tokens - 1
            thinking = {"type": "enabled", "budget_tokens": budget}
            temperature = 1
    else:
        if not is_adaptive:
            temperature = agent.temperature

    return build_langchain_model(
        model_id,
        max_tokens=agent.max_tokens,
        temperature=temperature,
        thinking=thinking,
        effort=effort,
    )
```

Then remove the now-unused `get_settings` import + the `_api_key` block from this module (api-key
handling now lives in `build_langchain_model`). Keep `MODEL_TIER_IDS`, `requires_adaptive_thinking`,
`effort_for_budget` imports.

- [ ] **Step 3: Run the existing deep tests — must still pass unchanged**

Run: `cd backend && uv run pytest tests/deep_runtime/test_model_factory.py tests/test_model_factory_api_key.py -q`
Expected: PASS (7 + 3) — byte-equivalent construction proven.

- [ ] **Step 4: Full gate + ruff**

Run: `cd backend && uv run pytest tests/ --ignore=tests/e2e -q && ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: full suite green (baseline + 8 new `tests/llm/` tests), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/deep_runtime/model_factory.py
git commit -F <msgfile>   # refactor(step11): build_chat_model delegates to src.llm.build_langchain_model
```

---

### Phase 1 closeout
- [ ] Full non-e2e gate green from clean run; ruff clean; single head `1a2770a28c39`.
- [ ] Independent review (parallel security + quality subagents) of `src/llm/` — confirm additive,
      byte-neutral on live paths (nothing calls `complete_text`/`build_utility_model` yet), correct
      layering (leaf below services + deep_runtime).
- [ ] Update memory topic file with Phase-1 SHAs. STOP — hand off Phase 2 planning.

---

## PHASE 2 — Re-home the 12 shared-SDK consumers (roadmap → JIT step-detail)

**Approach:** one behavior-preserving commit per consumer group. For each consumer: replace
`await client.messages.create(model=..., max_tokens=N, system=S, messages=[{"role":"user","content":U}])`
+ `response.content[0].text` with `await complete_text(system=S, user=U, tier=T, max_tokens=N,
temperature=..., prefill=...)`; **keep the existing `parse_llm_json(...)` + fallback untouched.** Drop
the now-unused `client`/`get_anthropic_client` from that consumer's signature/construction. Update the
consumer's own test to mock `complete_text` at its import site instead of the raw client
(SWAP-MOCK, ~167 tests). Characterization test asserts identical parsed output for identical model text.

**Tier mapping per consumer** (from spec §3 table): `haiku` → risk_assessor, relevance_assessor,
context_assembler `_summarize_history` (temp=0), intent_classifier (temp=0), governor_delegate_critique;
`resolved` → event_processor (×2), world_model (×2), memory extraction (×2), contradictions, verifier
(prefill `"{"`), presenter (×2), step_runner `minimal_claude_action`.

**Task groups (each = its own commit + gate):**
1. Governance: `risk_assessor.assess_risk`, `governor_delegate_critique._safe_critique` (load-bearing —
   3 deep middlewares depend on risk_assessor; do first + review hardest).
2. Perception/ingest: `event_processor` (×2), `relevance_assessor`, `world_model` (×2).
3. Memory: `memory_service/extraction` (×2), `contradictions`.
4. Execution/verify: `step_runner.minimal_claude_action`, `verifier._llm_judge` (prefill).
5. Context/presentation: `context_assembler._summarize_history` (text-only, no parse), `presenter` (×2).
6. Chat routing: `intent_classifier.classify_intent`.

**JIT grounding before Phase 2:** re-read each consumer's exact `create()` + parse lines and its test's
exact mock target (the sizing scout named them). Note `relevance_assessor` passes NO `system`;
`context_assembler`/`intent_classifier` pass a `[{"type":"text",...}]` block list; verifier re-prepends
`"{"`. `risk_assessor.assess_risk` currently takes `client: Any` + `get_or_assess_risk` — thread the
signature change through both deep + legacy callers (legacy callers vanish in Phase 4).

**Gate:** full suite green after each group; `agent_loop` becomes the ONLY raw-SDK caller left at
Phase-2 close (grep `get_anthropic_client` usage → only construction sites + `agent_loop`).

---

## PHASE 3 — Worker/MCP dual-loop fix (roadmap → JIT step-detail)

**Approach (spec §4 + §10.4):** replace the process-global `jarvis_tools = FastMCP(...)`
(`src/tools/server.py:14`) usage in `ToolExecutor` with a per-instance server. Add
`build_internal_mcp_server()` factory that reproduces the 3 mount + `set_up_component_manager` lines;
each `ToolExecutor` builds + enters its own `Client(own_server)` on its own loop (`tool_executor.py:279-284`).

**Tasks:**
1. `build_internal_mcp_server()` factory in `src/tools/server.py` (keep the module-global for the
   non-test script + backwards compat; factory returns a fresh mounted server).
2. `ToolExecutor` uses `build_internal_mcp_server()` for its `_internal_client` instead of the global.
3. **Dual-loop regression test:** construct two `ToolExecutor`s on two `asyncio` loops, call an internal
   tool from the second, assert no "Future attached to a different loop" (the D4 failure).
4. **Verify caveat (§10.4):** confirm `set_up_component_manager` runtime tool enable/disable state is
   NOT expected to propagate across the API + worker ToolExecutors (grep component-manager mutators;
   if shared state is expected, the factory shares the component manager while isolating the transport).

**Gate:** dual-loop test passes; full suite green; internal-tool calls from the worker path unblocked.
(Independent of runtime — may execute before Phase 2 if convenient.)

---

## PHASE 4 — Collapse runtime selection + delete 10B (per-surface; roadmap → JIT step-detail)

**Approach:** subtractive, **per surface** so the suite never goes broadly red. Before touching
`agent_loop` itself, re-home the one hard code edge: move `CancellationRequested` from `agent_loop.py:241`
to `src/services/execution_support.py` (dag_runner already imports it; agent_loop re-imports it back
until Phase 5). Then per surface, delete the `runtime == "deep"` branch's legacy `else` arm and rewrite
that surface's REWRITE-TO-DEEP tests.

**Task order:**
1. `CancellationRequested` → `execution_support.py`; `dag_runner` imports from new home; `agent_loop`
   re-imports it. (grep confirms only these users.) Commit.
2. **Chat surface:** collapse `agent_invoker.call_agent_stream` to deep-only (delete the `agent_loop`
   arm `:823-893`); `chat_processor` runtime branch → constant; rewrite `test_stream_deep_lead.py`,
   `test_chat_plan_event.py` (`LoopError` sanitization), `test_fix6_orchestrator_error_handling.py`,
   `test_context_jit_wiring.py`. **Keep `deep_single_lead`/`chat_planless`** (product shapes).
3. **Perception surface:** collapse `agent_invoker.call_agent` (`:1755` arm); rewrite
   `test_perception_deep_branch.py`.
4. **Autonomous surface:** collapse `step_runner` (`run_step_via_agent_loop`, `minimal_claude_action`
   fallback), `graph_executor` (3 `runtime == "deep"` gates + `_should_jit` + `_run_step_via_agent_loop`
   facade); rewrite `test_step_runner_deep_executor.py`, `test_graph_executor.py` (the one
   `agent_loop`-driving test), `test_execution_durability.py` (re-establish cancellation/gauges on the
   deep executor), `test_run_reconcile.py`, `test_autonomous_deep_e2e.py`.
5. **Delete the 10B control plane** (all now-dead): `runtime_gate.py` (inline `effective_runtime →
   "deep"` then delete), `runtime_breaker.py`, `scheduler/runtime_rollback_tick.py`,
   `api/routes_admin_runtime.py` (+ unwire its router include), `orchestrator/shadow_runner.py` +
   `run_shadow_turn` + `ShadowToolExecutor`/`_IntentRecordingShadowExecutor`/`DivergenceComparator`.
   DELETE tests: `test_runtime_gate.py`, `test_runtime_gate_static_deep.py`, `test_runtime_breaker*`,
   `test_runtime_override_escape_hatch.py`, `test_runtime_rollback_watcher.py`, `test_shadow_runner.py`,
   `test_divergence_comparator.py`, `test_settings_runtime_flag.py`, `test_agent_invoker_runtime_branch.py`,
   `test_agent_invoker_runtime_metric.py`.
6. Strip `runtime`/`JARVIS_RUNTIME`, `shadow_sample_rate`, `rollback_*_threshold` from settings; drop
   the "else legacy" arm at `app.py:76` (open pool unconditionally), `routes_health.py:481`,
   `checkpoint_reaper_tick.py:43`, `run.py:135`.

**Gate:** full suite green after EACH surface; grep for residual `effective_runtime`/`runtime == "deep"`
→ zero (outside deleted files).

---

## PHASE 5 — Delete `agent_loop` + raw client factory + Bedrock (roadmap → JIT step-detail)

**Tasks:**
1. Remove `agent_loop` imports from `agent_invoker`, `step_runner` (edges now dead after Phase 4);
   delete `src/orchestrator/agent_loop.py` + `tests/test_agent_loop.py`.
2. Delete `get_anthropic_client`/`close_anthropic_client`/`_anthropic_client` (`settings.py:338-358`)
   + the `AsyncAnthropicBedrock`/`AsyncAnthropic` construction; remove the ~350 defensive
   `@patch("...get_anthropic_client")` stubbers (mechanical: the symbol is gone → drop the patch /
   remove the now-unused `self._client` construction they guarded).
3. **Bedrock removal:** delete `settings.use_bedrock` + `JARVIS_USE_BEDROCK`, `BEDROCK_MODEL_TIERS`
   (`config/models.py:22-26`), the Bedrock arm of `get_haiku_model` + `resolved_model`, the
   `use_bedrock`/`bedrock_region` fields in `make_mock_settings` (`conftest.py:61-62`); delete
   Bedrock-only tests (`test_budget.py::test_bedrock_pricing`, ~2 in `test_secret_validation.py`).
4. Fix stale `deep_runtime/*` "mirrors agent_loop" doc comments (`_thinking.py`, `model_factory.py`,
   `middleware/{unavailable_server,capability_scope}.py`).

**Gate:** `rg 'agent_loop|get_anthropic_client|AsyncAnthropic|use_bedrock|Bedrock' src/ tests/` →
zero (except `langchain_anthropic`); full suite green from clean run.

---

## PHASE 6 — Docs / CLAUDE.md R1 (roadmap → JIT step-detail)

**Tasks:** rewrite CLAUDE.md "Two execution paths" → one deep runtime gated at action-time by
`permission_mode`; rewrite the "Runtime Resilience (agent_loop.py)" section for the deep runtime;
remove legacy / 10B / shadow / Bedrock references across CLAUDE.md + `docs/engineering-standards.md`;
mark the Step-11 spec BUILT with a §build-record. Doc-policy compliant (no volatile counts). Update
memory topic file.

**Gate:** doc review; no code change.

---

## Self-review notes (author)
- **Spec coverage:** §1 scope → Phases 1–6; §2 model layer → Phase 1; §3 re-homing → Phase 2; §4
  worker/MCP → Phase 3; §5 deletion inventory → Phases 4–5; §7 test migration → distributed across 2/4/5;
  §9 deploy consequence → recorded (Phase-F, out of this effort). All covered.
- **No placeholders in Phase 1** (full code). Phases 2–6 are explicitly a roadmap (each gets a JIT
  detailed plan doc before execution — stated up front, not a hidden TODO).
- **Type consistency:** `build_langchain_model` / `build_utility_model` / `complete_text` signatures are
  consistent between Task 1–3 definitions and the Phase-2 usage description; `complete_text` kwargs
  (`system`/`user`/`tier`/`max_tokens`/`temperature`/`prefill`) match the spec §3 contract.
