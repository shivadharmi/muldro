---
description: Review code changes against Jarvis architecture rules
user-invocable: true
---

# Architecture review

Review recent changes against the Jarvis architecture rules. Check for violations:

1. **Read CLAUDE.md** — the source of truth for architecture rules
2. **Check agent boundaries**:
   - Is business logic leaking into the OpenClaw plugin?
   - Is the planner the only component deciding intent?
   - Is the operator the only component calling external tools?
   - Does the governor sit before external writes?
3. **Check data contracts**:
   - Are API endpoints returning Pydantic models (not bare dicts)?
   - Are events normalized to the standard schema?
   - Are planner outputs structured task graphs (not free-form text)?
4. **Check security**:
   - Are external writes approval-gated?
   - Are secrets kept out of model context?
   - Are idempotency keys on events?
   - Is there an audit trail for external actions?
5. **Check code quality**:
   - Are all methods async?
   - Are type hints present?
   - Does ruff pass? `ruff check src/ tests/`
   - Do tests pass? `pytest tests/ -v`
6. **Report findings** — list violations with file:line references and suggested fixes
