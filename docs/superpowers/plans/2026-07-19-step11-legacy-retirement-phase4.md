# Step 11 — Phase 4 JIT Plan: collapse runtime selection to deep-only + delete 10B

> Fleshed against live code via a grounding scout (map verified on load-bearing anchors 2026-07-19).
> Process: main loop owns hot-file mutation + verify + commit synchronously; delegate parallel reviewers.
> Subtractive, **per surface**, full non-e2e gate green after EACH commit so the suite never goes
> broadly red. NO push/merge/deploy.

**Collapse rule (critical):** `settings.runtime` defaults to `"legacy"`. To collapse a surface you must
remove the `if runtime == "deep":` guard AND its legacy `else` arm **together**, leaving the deep body
unconditional — never delete the else while the guard (and default-legacy) remain. Inline `effective_runtime`
per call site; once every site is inlined, `runtime_gate.py` is dead and gets deleted (step 5).

**KEEP (NOT runtime — product flags):** `deep_single_lead`, `chat_planless`, `deep_context_jit`,
`deep_delegates_enabled`, `deep_inline_format`. Their `deep_context_jit`/`deep_delegates_enabled`
short-circuits stay; only the `== "deep"` sub-conditions become constant-true.

**Straggler discipline (Phase-2/3 lesson):** before closing each commit, grep EVERY test that imports
`agent_loop`/`Loop*`/`effective_runtime`/`runtime_gate` for that surface — not just the primary test file.

---

## Commit 1 — Re-home `CancellationRequested` (dependency root; safe, no behavior change)

- Move `class CancellationRequested` (`agent_loop.py:241`) **and** `_check_cancellation`
  (`agent_loop.py:247`, its trivial coupled helper) → `src/services/execution_support.py` (leaf; imports
  only contracts/errors/observability — verified clean home).
- `agent_loop.py` re-imports both back from `execution_support` (used at :470, :926) until it is deleted in
  Phase 5.
- Repoint `src/services/dag_runner.py:33` → import `CancellationRequested` from `execution_support`.
- Repoint `tests/test_execution_durability.py:8` → import `CancellationRequested`, `_check_cancellation`
  from `execution_support`. (Its `agent_loop`-signature assertions stay until the autonomous surface /
  Phase 5; only the import home moves here.)
- **Gate.** Commit.

## Commit 2 — Chat surface (`agent_invoker.call_agent_stream` + `chat_processor`)

- `agent_invoker.call_agent_stream` (`:719-723` resolve, `:748` deep guard, `:821-870+` legacy else): remove
  the `effective_chat_runtime()` resolve + `if runtime == "deep":` guard, keep the deep body unconditional,
  **delete the entire `agent_loop` else block** (the `Loop*`→SSE translation). Metric `AGENT_RUNTIME_CALLS`
  (`:747`) → drop the label or set constant `runtime="deep"`.
- `chat_processor._resolve_effective_mode` (`:344-386`, `== "deep"` at `:368`): the `effective_chat_runtime()
  == "deep"` sub-condition becomes constant-true; keep the `deep_single_lead` flag gate.
  **VERIFY AT BUILD:** the `return None` downgrades (`:361`, `:383-385`) currently fall back to the legacy
  multi-agent chat path. Confirm what `None` routes to downstream once there is no legacy — if `None` still
  means "not single-lead → deep multi-step," keep it; if it meant "legacy agent_loop," rewire to the deep
  equivalent. Do not assume.
- Drop the now-unused `agent_loop`/`Loop*` imports from `agent_invoker` that only the deleted arm used
  (keep those still used by the perception/autonomous arms until their commits).
- Rewrite chat tests (remove legacy-arm cases / agent_loop negative-patches, re-home `Loop*`):
  `test_chat_plan_event.py` (`LoopError`), `test_fix6_orchestrator_error_handling.py` (`LoopError`),
  `test_context_jit_wiring.py` (`LoopDone`), `test_stream_deep_lead.py`, `test_chat_single_lead.py`,
  `test_lead_delegate_routing.py` (drop "agent_loop not called" negative-patches).
- **Gate + straggler grep.** Commit.

## Commit 3 — Perception surface (`agent_invoker.call_agent`)

- `agent_invoker.call_agent` (`:1695-1699` resolve, `:1701` deep guard, `:1751+` legacy else): remove guard +
  resolve, keep deep body unconditional, delete the `agent_loop(stream=False)` else block. Metric `:1700`.
- Callers unaffected (`jarvis.py:864`, `perception_runner.py:143/277/445`) — signature unchanged.
- Rewrite `test_perception_deep_branch.py` (drop legacy case, re-home `LoopDone`),
  `test_agent_invoker_runtime_metric.py` (deep-only metric), and any perception test forcing runtime.
- **Gate + straggler grep.** Commit.

## Commit 4 — Autonomous surface (`step_runner` + `graph_executor`)

- `step_runner.run_step` (`:177-179` resolve, `:180` deep guard, `:183-188` legacy else + `minimal_claude_action`
  fallback): make `run_step_via_deep_agent` unconditional; **delete `run_step_via_agent_loop`** (`:379`, the
  legacy executor). Keep `minimal_claude_action` only if the deep path still needs a fallback — **VERIFY AT
  BUILD** whether it is legacy-only.
- `step_runner.build_step_context` JIT gate (`:553-559`): drop `== "deep"`, keep `deep_context_jit`.
- `graph_executor`: inline `"deep"` at the 4 gates — `_autonomous_jit` (`:250-266`), `execute_run` lease
  (`:315`), `_resume_run` (`:517`), `_resume_run_body` reconcile (`:592`, delete legacy `else :599-604`);
  **delete `_run_step_via_agent_loop` facade** (`:829-836`).
- `graph_executor_factory.py:48` — update the runtime-gate comment/wire if load-bearing.
- Rewrite `test_step_runner_deep_executor.py`, `test_graph_executor.py` (7 `agent_loop` cases — rewrite to the
  deep executor or delete if redundant with `test_autonomous_deep_e2e`), `test_execution_durability.py` (finish
  the agent_loop-signature removal started in Commit 1), `test_run_reconcile.py`, `test_autonomous_lease.py`,
  `test_autonomous_context_slim.py`, `test_autonomous_deep_e2e.py` (drop "legacy never called" spies),
  `idempotency/test_step_runner_wiring.py`, `test_step_runner_write_lock.py`.
- **Gate + straggler grep.** Commit.

## Commit 5 — Delete the 10B control plane

- Delete files: `runtime_gate.py`, `runtime_breaker.py`, `scheduler/runtime_rollback_tick.py`,
  `api/routes_admin_runtime.py`, `orchestrator/shadow_runner.py`, `orchestrator/shadow_tool_executor.py`,
  `orchestrator/divergence.py`.
- Unwire: `scheduler/service.py:19/36` (drop `RuntimeRollbackTickMixin` from bases), `app.py` (unmount the
  admin-runtime router), `jarvis.py:185-191` (drop `ShadowRunner` construction + `:37` import),
  `chat_single_lead.py:161-166` (drop `maybe_run_shadow` + `ShadowDecision` auth-decision),
  `agent_invoker.run_shadow_turn` (`:1530-1659`, delete), any `record_shadow_divergence`/`SHADOW_DIVERGENCE`
  metric.
- Delete tests: `test_runtime_gate.py`, `test_runtime_gate_static_deep.py`, `test_runtime_breaker*`,
  `test_runtime_rollback_watcher.py`, `test_runtime_override_escape_hatch.py`, `test_shadow_runner.py`,
  `test_shadow_tool_executor.py`, `test_divergence_comparator.py`, `test_settings_runtime_flag.py`,
  `test_agent_invoker_runtime_branch.py`, `test_agent_invoker_runtime_metric.py` (if now empty),
  `test_metrics_rollback_signals.py`, and the shadow half of `test_augment_inline_lead_scope.py`.
- **Gate.** Commit.

## Commit 6 — Strip settings + residual startup gates

- Remove settings fields: `runtime` (`:172`), `shadow_sample_rate` (`:219`), `rollback_*` (`:235-239`),
  `admin_api_token` (`:246`) **only if** grep confirms it is used solely by `routes_admin_runtime`.
- Remove `if settings.runtime == "deep"` gates: `app.py:76` (checkpointer always builds; keep degraded
  fallback), `run.py:138` (same), `routes_health.py:481` (drop the "disabled" arm),
  `scheduler/checkpoint_reaper_tick.py` (reaper always runs).
- `metrics_service.py:37` `AGENT_RUNTIME_CALLS` — becomes single-valued; keep or simplify.
- **Gate.** grep `effective_runtime|runtime == "deep"|shadow|rollback_` in `src/` → zero (outside Phase-5
  `agent_loop.py`, deleted files). Commit.

## Phase 4 closeout
- Full non-e2e gate green; ruff clean; single head `1a2770a28c39`; `agent_loop.py` still present (Phase 5
  deletes it) but only self-referenced + re-imported for `CancellationRequested`.
- Parallel spec-compliance + code-quality reviewers over the whole Phase-4 diff.
- Update memory topic file + MEMORY.md with Phase-4 SHAs. STOP before push/merge/deploy.
