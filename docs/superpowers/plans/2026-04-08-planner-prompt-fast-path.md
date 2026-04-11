# Planner Prompt Rewrite + Fast Path Expansion (Spec 1B-i) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the intelligence layer (prompts + parsing functions) on top of Spec 1A's capability infrastructure — new Planner prompt producing PlanOutput, Perceiver prompt merging Observer+Researcher, extract_plan(), intent_to_plan(), expanded fast intents, and keyword-to-capability matcher.

**Architecture:** Six additive components across 2 source files (`prompts.py`, `intent_classifier.py`) and 3 test files. No existing code is modified or deleted — new prompts coexist alongside old ones, new functions coexist alongside old ones. The existing 19-decision-type routing continues to work unchanged. The 4 new fast intents degrade gracefully to "acknowledge" until Spec 1B-ii wires in the new routing.

**Tech Stack:** Python 3.12, Pydantic v2, pytest + pytest-asyncio, ruff (line-length 100)

**Dependencies from Spec 1A (already implemented):**
- `PlanOutput`, `PlanStep`, `CapabilityGap` models in `backend/src/orchestrator/contracts.py:327-367`
- `generate_capability_summary()` in `backend/src/orchestrator/capability_summary.py:87-131`
- `parse_llm_json()` in `backend/src/llm_utils.py:9-21`

**Files changed:**
- Modify: `backend/src/orchestrator/intent_classifier.py` — add `extract_plan()`, `intent_to_plan()`, `_match_read_capability()`, `_READ_CAPABILITY_KEYWORDS`, expand `FAST_INTENTS` + `_VALID_INTENTS` + `INTENT_CLASSIFIER_PROMPT`
- Modify: `backend/src/orchestrator/prompts.py` — add `PLANNER_PROMPT_V2`, `PERCEIVER_PROMPT`
- Create: `backend/tests/test_extract_plan.py`
- Create: `backend/tests/test_intent_to_plan.py`
- Create: `backend/tests/test_prompt_v2.py`

**NOT changed (Spec 1B-ii):** jarvis.py, agents.py, contracts.py, route_resolver.py, graph_executor.py, frontend files.

---

### Task 1: `_match_read_capability()` — Keyword-to-Capability Matcher

**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py` (add after `_VALID_INTENTS` block, around line 84)
- Create: `backend/tests/test_intent_to_plan.py` (will hold tests for Tasks 1+4)

This is the lowest-level building block — a pure function with no dependencies on other new code. Build it first so Task 4 can use it.

- [ ] **Step 1: Write the failing tests for `_match_read_capability()`**

Create `backend/tests/test_intent_to_plan.py` with the keyword matcher tests:

```python
"""Tests for intent_to_plan(), _match_read_capability(), and expanded FAST_INTENTS."""

from __future__ import annotations

import pytest

from src.orchestrator.intent_classifier import _match_read_capability


# ── Test _match_read_capability ──────────────────────────────────────


class TestMatchReadCapability:
    """Keyword-based capability matching for fast-path single-read intents."""

    # Standard capabilities list simulating a workspace with major services connected
    CAPS = [
        "email.search",
        "email.read",
        "calendar.list",
        "calendar.get",
        "messaging.get_history",
        "messaging.search",
        "repo.list_prs",
        "repo.search_code",
        "knowledge.search",
    ]

    def test_email_keywords(self):
        assert _match_read_capability("Check my email", self.CAPS) == "email.search"

    def test_mail_keyword(self):
        assert _match_read_capability("Any new mail?", self.CAPS) == "email.search"

    def test_inbox_keyword(self):
        assert _match_read_capability("Show my inbox", self.CAPS) == "email.search"

    def test_gmail_keyword(self):
        assert _match_read_capability("Open gmail", self.CAPS) == "email.search"

    def test_calendar_keywords(self):
        result = _match_read_capability("What's on my calendar today?", self.CAPS)
        # calendar.read not in CAPS, falls back to first calendar.* match
        assert result.startswith("calendar.")

    def test_schedule_keyword(self):
        result = _match_read_capability("Show my schedule", self.CAPS)
        assert result.startswith("calendar.")

    def test_meeting_keyword(self):
        result = _match_read_capability("Any meetings tomorrow?", self.CAPS)
        assert result.startswith("calendar.")

    def test_slack_keywords(self):
        result = _match_read_capability("Check Slack messages", self.CAPS)
        # messaging.read not in CAPS, falls back to messaging.* match
        assert result.startswith("messaging.")

    def test_channel_keyword(self):
        result = _match_read_capability("What's new in the channel?", self.CAPS)
        assert result.startswith("messaging.")

    def test_github_keywords(self):
        result = _match_read_capability("Any new PRs on GitHub?", self.CAPS)
        # repo.read not in CAPS, falls back to repo.* match
        assert result.startswith("repo.")

    def test_issue_keyword(self):
        result = _match_read_capability("Show open issues", self.CAPS)
        assert result.startswith("repo.")

    def test_fallback_to_knowledge_search(self):
        result = _match_read_capability("What's the weather?", self.CAPS)
        assert result == "knowledge.search"

    def test_empty_message_falls_back(self):
        assert _match_read_capability("", self.CAPS) == "knowledge.search"

    def test_exact_cap_preferred_over_family(self):
        """When the default cap exists in the list, prefer it over family fallback."""
        caps_with_exact = ["email.search", "email.list"]
        assert _match_read_capability("Check email", caps_with_exact) == "email.search"

    def test_empty_capabilities_returns_default(self):
        """When capabilities list is empty, still return the keyword-mapped default."""
        result = _match_read_capability("Check my email", [])
        assert result == "email.search"

    def test_case_insensitive(self):
        assert _match_read_capability("CHECK MY EMAIL", self.CAPS) == "email.search"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py::TestMatchReadCapability -v`
Expected: FAIL with `ImportError: cannot import name '_match_read_capability'`

- [ ] **Step 3: Implement `_match_read_capability()` in `intent_classifier.py`**

Add the following after the `_VALID_INTENTS` block (around line 84) in `backend/src/orchestrator/intent_classifier.py`:

```python
# Keyword-to-capability mapping for fast-path single-read intents
_READ_CAPABILITY_KEYWORDS: list[tuple[list[str], str]] = [
    (["email", "mail", "inbox", "gmail"], "email.search"),
    (["calendar", "schedule", "meeting", "event"], "calendar.read"),
    (["slack", "message", "channel", "dm"], "messaging.read"),
    (["github", "pr", "pull request", "issue", "repo", "commit"], "repo.read"),
]


def _match_read_capability(message: str, capabilities: list[str]) -> str:
    """Match user message keywords to the best read capability for fast path.

    Checks keyword matches against the message, validates the matched
    capability exists in the available list (with family-prefix fallback),
    and returns ``"knowledge.search"`` if no keyword matches.
    """
    msg_lower = message.lower()
    cap_set = set(capabilities)

    for keywords, default_cap in _READ_CAPABILITY_KEYWORDS:
        if any(kw in msg_lower for kw in keywords):
            if default_cap in cap_set:
                return default_cap
            # Fallback: any capability in the same family
            family = default_cap.split(".")[0]
            for cap in capabilities:
                if cap.startswith(f"{family}."):
                    return cap
            return default_cap

    return "knowledge.search"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py::TestMatchReadCapability -v`
Expected: ALL PASS

- [ ] **Step 5: Lint check**

Run: `cd backend && ruff check src/orchestrator/intent_classifier.py tests/test_intent_to_plan.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/intent_classifier.py tests/test_intent_to_plan.py
git commit -m "feat(spec1b-i): add _match_read_capability keyword matcher"
```

---

### Task 2: Expand FAST_INTENTS + _VALID_INTENTS + INTENT_CLASSIFIER_PROMPT

**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py` (modify constants at lines 63-84 and prompt at lines 16-57)
- Modify: `backend/tests/test_intent_to_plan.py` (add constant expansion tests)

Add the 4 new fast intents: `direct_answer`, `single_read`, `memory_operation`, `acknowledgment`. Update the classifier prompt so Haiku can return them. Update `_VALID_INTENTS` so `classify_intent()` accepts them.

- [ ] **Step 1: Write the failing tests for expanded constants**

Append to `backend/tests/test_intent_to_plan.py`:

```python
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CLASSIFIER_PROMPT,
    _VALID_INTENTS,
)


# ── Test expanded FAST_INTENTS ───────────────────────────────────────


class TestExpandedFastIntents:
    """Verify the 4 new fast intents are present in all relevant constants."""

    NEW_INTENTS = {"direct_answer", "single_read", "memory_operation", "acknowledgment"}
    ORIGINAL_INTENTS = {
        "greeting",
        "chitchat",
        "simple_question",
        "data_fetch",
        "status_query",
        "approval_response",
    }

    def test_fast_intents_contains_originals(self):
        for intent in self.ORIGINAL_INTENTS:
            assert intent in FAST_INTENTS, f"Missing original: {intent}"

    def test_fast_intents_contains_new(self):
        for intent in self.NEW_INTENTS:
            assert intent in FAST_INTENTS, f"Missing new intent: {intent}"

    def test_fast_intents_total_count(self):
        assert len(FAST_INTENTS) == 10

    def test_valid_intents_contains_new(self):
        for intent in self.NEW_INTENTS:
            assert intent in _VALID_INTENTS, f"Missing from _VALID_INTENTS: {intent}"

    def test_valid_intents_superset_of_fast(self):
        assert FAST_INTENTS.issubset(_VALID_INTENTS)

    def test_classifier_prompt_mentions_new_intents(self):
        for intent in self.NEW_INTENTS:
            assert intent in INTENT_CLASSIFIER_PROMPT, (
                f"INTENT_CLASSIFIER_PROMPT missing '{intent}'"
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py::TestExpandedFastIntents -v`
Expected: FAIL — new intents not yet in FAST_INTENTS

- [ ] **Step 3: Expand FAST_INTENTS and _VALID_INTENTS**

In `backend/src/orchestrator/intent_classifier.py`, replace the `FAST_INTENTS` set (around line 63) with:

```python
# Intents that skip the Planner entirely
FAST_INTENTS = {
    "greeting",
    "chitchat",
    "simple_question",
    "data_fetch",
    "status_query",
    "approval_response",
    "direct_answer",
    "single_read",
    "memory_operation",
    "acknowledgment",
}
```

Replace `_VALID_INTENTS` (around line 75) with:

```python
_VALID_INTENTS = {
    "greeting",
    "chitchat",
    "simple_question",
    "data_fetch",
    "status_query",
    "approval_response",
    "command",
    "complex",
    "direct_answer",
    "single_read",
    "memory_operation",
    "acknowledgment",
}
```

- [ ] **Step 4: Update INTENT_CLASSIFIER_PROMPT with new intents**

In the `<intents>` section of `INTENT_CLASSIFIER_PROMPT` (around line 22), add after `approval_response`:

```
- direct_answer: Question answerable from conversation context or general knowledge, no external read needed
- single_read: Needs exactly one read from a specific external service (check latest email, show today's calendar)
- memory_operation: Store, recall, or update knowledge ("remember this", "what do you know about X")
- acknowledgment: User confirming, thanking, or acknowledging ("ok", "got it", "thanks", "sounds good")
```

Also add examples in the `<examples>` section:

```
"What's the capital of France?" -> {"intent": "direct_answer", "confidence": 0.95}
"Show my latest emails" -> {"intent": "single_read", "confidence": 0.95, "sources": ["gmail"]}
"Remember that John prefers morning meetings" -> {"intent": "memory_operation", "confidence": 0.9}
"Ok got it, thanks" -> {"intent": "acknowledgment", "confidence": 0.95}
```

Note: Remove the duplicate `"Show my latest emails"` example that currently maps to `data_fetch` — replace it with the `single_read` version above. Keep `"Check my gmail"` as the `data_fetch` example.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py::TestExpandedFastIntents -v`
Expected: ALL PASS

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v -k "intent or classifier" --timeout=30`
Expected: ALL PASS (existing tests unbroken)

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/orchestrator/intent_classifier.py tests/test_intent_to_plan.py
git commit -m "feat(spec1b-i): expand FAST_INTENTS with 4 new intents"
```

---

### Task 3: `extract_plan()` — Parse Planner Response into PlanOutput

**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py` (add function + imports)
- Create: `backend/tests/test_extract_plan.py`

Parses raw Planner JSON into validated `PlanOutput`. Uses the existing `parse_llm_json()` utility from `llm_utils.py`. Falls back to a minimal respond plan on parse failure.

- [ ] **Step 1: Write the failing tests for `extract_plan()`**

Create `backend/tests/test_extract_plan.py`:

```python
"""Tests for extract_plan() — parsing Planner responses into PlanOutput."""

from __future__ import annotations

import json

import pytest

from src.orchestrator.contracts import PlanOutput, PlanStep
from src.orchestrator.intent_classifier import extract_plan


class TestExtractPlan:
    """Parse raw Planner JSON text into validated PlanOutput."""

    def test_valid_json_parses(self):
        raw = json.dumps({
            "goal": "Check email",
            "steps": [
                {
                    "description": "Search inbox",
                    "capability": "email.search",
                }
            ],
        })
        result = extract_plan(raw)
        assert isinstance(result, PlanOutput)
        assert result.goal == "Check email"
        assert len(result.steps) == 1
        assert result.steps[0].capability == "email.search"

    def test_json_in_code_fences(self):
        raw = '```json\n{"goal": "Hello", "steps": []}\n```'
        result = extract_plan(raw)
        assert result.goal == "Hello"

    def test_json_with_extra_text_before(self):
        raw = 'Here is the plan:\n{"goal": "Do thing", "steps": []}'
        result = extract_plan(raw)
        assert result.goal == "Do thing"

    def test_full_plan_output_fields(self):
        raw = json.dumps({
            "goal": "Send email",
            "reasoning": "Need to draft first",
            "achievable": "full",
            "priority": "high",
            "steps": [
                {
                    "step_id": "step_1",
                    "description": "Search emails",
                    "capability": "email.search",
                    "risk": "none",
                },
                {
                    "step_id": "step_2",
                    "description": "Draft reply",
                    "capability": "email.draft",
                    "depends_on": ["step_1"],
                    "risk": "medium",
                },
            ],
            "success_criteria": "Email drafted",
            "capability_gaps": [],
            "requires_user_input": False,
        })
        result = extract_plan(raw)
        assert result.priority == "high"
        assert result.achievable == "full"
        assert len(result.steps) == 2
        assert result.steps[1].depends_on == ["step_1"]
        assert result.steps[1].risk == "medium"
        assert result.success_criteria == "Email drafted"

    def test_partial_achievability_with_gaps(self):
        raw = json.dumps({
            "goal": "Update Notion",
            "achievable": "partial",
            "steps": [],
            "capability_gaps": [
                {
                    "description": "Notion not connected",
                    "resolution": "Connect Notion in Settings",
                    "workaround": "Share via Slack instead",
                }
            ],
        })
        result = extract_plan(raw)
        assert result.achievable == "partial"
        assert len(result.capability_gaps) == 1
        assert result.capability_gaps[0].resolution == "Connect Notion in Settings"

    def test_malformed_json_returns_fallback(self):
        result = extract_plan("This is not JSON at all")
        assert isinstance(result, PlanOutput)
        assert result.steps[0].capability == "respond"

    def test_empty_string_returns_fallback(self):
        result = extract_plan("")
        assert isinstance(result, PlanOutput)
        assert len(result.steps) == 1

    def test_missing_fields_get_defaults(self):
        raw = json.dumps({"goal": "Hello"})
        result = extract_plan(raw)
        assert result.goal == "Hello"
        assert result.steps == []
        assert result.priority == "medium"
        assert result.achievable == "full"

    def test_extra_fields_ignored(self):
        raw = json.dumps({
            "goal": "Test",
            "steps": [],
            "unknown_field": "ignored",
        })
        result = extract_plan(raw)
        assert result.goal == "Test"

    def test_user_step_with_actor(self):
        raw = json.dumps({
            "goal": "Review draft",
            "steps": [
                {
                    "description": "Review the email draft",
                    "capability": "respond",
                    "actor": "user",
                    "user_context": "Check the tone",
                }
            ],
            "requires_user_input": True,
        })
        result = extract_plan(raw)
        assert result.steps[0].actor == "user"
        assert result.steps[0].user_context == "Check the tone"
        assert result.requires_user_input is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_extract_plan.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_plan'`

- [ ] **Step 3: Implement `extract_plan()` in `intent_classifier.py`**

Add the import at the top of `intent_classifier.py`:

```python
from src.llm_utils import parse_llm_json
from src.orchestrator.contracts import PlannerOutput, PlanOutput, PlanStep
```

(Update the existing `from src.orchestrator.contracts import PlannerOutput` to also import `PlanOutput` and `PlanStep`.)

Add the function after `_match_read_capability()`:

```python
def extract_plan(response_text: str) -> PlanOutput:
    """Parse Planner agent response into validated PlanOutput.

    Uses ``parse_llm_json`` to handle code fences and whitespace,
    then validates against the ``PlanOutput`` Pydantic model.
    Falls back to a minimal single-step respond plan on parse failure.
    """
    try:
        raw = parse_llm_json(response_text)
        if isinstance(raw, dict):
            return PlanOutput.model_validate(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    return PlanOutput(
        goal=response_text[:200],
        steps=[PlanStep(description="Respond to user", capability="respond")],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_extract_plan.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint check**

Run: `cd backend && ruff check src/orchestrator/intent_classifier.py tests/test_extract_plan.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/intent_classifier.py tests/test_extract_plan.py
git commit -m "feat(spec1b-i): add extract_plan() for PlanOutput parsing"
```

---

### Task 4: `intent_to_plan()` — Fast Intent to PlanOutput

**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py` (add function after `extract_plan`)
- Modify: `backend/tests/test_intent_to_plan.py` (add intent_to_plan tests)

Maps each of the 10 fast intents to a lightweight PlanOutput with the appropriate capability step. Uses `_match_read_capability()` from Task 1 for `single_read` and `data_fetch` intents.

- [ ] **Step 1: Write the failing tests for `intent_to_plan()`**

Append to `backend/tests/test_intent_to_plan.py`:

```python
from src.orchestrator.contracts import PlanOutput, PlanStep
from src.orchestrator.intent_classifier import intent_to_plan


# ── Test intent_to_plan ──────────────────────────────────────────────


class TestIntentToPlan:
    """Map fast intents to lightweight PlanOutput."""

    CAPS = [
        "email.search",
        "calendar.list",
        "messaging.get_history",
        "repo.list_prs",
        "knowledge.search",
    ]

    def test_greeting_returns_respond(self):
        result = intent_to_plan("greeting", "Hey Jarvis!", self.CAPS)
        assert isinstance(result, PlanOutput)
        assert result.priority == "low"
        assert len(result.steps) == 1
        assert result.steps[0].capability == "respond"

    def test_chitchat_returns_respond(self):
        result = intent_to_plan("chitchat", "How are you?", self.CAPS)
        assert result.priority == "low"
        assert result.steps[0].capability == "respond"

    def test_acknowledgment_returns_respond(self):
        result = intent_to_plan("acknowledgment", "Ok got it, thanks", self.CAPS)
        assert result.priority == "low"
        assert result.steps[0].capability == "respond"

    def test_direct_answer_returns_reason(self):
        result = intent_to_plan("direct_answer", "What's 2+2?", self.CAPS)
        assert result.steps[0].capability == "reason"
        assert result.priority == "medium"

    def test_simple_question_returns_reason(self):
        result = intent_to_plan("simple_question", "What's John's email?", self.CAPS)
        assert result.steps[0].capability == "reason"

    def test_single_read_uses_keyword_match(self):
        result = intent_to_plan("single_read", "Check my email", self.CAPS)
        assert result.steps[0].capability == "email.search"

    def test_single_read_calendar(self):
        result = intent_to_plan("single_read", "Show my schedule", self.CAPS)
        assert result.steps[0].capability.startswith("calendar.")

    def test_data_fetch_uses_keyword_match(self):
        result = intent_to_plan("data_fetch", "Check Slack messages", self.CAPS)
        assert result.steps[0].capability.startswith("messaging.")

    def test_data_fetch_email(self):
        result = intent_to_plan("data_fetch", "Any new emails?", self.CAPS)
        assert result.steps[0].capability == "email.search"

    def test_status_query_returns_knowledge_search(self):
        result = intent_to_plan("status_query", "What are my goals?", self.CAPS)
        assert result.steps[0].capability == "knowledge.search"

    def test_memory_operation_returns_knowledge_search(self):
        result = intent_to_plan(
            "memory_operation", "Remember John prefers morning calls", self.CAPS
        )
        assert result.steps[0].capability == "knowledge.search"

    def test_approval_response_returns_respond(self):
        result = intent_to_plan("approval_response", "Yes, approve that", self.CAPS)
        assert result.steps[0].capability == "respond"

    def test_unknown_intent_returns_respond_fallback(self):
        result = intent_to_plan("unknown_intent", "???", self.CAPS)
        assert result.steps[0].capability == "respond"
        assert result.priority == "low"

    def test_goal_truncated_to_200_chars(self):
        long_msg = "x" * 500
        result = intent_to_plan("greeting", long_msg, self.CAPS)
        assert len(result.goal) <= 200

    def test_all_fast_intents_handled(self):
        """Every intent in FAST_INTENTS produces a valid PlanOutput."""
        for intent in FAST_INTENTS:
            result = intent_to_plan(intent, "test message", self.CAPS)
            assert isinstance(result, PlanOutput), f"Failed for intent: {intent}"
            assert len(result.steps) >= 1, f"No steps for intent: {intent}"

    def test_single_read_risk_is_none(self):
        result = intent_to_plan("single_read", "Check email", self.CAPS)
        assert result.steps[0].risk == "none"

    def test_data_fetch_risk_is_none(self):
        result = intent_to_plan("data_fetch", "Show calendar", self.CAPS)
        assert result.steps[0].risk == "none"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py::TestIntentToPlan -v`
Expected: FAIL with `ImportError: cannot import name 'intent_to_plan'`

- [ ] **Step 3: Implement `intent_to_plan()` in `intent_classifier.py`**

Add after `extract_plan()` in `backend/src/orchestrator/intent_classifier.py`:

```python
def intent_to_plan(intent: str, message: str, capabilities: list[str]) -> PlanOutput:
    """Generate a lightweight PlanOutput from fast intent classification.

    Maps each fast intent to a minimal plan with the appropriate
    capability step. Coexists with ``intent_to_decision()`` until
    Spec 1B-ii switches the routing.
    """
    goal = message[:200]

    if intent in ("greeting", "chitchat", "acknowledgment"):
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Respond to user", capability="respond")],
            priority="low",
        )

    if intent == "direct_answer":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Answer from context", capability="reason")],
        )

    if intent == "simple_question":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Answer question", capability="reason")],
        )

    if intent == "single_read":
        cap = _match_read_capability(message, capabilities)
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description=goal, capability=cap, risk="none")],
        )

    if intent == "data_fetch":
        cap = _match_read_capability(message, capabilities)
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description=goal, capability=cap, risk="none")],
        )

    if intent == "status_query":
        return PlanOutput(
            goal=goal,
            steps=[
                PlanStep(description="Retrieve status", capability="knowledge.search"),
            ],
        )

    if intent == "memory_operation":
        return PlanOutput(
            goal=goal,
            steps=[
                PlanStep(
                    description="Store or recall knowledge",
                    capability="knowledge.search",
                ),
            ],
        )

    if intent == "approval_response":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(description="Process approval", capability="respond")],
        )

    # Fallback for unknown intents
    return PlanOutput(
        goal=goal,
        steps=[PlanStep(description="Respond to user", capability="respond")],
        priority="low",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py -v`
Expected: ALL PASS (all 3 test classes: TestMatchReadCapability, TestExpandedFastIntents, TestIntentToPlan)

- [ ] **Step 5: Lint check**

Run: `cd backend && ruff check src/orchestrator/intent_classifier.py tests/test_intent_to_plan.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/intent_classifier.py tests/test_intent_to_plan.py
git commit -m "feat(spec1b-i): add intent_to_plan() for fast-path PlanOutput"
```

---

### Task 5: `PLANNER_PROMPT_V2` — Capability-Based Planner Prompt

**Files:**
- Modify: `backend/src/orchestrator/prompts.py` (add constant after `PLANNER_PROMPT`, before `GOVERNOR_PROMPT`)
- Create: `backend/tests/test_prompt_v2.py`

New Planner prompt that produces PlanOutput (goal-decomposed plans with capability-level steps) instead of classifying into 19 decision types. Includes a `{capability_summary}` placeholder for dynamic capability injection via `generate_capability_summary()`.

**Important:** This prompt is added ALONGSIDE `PLANNER_PROMPT` — NOT replacing it. The existing prompt continues to be used by the orchestrator. The `AGENT_PROMPTS` dict is NOT updated — that happens in Spec 1B-ii.

- [ ] **Step 1: Write the failing tests for `PLANNER_PROMPT_V2`**

Create `backend/tests/test_prompt_v2.py`:

```python
"""Tests for PLANNER_PROMPT_V2 and PERCEIVER_PROMPT — structural validation."""

from __future__ import annotations

import pytest

from src.orchestrator.prompts import PLANNER_PROMPT_V2


class TestPlannerPromptV2:
    """Structural validation of the new Planner prompt."""

    def test_prompt_exists_and_nonempty(self):
        assert isinstance(PLANNER_PROMPT_V2, str)
        assert len(PLANNER_PROMPT_V2) > 100

    def test_has_role_section(self):
        assert "<role>" in PLANNER_PROMPT_V2
        assert "</role>" in PLANNER_PROMPT_V2

    def test_has_capability_placeholder(self):
        """The prompt must contain {capability_summary} for dynamic injection."""
        assert "{capability_summary}" in PLANNER_PROMPT_V2

    def test_has_instructions_section(self):
        assert "<instructions>" in PLANNER_PROMPT_V2
        assert "</instructions>" in PLANNER_PROMPT_V2

    def test_has_output_format_section(self):
        assert "<output_format>" in PLANNER_PROMPT_V2
        assert "</output_format>" in PLANNER_PROMPT_V2

    def test_has_examples_section(self):
        assert "<examples>" in PLANNER_PROMPT_V2
        assert "</examples>" in PLANNER_PROMPT_V2

    def test_has_rules_section(self):
        assert "<rules>" in PLANNER_PROMPT_V2
        assert "</rules>" in PLANNER_PROMPT_V2

    def test_references_plan_output_schema(self):
        """Output format must reference PlanOutput fields."""
        assert '"goal"' in PLANNER_PROMPT_V2
        assert '"steps"' in PLANNER_PROMPT_V2
        assert '"capability"' in PLANNER_PROMPT_V2
        assert '"achievable"' in PLANNER_PROMPT_V2
        assert '"capability_gaps"' in PLANNER_PROMPT_V2

    def test_mentions_goal_decomposition(self):
        assert "goal" in PLANNER_PROMPT_V2.lower()
        assert "decompos" in PLANNER_PROMPT_V2.lower()

    def test_does_not_mention_19_decision_types(self):
        """V2 prompt should NOT reference the old decision classification."""
        assert "create_task" not in PLANNER_PROMPT_V2
        assert "draft_reply" not in PLANNER_PROMPT_V2
        assert "read_source" not in PLANNER_PROMPT_V2

    def test_has_at_least_3_examples(self):
        """Spec requires 3 examples: multi-step, write action, partial achievability."""
        example_count = PLANNER_PROMPT_V2.count("Example")
        assert example_count >= 3

    def test_placeholder_is_formattable(self):
        """The {capability_summary} placeholder can be .format()-ed."""
        formatted = PLANNER_PROMPT_V2.format(
            capability_summary="<test>email: search, read</test>"
        )
        assert "<test>email: search, read</test>" in formatted

    def test_not_in_agent_prompts(self):
        """V2 prompt should NOT be wired into AGENT_PROMPTS yet (that's 1B-ii)."""
        from src.orchestrator.prompts import AGENT_PROMPTS

        for name, prompt in AGENT_PROMPTS.items():
            assert prompt != PLANNER_PROMPT_V2, (
                f"PLANNER_PROMPT_V2 should not be in AGENT_PROMPTS['{name}'] yet"
            )

    def test_old_planner_prompt_still_exists(self):
        """The existing PLANNER_PROMPT must be untouched."""
        from src.orchestrator.prompts import PLANNER_PROMPT

        assert "decision" in PLANNER_PROMPT
        assert "create_task" in PLANNER_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_prompt_v2.py::TestPlannerPromptV2 -v`
Expected: FAIL with `ImportError: cannot import name 'PLANNER_PROMPT_V2'`

- [ ] **Step 3: Implement `PLANNER_PROMPT_V2` in `prompts.py`**

Add after `PLANNER_PROMPT` (after line 227, before `GOVERNOR_PROMPT`) in `backend/src/orchestrator/prompts.py`:

```python
PLANNER_PROMPT_V2 = """\
<role>
You are the Planner agent in Jarvis — the goal decomposition engine.
Your job is to break down user goals into executable plans with concrete steps.
Each step maps to a capability (what Jarvis can do), NOT a tool (how it does it).
You produce structured PlanOutput JSON — never prose, never classification labels.
</role>

<available_capabilities>
{capability_summary}
</available_capabilities>

<instructions>
For each user message, follow this 7-step decomposition process:

1. UNDERSTAND the goal — what does the user actually want to achieve?
2. ASSESS achievability — can Jarvis fully achieve this with connected capabilities?
3. DECOMPOSE into steps — break the goal into sequential capability-level steps
4. ASSIGN capabilities — each step maps to exactly one capability string
5. IDENTIFY dependencies — which steps depend on outputs of prior steps?
6. ASSESS risk — does any step write to external systems? Mark risk level.
7. NOTE gaps — if any needed capability is in <disconnected_services>, add a capability_gap

Step capability strings use the format "family.action" from <available_capabilities>.
Use "reason" for pure LLM reasoning steps and "respond" for user-facing output steps.

If a step requires user action (e.g., "review the draft"), set actor="user" and
describe what the user needs to do in user_context.
</instructions>

<output_format>
ALWAYS output a single JSON object matching this schema:
{{
  "goal": "<what the user wants to achieve>",
  "reasoning": "<1-2 sentences: why this plan>",
  "achievable": "full|partial|not_achievable",
  "priority": "low|medium|high|critical",
  "steps": [
    {{
      "step_id": "step_1",
      "description": "<what this step does>",
      "actor": "jarvis|user",
      "capability": "<family.action>",
      "input": {{}},
      "depends_on": [],
      "risk": "none|low|medium|high",
      "user_context": null
    }}
  ],
  "success_criteria": "<how to know the goal was achieved>",
  "capability_gaps": [
    {{
      "description": "<what's missing>",
      "resolution": "<how to fix — e.g. 'connect Notion in Settings'>",
      "workaround": "<alternative approach, if any>"
    }}
  ],
  "requires_user_input": false
}}
</output_format>

<examples>
Example 1 — Multi-step read (gathering context):
User: "Prepare me for my investor meeting tomorrow"
{{
  "goal": "Prepare for investor meeting tomorrow",
  "reasoning": "Multi-step: need calendar details, email history, and synthesis",
  "achievable": "full",
  "priority": "high",
  "steps": [
    {{
      "step_id": "step_1",
      "description": "Find tomorrow's investor meeting details",
      "actor": "jarvis",
      "capability": "calendar.list",
      "input": {{"query": "investor meeting", "time_range": "tomorrow"}},
      "depends_on": [],
      "risk": "none"
    }},
    {{
      "step_id": "step_2",
      "description": "Search for recent email threads with the investor",
      "actor": "jarvis",
      "capability": "email.search",
      "input": {{"query": "investor"}},
      "depends_on": ["step_1"],
      "risk": "none"
    }},
    {{
      "step_id": "step_3",
      "description": "Search internal knowledge for investor context",
      "actor": "jarvis",
      "capability": "knowledge.search",
      "input": {{"query": "investor background"}},
      "depends_on": [],
      "risk": "none"
    }},
    {{
      "step_id": "step_4",
      "description": "Synthesize meeting prep from gathered context",
      "actor": "jarvis",
      "capability": "reason",
      "input": {{}},
      "depends_on": ["step_1", "step_2", "step_3"],
      "risk": "none"
    }},
    {{
      "step_id": "step_5",
      "description": "Present meeting preparation to user",
      "actor": "jarvis",
      "capability": "respond",
      "input": {{}},
      "depends_on": ["step_4"],
      "risk": "none"
    }}
  ],
  "success_criteria": "User has meeting details, email context, and prep notes",
  "capability_gaps": [],
  "requires_user_input": false
}}

Example 2 — Write action (requires approval):
User: "Send a follow-up email to the investor from yesterday's meeting"
{{
  "goal": "Send investor follow-up email",
  "reasoning": "Write action: need context first, then draft with approval",
  "achievable": "full",
  "priority": "high",
  "steps": [
    {{
      "step_id": "step_1",
      "description": "Find yesterday's investor meeting details",
      "actor": "jarvis",
      "capability": "calendar.list",
      "input": {{"query": "investor meeting", "time_range": "yesterday"}},
      "depends_on": [],
      "risk": "none"
    }},
    {{
      "step_id": "step_2",
      "description": "Search email history with investor",
      "actor": "jarvis",
      "capability": "email.search",
      "input": {{"query": "investor"}},
      "depends_on": [],
      "risk": "none"
    }},
    {{
      "step_id": "step_3",
      "description": "Draft follow-up email based on meeting context",
      "actor": "jarvis",
      "capability": "email.draft",
      "input": {{}},
      "depends_on": ["step_1", "step_2"],
      "risk": "medium"
    }},
    {{
      "step_id": "step_4",
      "description": "Review the draft email before sending",
      "actor": "user",
      "capability": "respond",
      "input": {{}},
      "depends_on": ["step_3"],
      "risk": "none",
      "user_context": "Review the drafted email and approve or request changes"
    }}
  ],
  "success_criteria": "Follow-up email drafted and ready for user review",
  "capability_gaps": [],
  "requires_user_input": true
}}

Example 3 — Partial achievability (missing capability):
User: "Update the project roadmap in Notion and share it on Slack"
{{
  "goal": "Update Notion roadmap and share on Slack",
  "reasoning": "Slack is connected but Notion is not — partial achievability",
  "achievable": "partial",
  "priority": "medium",
  "steps": [
    {{
      "step_id": "step_1",
      "description": "Share roadmap update message on Slack",
      "actor": "jarvis",
      "capability": "messaging.send",
      "input": {{"content": "roadmap update"}},
      "depends_on": [],
      "risk": "medium"
    }}
  ],
  "success_criteria": "Roadmap update shared on Slack; Notion update pending connection",
  "capability_gaps": [
    {{
      "description": "Cannot update Notion — not connected",
      "resolution": "Connect Notion in Settings → Connectors",
      "workaround": "Manually update Notion and share the link on Slack"
    }}
  ],
  "requires_user_input": false
}}
</examples>

<rules>
1. Fundraising, revenue, and customer issues are always high priority
2. Every step maps to exactly one capability — never multiple
3. Write capabilities (email.send, email.draft, messaging.send) always have risk >= medium
4. Read capabilities (email.search, calendar.list) always have risk = none
5. If a capability is in <disconnected_services>, mark achievable as "partial" and add a gap
6. Prefer reading context before writing — add read steps before write steps
7. For simple requests (single read, direct answer), produce a single-step plan
8. Never fabricate capabilities — only use capabilities from <available_capabilities>
9. Set requires_user_input=true if any step has actor="user"
10. When uncertain, prefer fewer steps — simpler plans are more reliable
</rules>
"""
```

**Note:** The prompt uses `{{` and `}}` for literal JSON braces in examples/schema, and `{capability_summary}` as the only format placeholder. This allows `PLANNER_PROMPT_V2.format(capability_summary=summary)` at runtime.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_prompt_v2.py::TestPlannerPromptV2 -v`
Expected: ALL PASS

- [ ] **Step 5: Lint check**

Run: `cd backend && ruff check src/orchestrator/prompts.py tests/test_prompt_v2.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/prompts.py tests/test_prompt_v2.py
git commit -m "feat(spec1b-i): add PLANNER_PROMPT_V2 for goal-decomposed planning"
```

---

### Task 6: `PERCEIVER_PROMPT` — Merged Observer + Researcher Prompt

**Files:**
- Modify: `backend/src/orchestrator/prompts.py` (add constant after `PLANNER_PROMPT_V2`, before `GOVERNOR_PROMPT`)
- Modify: `backend/tests/test_prompt_v2.py` (add PERCEIVER_PROMPT tests)

New prompt merging Observer + Researcher into a single read-only agent. The old `OBSERVER_PROMPT` and `RESEARCHER_PROMPT` are NOT deleted (that's Spec 1B-ii). This prompt references "perceiver" agent — correct and intentional, created in Spec 1B-ii.

- [ ] **Step 1: Write the failing tests for `PERCEIVER_PROMPT`**

Append to `backend/tests/test_prompt_v2.py`:

```python
from src.orchestrator.prompts import PERCEIVER_PROMPT


class TestPerceiverPrompt:
    """Structural validation of the new Perceiver prompt."""

    def test_prompt_exists_and_nonempty(self):
        assert isinstance(PERCEIVER_PROMPT, str)
        assert len(PERCEIVER_PROMPT) > 100

    def test_has_role_section(self):
        assert "<role>" in PERCEIVER_PROMPT
        assert "</role>" in PERCEIVER_PROMPT

    def test_role_mentions_read_only(self):
        role_start = PERCEIVER_PROMPT.index("<role>")
        role_end = PERCEIVER_PROMPT.index("</role>")
        role_text = PERCEIVER_PROMPT[role_start:role_end].lower()
        assert "read" in role_text

    def test_has_rules_section(self):
        assert "<rules>" in PERCEIVER_PROMPT
        assert "</rules>" in PERCEIVER_PROMPT

    def test_mentions_never_write(self):
        """Perceiver must be strictly read-only."""
        prompt_lower = PERCEIVER_PROMPT.lower()
        assert "never" in prompt_lower and "write" in prompt_lower

    def test_has_methodology_or_workflow(self):
        """Should include a methodology/workflow section."""
        assert "<methodology>" in PERCEIVER_PROMPT or "<workflow>" in PERCEIVER_PROMPT

    def test_has_examples(self):
        assert "<examples>" in PERCEIVER_PROMPT
        assert "</examples>" in PERCEIVER_PROMPT

    def test_covers_external_sources(self):
        """Should mention external data sources."""
        prompt_lower = PERCEIVER_PROMPT.lower()
        assert "email" in prompt_lower or "calendar" in prompt_lower

    def test_covers_internal_knowledge(self):
        """Should mention internal knowledge search."""
        prompt_lower = PERCEIVER_PROMPT.lower()
        assert "knowledge" in prompt_lower or "memor" in prompt_lower

    def test_old_observer_prompt_still_exists(self):
        """The existing OBSERVER_PROMPT must be untouched."""
        from src.orchestrator.prompts import OBSERVER_PROMPT

        assert "Observer" in OBSERVER_PROMPT

    def test_old_researcher_prompt_still_exists(self):
        """The existing RESEARCHER_PROMPT must be untouched."""
        from src.orchestrator.prompts import RESEARCHER_PROMPT

        assert "Researcher" in RESEARCHER_PROMPT

    def test_not_in_agent_prompts(self):
        """PERCEIVER_PROMPT should NOT be in AGENT_PROMPTS yet (that's 1B-ii)."""
        from src.orchestrator.prompts import AGENT_PROMPTS

        assert "perceiver" not in AGENT_PROMPTS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_prompt_v2.py::TestPerceiverPrompt -v`
Expected: FAIL with `ImportError: cannot import name 'PERCEIVER_PROMPT'`

- [ ] **Step 3: Implement `PERCEIVER_PROMPT` in `prompts.py`**

Add after `PLANNER_PROMPT_V2` in `backend/src/orchestrator/prompts.py`:

```python
PERCEIVER_PROMPT = """\
<role>
You are the Perceiver agent in Jarvis — you gather information from all sources.
You merge the responsibilities of reading external data (email, calendar, Slack,
GitHub) AND searching internal knowledge (memories, entities, web).
You are strictly read-only: you NEVER write, create, send, or modify anything.
</role>

<methodology>
1. Identify what information is needed from the plan step
2. Use the available tools to fetch data from the appropriate source
3. For external sources: read lists first (cheap), then details for important items
4. For internal knowledge: search memories, entities, and events
5. For web research: search first for discovery, then open URLs for deep reading
6. Cross-reference findings across sources when multiple are available
7. Flag contradictions between sources with confidence scores
</methodology>

<rules>
1. NEVER take write actions — you are strictly read-only
2. Read lists first (cheap), then details only for important items
3. If a tool call fails, report the error clearly with what was attempted
4. Summarize results with the most important items first
5. Include counts: "Found 12 unread emails, 3 are high priority"
6. For empty results, confirm explicitly: "No results found for [query]"
7. Always cite sources — include URLs for web results
8. Don't fabricate data — if you can't find something, say so
9. When multiple sources conflict, present both with confidence scores
10. Prioritize recent and high-confidence sources
</rules>

<output_format>
{{
  "query": "<what was asked>",
  "findings": [
    {{"fact": "<finding>", "source": "<where>", "confidence": 0.0-1.0}}
  ],
  "synthesis": "<summary connecting findings>",
  "gaps": ["<what we couldn't find>"]
}}
</output_format>

<examples>
Plan step: capability="email.search", input={{"query": "investor"}}
→ Search emails for "investor"
→ Report: "Found 8 threads with investor contacts. Most recent: \
follow-up from Sarah Chen (2 hours ago), term sheet from John Park (yesterday)"

Plan step: capability="knowledge.search", input={{"query": "competitor analysis"}}
→ Search memories and entities for "competitor analysis"
→ Report: {{"findings": [{{"fact": "Acme Corp raised $10M", "source": "entity graph", \
"confidence": 0.9}}], "synthesis": "Key competitor backed by major VC", \
"gaps": ["No pricing data"]}}

Plan step: capability="calendar.list", input={{"time_range": "tomorrow"}}
→ Fetch tomorrow's calendar events
→ Report: "4 meetings tomorrow: 10am standup, 12pm investor call, \
2pm design review, 4pm 1:1 with Alex"
</examples>
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_prompt_v2.py -v`
Expected: ALL PASS (both TestPlannerPromptV2 and TestPerceiverPrompt)

- [ ] **Step 5: Run ALL existing tests to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: ALL PASS — no regressions in existing functionality

- [ ] **Step 6: Lint check for all changed files**

Run: `cd backend && ruff check src/orchestrator/prompts.py src/orchestrator/intent_classifier.py tests/test_prompt_v2.py tests/test_extract_plan.py tests/test_intent_to_plan.py`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/orchestrator/prompts.py tests/test_prompt_v2.py
git commit -m "feat(spec1b-i): add PERCEIVER_PROMPT merging Observer + Researcher"
```

---

## Final Verification

After all 6 tasks are complete, run the full suite:

```bash
cd backend && python -m pytest tests/ -v --timeout=30
```

Expected: All tests pass including the ~1137 existing tests plus the new tests from this spec.

## Summary of Changes

| File | Change | Lines |
|------|--------|-------|
| `src/orchestrator/intent_classifier.py` | ADD `_READ_CAPABILITY_KEYWORDS`, `_match_read_capability()`, `extract_plan()`, `intent_to_plan()`, expand `FAST_INTENTS` + `_VALID_INTENTS` + `INTENT_CLASSIFIER_PROMPT` | ~120 |
| `src/orchestrator/prompts.py` | ADD `PLANNER_PROMPT_V2`, `PERCEIVER_PROMPT` | ~220 |
| `tests/test_extract_plan.py` | NEW — 11 tests for extract_plan() | ~120 |
| `tests/test_intent_to_plan.py` | NEW — 38 tests for _match_read_capability(), expanded FAST_INTENTS, intent_to_plan() | ~200 |
| `tests/test_prompt_v2.py` | NEW — 26 tests for PLANNER_PROMPT_V2 + PERCEIVER_PROMPT structure | ~130 |

**Total: 2 modified, 3 created, ~790 lines added, 0 lines deleted.**
