# Spike decision — Step 7C Phase 0 deep read-back annotation → SSE `tool_result`

**Date:** 2026-07-08 · **Branch:** `rebuild/first-principles` @ `e020d21` (plan) · **Status:** BOTH SUB-PROBES PASS — **design CONFIRMED**, plan proceeds to Phase 1 UNCHANGED.

**Probe (offline, no API key, no Postgres, no Redis):** `backend/spikes/deep_readback/probe.py`.
Run: `uv run python spikes/deep_readback/probe.py` (exit 0 = every assertion held).

---

## The spike question (could DISPROVE)

7C adds a NEW deep-runtime `@wrap_tool_call` middleware `readback`, placed INNER of `write_lock` and OUTER of the central `jarvis_tool_dispatcher` — chain `… → write_lock → readback → dispatcher`. For an irreversible/external write it runs the tool via `await handler(request)` (the dispatcher executes and returns a **bare `ToolMessage`**), then ANNOTATES a `verification` key onto that ToolMessage's content-JSON and returns a `model_copy` of it — **never** touching `status` (the SSE adapter maps `blocked ← status == "error"`).

The annotation-through-SSE trick is already proven for 7B2's critique middleware, but only on the `task` tool's **`Command`** result (`docs/.../2026-07-08-deep-delegate-subagents.md` §0.4), and inner-of-`write_lock` placement is proven by `write_lock.py`. The **specific combination was UNPROVEN offline end-to-end**: a read-back `@wrap_tool_call` inner of `write_lock` reading a **bare dispatcher `ToolMessage`** (not a `Command`), re-annotating a content-JSON key, and that annotation surviving `write_lock` + the full chain + `stream_deep_agent_events` to a `tool_result` SSE frame with `blocked == False` and **no `stream_adapter` change**. Every prior deep phase's spike caught a non-obvious surprise; this one could have DISPROVEN the "no adapter change" claim or exposed a `model_copy`/content-type gotcha.

## The harness

Two sub-probes drive the REAL `build_deep_agent` (`src.deep_runtime.agent_builder`) + `stream_deep_agent_events` (`src.deep_runtime.stream_adapter`), mirroring the `spikes/deep_delegate/*` pattern:

- A fake scripted-streaming `BaseChatModel` (reused from `subagent_gated_probe.ScriptedModel`, no API key) emits ONE tool call to a stub Jarvis write tool `mock_write`, then a terminal reply.
- The REAL central `make_jarvis_tool_dispatcher` whose `execute_tool` returns a bare success dict `{"message_id": "m1"}` → the dispatcher wraps it as `ToolMessage(content=json, status="success")`.
- The REAL `make_write_lock_middleware(redis=None)` — it falls through, returning `await handler(request)` unchanged. Its `async with acquire_write_lock(...)` branch is **return-value-transparent** (both branches `return await handler(request)`; the context manager cannot alter the returned object), so `redis=None` faithfully exercises the exact return path `readback` depends on — no Redis needed.
- A read-back-shaped `@wrap_tool_call` middleware placed **inner of `write_lock`, outer of the dispatcher** via `extra_middleware=(write_lock, readback, dispatcher)` (first tuple element = outermost). Empty `capability_scope` + `db_factory=None` → no scope guard installed and no write-cap `ValueError`, so the write reaches the chain unfiltered.

Assemble → stream → collect the frozen SSE frames.

## The empirical finding

**Sub-probe 1 — UNVERIFIED annotate:** `readback` merges `{"verification": {"verdict": "unverified"}}` onto the bare `ToolMessage.content` and returns a `model_copy`. The adapter yielded a `tool_result` frame whose `result` JSON was `{"message_id": "m1", "verification": {"verdict": "unverified"}}`, with `blocked == False`. **The annotation survived, merged (did not replace) the dispatcher payload (`message_id` intact), and did not flip `blocked`.** PASS.

**Sub-probe 2 — CONTRADICTED escalate-first:** `readback` annotates `{"verification": {"verdict": "contradicted", "escalation": {capability, artifact_ref, observed}}}`. The `tool_result` frame carried the full `verification.escalation` block and was **still `blocked == False`.** This confirms escalate-first semantics: a CONTRADICTED verdict on an **already-executed** write SURFACES the escalation on the frame — it does **not** block the write (the write already landed) and does **not** auto-run any compensator. PASS.

Actual stdout (both sub-probes):

```
Sub-probe 1 — UNVERIFIED annotate survives SSE tool_result (blocked==False)
  tool_result carrying a `verification` key present? True
    annotated content = {'message_id': 'm1', 'verification': {'verdict': 'unverified'}}
    verification.verdict = 'unverified'  (expect 'unverified')
    frame.blocked = False  (expect False)
    original dispatcher key survived? message_id='m1'
  Sub-probe 1 verdict: PASS

Sub-probe 2 — CONTRADICTED escalate-first surfaces, still blocked==False
  tool_result carrying a `verification` key present? True
    annotated content = {'message_id': 'm1', 'verification': {'verdict': 'contradicted',
      'escalation': {'capability': 'mock.write', 'artifact_ref': {'kind': 'message', 'id': 'm1'},
      'observed': 'write landed but post-write read disagrees'}}}
    verification.verdict = 'contradicted'  (expect 'contradicted')
    verification.escalation present? True  (expect True)
    frame.blocked = False  (expect False — surface, not block)
  Sub-probe 2 verdict: PASS

OVERALL: ALL ASSERTIONS HELD — 7C mechanism CONFIRMED
```

No surprises. The bare-`ToolMessage` path behaves identically to 7B2's `Command` path for annotation survival:

- `model_copy(update={"content": …})` on the dispatcher's `ToolMessage` survives deepagents' `ToolNode` and the `stream_deep_agent_events` `messages`-channel handling to the `tool_result` frame **unchanged** — no re-serialization or content stripping.
- The adapter reads `result = msg.content` (the annotated JSON string) and `blocked = getattr(msg, "status", None) == "error"`. Because `readback` leaves `status="success"` untouched, `blocked == False` for both UNVERIFIED and CONTRADICTED. **The `stream_adapter` is untouched.**
- The annotation is a KEY inside the content-JSON, not a status change — exactly as required (there is no third `status` state for "unverified"/"contradicted"; the binary `success`/`error` mapping stays intact).

## Verdict

**Design CONFIRMED.** A read-back `@wrap_tool_call` inner of `write_lock`, reading the bare dispatcher `ToolMessage`, re-annotating a `verification` content-JSON key via `model_copy`, and returning it, survives the full chain + adapter to a `tool_result` SSE frame with `blocked == False` and NO `stream_adapter` change. Both the UNVERIFIED annotate path and the CONTRADICTED escalate-first (surface-not-block) path hold offline. Phase 1 (`src/deep_runtime/middleware/readback.py`, dormant behind `deep_readback_enabled=False`) proceeds unchanged.

**Note (faithful but partial):** the probe exercises the middleware **shape** and the annotation-survival path with a directly-supplied verdict — it deliberately does NOT wire the real `src/services/verification/` `ReadBackVerifier`/`is_write_verification_required` predicate (that logic and its verdict values are proven independently by the Step-3 package's own tests, reused verbatim in 7C). The spike's sole job was the UNPROVEN seam: annotation of a bare `ToolMessage` surviving to the SSE frame without an adapter change. That seam is proven.
