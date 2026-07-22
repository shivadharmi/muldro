# Step 10B Phase 0 Spike — Shadow Write-Suppression

Gate for the Step 10B "shadow-compare" control plane (see
`docs/superpowers/plans/2026-07-08-activation-gate-ledger.md` and the Step 10B scope
doc). Nothing in Phase 2 (the real `ShadowToolExecutor` + shadow-compare harness)
should start until this offline, no-infra spike passes.

## VERDICT: PROVEN

All 3 claims PASS in the main run, and the negative control correctly demonstrates
teeth (it FAILS claim (a) on purpose when write-classification is disabled, proving
suppression is load-bearing and not incidental).

## What it proves

- **(a) Suppression:** under `ShadowToolExecutor`, a write-capability tool call
  (`gmail_send` → `email.send`) returns the synthetic suppressed shape and the
  real-dispatch spy is called **zero** times for it. The read tool call
  (`gmail_search` → `email.search`) passes through to the spy exactly once.
  Classification uses the repo's real, pure `is_read_only_capability()`
  (`src/integrations/capabilities.py:227`) — not a reimplementation. Unknown
  capabilities fail closed (treated as write → suppressed).
- **(b) Continuation (the disprove-able claim):** suppressing a write mid-loop does
  not derail a minimal but realistic agent loop (read → write(suppressed) → final).
  The loop threads the synthetic result back into message history as an
  Anthropic-shaped `tool_result` block (mirrors
  `src/orchestrator/agent_loop.py`'s `{"type": "tool_result", "tool_use_id": ...,
  "content": json.dumps(result)}` convention), and the fake model's final step
  genuinely `json.loads()`s that threaded-back content and branches on it before
  answering. The assertion checks the *specific branch* the model landed in
  (`"suppressed by shadow harness"` in the final text) — a no-op fake model that
  ignored tool results would land in the wrong branch and fail this assertion.
- **(c) Capturability:** the loop's output is captured into a `ShadowDecision`
  dataclass (`route`, `tool_intents: frozenset[str]`, `final_text: str`), all
  non-empty, suitable for a future legacy-vs-deep diff.

## The synthetic-result contract (for Phase 2)

The real `ShadowToolExecutor.execute_tool` must return, for every suppressed
write-capability call, exactly this shape (plain JSON-serializable dict, no
extra required keys):

```python
{
    "shadow_suppressed": True,
    "tool": tool_name,       # str — the tool name that was called
    "capability": capability,  # str | None — resolved capability, or None if unresolved
}
```

Properties this shape must preserve (verified by the spike):
- It has no `"error"` key, so it is **not** misclassified as a tool error by
  `agent_loop.py`'s `is_error` detection (`"error" in result and status not in
  (ok/success/updated/ingested)`) — it reads as an ordinary successful tool result.
- It round-trips cleanly through `json.dumps` / `json.loads` when threaded back
  into message history as a `tool_result.content` string.
- Downstream consumers (real or fake model) can branch on the presence of the
  `shadow_suppressed` key to distinguish "write suppressed" from "write actually
  happened" without any special-casing beyond a dict `.get()`.

## Run

```bash
cd backend
uv run python spikes/step10b_shadow/spike_shadow_suppression.py
# or, negative control only:
uv run python spikes/step10b_shadow/spike_shadow_suppression.py --negative-control-only
```

Default invocation runs the main claim run (prints PASS/FAIL per claim + "MAIN RUN
VERDICT: PROVEN") followed automatically by the negative control (prints "CORRECTLY
FAILED claim (a)"), then an overall verdict line.

## Reading the result

- `OVERALL VERDICT: PROVEN` → proceed to Phase 2: build the real
  `ShadowToolExecutor` wrapping `ToolExecutor.execute_tool`
  (`src/orchestrator/tool_executor.py:306`), wired into the deep-runtime shadow
  path, using the synthetic-result contract documented above.
- `OVERALL VERDICT: DISPROVEN` → do **not** proceed. If claim (b) specifically
  fails (the loop stalls/errors on the synthetic result, or the assertion lands in
  the wrong branch), the whole-engine-shadow approach is disproven — fall back to
  per-step decision-point comparison instead of a full mid-loop shadow executor.

## Notes

- Pure offline spike: fake model, fake tool→capability map, in-process spy. No
  live API, no DB/Redis, no real deep runtime (deepagents/LangGraph deliberately
  NOT pulled in — that heavier real-build-path test is a later phase). The only
  coupling to `src/` is importing the real, dependency-free
  `is_read_only_capability()` so the classification reflects real behavior.
- This directory is a throwaway spike — not imported by `src/`, not wired into
  pytest (no `test_` prefix, not under `tests/`).
