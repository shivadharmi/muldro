# Step-10 Cutover (Phases A–D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the deep runtime + chat permission model across chat + perception + autonomous, validated by a local full-stack end-to-end test on deep. **This session runs Phases A–D only** and STOPS at local validation — NO push, merge, or deploy (Phases E/F deferred).

**Architecture:** The only surface not yet flippable is perception: `call_agent` (non-stream) has no deep branch (B6). Phase A builds it — a `runtime=="deep"` branch mirroring `run_shadow_turn`'s consume-and-capture pattern, with honest-provenance auth-source so a **headless** perception run never interrupts. Phases B–D are config/verification/e2e — no shadow-compare (validation is a live local e2e on deep).

**Tech Stack:** Python 3.12, FastMCP, LangGraph/Deep Agents (`AsyncPostgresSaver`, `stream_deep_agent_events`), pytest (no pytest-asyncio — custom `asyncio.run` hook; write `async def test_...`), Playwright for frontend e2e, docker-compose infra.

**Spec:** `docs/superpowers/specs/2026-07-19-step10-live-cutover-design.md`

**Baseline (verify at start):** `uv run pytest tests/ --ignore=tests/e2e -q` → 3688 passed / 18 skipped; `uv run alembic heads` → single `1a2770a28c39`; ruff clean; frontend 124 tests + build. Branch `rebuild/first-principles` @ `202a5e7`, main undiverged. Infra: `docker compose up -d postgres redis qdrant`; `uv sync --all-extras` (NO pip). ZERO migrations expected for the whole plan.

**Test harness notes:** `make_mock_settings()`, `TEST_USER_ID`, `TEST_WORKSPACE_ID` from `tests/conftest.py`. **MagicMock-truthy hazard:** any test exercising the deep branch must set `deep_*`/`runtime` explicitly. `@patch` binds in the DEFINING module. Mock Anthropic via `@patch("src.orchestrator.jarvis.get_anthropic_client")`. Main loop owns verify+commit; confirm each SHA + gate count yourself (implementers do NOT commit). `-F <file>` commits (zsh backtick gotcha).

---

## Phase A — B6: perception deep branch in `call_agent`

**What:** Add a `runtime == "deep"` branch to `AgentInvoker.call_agent` (`src/orchestrator/agent_invoker.py:1663`) so Perceiver + Librarian (`perception_runner.py:143/277/446`) and briefing (via the `JarvisOrchestrator.call_agent` facade `jarvis.py:864`) run on `build_deep_agent` when `effective_runtime("perception")=="deep"`. Dormant + byte-neutral on legacy. ZERO migrations.

**Design decisions (locked from grounding):**
- **Non-stream → text:** mirror `run_shadow_turn` (`agent_invoker.py:1626-1635`) — iterate `self._stream_and_reap(...)`, capture the `agent_done` frame's `"text"` (`stream_adapter.py:272`), return it.
- **Auth-source = honest provenance, no hang:** pass `authorization_source=AuthorizationSource.AUTONOMOUS` + `pre_approved_capabilities=frozenset(agent.capability_scope)`. This keeps the trust_gate audit trail truthful (perception is NOT a user request; it ingests untrusted email/Slack content) while every write short-circuits at `trust_gate:358` (pre-approved) so a headless run NEVER hits `interrupt()`. Parity-with-legacy on gating (legacy perception writes are ungated too). Do NOT pass `permission_mode` (leave `None` → permission_gate not installed).
- **Checkpointer:** none needed — the build auto-uses `MemorySaver()` fallback (`agent_invoker.py:622`); perception never pauses/resumes; `_stream_and_reap`'s reap is a MemorySaver no-op.
- **Surface string:** `call_agent` resolves `effective_runtime("perception", ...)` for ALL its callers (perception + briefing). Redis via `self._services.extras.get("redis") if self._services else None` (same as `call_agent_stream`).

**Files:**
- Modify: `src/orchestrator/agent_invoker.py` (`call_agent`, ~1663–1735) — add the deep branch + imports (`AuthorizationSource`, `make_thread_id`, `build_system_message` — confirm which are already imported in-module; `call_agent_stream` uses all three, so they are).
- Test: `tests/test_perception_deep_branch.py` (new). Harness template: `tests/test_chat_deep_runtime_parity.py` + `tests/test_agent_invoker_runtime_metric.py` (they exercise the `call_agent_stream` deep branch — mirror their mock setup for `_stream_and_reap`/`effective_runtime`).

### Task A1: `call_agent` deep branch — legacy path stays byte-neutral (negative control first)

- [ ] **Step 1: Write the parity test (byte-neutral when perception is legacy)**

Create `tests/test_perception_deep_branch.py`. Mirror the mock-invoker construction from `tests/test_chat_deep_runtime_parity.py`. This test asserts that with `runtime` resolving `legacy` (default), `call_agent` runs the legacy `agent_loop` exactly as today (the deep branch is not taken).

```python
# async def test — no pytest-asyncio in this repo
from unittest.mock import AsyncMock, patch
# ... construct invoker per test_chat_deep_runtime_parity.py's helper ...

async def test_call_agent_legacy_when_perception_not_deep(mock_invoker):
    # effective_runtime("perception") resolves "legacy" (no redis keys / default)
    with patch("src.orchestrator.agent_invoker.effective_runtime", AsyncMock(return_value="legacy")):
        # legacy agent_loop is invoked; deep build path is NOT called
        with patch.object(mock_invoker, "_build_deep_agent_for") as build_deep:
            text = await mock_invoker.call_agent("librarian", "obs", user_id="u", workspace_id="w")
    build_deep.assert_not_called()
    assert isinstance(text, str)
```

- [ ] **Step 2: Run it — should PASS already** (legacy path unchanged; the deep branch doesn't exist yet so `_build_deep_agent_for` is never called from `call_agent`).

Run: `uv run pytest tests/test_perception_deep_branch.py::test_call_agent_legacy_when_perception_not_deep -v`
Expected: PASS (this pins byte-neutrality before the change).

- [ ] **Step 3: Write the forced deep-branch test (RED)**

```python
async def test_call_agent_deep_branch_returns_agent_done_text(mock_invoker):
    frames = [
        {"event": "text_delta", "text": "hel"},
        {"event": "agent_done", "text": "hello from deep librarian"},
    ]
    async def fake_reap(*a, **k):
        for f in frames:
            yield f
    with patch("src.orchestrator.agent_invoker.effective_runtime", AsyncMock(return_value="deep")), \
         patch.object(mock_invoker, "_build_deep_agent_for", AsyncMock(return_value=object())), \
         patch.object(mock_invoker, "_stream_and_reap", fake_reap):
        text = await mock_invoker.call_agent("librarian", "obs", user_id="u", workspace_id="w")
    assert text == "hello from deep librarian"

async def test_call_agent_deep_uses_autonomous_authsource_and_pre_approved_scope(mock_invoker):
    async def fake_reap(*a, **k):
        yield {"event": "agent_done", "text": "ok"}
    captured = {}
    async def fake_build(agent, tools, **kw):
        captured.update(kw)
        return object()
    with patch("src.orchestrator.agent_invoker.effective_runtime", AsyncMock(return_value="deep")), \
         patch.object(mock_invoker, "_build_deep_agent_for", fake_build), \
         patch.object(mock_invoker, "_stream_and_reap", fake_reap):
        await mock_invoker.call_agent("librarian", "obs", user_id="u", workspace_id="w")
    from src.deep_runtime.authorization import AuthorizationSource
    assert captured["authorization_source"] == AuthorizationSource.AUTONOMOUS
    # honest provenance: agent's own scope is pre-approved so no headless interrupt
    assert captured["pre_approved_capabilities"] == frozenset(
        mock_invoker._agents["librarian"].capability_scope
    )
    assert captured.get("permission_mode") is None
```

- [ ] **Step 4: Run — verify RED**

Run: `uv run pytest tests/test_perception_deep_branch.py -k deep -v`
Expected: FAIL (no deep branch yet — returns legacy text / build not called).

- [ ] **Step 5: Implement the deep branch in `call_agent`**

Insert AFTER the `system_blocks = self.build_system_prompt(...)` line (~1690) and BEFORE the `text = ""` / `async for evt in agent_loop(...)` legacy block. Resolve runtime first, then branch:

```python
        runtime = await effective_runtime(
            "perception",
            redis=self._services.extras.get("redis") if self._services else None,
            settings=self._settings,
        )
        if runtime == "deep":
            # B6 (Step 10): perception + briefing run on the deep runtime. Headless origin
            # (no synchronous approver) → AUTONOMOUS provenance (honest audit trail) with the
            # agent's own capability_scope pre-approved, so every write short-circuits at
            # trust_gate (never interrupt()) — parity with legacy's ungated perception writes,
            # but truthful about provenance. No permission_mode (permission_gate not installed).
            # No durable checkpointer needed (never pauses); build auto-uses MemorySaver.
            thread_id = make_thread_id(workspace_id)
            deep_agent = await self._build_deep_agent_for(
                agent,
                tools,
                user_id=user_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                authorization_source=AuthorizationSource.AUTONOMOUS,
                pre_approved_capabilities=frozenset(agent.capability_scope),
                system_prompt=build_system_message(system_blocks),
                context_block=context_block,
            )
            graph_input = {"messages": [{"role": "user", "content": message}]}
            final_text = ""
            async for frame in self._stream_and_reap(
                deep_agent, graph_input,
                thread_id=thread_id, agent_name=agent_name, model=model,
            ):
                if isinstance(frame, dict) and frame.get("event") == "agent_done":
                    final_text = frame.get("text", "")
            return final_text
```

Confirm at implement-time: `context_block` is assembled just above (it is — `call_agent` builds `context_block` before `system_blocks`); `AuthorizationSource`, `make_thread_id`, `build_system_message`, `effective_runtime` are already imported in-module (used by `call_agent_stream`).

- [ ] **Step 6: Run the deep tests — GREEN**

Run: `uv run pytest tests/test_perception_deep_branch.py -v`
Expected: all PASS.

- [ ] **Step 7: Negative control with teeth**

Temporarily change `authorization_source=AuthorizationSource.AUTONOMOUS` → `DIRECT_USER_REQUEST` and re-run `test_call_agent_deep_uses_autonomous_authsource_and_pre_approved_scope` — it MUST fail (proves the provenance assertion has teeth). Revert.

Run: `uv run pytest tests/test_perception_deep_branch.py -k authsource -v` (after the temp mutation)
Expected: FAIL; then revert and confirm PASS.

- [ ] **Step 8: Full gate + commit**

Run: `uv run pytest tests/ --ignore=tests/e2e -q` → 3688+N passed / 18 skipped. `uv run ruff check src tests`.
Commit (main loop, `-F`): `feat(step10): B6 — perception deep branch in call_agent (AUTONOMOUS provenance, dormant)`

---

## Phase B — Activation config + wiring verification

**What:** No production code change — deep is enabled by ENV vars (`JARVIS_RUNTIME=deep`, `JARVIS_DEEP_SINGLE_LEAD=true`, `JARVIS_CHAT_PLANLESS=true`; per-ws `permission_mode` default stays `auto`). `settings.py` defaults stay legacy so the 3688-suite is byte-neutral. Confirm the wiring that makes the ENV flip real.

### Task B1: Verify runtime wiring end-to-end (verification task, no code unless a gap surfaces)

- [ ] **Step 1: Confirm worker durable checkpointer gating** — ALREADY CONFIRMED at plan-write: `run.py:135` builds `AsyncPostgresSaver` gated on `settings.runtime=="deep"`, resilient, injected into `JarvisOrchestrator(checkpointer_provider=...)`. Re-verify by reading `run.py:125-160`. No change expected.

- [ ] **Step 2: Confirm API checkpointer gating** — `src/api/app.py:76` builds it on `runtime=="deep"`. Re-read. No change.

- [ ] **Step 3: Assert the three surfaces resolve deep under the env.** Write a small test that, with `settings.runtime="deep"` and no redis override, `effective_runtime("chat"/"perception"/"autonomous")` all return `"deep"` (tier-4 static fallback). If a helper test already covers this (`tests/` for `runtime_gate`), extend it; else add `tests/test_runtime_gate_static_deep.py`.

```python
async def test_all_surfaces_resolve_deep_on_static_runtime():
    from src.services.runtime_gate import effective_runtime
    s = make_mock_settings(); s.runtime = "deep"
    for surface in ("chat", "perception", "autonomous"):
        assert await effective_runtime(surface, redis=None, settings=s) == "deep"
```

- [ ] **Step 4: Run + commit**

Run: `uv run pytest tests/test_runtime_gate_static_deep.py -v` → PASS.
Commit: `test(step10): assert all three surfaces resolve deep on static JARVIS_RUNTIME=deep`

---

## Phase C — R0 whole-branch holistic review (pre-validation)

### Task C1: Independent holistic review of the entire rebuild diff

- [ ] **Step 1: Dispatch two parallel read-only reviewers** over `git diff main...HEAD` (the whole 0→10 rebuild, now including B6):
  - **Opus holistic reviewer:** cross-step invariant integrity — tools-are-schemas / execution-is-central on both paths; capability-scope OUTER / dispatcher INNER; TrustEngine as the single autonomous gate; workspace-isolation on every new surface (checkpointer `thread_id`, reaper); B6's AUTONOMOUS-provenance perception branch is coherent with the provenance-taint design; legacy byte-neutral under default flags.
  - **Security reviewer:** cross-path write-lock, checkpointer tenant-binding (A6), permission_gate/trust_gate fail-closed, B6 headless-no-interrupt property, no cross-tenant resume.
- [ ] **Step 2:** Triage findings. Any CRITICAL/HIGH → fix on-branch (own task), re-review, full gate. Only proceed to Phase D on a SHIP verdict with no open CRITICAL/HIGH.
- [ ] **Step 3:** Re-run both gates from clean state: `uv run pytest tests/ --ignore=tests/e2e -q`; `cd frontend && npm run test && npm run build`. Confirm single head `1a2770a28c39` drift-free.

---

## Phase D — Local full-stack e2e on deep (the validation gate)

**What:** Bring up the whole stack locally on deep and drive every flow. Green here = the session's success criterion. This is a runbook, not unit tests — record actual observed output for each step.

### Task D1: Environment bring-up

- [ ] **Step 1:** Infra up: `docker compose up -d postgres redis qdrant` (watch disk: `docker builder prune -af` if Qdrant WAL grows).
- [ ] **Step 2:** `cd backend && uv run alembic upgrade head` (single head). Seed a User→Workspace FK chain for the test workspace (script or existing seed).
- [ ] **Step 3:** Start the backend on deep (own terminal — do NOT edit `backend/` while `--reload` runs):
  `JARVIS_RUNTIME=deep JARVIS_DEEP_SINGLE_LEAD=true JARVIS_CHAT_PLANLESS=true uv run python run.py --worker`
  Confirm logs: `[deep_runtime] worker durable checkpointer ready` + API checkpointer built.
- [ ] **Step 4:** `cd frontend && npm run dev`. Confirm it talks to the local backend.

### Task D2: Chat permission-model e2e (Playwright + HTTP/WS)

- [ ] **Step 1 — bypass:** entitle the test workspace (`Workspace.settings["allow_bypass"]=True` via DB — no endpoint), set `permission_mode=bypass`, send a chat write. Assert: single-lead runs, write executes ungated, one assistant bubble, reply present.
- [ ] **Step 2 — ask (approve):** `permission_mode=ask`, trigger a write. Assert: `approval_needed` SSE → in-chat `InlineApprovalCard`; approve → `POST /v1/jarvis/chat/resume` → write fires exactly once → reply. Verify no double-bubble.
- [ ] **Step 3 — ask (reject):** trigger a write, reject. Assert: write does NOT fire; a quotable rejection reason reaches the model; reply acknowledges.
- [ ] **Step 4 — auto:** `permission_mode=auto`, a low-risk write passes un-paused; a risky write interrupts (RiskAssessor). Assert both.
- [ ] **Step 5 — planless reroute:** confirm `JARVIS_CHAT_PLANLESS=true` drops the Planner (no Plan record / PlanReady) yet still executes + replies; `write_todos` todos render inline in chat.
- [ ] **Step 6 — per-ws default:** with no explicit `permission_mode`, confirm the workspace default resolves (`auto` raw default) at the interactive handler.

### Task D3: Perception e2e on deep (B6)

- [ ] **Step 1:** Seed a perception source with new events (or inject a normalized event). Trigger a scheduler perception tick (or call the perception path directly).
- [ ] **Step 2:** Assert Perceiver + Librarian ran on deep (log: deep build for `perceiver`/`librarian`; `AGENT_RUNTIME_CALLS{runtime="deep"}` increments) and did NOT hang (no stranded checkpoint; run completes).
- [ ] **Step 3:** Assert entities/memories were extracted (DB rows) and a briefing generates ONCE (no double/false briefing).

### Task D4: Autonomous scripted durable-resume e2e (the hardest)

- [ ] **Step 1:** Seed a multi-step `Plan` with a write step (a `TaskRun`/`Plan` with `source="background"` or a scripted GraphExecutor invocation) so the scheduler picks it up on deep.
- [ ] **Step 2:** Assert the autonomous step runs via `run_autonomous_deep_step` (deep), TrustEngine gates the write step (approval persisted), approve it.
- [ ] **Step 3 — exactly-once:** after approve+resume, assert the external effect happened EXACTLY once — query the `idempotency_ledger` for a single success row for the step's identity_key; assert `double_fire` counter did not increment. Force a replay (kill+resume mid-step) and re-assert single effect (reconcile-from-event-log + ledger).
- [ ] **Step 4 — lease:** assert the single-flight lease is held during execution (no concurrent double-pick).

### Task D5: Byte-neutral legacy control

- [ ] **Step 1:** Restart the backend WITHOUT the deep env (`uv run python run.py --worker`). Assert a chat turn + perception tick run LEGACY (`AGENT_RUNTIME_CALLS{runtime="legacy"}`), identical to pre-change behavior.

### Task D6: Session close-out (NO push/merge/deploy)

- [ ] **Step 1:** Record the e2e results (a short results doc `docs/superpowers/plans/2026-07-19-step10-e2e-results.md`).
- [ ] **Step 2:** Final full gate: `uv run pytest tests/ --ignore=tests/e2e -q`; frontend gate; single head; ruff clean; tree clean (or only the results doc + B6 commit).
- [ ] **Step 3:** Update memory (`project_first_principles_rebuild.md` + `MEMORY.md`) with the Session-8 block: B6 built, all-three-deep validated locally, branch ready to merge, Phases E/F deferred.
- [ ] **Step 4: STOP.** Do NOT push, merge, or deploy. Surface the ready-to-merge state to the user; Phase E (merge + CLAUDE.md R1) and Phase F (prod deploy) are the next gated session(s).

---

## Self-review notes
- **Spec coverage:** Phase A = B6 (spec §2.1/§3 Phase A); Phase B = activation config + worker checkpointer verify (spec §3 Phase B / open item 2 — RESOLVED at plan-write); Phase C = R0 (spec §3 Phase C); Phase D = local e2e incl. scripted autonomous (spec §3 Phase D, open item 3). Merge/deploy correctly EXCLUDED (session boundary).
- **Auth-source open item (spec §7.1):** RESOLVED — `AUTONOMOUS` + `pre_approved_capabilities=frozenset(agent.capability_scope)` (honest provenance, no headless interrupt).
- **Placeholders:** none — production code is verbatim from grounding; test code is concrete (align mock construction with `test_chat_deep_runtime_parity.py` at execution).
- **Types:** `AuthorizationSource.AUTONOMOUS` (`src/deep_runtime/authorization.py`), `agent.capability_scope: set[str]` (`agents.py:199`), `_stream_and_reap`/`_build_deep_agent_for` signatures verified.
- **ZERO migrations** across the whole plan.
