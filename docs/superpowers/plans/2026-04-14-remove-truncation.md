# Remove Intelligence-Layer Truncation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all truncation that destroys content flowing between agents, in persistence/audit, and in search queries — while keeping logging and display truncation intact.

**Architecture:** Systematic removal of hardcoded `[:N]` char limits from 8 backend files. No new abstractions — just delete the slicing. Add display constraint guidance to PRESENTER_PROMPT. Change Telegram from truncation to message splitting.

**Tech Stack:** Python (FastAPI, SQLAlchemy, Anthropic SDK), pytest

---

### Task 1: Remove step output and plan analysis truncation in jarvis.py

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py`

- [ ] **Step 1: Delete `_STEP_OUTPUT_CHAR_LIMIT` constant**

Remove lines 83-85:
```python
# REMOVE these 3 lines:
# Max chars to carry forward from each prior step's output to downstream agents.
# 30K chars ≈ 7,500 tokens — keeps context manageable while transferring full documents.
_STEP_OUTPUT_CHAR_LIMIT = 30_000
```

- [ ] **Step 2: Remove truncation at line 804 (non-streaming prior outputs)**

Change:
```python
parts.append(f"[{key}]:\n{str(output)[:_STEP_OUTPUT_CHAR_LIMIT]}")
```
To:
```python
parts.append(f"[{key}]:\n{str(output)}")
```

- [ ] **Step 3: Remove truncation at line 848 (non-streaming presenter outputs)**

Change:
```python
truncated = str(output)[:_STEP_OUTPUT_CHAR_LIMIT]
parts.append(f"[{agent_key}]:\n{truncated}")
```
To:
```python
parts.append(f"[{agent_key}]:\n{str(output)}")
```

- [ ] **Step 4: Remove truncation at line 865 (non-streaming plan analysis)**

Change:
```python
presenter_msg += f"\nAnalysis: {plan_text[:2000]}"
```
To:
```python
presenter_msg += f"\nAnalysis: {plan_text}"
```

- [ ] **Step 5: Remove truncation at line 1141 (streaming prior outputs)**

Change:
```python
parts.append(f"[{key}]:\n{str(output)[:_STEP_OUTPUT_CHAR_LIMIT]}")
```
To:
```python
parts.append(f"[{key}]:\n{str(output)}")
```

- [ ] **Step 6: Remove truncation at line 1195 (streaming presenter outputs)**

Change:
```python
truncated = str(output)[:_STEP_OUTPUT_CHAR_LIMIT]
parts.append(f"[{agent_key}]:\n{truncated}")
```
To:
```python
parts.append(f"[{agent_key}]:\n{str(output)}")
```

- [ ] **Step 7: Remove truncation at line 1213 (streaming plan analysis)**

Change:
```python
f"Plan: {json.dumps(plan_dict)}\nAnalysis: {plan_text[:2000]}\n"
```
To:
```python
f"Plan: {json.dumps(plan_dict)}\nAnalysis: {plan_text}\n"
```

- [ ] **Step 8: Remove query truncation at line 2560 (context builder query)**

Change:
```python
query=message[:500],
```
To:
```python
query=message,
```

- [ ] **Step 9: Verify lint passes**

Run: `cd backend && ruff check src/orchestrator/jarvis.py`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add backend/src/orchestrator/jarvis.py
git commit -m "fix: remove step output, plan analysis, and query truncation in orchestrator"
```

---

### Task 2: Remove conversation history truncation in jarvis.py

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py`

- [ ] **Step 1: Increase max_chars parameter from 8000 to 20000**

At line 2379, change:
```python
max_chars: int = 8000,
```
To:
```python
max_chars: int = 20000,
```

- [ ] **Step 2: Remove per-message snippet truncation at line 2415**

Change:
```python
snippet = content[:1000] if len(content) > 1000 else content
```
To:
```python
snippet = content
```

- [ ] **Step 3: Remove text truncation at line 2469 (summarization input)**

Change:
```python
text = "\n".join(lines)[:4000]
```
To:
```python
text = "\n".join(lines)
```

- [ ] **Step 4: Remove summary vector payload truncation at line 2509**

Change:
```python
"summary": summary[:500],
```
To:
```python
"summary": summary,
```

- [ ] **Step 5: Remove fallback truncation at line 2525**

Change:
```python
return "\n".join(lines)[:500] + "..."
```
To:
```python
return "\n".join(lines)
```

- [ ] **Step 6: Verify lint passes**

Run: `cd backend && ruff check src/orchestrator/jarvis.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add backend/src/orchestrator/jarvis.py
git commit -m "fix: remove conversation history truncation, increase budget to 20K"
```

---

### Task 3: Remove context builder truncation

**Files:**
- Modify: `backend/src/services/context_builder.py`

- [ ] **Step 1: Remove hard truncation in `to_prompt` at line 414**

Change:
```python
result = "\n\n".join(sections) if sections else ""
if len(result) > max_chars:
    result = result[:max_chars] + "\n\n[context truncated]"
return result
```
To:
```python
return "\n\n".join(sections) if sections else ""
```

Also remove the `max_chars = max_tokens * 4` line that precedes the truncation check (look for it in the method body, around line 335).

- [ ] **Step 2: Remove `max_tokens` parameter from `to_prompt` signature**

Change:
```python
def to_prompt(pack: ContextPack, max_tokens: int = 3000) -> str:
```
To:
```python
def to_prompt(pack: ContextPack) -> str:
```

Remove all references to `max_tokens` and `max_chars` within the method body.

- [ ] **Step 3: Remove hard truncation in `to_prompt_compressed` at lines 482-485**

Change:
```python
result = "\n\n".join(sections)
max_chars = max_tokens * 4
if len(result) > max_chars:
    result = result[:max_chars] + "\n\n[context truncated]"
```
To:
```python
result = "\n\n".join(sections)
```

- [ ] **Step 4: Remove `max_tokens` parameter from `to_prompt_compressed` signature**

Change:
```python
async def to_prompt_compressed(
    pack: ContextPack,
    client,
    model: str,
    max_tokens: int = 3000,
) -> str:
```
To:
```python
async def to_prompt_compressed(
    pack: ContextPack,
    client,
    model: str,
) -> str:
```

Remove all references to `max_tokens` and `max_chars` within the method body.

- [ ] **Step 5: Remove summarization fallback truncation at line 509**

Change:
```python
return text[:1200] + "..."
```
To:
```python
return text
```

- [ ] **Step 6: Fix all callers of `to_prompt` and `to_prompt_compressed`**

Search for all callers that pass `max_tokens` argument and remove it:
```bash
cd backend && grep -rn "to_prompt\|to_prompt_compressed" src/ --include="*.py"
```
Update each caller to remove the `max_tokens` kwarg if passed.

- [ ] **Step 7: Verify lint passes**

Run: `cd backend && ruff check src/services/context_builder.py`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add backend/src/services/context_builder.py
git commit -m "fix: remove context pack truncation, let context window be the natural limit"
```

---

### Task 4: Remove tool result and thinking truncation in agent_loop.py

**Files:**
- Modify: `backend/src/orchestrator/agent_loop.py`

- [ ] **Step 1: Remove tool result truncation at lines 478-483**

Change:
```python
# Truncate large results for persistence
persisted_output: Any = result
if isinstance(result, str) and len(result) > 2000:
    persisted_output = result[:2000] + "...[truncated]"
elif isinstance(result, dict):
    result_str = json.dumps(result, default=str)
    if len(result_str) > 2000:
        persisted_output = {"_truncated": result_str[:2000]}
```
To:
```python
persisted_output: Any = result
```

Remove the entire if/elif block.

- [ ] **Step 2: Remove thinking trace truncation at lines 575-576**

Change:
```python
thinking_summary = "".join(thinking_chunks)
if len(thinking_summary) > 5000:
    thinking_summary = thinking_summary[:5000] + "...[truncated]"
thinking_summary = thinking_summary or None
```
To:
```python
thinking_summary = "".join(thinking_chunks) or None
```

- [ ] **Step 3: Verify lint passes**

Run: `cd backend && ruff check src/orchestrator/agent_loop.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add backend/src/orchestrator/agent_loop.py
git commit -m "fix: remove tool result and thinking trace truncation in agent loop"
```

---

### Task 5: Remove audit log truncation in hooks.py

**Files:**
- Modify: `backend/src/orchestrator/hooks.py`

- [ ] **Step 1: Remove `_truncate` usage at lines 122-123**

Change:
```python
input_summary=_truncate(_sanitize_secrets(str(tool_input)), 500),
output_summary=_truncate(_sanitize_secrets(str(tool_result)), 500),
```
To:
```python
input_summary=_sanitize_secrets(str(tool_input)),
output_summary=_sanitize_secrets(str(tool_result)),
```

- [ ] **Step 2: Check if `_truncate` is still used elsewhere in the file**

If `_truncate` is no longer referenced anywhere else in `hooks.py`, delete the function definition (lines 148-151):
```python
def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
```

- [ ] **Step 3: Verify lint passes**

Run: `cd backend && ruff check src/orchestrator/hooks.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add backend/src/orchestrator/hooks.py
git commit -m "fix: remove audit log truncation, store full tool input/output"
```

---

### Task 6: Remove graph executor truncation

**Files:**
- Modify: `backend/src/services/graph_executor.py`

- [ ] **Step 1: Delete `_STEP_OUTPUT_CHAR_LIMIT` constant (lines 32-33)**

Remove:
```python
# Max chars to carry forward from each prior step's output to downstream agents.
_STEP_OUTPUT_CHAR_LIMIT = 30_000
```

- [ ] **Step 2: Remove prior step output truncation at line 1393**

Change:
```python
prior_parts.append(
    f"[{desc}]:\n{str(result_text)[:_STEP_OUTPUT_CHAR_LIMIT]}"
)
```
To:
```python
prior_parts.append(
    f"[{desc}]:\n{str(result_text)}"
)
```

- [ ] **Step 3: Remove query truncation at line 307**

Change:
```python
query=plan.goal[:500] if plan.goal else "",
```
To:
```python
query=plan.goal or "",
```

- [ ] **Step 4: Remove query truncation at line 1504**

Change:
```python
query=query[:500] if query else "",
```
To:
```python
query=query or "",
```

- [ ] **Step 5: Remove output summary truncation at line 63**

Change:
```python
output_summary=(str(s.output_data.get("result", ""))[:200] if s.output_data else None),
```
To:
```python
output_summary=(str(s.output_data.get("result", "")) if s.output_data else None),
```

- [ ] **Step 6: Remove key findings truncation at line 649**

Change:
```python
str(s.output_data.get("result", ""))[:100]
```
To:
```python
str(s.output_data.get("result", ""))
```

- [ ] **Step 7: Remove checkpoint output summary truncation at line 1611**

Change:
```python
"output_summary": str(s.output_data)[:500] if s.output_data else None,
```
To:
```python
"output_summary": str(s.output_data) if s.output_data else None,
```

- [ ] **Step 8: Remove step JSON truncation at line 1647**

Change:
```python
parts.append(f"- {step.task_id}: {json.dumps(step.output_data)[:200]}")
```
To:
```python
parts.append(f"- {step.task_id}: {json.dumps(step.output_data)}")
```

- [ ] **Step 9: Remove all error message truncation (7 locations)**

At each of these lines, remove `[:500]`:
- Line 448: `run.error = {"type": type(exc).__name__, "message": str(exc)[:500]}` → `"message": str(exc)}`
- Line 455: `"error": str(exc)[:500],` → `"error": str(exc),`
- Line 546: `run.error = {"type": type(exc).__name__, "message": str(exc)[:500]}` → `"message": str(exc)}`
- Line 1121: `"message": str(exc)[:500],` → `"message": str(exc),`
- Line 1134: `step.output_data = {"error": str(exc)[:500]}` → `{"error": str(exc)}`
- Line 1136: `step.error = {"message": str(exc)[:500], "final": True}` → `"message": str(exc), "final": True}`
- Line 1143: `"error": str(exc)[:500],` → `"error": str(exc),`

- [ ] **Step 10: Verify lint passes**

Run: `cd backend && ruff check src/services/graph_executor.py`
Expected: `All checks passed!`

- [ ] **Step 11: Commit**

```bash
git add backend/src/services/graph_executor.py
git commit -m "fix: remove all truncation in graph executor"
```

---

### Task 7: Remove goal truncation in intent_classifier.py

**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py`

- [ ] **Step 1: Remove goal truncation at line 156 (extract_plan fallback)**

Change:
```python
return PlanOutput(
    goal=response_text[:200],
```
To:
```python
return PlanOutput(
    goal=response_text,
```

- [ ] **Step 2: Remove goal truncation at line 162 (intent_to_plan)**

Change:
```python
goal = message[:200]
```
To:
```python
goal = message
```

- [ ] **Step 3: Verify lint passes**

Run: `cd backend && ruff check src/orchestrator/intent_classifier.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add backend/src/orchestrator/intent_classifier.py
git commit -m "fix: remove goal truncation in intent classifier"
```

---

### Task 8: Add display constraint guidance to PRESENTER_PROMPT

**Files:**
- Modify: `backend/src/orchestrator/prompts.py`

- [ ] **Step 1: Add display constraint rules to PRESENTER_PROMPT**

In the `<rules>` section (around line 560-571), add after rule 10:

```python
11. Surface titles must be under 80 characters
12. Surface subtitles must be under 120 characters
```

- [ ] **Step 2: Add Telegram constraint to the presenter message construction in jarvis.py**

In the presenter message construction at lines 856-857 and 1203-1204, when `surface == "telegram"`, add constraint guidance.

Change the non-streaming presenter message (line 856):
```python
presenter_msg = (
    f"Format this for the user ({surface}). "
    f"Be conversational and helpful.\n\n"
```
To:
```python
telegram_hint = (
    " Keep under 3500 chars. Prioritize action items and key findings."
    if surface == "telegram"
    else ""
)
presenter_msg = (
    f"Format this for the user ({surface}).{telegram_hint} "
    f"Be conversational and helpful.\n\n"
```

Apply the same pattern to the streaming presenter message (line 1203):
```python
presenter_msg = (
    f"Respond to the user ({surface}). "
    f"Be conversational and helpful.\n\n"
```
To:
```python
telegram_hint = (
    " Keep under 3500 chars. Prioritize action items and key findings."
    if surface == "telegram"
    else ""
)
presenter_msg = (
    f"Respond to the user ({surface}).{telegram_hint} "
    f"Be conversational and helpful.\n\n"
```

- [ ] **Step 3: Verify lint passes**

Run: `cd backend && ruff check src/orchestrator/prompts.py src/orchestrator/jarvis.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add backend/src/orchestrator/prompts.py backend/src/orchestrator/jarvis.py
git commit -m "feat: inform Presenter of display constraints instead of backend truncation"
```

---

### Task 9: Change Telegram from truncation to message splitting

**Files:**
- Modify: `backend/src/interface/telegram.py`

- [ ] **Step 1: Add a message splitting helper**

Add at the top of the file (after imports):
```python
def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split a long message into chunks that fit Telegram's limit.

    Splits on paragraph boundaries first, then on line boundaries.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        # Try to split on double-newline (paragraph)
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            # Fall back to single newline
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            # Hard split
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks
```

- [ ] **Step 2: Replace briefing truncation at line 159**

Change:
```python
briefing_text = result.get("briefing", "No briefing available.")
if len(briefing_text) > 4000:
    briefing_text = briefing_text[:4000] + "\n\n_(truncated)_"
await update.message.reply_text(briefing_text, parse_mode="Markdown")
```
To:
```python
briefing_text = result.get("briefing", "No briefing available.")
for chunk in _split_message(briefing_text):
    await update.message.reply_text(chunk, parse_mode="Markdown")
```

- [ ] **Step 3: Replace response truncation at line 219**

Change:
```python
if len(response) > 4000:
    response = response[:4000] + "\n\n_(truncated)_"
await update.message.reply_text(response, parse_mode="Markdown")
```
To:
```python
for chunk in _split_message(response):
    await update.message.reply_text(chunk, parse_mode="Markdown")
```

- [ ] **Step 4: Verify lint passes**

Run: `cd backend && ruff check src/interface/telegram.py`
Expected: `All checks passed!`

- [ ] **Step 5: Write test for `_split_message`**

Create or add to `backend/tests/test_telegram.py`:
```python
from src.interface.telegram import _split_message


class TestSplitMessage:
    def test_short_message_returns_single_chunk(self):
        assert _split_message("hello") == ["hello"]

    def test_splits_on_paragraph_boundary(self):
        text = "A" * 3000 + "\n\n" + "B" * 3000
        chunks = _split_message(text, limit=4000)
        assert len(chunks) == 2
        assert chunks[0] == "A" * 3000
        assert chunks[1] == "B" * 3000

    def test_splits_on_newline_when_no_paragraph(self):
        text = "A" * 3000 + "\n" + "B" * 3000
        chunks = _split_message(text, limit=4000)
        assert len(chunks) == 2

    def test_hard_split_when_no_newlines(self):
        text = "A" * 8000
        chunks = _split_message(text, limit=4000)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4000
        assert len(chunks[1]) == 4000
```

- [ ] **Step 6: Run test**

Run: `cd backend && pytest tests/test_telegram.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/interface/telegram.py backend/tests/test_telegram.py
git commit -m "fix: split long Telegram messages instead of truncating"
```

---

### Task 10: Update existing tests

**Files:**
- Modify: `backend/tests/test_graph_executor.py`
- Modify: any tests referencing `_STEP_OUTPUT_CHAR_LIMIT` or `to_prompt(max_tokens=...)`

- [ ] **Step 1: Search for test references to removed constants/functions**

```bash
cd backend && grep -rn "_STEP_OUTPUT_CHAR_LIMIT\|max_tokens.*3000\|to_prompt.*max_tokens" tests/ --include="*.py"
```

Fix any imports or references to the deleted constant or changed function signatures.

- [ ] **Step 2: Run the full orchestrator and graph executor test suite**

Run: `cd backend && pytest tests/ -v -k "orchestrator or graph_executor or jarvis or context_builder or agent_loop or hooks or intent_classifier" --ignore=tests/test_orchestrator_routing.py`
Expected: All tests PASS (the `test_orchestrator_routing.py` failures are pre-existing)

- [ ] **Step 3: Fix any test failures**

If tests fail due to the removed truncation (e.g., assertions checking truncated output), update the assertions to expect full content.

- [ ] **Step 4: Run full lint check**

Run: `cd backend && ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: update tests for truncation removal"
```
