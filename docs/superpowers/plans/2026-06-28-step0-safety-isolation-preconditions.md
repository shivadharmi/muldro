# Step 0 — Safety + Isolation Preconditions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the blocking, no-behavior-change preconditions for the first-principles rebuild — wire capability-scope enforcement into the Deep Agents builder, close one cross-tenant leak, add A2UI schema versioning, delete dead code, and run three substrate spikes — so later steps build on a safe, known foundation.

**Architecture:** Pure additions + deletions + one query fix on the existing codebase. The live runtime (`agent_loop`) is untouched; all wiring targets the not-yet-live `deep_runtime` scaffold and contract/query fixes. Three investigation spikes (interrupt mechanism, `AsyncPostgresSaver`, prompt caching) produce written findings that gate later steps — they ship no production behavior change.

**Tech Stack:** Python 3.12, pytest (+ async via root `conftest.py` `pytest_pyfunc_call`), SQLAlchemy 2 / asyncpg, Pydantic v2, ruff, LangChain/LangGraph + `deepagents`, alembic.

**Source spec:** [`docs/superpowers/specs/2026-06-28-first-principles-rebuild-design.md`](../specs/2026-06-28-first-principles-rebuild-design.md) §6 Step 0, §4.3, §4.7, §4.8, §4.10, §5.

**Out of scope (own follow-on plans):**
- **`agents.workspace_id` tenancy (spec §6.1 / agentic-redesign "Step A").** Carved out: it needs a two-alembic-head merge, a built-in-namespace schema decision (NULL = built-in vs sentinel workspace), and converts `JarvisOrchestrator._agents` from a process-global singleton to a per-workspace cache — too large and too behavior-changing for Step 0.
- Everything in Steps 1–10.

**Run all commands from `backend/` with the venv active:** `cd backend && source .venv/bin/activate`.

**Pre-flight (run once before starting):**
```bash
cd backend && source .venv/bin/activate && pytest tests/deep_runtime/ -q
```
Expected: PASS (establishes the green baseline for the files this plan touches).

---

## Task 1: Delete the dead `backend/src/workflows/` package

The package hardcodes `WorkflowStep` sequences (banned by CLAUDE.md) and has **zero** production importers; the scheduler/presenter `meeting_prep` paths are independent (they call the orchestrator/presenter, not `src.workflows`) and survive.

**Files:**
- Delete: `backend/src/workflows/__init__.py`, `context.py`, `daily_briefing.py`, `inbox_triage.py`, `meeting_prep.py`, `research_agent.py`, `workflow_registry.py`
- Delete: `backend/tests/test_meeting_prep.py`

- [ ] **Step 1: Run the deletion guard — must return zero**

Run:
```bash
grep -rn -E "src\.workflows|from src\.workflows|workflow_registry|\bWorkflowRegistry\b|\bWorkflowContext\b|\bWorkflowStep\b" \
  src/ tests/ --include="*.py" \
  | grep -v "src/workflows/" \
  | grep -v "tests/test_meeting_prep.py" || echo "GUARD_CLEAN"
```
Expected: prints `GUARD_CLEAN` (no live importer outside the package + its one test).

- [ ] **Step 2: Confirm no string-dispatch into the package (sanity)**

Run:
```bash
grep -rn -E "\"meeting_prep\"|\"daily_briefing\"|\"inbox_triage\"|\"research_agent\"" src/services/scheduler/ src/services/presenter.py
```
Expected: hits are the **orchestrator/presenter** path (`schedule_dispatch.py` calls `orchestrator.process_message`; `presenter.py` `VIEW_TYPE_MAP`/`generate_meeting_prep`). None import `src.workflows`. These stay — do not touch them.

- [ ] **Step 3: Delete the package and its test**

Run:
```bash
git rm backend/src/workflows/__init__.py backend/src/workflows/context.py \
  backend/src/workflows/daily_briefing.py backend/src/workflows/inbox_triage.py \
  backend/src/workflows/meeting_prep.py backend/src/workflows/research_agent.py \
  backend/src/workflows/workflow_registry.py backend/tests/test_meeting_prep.py
```

- [ ] **Step 4: Verify the suite is still green + lint clean**

Run:
```bash
ruff check src/ && pytest tests/ -q
```
Expected: ruff clean; full suite PASS (no collection error from the removed module).

- [ ] **Step 5: Commit**

```bash
git commit -m "chore(rebuild): delete dead src/workflows package (Step 0)

Hardcoded WorkflowStep sequences (banned by CLAUDE.md), zero production
importers. scheduler/presenter meeting_prep paths are independent and survive."
```

---

## Task 2: Delete the dead context-budget scaffold

`token_estimator.py` and `context_builder.to_prompt_compressed` (+ its only-callers `SECTION_STRATEGIES`, `_summarize_section`) have **zero** callers. The live `ContextBuilder.to_prompt` stays.

**Files:**
- Delete: `backend/src/orchestrator/token_estimator.py` (whole file)
- Modify: `backend/src/services/context_builder.py` (remove the `~409–494` dead block: the `# Haiku-summarized context compression` header → `SECTION_STRATEGIES` → `to_prompt_compressed` → `_summarize_section`, ending immediately before `def _rerank_by_relevance`)

- [ ] **Step 1: Run the deletion guard — must return zero**

Run:
```bash
grep -rn -E "token_estimator|MAX_CONTEXT_UTILIZATION|estimate_message_tokens|estimate_tokens|MODEL_CONTEXT_WINDOWS|to_prompt_compressed|_summarize_section|SECTION_STRATEGIES" \
  src/ tests/ --include="*.py" \
  | grep -v "src/orchestrator/token_estimator.py" \
  | grep -v "src/services/context_builder.py" || echo "GUARD_CLEAN"
```
Expected: prints `GUARD_CLEAN`.

- [ ] **Step 2: Delete `token_estimator.py`**

Run:
```bash
git rm backend/src/orchestrator/token_estimator.py
```

- [ ] **Step 3: Remove the dead block from `context_builder.py`**

Open `backend/src/services/context_builder.py`. Delete the contiguous block that starts at the `# Haiku-summarized context compression` comment header (around line 409), spanning `SECTION_STRATEGIES`, `async def to_prompt_compressed(...)`, and module-level `async def _summarize_section(...)`, ending on the line immediately **before** `def _rerank_by_relevance` (around line 497). Do **not** touch `def to_prompt` (around line 325) or `_rerank_by_relevance`.

- [ ] **Step 4: Auto-fix any now-unused imports, then verify**

Run:
```bash
ruff check src/services/context_builder.py --fix && ruff check src/ && pytest tests/ -q
```
Expected: ruff clean (a local `import asyncio` lived inside the deleted block, so no top-level import should dangle — `--fix` removes any that do); full suite PASS.

- [ ] **Step 5: Commit**

```bash
git commit -am "chore(rebuild): delete dead context-budget scaffold (Step 0)

token_estimator.py + to_prompt_compressed/SECTION_STRATEGIES/_summarize_section
have zero callers; spec adopts SummarizationMiddleware instead. Live to_prompt kept."
```

---

## Task 3: Close the cross-tenant alias leak in the world model (isolation HOLE 1)

`find_entity` and `_find_by_name_or_alias` filter `Entity.workspace_id` on the outer query but their `EntityAlias` subqueries are **unscoped** — a workspace-A lookup can match a workspace-B alias. `EntityAlias.workspace_id` already exists (NOT NULL), so this is a query fix, no migration. We extract a tiny statement-builder so the test exercises production code (RED before the fix).

**Files:**
- Modify: `backend/src/services/world_model.py` (`find_entity` ~445, `_find_by_name_or_alias` ~476 — extract `_find_entity_stmt` + scope both alias subqueries)
- Test: `backend/tests/test_world_model_alias_isolation.py` (create)

- [ ] **Step 1: Extract the statement builder (pure refactor — preserve the CURRENT unscoped subquery)**

In `backend/src/services/world_model.py`, add this module-level helper that reproduces the **current** (still-buggy, unscoped) query exactly — no behavior change yet — and route `find_entity` through it:

```python
def _find_entity_stmt(user_id: str, query: str, workspace_id: str):
    """Build the find_entity SELECT. Extracted so isolation tests compile it.

    NOTE: the EntityAlias subquery is intentionally left UNSCOPED here to match
    current behavior; Step 4 adds the workspace_id filter that the test demands.
    """
    pattern = f"%{query}%"
    return (
        select(Entity)
        .where(
            Entity.user_id == user_id,
            Entity.workspace_id == workspace_id,
            or_(
                Entity.canonical_name.ilike(pattern),
                Entity.entity_id.in_(
                    select(EntityAlias.entity_id).where(EntityAlias.alias.ilike(pattern))
                ),
            ),
        )
        .order_by(Entity.importance_score.desc())
    )
```

Then change `find_entity`'s body to use it (serialization unchanged):

```python
    async def find_entity(self, user_id: str, query: str, workspace_id: str = "") -> list[dict]:
        """Search entities by name or alias. Ordered by importance."""
        result = await self._db.execute(_find_entity_stmt(user_id, query, workspace_id))
        entities = result.scalars().all()
        return [ ... ]  # leave the existing serialization unchanged
```

- [ ] **Step 2: Verify the refactor is behavior-neutral**

Run: `pytest tests/ -q -k "world_model or entity"`
Expected: PASS (pure extraction; no semantics changed yet).

- [ ] **Step 3: Write the failing isolation test**

Create `backend/tests/test_world_model_alias_isolation.py`:

```python
"""Cross-tenant isolation: the find_entity alias subquery must be workspace-scoped.
Compiled-SQL assertion against the production statement builder (no real DB needed;
Postgres-only column types block SQLite create_all)."""

from sqlalchemy.dialects import postgresql

from src.services.world_model import _find_entity_stmt


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def test_find_entity_alias_subquery_is_workspace_scoped():
    sql = _compile(_find_entity_stmt("usr_1", "acme", "ws_A"))
    assert "entity_aliases.workspace_id = 'ws_a'" in sql, (
        "find_entity alias subquery is NOT workspace-scoped — cross-tenant leak"
    )
```

- [ ] **Step 4: Run to verify it FAILS (genuine RED against production code)**

Run: `pytest tests/test_world_model_alias_isolation.py -v`
Expected: FAIL — the compiled SQL from `_find_entity_stmt` lacks `entity_aliases.workspace_id` (the production helper is still unscoped).

- [ ] **Step 5: Apply the fix to BOTH sites**

In `_find_entity_stmt`, scope the alias subquery:

```python
                Entity.entity_id.in_(
                    select(EntityAlias.entity_id).where(
                        EntityAlias.alias.ilike(pattern),
                        EntityAlias.workspace_id == workspace_id,
                    )
                ),
```

And in `_find_by_name_or_alias` (~line 501), the same fix on its alias subquery:

```python
                    Entity.entity_id.in_(
                        select(EntityAlias.entity_id).where(
                            EntityAlias.alias == alias,
                            EntityAlias.workspace_id == workspace_id,
                        )
                    ),
```

- [ ] **Step 5b: Run the test to verify it PASSES**

Run: `pytest tests/test_world_model_alias_isolation.py -v`
Expected: PASS (the compiled SQL now carries `entity_aliases.workspace_id = 'ws_a'`).

- [ ] **Step 6: Verify no regression**

Run: `pytest tests/ -q -k "world_model or entity"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git commit -am "fix(rebuild): workspace-scope EntityAlias subqueries (Step 0 isolation HOLE 1)

find_entity + _find_by_name_or_alias alias subqueries lacked a workspace_id
filter -> a workspace-A lookup could match a workspace-B alias. Extracted
_find_entity_stmt so the compiled-SQL test exercises production code."
```

> **Note:** This is the Step-0 isolation deliverable for the world-model path. The broader "A cannot read B via Store/checkpointer" blocking test (spec §4.10) is gated on `AsyncPostgresSaver` being installed — it is a deliverable of Task 7's spike / Step 10 wiring, not Step 0.

---

## Task 4: Add A2UI schema `version` field (forward/backward-compatible)

Add a `version` field to `A2UISurface`/`A2UIComponent` so the upcoming A2UI changes can be rolled out without a synchronized frontend deploy. Both models already use `extra="ignore"` and unknown component types only warn (never raise), so this is purely additive.

**Files:**
- Modify: `backend/src/ui/contracts.py` (add `A2UI_SCHEMA_VERSION` constant + `version: int` on both models)
- Test: `backend/tests/test_a2ui_versioning.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_a2ui_versioning.py`:

```python
"""Back-compat + forward-compat for the A2UI schema `version` field.
RED before the contracts.py diff (A2UI_SCHEMA_VERSION import fails)."""

from src.ui.contracts import A2UI_SCHEMA_VERSION, A2UIComponent, A2UISurface


def test_component_without_version_defaults_to_current() -> None:
    comp = A2UIComponent.model_validate({"type": "Text", "id": "c1", "properties": {"text": "hi"}})
    assert comp.version == A2UI_SCHEMA_VERSION


def test_surface_without_version_defaults_to_current() -> None:
    surf = A2UISurface.model_validate({
        "type": "surface", "id": "surf_1",
        "children": [{"type": "Text", "id": "c1", "properties": {"text": "hi"}}],
        "metadata": {},
    })
    assert surf.version == A2UI_SCHEMA_VERSION
    assert surf.children[0].version == A2UI_SCHEMA_VERSION


def test_unknown_future_version_does_not_crash() -> None:
    surf = A2UISurface.model_validate({
        "version": 999, "type": "surface", "id": "s1",
        "children": [{"version": 999, "type": "FutureWidget", "id": "c1", "properties": {}}],
        "metadata": {"x": 1},
    })
    assert surf.version == 999
    assert surf.children[0].type == "FutureWidget"


def test_round_trip_preserves_version() -> None:
    surf = A2UISurface(id="s1", children=[A2UIComponent(type="Text", id="c1", properties={"text": "x"})])
    reparsed = A2UISurface.model_validate(surf.model_dump())
    assert reparsed.version == A2UI_SCHEMA_VERSION
    assert reparsed.children[0].version == A2UI_SCHEMA_VERSION
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `pytest tests/test_a2ui_versioning.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'A2UI_SCHEMA_VERSION' from 'src.ui.contracts'`.

- [ ] **Step 3: Add the constant + field**

In `backend/src/ui/contracts.py`, after `logger = logging.getLogger(__name__)`:

```python
# Current A2UI schema version. Bump on contract changes readers must distinguish.
# Readers MUST tolerate unknown future values and missing values (defaults applied).
A2UI_SCHEMA_VERSION = 1
```

Add `version: int = A2UI_SCHEMA_VERSION` as the first field of `A2UIComponent` (above `type`) and of `A2UISurface` (above `type`). Leave `model_config = ConfigDict(extra="ignore")` and `A2UIComponent.model_rebuild()` unchanged.

- [ ] **Step 4: Run to verify it PASSES**

Run: `pytest tests/test_a2ui_versioning.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Verify no regression in UI/contract tests**

Run: `pytest tests/ -q -k "a2ui or surface or contract or ui"`
Expected: PASS (existing persisted-surface tests still deserialize — the field defaults).

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(rebuild): add A2UI schema version field (Step 0)

Additive version: int on A2UISurface/A2UIComponent (default 1). Backward-compat
(missing -> default) and forward-compat (unknown future version + extra fields
deserialize via extra=ignore). Enables A2UI rollout without synchronized FE deploy."
```

---

## Task 5: Wire capability-scope enforcement into the Deep Agents builder

The `deep_runtime` builder ships `extra_middleware=()` and never installs the capability-scope guard; the middleware also lets a DB exception propagate instead of denying. Wire the guard in, fail closed at construction for write-capable agents, and deny on DB error. **`build_deep_agent` becomes `async`** (the write-capability check needs a DB lookup), so the three existing builder tests convert to `await`.

**Files:**
- Modify: `backend/src/deep_runtime/agent_builder.py` (async + install guard + fail-closed-at-construction)
- Modify: `backend/src/deep_runtime/middleware/capability_scope.py` (`_is_in_scope`: deny on DB exception)
- Test: `backend/tests/deep_runtime/test_capability_scope.py` (add deny-on-DB-exception)
- Test: `backend/tests/deep_runtime/test_agent_builder.py` (convert 3 legacy tests to async; add 2 new)

- [ ] **Step 1: Write the failing deny-on-DB-exception test**

Append to `backend/tests/deep_runtime/test_capability_scope.py` (reuses the file's existing `_agent`, `_fake_db_factory`, `_request`, `_hook`, `handler`, `WORKSPACE_ID`):

```python
async def test_db_exception_is_blocked(handler):
    """Fail-closed: a registry lookup error DENIES the call (not propagate)."""
    agent = _agent({"math.multiply"})
    mw = make_capability_scope_middleware(
        agent=agent, workspace_id=WORKSPACE_ID, db_factory=_fake_db_factory()
    )
    registry = AsyncMock()
    registry.get_tool = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "src.deep_runtime.middleware.capability_scope.ToolRegistry",
        return_value=registry,
    ):
        result = await _hook(mw)(_request("multiply"), handler)
    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `pytest tests/deep_runtime/test_capability_scope.py::test_db_exception_is_blocked -v`
Expected: FAIL — `RuntimeError: db down` propagates out of the hook (no deny `ToolMessage`).

- [ ] **Step 3: Make `_is_in_scope` deny on DB exception**

In `backend/src/deep_runtime/middleware/capability_scope.py`, wrap the DB block in `_is_in_scope`:

```python
    try:
        async with db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)
            tool = await registry.get_tool(tool_name)
    except Exception:
        logger.warning(
            "[deep_runtime] %s DENIED %s — capability lookup failed (fail-closed)",
            agent.name, tool_name,
        )
        return False
    if tool is None:
        return False
    capability = getattr(tool, "capability", None)
    if not capability:
        return False
    return capability in scope
```

(Ensure `logger` is defined in the module; it is — used elsewhere.)

- [ ] **Step 4: Run to verify it PASSES**

Run: `pytest tests/deep_runtime/test_capability_scope.py -v`
Expected: all PASS (existing + new deny-on-DB-exception).

- [ ] **Step 5: Commit the middleware fix**

```bash
git commit -am "fix(rebuild): capability_scope denies on DB exception (Step 0, fail-closed)"
```

- [ ] **Step 6: Write the failing builder tests (async + fail-closed-at-construction)**

Add to `backend/tests/deep_runtime/test_agent_builder.py` (with the new imports it needs at the top: `json`, `asynccontextmanager`, `SimpleNamespace`, `AsyncMock`, `patch`, `ToolMessage`, plus a `_fake_db_factory`, `_operator_agent`, and a `@tool def send_email`):

```python
async def test_build_deep_agent_installs_scope_guard_and_blocks_out_of_scope():
    """A built agent's installed capability_scope guard blocks an out-of-scope tool."""
    agent = _operator_agent({"calendar.read"})
    resolver = AsyncMock(); resolver.is_write_capability = AsyncMock(return_value=False)
    registry = AsyncMock()
    registry.get_tool = AsyncMock(return_value=SimpleNamespace(capability="email.send", server="gmail"))
    with (
        patch("src.deep_runtime.agent_builder.CapabilityResolver", return_value=resolver),
        patch("src.deep_runtime.middleware.capability_scope.ToolRegistry", return_value=registry),
    ):
        compiled = await build_deep_agent(
            agent, tools=[send_email], workspace_id="ws_test", db_factory=_fake_db_factory(),
        )
        assert isinstance(compiled, CompiledStateGraph)
        from src.deep_runtime.middleware.capability_scope import make_capability_scope_middleware
        guard = make_capability_scope_middleware(
            agent=agent, workspace_id="ws_test", db_factory=_fake_db_factory(),
        )
        handler = AsyncMock()
        handler.return_value = ToolMessage(content="executed", tool_call_id="call_123")
        request = SimpleNamespace(tool_call={"name": "send_email", "args": {}, "id": "call_123"})
        result = await guard.awrap_tool_call(request, handler)
    handler.assert_not_awaited()
    assert result.status == "error"


async def test_build_deep_agent_refuses_write_agent_without_scope_middleware():
    """Builder refuses to compile a write-capable agent with no scope guard."""
    import pytest
    agent = _operator_agent({"email.send"})
    resolver = AsyncMock(); resolver.is_write_capability = AsyncMock(return_value=True)
    with patch("src.deep_runtime.agent_builder.CapabilityResolver", return_value=resolver):
        with pytest.raises(ValueError, match="refusing to compile agent 'operator'"):
            await build_deep_agent(agent, tools=[send_email], db_factory=None)
```

Also **convert the 3 existing tests** (`test_build_deep_agent_returns_compiled_state_graph`, `..._accepts_name_and_system_prompt_overrides`, `..._accepts_extra_middleware`) to `async def` + `await build_deep_agent(...)`. They pass `capability_scope=set()`, so the empty-scope path keeps compiling without a `db_factory`.

- [ ] **Step 7: Run to verify the new tests FAIL**

Run: `pytest tests/deep_runtime/test_agent_builder.py -v`
Expected: the 2 new tests FAIL (`TypeError: unexpected keyword 'workspace_id'` / object not awaitable; `DID NOT RAISE ValueError`); the 3 converted tests also fail until Step 8 lands (function not yet async).

- [ ] **Step 8: Make `build_deep_agent` async + install the guard + fail-closed gate**

Replace the imports block and `build_deep_agent` in `backend/src/deep_runtime/agent_builder.py` with:

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from langgraph.graph.state import CompiledStateGraph

from src.deep_runtime.middleware.capability_scope import make_capability_scope_middleware
from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent
from src.services.capability_resolver import CapabilityResolver


async def _has_write_capability_in_scope(agent: SubAgent, workspace_id: str, db_factory) -> bool:
    scope = getattr(agent, "capability_scope", None)
    if not scope:
        return False
    if db_factory is None:
        return True  # cannot prove read-only -> fail closed
    try:
        async with db_factory() as db:
            resolver = CapabilityResolver(db, workspace_id=workspace_id or "")
            for capability in scope:
                if await resolver.is_write_capability(capability):
                    return True
        return False
    except Exception:
        return True  # cannot prove read-only -> fail closed


async def build_deep_agent(
    agent: SubAgent,
    tools: list[Any],
    *,
    workspace_id: str = "",
    db_factory=None,
    extra_middleware: Sequence[Any] = (),
    system_prompt: str | None = None,
    name: str | None = None,
) -> CompiledStateGraph:
    middleware: list[Any] = []
    if db_factory is not None:
        middleware.append(
            make_capability_scope_middleware(
                agent=agent, workspace_id=workspace_id, db_factory=db_factory,
            )
        )
    middleware.extend(extra_middleware)

    has_scope_mw = any(
        getattr(mw, "name", None) == "capability_scope_guard"
        or type(mw).__name__ == "capability_scope_guard"
        for mw in middleware
    )
    if not has_scope_mw and await _has_write_capability_in_scope(agent, workspace_id, db_factory):
        raise ValueError(
            f"refusing to compile agent '{agent.name}': it has a write-class capability "
            "in scope but no capability_scope middleware would be installed (fail-closed). "
            "Pass db_factory so the scope guard is installed."
        )

    return create_deep_agent(
        model=build_chat_model(agent),
        tools=tools,
        system_prompt=system_prompt or agent.prompt,
        middleware=middleware,
        name=name or agent.name,
    )
```

- [ ] **Step 9: Run the full deep_runtime suite to verify it PASSES**

Run: `pytest tests/deep_runtime/ -v`
Expected: all PASS (3 converted builder tests + 2 new + existing capability_scope tests + deny-on-DB-exception).

- [ ] **Step 10: Grep for any other `build_deep_agent` caller (must be none outside tests)**

Run:
```bash
grep -rn "build_deep_agent" src/ | grep -v "src/deep_runtime/agent_builder.py" || echo "NO_OTHER_CALLERS"
```
Expected: `NO_OTHER_CALLERS` (confirmed: zero production importers today; if any appear, add `await` + thread `workspace_id`/`db_factory`).

- [ ] **Step 11: Commit**

```bash
git commit -am "feat(rebuild): wire capability_scope into build_deep_agent (Step 0)

build_deep_agent is now async, installs the capability_scope guard first when a
db_factory is given, and REFUSES to compile a write-capable agent without it
(fail-closed at construction). Closes the latent deep_runtime no-scope gap before
the Deep Agents path serves traffic. 3 legacy builder tests converted to async."
```

---

## Task 6: SPIKE — can a `wrap_tool_call` middleware raise `interrupt()`?

The unified gate (§4.3, Step 6) depends on raising a LangGraph `interrupt()` from inside a `wrap_tool_call` wrapper. If that is not supported, the fallback is `HumanInTheLoopMiddleware` with a `when=` predicate (or a dedicated gate node). This spike decides the gate topology. **No production behavior change** — it produces a written finding.

**Files:**
- Create: `backend/spikes/interrupt_in_wrap_tool_call/probe.py` (throwaway)
- Create: `docs/superpowers/spikes/2026-06-28-interrupt-in-wrap-tool-call.md` (finding)

- [ ] **Step 1: Write the probe**

Create `backend/spikes/interrupt_in_wrap_tool_call/probe.py` that builds a minimal `deepagents` agent with a `@wrap_tool_call` middleware whose wrapper calls `langgraph.types.interrupt(...)` before delegating, compiles it with an `AsyncPostgresSaver`-or-`MemorySaver` checkpointer + a `thread_id`, invokes it on a turn that triggers a tool call, and observes whether execution **pauses** (a `__interrupt__` is surfaced) and whether `Command(resume=...)` continues correctly — vs. the interrupt being swallowed or erroring.

- [ ] **Step 2: Run the probe and record the outcome**

Run: `cd backend && source .venv/bin/activate && python -m spikes.interrupt_in_wrap_tool_call.probe`
Record: does the run surface `__interrupt__` from inside the wrapper? Does `Command(resume=...)` resume past it? Any exception/traceback?

- [ ] **Step 3: Write the finding + decision**

Create `docs/superpowers/spikes/2026-06-28-interrupt-in-wrap-tool-call.md` with: the probe result, and a **decision**: (A) gate = `wrap_tool_call` raising `interrupt()` (preferred), or (B) fallback = `HumanInTheLoopMiddleware`/dedicated gate node. Record the exact API shape the Step-6 gate will use.

- [ ] **Step 4: Commit the finding (delete or keep the throwaway probe)**

```bash
git add docs/superpowers/spikes/2026-06-28-interrupt-in-wrap-tool-call.md
git commit -m "spike(rebuild): interrupt-from-wrap_tool_call finding + gate-topology decision (Step 0)"
```

---

## Task 7: SPIKE — install `AsyncPostgresSaver`; prove durable resume + pin a non-pickle serializer

The named execution-truth owner (`langgraph.checkpoint.postgres`) is **not installed**. This spike adds the dependency, proves durable resume works against the dev Postgres, and pins a non-pickle serializer. It also establishes the harness for the Step-10 "A cannot read B via checkpointer" isolation test. **No production wiring** — the autonomous cutover is Step 10.

**Files:**
- Modify: `backend/pyproject.toml` (add `langgraph-checkpoint-postgres`)
- Create: `backend/spikes/postgres_saver/probe.py` (throwaway)
- Create: `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md` (finding)

- [ ] **Step 1: Add the dependency and install**

Add `langgraph-checkpoint-postgres` to `backend/pyproject.toml` dependencies. Run:
```bash
cd backend && source .venv/bin/activate && pip install -e . && python -c "import langgraph.checkpoint.postgres; print('OK')"
```
Expected: prints `OK` (no `ModuleNotFoundError`).

- [ ] **Step 2: Write the durable-resume probe**

Create `backend/spikes/postgres_saver/probe.py`: build a tiny LangGraph graph with one node that performs a recorded side effect behind a per-`(thread_id)` idempotency check, run it under `AsyncPostgresSaver` (using `JARVIS_DATABASE_URL`), kill mid-run (raise after the side effect, before checkpoint), then resume the same `thread_id` and assert the side effect fired **exactly once**. Configure a JSON/msgpack serializer (explicitly **not** pickle) and confirm round-trip.

- [ ] **Step 3: Run the probe (requires dev Postgres)**

Run:
```bash
docker compose up -d   # ensure Postgres is up
cd backend && source .venv/bin/activate && python -m spikes.postgres_saver.probe
```
Record: did resume re-run the node from the top? did the idempotency guard prevent a double side effect? did the non-pickle serializer round-trip the state?

- [ ] **Step 4: Write the finding**

Create `docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md`: durability mode used (per-invocation `sync`), resume semantics observed, the serializer pinned, and the confirmed shape for the Step-1 idempotency ledger acceptance test (kill-after-write-before-checkpoint → resume → exactly-once).

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml docs/superpowers/spikes/2026-06-28-asyncpostgres-saver.md
git commit -m "spike(rebuild): install AsyncPostgresSaver + durable-resume finding + serializer pin (Step 0)"
```

---

## Task 8: SPIKE + observability — prompt caching survives the explicit `middleware=` shape

The spec assumes prompt caching keeps working on the `deep_runtime` path even though the builder passes an explicit `middleware=` list. Verify `cache_read_input_tokens > 0` on a 2nd turn, and add minimal context/cache observability so this stays visible.

**Files:**
- Create: `backend/spikes/caching/probe.py` (throwaway)
- Create: `docs/superpowers/spikes/2026-06-28-prompt-caching.md` (finding)
- Modify: `backend/src/deep_runtime/middleware/__init__.py` or the budget/usage middleware (add a debug log of `cache_read_input_tokens` / `cache_creation_input_tokens` per model call)

- [ ] **Step 1: Write the caching probe**

Create `backend/spikes/caching/probe.py`: build a `deep_runtime` agent via `build_deep_agent` with a large stable system prompt, run two turns on the same thread, and capture usage. (Use a real API key from env if available; otherwise document that this must run in an environment with credentials.)

- [ ] **Step 2: Run and record**

Run: `cd backend && source .venv/bin/activate && python -m spikes.caching.probe`
Record: 1st-turn `cache_creation_input_tokens` and 2nd-turn `cache_read_input_tokens`. **Acceptance:** 2nd-turn `cache_read_input_tokens > 0`.

- [ ] **Step 3: Add cache observability (failing test first)**

Write a test asserting the usage/budget middleware logs (or records on the usage span) `cache_read_input_tokens` and `cache_creation_input_tokens`. Run it (FAIL), add the minimal logging, run again (PASS).

Run: `pytest tests/deep_runtime/ -q -k "cache or usage or budget"`
Expected: PASS after the change.

- [ ] **Step 4: Write the finding + commit**

Create `docs/superpowers/spikes/2026-06-28-prompt-caching.md` (the observed numbers + whether caching survived). Then:
```bash
git add docs/superpowers/spikes/2026-06-28-prompt-caching.md backend/src/deep_runtime/
git commit -m "spike(rebuild): confirm prompt caching survives explicit middleware= + add cache observability (Step 0)"
```

---

## Final verification

- [ ] **Run the full suite + lint**

Run:
```bash
cd backend && source .venv/bin/activate && ruff check src/ tests/ && pytest tests/ -q
```
Expected: ruff clean; full suite PASS.

- [ ] **Confirm Step-0 exit criteria**
  - `src/workflows/` and the dead context-budget scaffold are gone; suite green.
  - `find_entity`/`_find_by_name_or_alias` alias subqueries are workspace-scoped (isolation HOLE 1 closed).
  - `A2UISurface`/`A2UIComponent` carry a `version` field (back/forward-compatible).
  - `build_deep_agent` installs the capability_scope guard, is async, and refuses to compile a write-capable agent without it; the middleware denies on DB error.
  - Three spike findings exist under `docs/superpowers/spikes/` with explicit decisions: gate topology (Task 6), durable resume + serializer (Task 7), caching (Task 8).
  - `agents.workspace_id` is **deferred** to its own plan (isolation HOLE 2 / spec §6.1).
