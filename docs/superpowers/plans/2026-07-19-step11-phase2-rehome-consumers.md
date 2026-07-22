# Step 11 · Phase 2 — Re-home 12 shared-SDK consumers onto `UtilityLLM`

> Detailed JIT plan for Phase 2 of `2026-07-19-step11-legacy-retirement-implementation.md`.
> Grounded against live code (2 scout card-sets, 2026-07-19). Behavior-preserving seam swap.

## Standing pattern (every consumer)

Replace `await <client>.messages.create(model=M, max_tokens=N, system=S, messages=[{"role":"user","content":U}], [temperature=T], [+{"role":"assistant","content":P}])`
and its `response.content[0].text` (or block-join) with:

```python
from src.llm.utility import complete_text  # add at module top
...
text = await complete_text(system=S, user=U, tier=<"haiku"|"resolved">, max_tokens=N,
                           [temperature=T], [prefill=P])
```

- **Keep each consumer's existing parse verbatim** (`parse_llm_json(...)`, `.model_validate(...)`,
  `.get(...)`, prefill re-prepend) — `complete_text` returns exactly the raw text the SDK returned.
- **tier:** `get_haiku_model()` / `MODEL_TIERS["haiku"]` → `"haiku"`; `settings.resolved_model` → `"resolved"`.
- **Client-teardown policy:**
  - *Module-function* `client` params → **drop now**, update callers (cheap, clean).
  - *Constructor* `client` params / `self._client` from `get_anthropic_client` → **keep now, unused**;
    teardown is Phase 5 (factory deletion), one place.
- **Test:** re-point the consumer's test to patch `complete_text` at the consumer module path
  (e.g. `src.services.risk_assessor.complete_text`, an `AsyncMock` returning the raw text). The
  re-pointed existing test IS the characterization test (same text in → same parsed output).
- **Drop vestigial `model` override params** where they only defaulted to the tier (no prod caller
  passes a custom model); rework the 2 override tests to assert `tier`.

## Per-consumer table

| # | Consumer (file) | tier | system | temp | prefill | client policy | test target |
|---|---|---|---|---|---|---|---|
| **G1 Governance** |
| 1 | `services/risk_assessor.py::assess_risk` | haiku | str | — | — | **drop** param (cascade: `get_or_assess_risk`, `trust_gate.py:67`, `agent_invoker.py:398-402`); drop `model` | `test_risk_assessor.py` (mock_client → `complete_text`) |
| 2 | `deep_runtime/middleware/governor_delegate_critique.py::_safe_critique` | haiku | str | — | — | **drop** `client` kwarg on factory + its `agent_invoker` wiring; drop `model` | `test_governor_delegate_critique.py` + `test_critique_injection.py` (call_args → complete_text kwargs) |
| **G2 Perception/ingest** |
| 3 | `services/event_processor.py::_score_event` + `_score_events_batch` | resolved | str | — | — | keep `self._client` | `test_event_processor.py` (patch `event_processor.complete_text`) |
| 4 | `services/relevance_assessor.py::assess_relevance` | haiku | **None** | — | — | **drop** param (caller `perception_runner.py:378`); drop `model` | `test_relevance_assessor.py` (:223 model-override → tier) |
| 5 | `services/world_model.py::extract_from_text` + `_call_extraction` | resolved | str | — | — | keep `self._client` | `test_world_model.py` (patch `world_model.complete_text`) |
| **G3 Memory** |
| 6 | `services/memory_service/extraction.py::_call_extraction` + `_call_preference_extraction` | resolved | str | — | — | keep `self._client` (`_base`) | `test_memory_service.py` (patch `...extraction.complete_text`) |
| 7 | `services/memory_service/contradictions.py::_check_contradiction_pair` | resolved | str | — | — | keep `self._client` (`_base`) | `test_memory_cascade_delete.py` (patch `...contradictions.complete_text`) |
| **G4 Execution/verify** |
| 8 | `services/step_runner.py::minimal_claude_action` | resolved | str | — | — | keep ctor `client`/`self._client` | NEW test patching `step_runner.complete_text` (capture raw into local — fallback reads text twice) |
| 9 | `services/verifier.py::_llm_judge` | resolved | str | — | **`"{"`** | keep `self._client` | `test_verifier_service.py` (re-point `_client.messages.create` → `complete_text`; keep `"{" +` prepend) |
| **G5 Context/presentation** |
| 10 | `orchestrator/context_assembler.py::_summarize_history` | haiku | **block-list** | **0** | — | keep ctor `client`/`self._client` | `test_context_assembler.py` (NEW patch `context_assembler.complete_text`); **TEXT output — no parse**, drops `"".join` |
| 11 | `services/presenter.py::_call_meeting_prep` + `_call_claude` | resolved | str | — | — | keep `self._client` | `test_ontology.py` (:172 system assert → complete_text kwarg) |
| **G6 Chat routing** |
| 12 | `orchestrator/intent_classifier.py::classify_intent` | haiku | **block-list** | **0** | — | **drop** `client`+`model` params (caller `chat_processor.py:461`); wholesale-patch tests unaffected | `test_intent_to_plan.py:44,56` (drop client arg → patch `intent_classifier.complete_text`) |

## Task order (each group = commit(s) + full gate; TDD RED→GREEN per consumer)
1. **G1 governance FIRST** (load-bearing — risk_assessor feeds 3 deep middlewares; hardest review).
2. G2 perception/ingest. 3. G3 memory. 4. G4 execution/verify (verifier prefill care).
5. G5 context/presentation (context_assembler text-not-JSON care). 6. G6 chat routing.

**Per consumer:** re-point/author test (RED) → swap the call + drop params per policy → update callers
→ run consumer test (GREEN) → group full gate. **Parallel reviewers** at the G1 checkpoint and at
Phase-2 close. Byte-neutral elsewhere; ZERO migrations; single head `1a2770a28c39`.

## Watch-items (from grounding)
- **verifier**: `complete_text(..., prefill="{")` returns the continuation; keep verifier's own
  `text = "{" + (result or "")` re-prepend → byte-identical parse.
- **step_runner**: capture into a local (`raw = await complete_text(...)`) — the JSONDecodeError
  fallback reads the text twice.
- **context_assembler / intent_classifier**: block-list `system=[{"type":"text",...}]` + `temperature=0`;
  the `"".join(b.text ...)` collapses to the returned string (no parse for summarize).
- **risk_assessor cascade** is the only cross-file signature change on the surviving deep path —
  verify `get_or_assess_risk` callers (`trust_gate`, `agent_invoker`) compile + tests pass.
- Dead `if use_bedrock` branches inside `context_assembler._summarize_history` (model resolve) become
  removable now (the swap routes through `complete_text`); Bedrock consts fully deleted in Phase 5.
