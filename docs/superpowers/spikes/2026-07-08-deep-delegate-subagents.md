# Spike decision — Step 7B2 deep-delegate subagents (gate / stream / GP-disable / critique)

**Date:** 2026-07-08 · **Branch:** `rebuild/first-principles` @ `da337c8` (plan) · **Status:** ALL PROBES PASS — plan proceeds to Phase 1 UNCHANGED.

**Probes (offline, no API key, no Postgres):**
- `backend/spikes/deep_delegate/subagent_gated_probe.py` — Phase 0.1 (gating, both build methods + negative control), 0.2 (child streaming characterization), 0.3 (GP disable + key-scoping + negative control).
- `backend/spikes/deep_delegate/critique_probe.py` — Phase 0.4 (lead-side `@wrap_tool_call` reads + annotates the `task` `Command` + negative control).

Run: `uv run python spikes/deep_delegate/subagent_gated_probe.py` and `… critique_probe.py` (each exits 0 = all assertions held).

---

## The four questions and their answers

### 0.1 — Does a read-only child, registered via `create_deep_agent(subagents=[…])`, run GATED when invoked through the built-in `task` tool?

**YES — both build methods gate cleanly, offline.**

- Method **A** = `CompiledSubAgent{name, description, runnable=build_deep_agent(perceiver_cfg, child_shells, workspace_id, db_factory, extra_middleware=(dispatcher,))}` — the child's `capability_scope` is installed by `build_deep_agent` itself; the gate is baked into the child's OWN compiled graph.
- Method **B** = raw `SubAgent{…, model, tools, middleware=[capability_scope, dispatcher]}` — deepagents' `create_sub_agent` assembles the child's own middleware from the spec.

Both: the in-scope read (`internal.search`) reached the child's `jarvis_tool_dispatcher.execute_tool` (recorded), and the out-of-scope write (`email.send`) was DENIED by the child's own `capability_scope` → `ToolMessage(status="error")`, never dispatched. **Negative control:** stripping the child's `capability_scope` lets the out-of-scope write execute → proves the CHILD gate (not the parent) does the work. This confirms Fork-2: parent middleware does NOT reach children; each child carries its own gate.

**DECISION → Method A (`CompiledSubAgent{runnable=build_deep_agent(...)}`).** Rationale: A reuses the exact gated builder the lead uses (same `make_capability_scope_middleware` install path + the fail-closed `ValueError` refuse-to-compile guard for write-capable agents), so the delegate's gate is guaranteed identical in construction to the lead's — no reliance on deepagents re-assembling child middleware. Method B stays the documented fallback (it also passed).

### 0.2 — Does the child's streamed output corrupt the frozen SSE contract (double-emission / mis-attribution)?

**NO. The frozen 8-frame SSE contract survives UNCHANGED — no adapter change, no Phase 4b.**

Empirical finding: even when the child ran its full two-turn path and emitted a free-text reply (`"CHILD-REPLY: found 42 results."`), that text did **not** appear in any parent-attributed `text_delta` frame. The parent stream carried only the LEAD's text deltas (`langgraph_node: "model"`, `checkpoint_ns: "model:…"`); the child's summary rode back solely in the `task` `tool_result` frame. Mechanism (confirmed): `deepagents`' `task` runs the child via `.ainvoke` (blocking, single-shot) inside the tool; with `subgraphs=False` on the parent `astream(stream_mode=["messages","updates"])`, the child's `.ainvoke` token deltas are NOT surfaced in the parent `messages` channel.

Consequence: the plan's mitigation ladder (structured `response_format`, `subgraphs=True`+attribution, node-metadata filter) is **not needed**. `stream_adapter.py` is untouched.

### `response_format` — optional, not load-bearing

Because 0.2 proved the child's free-text reply cannot leak, `response_format=DELEGATE_RESPONSE_FORMAT` is **not** required for streaming safety. It remains a Phase-2 enhancement for a cleaner critique input (a structured summary mirroring the Perceiver JSON contract, `prompts.py:384-399`). Adding it requires `build_deep_agent` to forward `response_format` into `create_deep_agent` (a small, additive change). **Phase 2 decides** whether the cleaner-summary value justifies threading it through; if adopted, `build_deep_agent(response_format=…)` is the seam. Either way the 8-frame contract is unaffected.

### 0.3 — Can the ambient general-purpose (GP) `task` child be disabled?

**YES — via a process-global `HarnessProfile` keyed `"anthropic:<model_name>"`, and it is key-scoped.**

`build_deep_agent` passes a pre-built `ChatAnthropic` (a `BaseChatModel`, not a `"provider:model"` string). `create_deep_agent`'s `_model_spec` is therefore `None` (`graph.py:548`), so `_harness_profile_for_model` derives the key from the model instance: `get_model_provider` → `"anthropic"`, `get_model_identifier` → `_string_attr(model,"model_name") or _string_attr(model,"model")` (`_models.py`), which for a real `ChatAnthropic` resolves via the `.model` fallback (it has no `.model_name` attr) to e.g. `"claude-sonnet-4-6"` ⇒ lookup key `"anthropic:claude-sonnet-4-6"` (then provider-only `"anthropic"` fallback). Verified against a live `ChatAnthropic` (review): key is `anthropic:claude-sonnet-4-6`; the spike's `ScriptedModel` reproduces the same key via a `model_name` field + forced `_get_ls_params` (a faithful, different-branch stand-in). Registering `HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))` under `"anthropic:<lead_model_name>"` made the `"general-purpose"` subagent ABSENT while `task` still routed to `"researcher"`. **Negative control:** without the registration, `"general-purpose"` is present. **Key-scoping:** a lead built on a DIFFERENT model (`anthropic:claude-opus-4-8`) was UNAFFECTED (GP present) — the model-scoped key does not disable GP process-wide.

**DECISION → model-scoped HarnessProfile registration** (`register_harness_profile("anthropic:<model_name>", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))`), preferred over provider-wide `"anthropic"` (which would disable GP for every anthropic `create_deep_agent` in the process). Registration is process-global and idempotent; the delegate builder registers under the lead agent's resolved model id. The name-collision shortcut (supply a `"general-purpose"`-named spec) also works but conflates our delegate's identity — not chosen. **Introspection method:** allowed subagent types were read behaviorally off `task(subagent_type="__list__")`'s "the only allowed types are …" error string (the `subagent_graphs` keys are a closure, not directly attribute-accessible).

**Activation caveat (Step 8/10):** the GP-disable key must match the LIVE lead's model. Since 7B2 is scaffold-only (no live lead→delegate routing), the wiring registers under the routed agent's resolved model id at build time; a provider-global re-audit is a documented Step-10 activation gate.

### 0.4 — Can a lead-side `@wrap_tool_call` read + annotate the `task` `Command`?

**YES.** A `@wrap_tool_call` middleware that does NOT skip `task` runs the delegate (`result = await handler(request)` → the `task` tool → the child), receives a `langgraph.types.Command`, reads the summary at `result.update["messages"][0].content`, merges `"unreviewed"`/`"critique"` into the content JSON, and returns a **rebuilt `Command`** (`update={**result.update, "messages":[new ToolMessage]}`). The annotation survived through the adapter to the `task` `tool_result` frame: clean verdict → `unreviewed:false`; flagged verdict → `unreviewed:true` + concerns, result STILL returned (`blocked:false`, fail-open for a read-only delegate). **Negative control:** a middleware that SKIPS `task` (builtin exemption, like every current gate) produced NO annotation — proving the non-skip is load-bearing.

**DECISION → critique = lead-side `@wrap_tool_call` on `task`, placed OUTER of `SubAgentMiddleware`, returning a rebuilt `Command`.** Command-unwrap recipe: `result.update["messages"][0]` is the summary `ToolMessage`; rebuild via `Command(update={**result.update, "messages":[ToolMessage(annotated_content, tool_call_id=…, name=…, status=…)]})`. Annotation channel = a key inside the content JSON (`ToolMessage.status` is binary success/error — no room for a 3rd "unreviewed" state; the SSE mapping keys `blocked` off `status=="error"`). Fail-open-annotated for reads (never a block); fail-closed for writes (defensive, unreached in 7B2's read-only delegate).

---

## Net decisions carried into Phases 1–7

1. Build method = **A (`CompiledSubAgent{runnable=build_deep_agent(...)}`)**; B is the fallback.
2. **No `stream_adapter` change / no Phase 4b** — the 8-frame contract is intact.
3. `response_format=DELEGATE_RESPONSE_FORMAT` = **optional Phase-2 enhancement** (cleaner critique input), needs `build_deep_agent(response_format=…)` forwarding if adopted; NOT a streaming requirement.
4. GP-disable = **model-scoped `HarnessProfile` under `"anthropic:<model_name>"`**, idempotent, key-scoped; provider-wide re-audit = Step-10 gate.
5. Critique = **lead-side `@wrap_tool_call` on `task`, OUTER of `SubAgentMiddleware`, rebuilt `Command`**, content-JSON annotation, fail-open reads / fail-closed writes.
6. Everything DORMANT behind `JARVIS_RUNTIME=deep` AND `deep_delegates_enabled=False`; flag-off `deep` = byte-identical to 7B1; no live lead→delegate routing (Step 8/10).
