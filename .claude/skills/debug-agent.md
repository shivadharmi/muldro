---
description: Debug orchestrator agent routing, tool calls, and trace issues
user-invocable: true
---

# Debug agent behavior

Use this when an agent isn't behaving as expected — wrong routing, tool call failures, budget issues, or trace gaps.

## Diagnostic steps

1. **Check traces**: Read `backend/src/orchestrator/tracing.py` and query traces:
   - GET `/v1/traces` — recent traces
   - GET `/v1/traces/{id}` — specific trace with agent spans
   - GET `/v1/traces/performance` — performance stats
2. **Check agent routing** in `backend/src/orchestrator/jarvis.py`:
   - `JarvisOrchestrator.process_message()` / `process_message_stream()` classify intent via Planner, then route via RouteResolver
   - Verify the routing logic matches the user intent
   - Check direct handlers (`set_goal`, `set_instruction`, `schedule_reminder`, `add_to_brief`) which execute before pipeline resolution
3. **Check tool scopes** in `backend/src/orchestrator/agents.py`:
   - `AGENT_TOOL_SCOPES` — is the tool in the agent's scope?
   - `SubAgent.can_use_tool()` — the enforcement point
4. **Check hooks** in `backend/src/orchestrator/hooks.py`:
   - Governor pre-tool hook — is it blocking the tool call?
   - Audit post-tool hook — check for logged errors
5. **Check budget** in `backend/src/orchestrator/budget.py`:
   - Per-agent cost tracking with daily limits
   - 3-mode degradation: normal → degraded → paused
   - Check if agent is in degraded/paused mode
6. **Check service wiring** in `backend/run.py:_build_services()`:
   - Is the required service instantiated?
   - Is it passed to the intelligence server via `configure()`?
7. **Check MCP resilience** in `backend/src/services/mcp_resilience.py`:
   - Circuit breaker state per MCP server
   - Is the external MCP server circuit open?
8. **Check runtime resilience** in `backend/src/orchestrator/agent_loop.py`:
   - Tool timeout: 60s via `asyncio.wait_for` — timed-out tools return `{"error": "...", "timed_out": true}`
   - API retry: 3 attempts with exponential backoff (2s/4s/8s) for `anthropic.RateLimitError`
   - Tool error signaling: `is_error: true` flag on error dicts so Claude knows the tool failed
9. **Run relevant tests**:
   - `pytest tests/test_orchestrator.py -v` — routing tests
   - `pytest tests/golden/ -v` — agent behavior golden tests
   - `pytest tests/test_integration.py -v` — recovery + circuit breaker
