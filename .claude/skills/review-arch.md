---
description: Review code changes against Muldro architecture rules and agent boundaries
user-invocable: true
---

# Architecture review

Review recent changes against Muldro architecture rules. Run `git diff` to see what changed, then check:

## 1. Agent boundaries (from CLAUDE.md)
- Is the **Planner** the only component deciding intent? (structured task graphs, never free-form text)
- Is the **Operator** the only component calling external write tools?
- Does the **Governor** sit before every external write?
- Is the **Presenter** the only one generating user-facing output?
- Is the **Researcher** strictly read-only?

## 2. Tool scope enforcement
- Check `backend/src/orchestrator/agents.py` `AGENT_TOOL_SCOPES` — are new tools assigned to the correct agents?
- No agent should have tools outside its role boundary
- External write tools (gmail_send, calendar_create, slack_post, github_*) must only be in Operator scope

## 3. Data contracts
- API endpoints return Pydantic models (not bare dicts)? Check `backend/src/api/schemas.py`
- Events normalized to standard schema? Check against `NormalizedEvent` model
- Planner outputs are structured task graphs (not free-form text)?
- New models use ULID string IDs with type prefix?

## 4. Service registration
- New services registered in `backend/run.py:_build_services()`?
- New MCP tools added to `backend/src/tools/intelligence_server.py` or `communication_server.py`?
- New routes wired into `backend/src/api/app.py`?

## 5. Security
- External writes approval-gated?
- Secrets not stored in memory or model context?
- Idempotency keys on events?
- Audit trail for external actions via `AuditService`?

## 6. Code quality
- All methods async?
- Type hints present?
- `cd backend && ruff check src/ tests/`
- `cd backend && pytest tests/ -v`

## 7. Report
List violations with `file_path:line_number` references and suggested fixes.
