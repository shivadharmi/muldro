# Spike: deep inline-format reply + surface block, and `@after_model` extraction

**Date:** 2026-07-08 · **Task:** Step 7B1, Task 0.1 + 0.2 (DECISION GATE)
**Status:** ✅ **PASS (both) — the deep-collapse assumptions hold offline. Not blocked.**
**Probes:** `backend/spikes/deep_collapse/inline_format_probe.py`,
`backend/spikes/deep_collapse/extraction_mw_probe.py` (throwaway; run offline, no API key)
**Run:** `cd backend && uv run python spikes/deep_collapse/inline_format_probe.py`
and `… spikes/deep_collapse/extraction_mw_probe.py`
**Versions:** deepagents 0.6.11, Python 3.13, `rebuild/first-principles`.

---

## DECISION

> **Both 7B1 dormant assumptions are PROVEN offline.**
> **(0.1)** A real `create_deep_agent(model=<scripted streaming fake>, tools=[])` can
> emit a Presenter-voice chat reply followed by a fenced ` ```json:surface ` block in
> ONE streamed message; the fence + JSON survive token-by-token streaming intact even
> when split MID-marker/MID-JSON across chunk boundaries, and the *existing*
> `surface_mapping` parsers (`strip_surface_blocks`, `extract_surface_spec`) consume the
> reconstructed `agent_done.text` unchanged (`should_surface is True`). The inline-format
> collapse (no separate surface-builder call) is viable.
> **(0.2)** An `@after_model` middleware of the same shape as `make_budget_middleware`
> fires **exactly once** per text-only turn inside a real `create_deep_agent` and its
> async body reads `state["messages"]` (first human + last AI) and awaits an injected
> async extractor with the turn's human text present. Re-homing Librarian extraction
> onto `@after_model` is viable.

Both tasks can proceed. Neither disproved.

---

## Spike 0.1 — inline-format streams reply + surface block ✅ PASS

### What the probe proves

A custom `BaseChatModel` (`SurfaceStreamingFakeModel`) streams the full Presenter
output as **17 text deltas of ≤7 chars each**, deliberately slicing the
` ```json:surface ` fence marker AND the JSON body across chunk boundaries (e.g. the
first three deltas are `'Here is'`, `' your s'`, `'ummary.'`). `tools=[]`, single
turn. The frames are reconstructed by the real `stream_deep_agent_events` adapter.

The exact fence syntax + JSON shape are **dictated by `src/services/surface_mapping.py`**,
not invented:
- `_SURFACE_SPEC_RE = re.compile(r"```json:surface\s*\n(.*?)\n```", re.DOTALL)`
- `SurfaceSpec` (in `src/contracts/__init__.py`) requires `kind` (a valid `SurfaceKind`,
  e.g. `"summary"`) + `title`; `should_surface` defaults to `False`, so the probe sets
  it `True` explicitly.

The streamed message (verbatim `repr`):

```
'Here is your summary.\n\n```json:surface\n{"should_surface": true, "kind": "summary", "title": "Weekly Summary"}\n```'
```

### Observed frame shapes (verbatim from the run)

```
frame event sequence: ['agent_start', 'text_delta' ×17, 'agent_done']
text_delta frame count: 17
first 3 text_delta texts: ['Here is', ' your s', 'ummary.']

agent_done.text (repr):
  'Here is your summary.\n\n```json:surface\n{"should_surface": true, "kind": "summary", "title": "Weekly Summary"}\n```'

strip_surface_blocks(agent_done.text) -> 'Here is your summary.'
extract_surface_spec(agent_done.text) -> SurfaceSpec(should_surface=True, kind='summary',
    title='Weekly Summary', subtitle=None, status=None, priority=None, metrics=[], tags=[])
```

### Assertions (all held)

- **(a)** reply arrives as `text_delta` frames **and** the joined `agent_done.text`
  contains `REPLY_TEXT`; further, `agent_done.text == FULL_TEXT` byte-for-byte — streaming
  reconstruction did **not** mangle the text.
- **(b)** `strip_surface_blocks(agent_done.text) == "Here is your summary."` — the fence
  is fully removed and the fence marker does not leak into the clean reply.
- **(c)** `extract_surface_spec(agent_done.text)` parses the block; `.should_surface is True`,
  `.kind == "summary"`, `.title == "Weekly Summary"`.

### Key finding

The surface block is **not** streamed as a distinct event type — it rides the ordinary
`text_delta`/`agent_done.text` channel. The adapter accumulates every `text_delta` into
`text_parts` and joins them for `agent_done.text` (see `stream_adapter.py`
`text_parts.append(text)` → `"".join(text_parts)`), so a fence split across chunk
boundaries is transparently re-assembled before any parsing happens. The inline-format
collapse therefore needs **no adapter change**: the Presenter's surface spec is recovered
by running the *existing* `surface_mapping` parsers on `agent_done.text` after the stream
completes. (The clean chat reply for the user is `strip_surface_blocks(agent_done.text)`;
the workspace surface is `extract_surface_spec(agent_done.text)`.)

---

## Spike 0.2 — `@after_model` extraction middleware fires + drives async extraction ✅ PASS

### What the probe proves

An `@after_model` middleware built exactly like `make_budget_middleware`'s hook shape
(`from langchain.agents.middleware import after_model`; async body `(state, runtime) -> None`
reading `state["messages"]`) is attached via `create_deep_agent(model=<fake>, tools=[],
middleware=[the_mw])`. Its body pulls the first `HumanMessage` + the last `AIMessage`
text from `state["messages"]` and `await`s an injected `AsyncMock` extractor. One turn is
invoked with `agent.ainvoke({"messages":[{"role":"user","content":"remember Bob works at
Acme"}]}, {"configurable":{"thread_id":"spike2"}})`.

The fake model emits a **single text-only turn (no tool call)**, so the agent loop runs
the model once → `after_model` fires once.

### Observed behavior (verbatim from the run)

```
final message types: ['HumanMessage', 'AIMessage']
extractor.await_count = 1 (expect 1)
extractor.await_args  = call('remember Bob works at Acme', "Noted — I'll remember that Bob works at Acme.")

extractor called with human_text = 'remember Bob works at Acme'
extractor called with ai_text    = "Noted — I'll remember that Bob works at Acme."
```

### Assertions (all held)

- `extractor.await_count == 1` — `@after_model` fired **exactly once** for the single turn.
- the turn's human text (`"remember Bob works at Acme"`) is present in the extractor's
  first positional arg.
- the turn's AI reply is present in the extractor's second positional arg.

### Key finding

`@after_model` gives the final post-model state with both the human turn and the assistant
message attached, and (decorated on an `async def`) its async body runs to completion inside
the graph — an async DB/extraction side effect needs no thread hop. This is the same hook
`make_budget_middleware` already relies on, so re-homing Librarian entity/memory extraction
onto it is a proven pattern. Fire-count parity note: `@after_model` fires **once per model
call**, so a multi-turn (tool-using) run would fire it multiple times; the extraction
re-home must be idempotent / turn-terminal-aware if applied to tool-taking agents (out of
scope for this offline gate — the probe uses a single text-only turn, one model call).

---

## Caveats / scope

- **Offline + scripted only.** Both probes drive a fake `BaseChatModel`; no Anthropic API,
  no real token stream. This proves the *mechanics* (fence survives chunk re-assembly;
  `@after_model` fires and its async body runs) — it does **not** prove a real Anthropic
  model will *choose* to emit a well-formed fence, nor real cache behaviour. Those belong to
  the live smoke (same gate class as `2026-07-06-langgraph-stream-to-sse.md`).
- **0.2 fire-count** is asserted for a single text-only turn (one model call). For
  tool-taking agents `@after_model` fires per model round; a real extraction re-home must be
  turn-terminal-aware. Not built or tested here.
