# Fix-6: Orchestrator Error Handling & Routing

**Priority:** P1 — error propagation and correctness
**Risk:** Medium — touches core orchestrator flow and contracts
**Estimated files:** ~10-12
**Dependencies:** After Fix-5 (contracts cleanup)

## Overview

Three themes:

1. **Error propagation** — Stop swallowing agent errors, propagate step failures to the Presenter, surface user action steps.
2. **Dead code & stale references** — Delete unused `_execute_plan_via_graph`, dead vector embedding block in `_summarize_history`, stale `AGENT_EVENT_TYPES` entries, and fix placeholder injection.
3. **Routing edge cases** — Handle unknown capabilities with descriptive errors, prevent circular `depends_on`, fix race between surface push and SSE `done`, eliminate double-Presenter on `system.respond`.

---

## Phase 1: Error Propagation (HIGH)

### Task 1.1: Surface `user_steps` to the Presenter

**Files:** `backend/src/orchestrator/jarvis.py`

`user_steps` is collected at lines 786, 791-793 (non-streaming) and 1040, 1045-1047 (streaming) but never referenced after collection.

**Changes:**

In the **non-streaming** path (`process_message`, after the step execution loop at line 828):
- After the `for step, agent_name, tools in step_routing:` loop, if `user_steps` is non-empty, build a structured block:
  ```python
  user_action_block = ""
  if user_steps:
      actions = "\n".join(
          f"- {s.description}" + (f" ({s.user_context})" if s.user_context else "")
          for s in user_steps
      )
      user_action_block = f"\n\nUser actions required:\n{actions}"
  ```
- Append `user_action_block` to `presenter_msg` (line 836-843) so the Presenter includes user instructions in its response.
- Add `user_action_block` to `result` dict under key `"user_actions"` for structured access.

In the **streaming** path (`process_message_stream`, after the step execution loop at line 1088):
- Same `user_action_block` construction after the loop.
- Append to `presenter_msg` (line 1096-1107).
- Yield a `{"event": "user_actions", "steps": [{"description": s.description, "context": s.user_context} for s in user_steps]}` event before the presenter call.

### Task 1.2: Propagate `_call_agent` errors instead of returning empty string

**File:** `backend/src/orchestrator/jarvis.py`

`_call_agent` (line 2742) currently returns `text` which is `""` if `LoopError` is yielded by `agent_loop`. The caller at line 820-828 stores this empty string as the agent result with no indication of failure.

**Changes:**

In `_call_agent` (line 2786-2820):
- Add error tracking:
  ```python
  text = ""
  error = None
  async for evt in agent_loop(...):
      if isinstance(evt, LoopDone):
          text = evt.text
          ...
      elif isinstance(evt, LoopError):
          error = evt.error
          logger.warning(
              "agent_call_failed",
              extra={"agent": agent_name, "error": error},
          )
  if error and not text:
      return f"[Agent error: {error}]"
  return text
  ```

This ensures the Presenter sees a failure message instead of an empty string. The `[Agent error: ...]` prefix is detectable by callers that need structured handling.

### Task 1.3: Unknown capability returns error instead of dispatching to Operator

**File:** `backend/src/services/capability_resolver.py`

At line 103-106, when `resolver.resolve(step_capability)` returns empty tools, the function returns `"operator"` — dispatching to an agent with no tools.

**Changes:**

In `route_step` (line 96-114):
- After `tools = await resolver.resolve(step_capability)` at line 104:
  ```python
  if not tools:
      logger.warning(
          "No tools found for capability %s — cannot route step",
          step_capability,
      )
      return ""  # Empty string signals unroutable
  ```
- In the callers at `jarvis.py` lines 799-801 and 1052-1055, after `route_step`:
  ```python
  agent_name = await route_step(step.capability, resolver)
  if not agent_name:
      step_routing.append((step, "", []))  # Will be handled as error
      continue
  ```
- In the execution loop (lines 804-828 and 1058-1088), add a check before dispatching:
  ```python
  if not agent_name and not step.capability.startswith("system."):
      error_msg = f"No tools available for capability '{step.capability}'"
      logger.warning(error_msg)
      result[f"error_{step.step_id}"] = error_msg
      continue
  ```

---

## Phase 2: Dead Code & Stale References (MEDIUM + LOW)

### Task 2.1: Delete `_execute_plan_via_graph` dead method

**File:** `backend/src/orchestrator/jarvis.py`

The method at line 2963 is defined but never called anywhere in the codebase (confirmed via grep). It also creates `ContextBuilder` without `graph_engine`/`vector_store` kwargs, making it broken even if it were called.

**Changes:**
- Delete the entire `_execute_plan_via_graph` method (line 2963 to end of method).

### Task 2.2: Delete dead vector embedding block in `_summarize_history`

**File:** `backend/src/orchestrator/jarvis.py`

Lines 2207-2234 reference undeclared attributes `_vector_store`, `_embedding_service`, `_current_user_id`. The `getattr` guards prevent crashes but the block is dead code — `JarvisOrchestrator` never sets these attributes.

**Changes:**
- Delete the `if` block from line 2207 (`if (conversation_id and summary ...`) through line 2234 (end of the outer `except` block).

### Task 2.3: Fix `{capability_summary}` placeholder left verbatim

**File:** `backend/src/orchestrator/jarvis.py`

At line 2498-2499, `prompt.format(capability_summary=capability_summary)` is only called when `capability_summary` is truthy. If the planner prompt contains `{capability_summary}` but no summary was generated, the literal `{capability_summary}` string appears in the system prompt.

**Changes:**
- Replace the conditional at line 2498-2499:
  ```python
  if agent.name == "planner":
      prompt = prompt.format(
          capability_summary=capability_summary or "No capabilities connected yet."
      )
  ```

### Task 2.4: Remove stale `AGENT_EVENT_TYPES` entries

**File:** `backend/src/orchestrator/jarvis.py`

At line 52-61, verify whether `research_started`/`research_completed` entries exist. Current set does not contain them (already clean). No action needed unless Fix-5 left them.

**Verification only** — if entries `research_started` or `research_completed` are present, remove them.

### Task 2.5: Fix `SpanRecord.decision` stale field

**File:** `backend/src/orchestrator/contracts.py`

At line 106, `decision: str | None = None` on `SpanRecord` is a legacy field from the old decision-type routing. No code writes to it.

**Changes:**
- Delete `decision: str | None = None` from `SpanRecord` (line 106).
- `MessageMetadata.decision` at line 148 is already typed as `PlanOutput | None` — correct, no change needed.

### Task 2.6: Document `"none"` capability

**File:** `backend/src/orchestrator/jarvis.py`

At line 101, `"none"` appears in the set `{"reason", "respond", "none"}` used to skip surface generation.

**Changes:**
- Add inline comment:
  ```python
  # "none" = planner indicated no external capability needed (pure reasoning)
  if caps <= {"reason", "respond", "none"}:
  ```

---

## Phase 3: Contract Improvements (LOW)

### Task 3.1: `AgentResult.response_text` — distinguish failure from empty

**File:** `backend/src/orchestrator/contracts.py`

At line 32, `response_text: str = ""` makes a failed agent indistinguishable from one that returned nothing.

**Changes:**
- Change line 32: `response_text: str | None = None`
- Update all callers that check `response_text` to handle `None` (grep for `.response_text`).

### Task 3.2: `StepResult.duration_ms` — use None for unknown

**File:** `backend/src/orchestrator/contracts.py`

At line 46, `duration_ms: int = 0` is misleading on timeout (0ms implies instant).

**Changes:**
- Change line 46: `duration_ms: int | None = None`
- Update callers that read `duration_ms` to handle `None`.

### Task 3.3: Add circular `depends_on` validation to `PlanOutput`

**File:** `backend/src/orchestrator/contracts.py`

`PlanStep.depends_on` (line 360) accepts arbitrary step IDs with no cycle detection.

**Changes:**
- Add a `@model_validator(mode="after")` on `PlanOutput`:
  ```python
  from pydantic import model_validator

  @model_validator(mode="after")
  def _validate_step_dependencies(self) -> PlanOutput:
      step_ids = {s.step_id for s in self.steps if s.step_id}
      for step in self.steps:
          # Self-reference check
          if step.step_id and step.step_id in step.depends_on:
              raise ValueError(
                  f"Step '{step.step_id}' depends on itself"
              )
          # Unknown dependency check
          for dep in step.depends_on:
              if dep and dep not in step_ids:
                  raise ValueError(
                      f"Step '{step.step_id}' depends on unknown step '{dep}'"
                  )
      # Cycle detection (topological sort)
      visited: set[str] = set()
      temp: set[str] = set()
      adj = {s.step_id: s.depends_on for s in self.steps if s.step_id}

      def visit(node: str) -> None:
          if node in temp:
              raise ValueError(f"Circular dependency detected involving '{node}'")
          if node in visited:
              return
          temp.add(node)
          for dep in adj.get(node, []):
              if dep:
                  visit(dep)
          temp.remove(node)
          visited.add(node)

      for sid in adj:
          visit(sid)
      return self
  ```

---

## Phase 4: Race Conditions & Double-Execution (MEDIUM)

### Task 4.1: Fix race between `_spawn_background` surface push and SSE `done`

**File:** `backend/src/orchestrator/jarvis.py`

At lines 1133-1147 (streaming path), the surface push is fire-and-forget via `_spawn_background`, but `done` is yielded immediately after. The frontend may receive `done` before the surface exists.

**Changes:**
- Replace `_spawn_background` with `await` for the surface push:
  ```python
  try:
      await self._push_workspace_surface(
          plan, user_id, workspace_id, None, response_text=presenter_text,
      )
  except Exception:
      logger.warning("Surface push failed", exc_info=True)

  yield {"event": "done", "trace_id": trace.trace_id, "run_id": None}
  ```

### Task 4.2: Fix double-Presenter on `system.respond`/`system.acknowledge`

**File:** `backend/src/orchestrator/jarvis.py`

At lines 2707-2708, `system.respond` and `system.acknowledge` return `{}`. But the caller doesn't mark a presenter step as handled, so the fallback Presenter block (lines 830-850 non-streaming, 1090-1122 streaming) still fires.

**Changes:**

`system.respond` and `system.acknowledge` ARE respond-type steps, so the `has_presenter_step` check should treat them as such:
- Update the `has_presenter_step` check at lines 831-833 and 1091-1093:
  ```python
  has_presenter_step = any(
      s.capability in ("reason", "respond")
      or s.capability in ("system.respond", "system.acknowledge")
      for s in plan.steps
      if s.actor == "jarvis"
  )
  ```

### Task 4.3: Capture respond step output for `presenter_text`

**File:** `backend/src/orchestrator/jarvis.py`

At lines 1094-1122 (streaming path), `presenter_text` is only set inside the `if not has_presenter_step` block. When a plan has an explicit `respond` step, `presenter_text` stays `""` and `_push_workspace_surface` gets an empty preview.

**Changes:**

In the streaming execution loop (lines 1080-1088), capture text from respond steps:
```python
async for evt in self._call_agent_stream(...):
    yield evt
    if (
        step.capability in ("reason", "respond")
        and evt.get("event") == "agent_done"
    ):
        presenter_text = evt.get("text", "")
```

Move `presenter_text = ""` initialization before the execution loop (before line 1058).

---

## Verification

After all phases:

```bash
# Lint
ruff check backend/src/orchestrator/jarvis.py backend/src/orchestrator/contracts.py backend/src/services/capability_resolver.py

# Existing tests
pytest backend/tests/ -v -k "orchestrator or contracts or capability_resolver" --tb=short

# New test coverage
pytest backend/tests/test_contracts.py -v  # cycle detection
pytest backend/tests/test_orchestrator.py -v  # error propagation, user_steps
```

### Test additions

1. **`test_plan_output_circular_dependency`** — Verify `PlanOutput` rejects self-referencing and cyclic `depends_on`.
2. **`test_call_agent_propagates_error`** — Mock `agent_loop` to yield `LoopError`, verify `_call_agent` returns error string (not `""`).
3. **`test_route_step_unknown_capability`** — Verify `route_step` returns `""` when no tools exist.
4. **`test_user_steps_included_in_presenter`** — Verify `user_steps` descriptions appear in presenter message.
5. **`test_system_respond_no_double_presenter`** — Verify `has_presenter_step` is `True` when plan includes `system.respond`.
