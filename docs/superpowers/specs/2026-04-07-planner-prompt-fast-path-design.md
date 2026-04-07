# Spec 1B-i: Planner Prompt Rewrite + Fast Path Expansion

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1A (Capability Infrastructure) — needs PlanOutput, capability summary, discover_capabilities tool
**Builds toward:** Spec 1B-ii (Routing Migration + Agent Merge + Cleanup)

## Problem Statement

The Planner prompt produces classification labels (19 decision types) instead of goal-decomposed plans. The fast path handles only 6 intent types. This spec rewrites the Planner prompt to produce `PlanOutput` and expands the fast path — without changing routing or deleting any existing code. The existing system continues to work alongside the new prompt.

## Design

### Component 1: Rewritten Planner Prompt

Replace `PLANNER_PROMPT` and `JARVIS_DECISION_FRAMEWORK` with a new prompt that produces `PlanOutput`. The capability summary (Spec 1A) is injected dynamically.

Full prompt defined in spec (see parent 1B for complete prompt text with examples). Key structure:
- `<role>`: Goal decomposition, not classification
- `<available_capabilities>`: Dynamic from `generate_capability_summary()`
- `<instructions>`: 7-step decomposition process
- `<output_format>`: PlanOutput JSON schema
- `<examples>`: 3 examples (multi-step, write action, partial achievability)

### Component 2: `extract_plan()` Function

New function in `intent_classifier.py` that parses raw Planner JSON into `PlanOutput`:

```python
def extract_plan(response_text: str) -> PlanOutput:
    """Parse Planner agent response into validated PlanOutput."""
    raw = parse_llm_json(response_text)
    return PlanOutput.model_validate(raw)
```

Replaces `extract_decision()` (which returns `PlannerOutput`). Both coexist until Spec 1B-ii.

### Component 3: Expanded Fast Intents

Add 4 new fast intents to `FAST_INTENTS`:
- `direct_answer` — answerable from context
- `single_read` — one read capability needed
- `memory_operation` — store/recall
- `acknowledgment` — user confirming

### Component 4: `intent_to_plan()` Function

New function that generates lightweight `PlanOutput` from fast intents:

```python
def intent_to_plan(intent: str, message: str, capabilities: list[str]) -> PlanOutput:
    if intent in ("greeting", "chitchat", "acknowledgment"):
        return PlanOutput(goal=message, steps=[PlanStep(description="Respond", capability="respond")], priority="low")
    if intent == "single_read":
        cap = _match_read_capability(message, capabilities)
        return PlanOutput(goal=message, steps=[PlanStep(description=message, capability=cap, risk="none")])
    # ... etc for each fast intent
```

Replaces `intent_to_decision()`. Both coexist until Spec 1B-ii.

### Component 5: `_match_read_capability()` Helper

Keyword-based matcher for fast path single-read intents:
- email/mail/inbox → `email.search`
- calendar/schedule/meeting → `calendar.read`
- slack/message/channel → `messaging.read`
- github/pr/issue → `repo.read`
- fallback → `knowledge.search`

### Component 6: Perceiver Prompt (New)

New `PERCEIVER_PROMPT` added to `prompts.py` — merges Observer + Researcher responsibilities into a single read-only information gathering agent. The old prompts are NOT deleted yet (that's Spec 1B-ii).

## Files Changed

### Modified Files
- `src/orchestrator/prompts.py` — ADD new `PLANNER_PROMPT_V2` and `PERCEIVER_PROMPT` (alongside existing prompts, not replacing)
- `src/orchestrator/intent_classifier.py` — ADD `extract_plan()`, `intent_to_plan()`, `_match_read_capability()`, expanded `FAST_INTENTS`. Keep existing `extract_decision()`, `intent_to_decision()`.

### NOT Modified (saved for Spec 1B-ii)
- `src/orchestrator/jarvis.py` — untouched (still uses old routing)
- `src/orchestrator/agents.py` — untouched (observer/researcher still exist)
- `src/orchestrator/contracts.py` — untouched (`PlannerOutput` still exists; `PlanOutput` already added in Spec 1A)
- `src/services/route_resolver.py` — untouched
- `src/services/graph_executor.py` — untouched
- All frontend files — untouched

## Testing Strategy

- Unit tests: Planner prompt with sample inputs → verify PlanOutput JSON structure
- Unit tests: `extract_plan()` parses valid JSON, handles malformed input
- Unit tests: `intent_to_plan()` for each of 10 fast intents
- Unit tests: `_match_read_capability()` keyword matching
- Unit tests: expanded FAST_INTENTS includes all new intents
- Quality tests: 10 sample user messages → verify Planner produces reasonable plans

## Success Criteria

1. New Planner prompt produces valid PlanOutput JSON for complex requests
2. `extract_plan()` reliably parses Planner responses
3. `intent_to_plan()` handles all 10 fast intents correctly
4. Existing `extract_decision()` and `intent_to_decision()` still work
5. Old routing continues unchanged

## Blast Radius

**Minimal — 2 files modified, all additive.**

| File | Change | Risk |
|------|--------|------|
| `src/orchestrator/prompts.py` | ADD 2 new prompt constants | **Minimal** — existing prompts untouched |
| `src/orchestrator/intent_classifier.py` | ADD 3 functions + expand constant | **Low** — existing functions untouched |

### Total: ~5 files (2 modified, 3 new test files)
