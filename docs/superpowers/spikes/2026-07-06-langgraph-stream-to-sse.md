# Spike: LangGraph `astream` → the 7 frozen chat-SSE dict shapes

**Date:** 2026-07-06 · **Task:** Step 6A, Task 0 (DECISION GATE)
**Status:** ✅ **PASS — approach is feasible. Not blocked.**
**Probe:** `backend/spikes/deep_stream/probe.py` (throwaway; runs offline, no API key)
**Run:** `cd backend && uv run python spikes/deep_stream/probe.py`

---

## DECISION

> **Adapter uses `stream_mode=["messages","updates"]`; telemetry via `usage_metadata`
> (summed across AIMessages) + `BudgetTracker.calculate_cost` + `time.monotonic()`;
> `blocked` via `ToolMessage.status=="error"` today, with a `stream_mode="custom"`
> writer marker RECOMMENDED for an unambiguous, tool-name-bearing `blocked=True`;
> caching CONFIRMED-STRUCTURAL (flattened `system_prompt` reaches the model as one
> unsplit text block) — real cache-hit proof (`cache_read_input_tokens>0` on turn 2)
> DEFERRED to the live smoke.**

The adapter task can proceed. Token-level streaming through a compiled deepagents
LangGraph agent is real, and every one of the 7 frozen shapes is reconstructable.

---

## What the probe proves

A custom `BaseChatModel` subclass (`ScriptedFakeChatModel`) streams scripted
`AIMessageChunk`s mirroring `langchain-anthropic`'s wire shape:
- **thinking** deltas → content block `{"type":"thinking","thinking":…}`
- **text** deltas → content block `{"type":"text","text":…}`
- **tool call** → `tool_call_chunks=[tool_call_chunk(name,args,id,index)]`
- **terminal chunk** → `usage_metadata` + `response_metadata.stop_reason`

Scripted for **two turns** (turn 1: thinking + text + `echo` tool call; turn 2:
final text), so `create_deep_agent(model=fake, tools=[echo], checkpointer=MemorySaver())`
actually runs the tool then re-invokes the model. The `echo` body ran exactly once.

---

## Per-shape reconstruction (verbatim from the run)

| # | Shape | Source `stream_mode` | Status |
|---|-------|----------------------|--------|
| 1 | `agent_start` | **synthesized pre-stream** (agent+model known before `astream`) | ✅ |
| 2 | `thinking` | **`messages`** — `AIMessageChunk` content block `type=="thinking"` → `is_thinking=True` | ✅ |
| 3 | `text_delta` | **`messages`** — `AIMessageChunk` content block `type=="text"` | ✅ |
| 4 | `tool_call` | **`updates`** — full `AIMessage.tool_calls` (name + parsed `args`) | ✅ |
| 5 | `tool_result` | **`messages`** — `ToolMessage`; `blocked ← status=="error"` | ✅ |
| 6 | `agent_done` | **synthesized at stream end** — summed `usage_metadata` + `BudgetTracker` + `monotonic` | ✅ |
| 7 | `error` | **synthesized** — `astream` exception caught → sanitized frame (raw NEVER emitted) | ✅ |

Reconstructed dicts printed by the probe:

```
agent_start  {'event':'agent_start','agent':'presenter','model':'claude-sonnet-4-6'}
thinking     {'event':'thinking','agent':'presenter','text':'echo this.','is_thinking':True}
text_delta   {'event':'text_delta','agent':'presenter','text':"'hello'."}
tool_call    {'event':'tool_call','agent':'presenter','tool':'echo','input':{'text':'hello'}}
tool_result  {'event':'tool_result','agent':'presenter','tool':'echo','result':'echo: hello','blocked':False,'latency_ms':1}
agent_done   {'event':'agent_done','agent':'presenter','text':"Let me echo that.Done — echoed 'hello'.",
              'input_tokens':180,'output_tokens':37,'cache_creation_tokens':0,'cache_read_tokens':240,
              'tools_called':['echo'],'latency_ms':7,'cost_usd':0.001167}
error        {'event':'error','agent':'presenter','code':'internal_error',
              'message':'An internal error occurred.','correlation_id':'corr_probe_0001'}
```

(`thinking`/`text_delta` show the *last* delta only because the probe overwrites a
dict slot for the summary; the real adapter emits **one event per delta** as it
streams — the raw dump in Section 1 shows every chunk arriving individually.)

---

## Precise object types yielded

- **`stream_mode="messages"`** yields **`(message, metadata)` tuples**:
  - `message` is an `AIMessageChunk` for model output, or a full `ToolMessage` for
    tool output (tool results are not token-streamed — they arrive whole).
  - `metadata` is a dict carrying `langgraph_node`, `ls_integration`, `thread_id`, etc.
- **`stream_mode="updates"`** yields **`{node_name: {"messages":[...]}}` dicts**:
  - node `"model"` → a full `AIMessage` (complete `tool_calls`, `usage_metadata`).
  - node `"tools"` → a `ToolMessage`.
  - middleware nodes (`PatchToolCallsMiddleware.before_agent`,
    `TodoListMiddleware.after_model`, …) emit `None` payloads — skip them.
- **`stream_mode=["messages","updates"]`** yields **`(stream_mode_name, payload)`
  tuples**, interleaving the two above. This is the combination the adapter consumes.
- **`stream_mode="custom"`** yields whatever a middleware passes to
  `get_stream_writer()(...)` — used for the recommended `blocked` marker.

### `is_thinking` discrimination (text vs reasoning)

`langchain-anthropic` maps Anthropic streaming deltas onto `AIMessageChunk` content
blocks (verified in `langchain_anthropic/chat_models.py`):
- `text_delta` / `citations_delta` → block `{"type":"text","text":…}`
- `thinking_delta` / `signature_delta` → block `{"type":"thinking","thinking":…}`
- `input_json_delta` → `tool_call_chunks` (streamed tool args), block `{"type":"tool_use"}`

So the adapter discriminates on the **content block `type`**: `"thinking"` →
`thinking{is_thinking=True}`; `"text"` → `text_delta`. `redacted_thinking` arrives as
a `content_block_start` (no delta) — treat as thinking with empty/opaque text.

**Parity caveat (legacy `is_thinking=False`):** the legacy loop
(`agent_loop.py`) additionally relabels *plain text blocks that precede a tool call*
as `thinking{is_thinking=False}` ("reasoning text"). On the deep path those are
ordinary `type=="text"` blocks and will map to `text_delta`. There is **no native
`is_thinking=False` signal** in the LangGraph stream. The adapter implementer must
choose: (a) accept that deep-path pre-tool text is `text_delta` (simpler; a benign
divergence), or (b) buffer text emitted while a tool call is pending and re-emit it
as `thinking{is_thinking=False}` to preserve byte-for-byte parity. Recommend (a)
unless a frozen-contract parity test forces (b).

---

## Telemetry (`agent_done`): reconstructable in the adapter, no custom writer needed

- **Tokens:** each `AIMessageChunk` terminal chunk carries `usage_metadata`
  (`input_tokens`, `output_tokens`, `input_token_details.{cache_read,cache_creation}`).
  The adapter **sums across every AIMessage** in the run (turn 1 + turn 2 →
  `input=180, output=37, cache_read=240`). `usage_metadata` also rides the full
  `AIMessage` in `updates`, so either stream carries it.
- **Cost:** reuse `BudgetTracker().calculate_cost(model, in, out, cache_creation_input_tokens=…, cache_read_input_tokens=…)`
  — produced `cost_usd=0.001167`, matching a hand check. Emit as `round(cost, 6)`
  exactly like `agent_invoker.py:234`.
- **Latency:** `time.monotonic()` around the `astream` loop → `latency_ms`.
- **`tools_called`:** collected from `tool_call` events; `None` when empty (legacy shape).

No `custom` writer is required for telemetry.

---

## `blocked` — how a guard denial surfaces (and the recommendation)

Three sub-tests in Section 3:

- **3a (REAL guard):** `make_capability_scope_middleware(agent=…, db_factory=None)`
  with a non-empty scope that does not cover `echo` denies the call via the real code
  path. It surfaces as **`ToolMessage(status="error")`** with the real denial JSON
  (`{"error":"Agent 'probe_agent' is not permitted to call 'echo' — capability is
  outside its scope."}`), and the `echo` body **did not run** (0 calls). So
  `blocked=True` is reconstructable as `status=="error"`.
  - **CAVEAT:** the denial `ToolMessage` has **`name=None`**. To fill
    `tool_result.tool`, the adapter must recover the tool name from
    `ToolMessage.tool_call_id` → the preceding `tool_call`.
- **3b (genuine tool error):** a tool that *raises* does **not** become a
  `ToolMessage(status="error")` under deepagents' default `ToolNode`; the exception
  **propagates out of `astream`** (→ the sanitized `error` frame). So in the current
  wiring, a `status=="error"` `ToolMessage` is effectively always a guard denial.
- **3c (custom writer, RECOMMENDED):** a `@wrap_tool_call` middleware that calls
  `get_stream_writer()({"jarvis_event":"tool_blocked","tool":…,"blocked":True})` is
  surfaced verbatim by `stream_mode="custom"`. This gives an **unambiguous,
  tool-name-bearing** blocked signal independent of `ToolMessage` shape.

**Recommendation:** ship the adapter with `blocked ← status=="error"` (works today),
and have the capability-scope guard *also* emit the 3c custom marker so `blocked`
never depends on parsing error text or correlating a `name=None` ToolMessage. This is
a small, additive change to `capability_scope.py` in the guard's denial branch.

---

## Prompt caching (cost-regression guard): CONFIRMED-STRUCTURAL

Section 4b captured the system messages the model actually received. The flattened
`system_prompt` reaches the model as **one contiguous text block**
(`[{"type":"text","text":"<<MARKER>> …"}]`), marker intact, **not split** into
multiple blocks. `create_deep_agent` *appends* its own scaffolding **after** our
prompt inside the *same* block, so the whole prefix is a single cacheable unit.

- This is **structural only** (fake model, no API). It proves flattening does not
  fragment the prompt into multiple system blocks (which would break `cache_control`
  placement).
- 🔊 **ESCALATION FLAG (deferred, not resolved here):** the *live* cache-hit proof —
  `cache_creation_input_tokens>0` on turn 1 and `cache_read_input_tokens>0` on turn 2
  through `ChatAnthropic` + `AnthropicPromptCachingMiddleware` on the explicit
  `middleware=` shape — is **still pending an API key** and is owned by the live-smoke
  task. This is the same gate as `docs/superpowers/spikes/2026-06-28-prompt-caching.md`.
  Losing the soul/role cache is a real per-turn cost regression; treat "caching works
  on the deep path" as **assumed until the live smoke confirms it**, and if the live
  smoke shows `cache_read==0` on turn 2, add `AnthropicPromptCachingMiddleware`
  explicitly ahead of the policy middlewares and re-verify.

---

## `error` sanitation

Section 4a: when `astream` raises (a model/tool exception carrying a sensitive
detail), the adapter's outer `try/except` emits only the client-safe frame
(`code`, generic `message`, `correlation_id`) and logs the raw exception — never
emits it. This matches `agent_invoker.py:210-222` exactly.

---

## Concerns handed to the adapter implementer

1. **Sum `usage_metadata` across all AIMessages** (one per model turn) — a single
   final message under-counts multi-turn runs. Legacy sums `response.usage` per loop.
2. **`tool_call` from `updates`** (full `AIMessage.tool_calls`) is cleanest. If you
   reconstruct from `messages`, you must accumulate `tool_call_chunks` per index until
   the args JSON parses (real Anthropic streams args across many chunks; the probe
   sent them whole, which under-tests that path).
3. **Blocked `ToolMessage` has `name=None`** — recover the tool name via
   `tool_call_id`; better, adopt the 3c custom marker.
4. **`is_thinking=False` has no native signal** — decide parity strategy (a vs b above).
5. **Per-tool `latency_ms`** is not in the raw stream; time it between the `tool_call`
   and its `ToolMessage`, or emit it from a `wrap_tool_call` timer / custom writer.
6. **Filter middleware `None` updates** (`*.before_agent` / `*.after_model`).
7. **Live cache-hit smoke is a hard prerequisite** before declaring the deep path
   cost-neutral (see escalation flag).
```
