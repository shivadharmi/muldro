# Step 11 — Phase 3 JIT Plan: Worker/MCP dual-loop fix (as built)

> Detailed step plan for Phase 3 of the master plan
> (`2026-07-19-step11-legacy-retirement-implementation.md`). Independent of runtime selection.
> **The spec's original root cause (FastMCP transport) was empirically disproven at build; this doc
> records the corrected DB-factory fix that was actually implemented.** See spec §4 build note.

## Root cause (corrected, verified against live code 2026-07-19)

- Internal MCP tools acquire their DB session via `_shared._get_db()`
  (`src/tools/intelligence_server/_shared.py`), which used the **module-global `_shared._db_factory`**.
- `configure_tool_servers()` sets that global **last-writer-wins** on BOTH the API thread (per chat
  request, `routes_chat.py:96`) and the worker thread (startup, `run.py:125`).
- The engine is `threading.local` (`src/models/database.py:7`) — each thread's asyncpg pool binds to
  that thread's loop — but the shared **global pointer** meant a worker background tool call could use
  the API thread's loop-bound engine → `got Future attached to a different loop` (Step-10 D4).

## Why the transport diagnosis was wrong (3 build probes)

1. `list_tools()` across two concurrent loops on the SHARED global server → both succeed.
2. Full `call_tool()` round-trip, same setup → both succeed (FastMCP in-memory transport = fresh
   per-connection streams; sharing the server object across loops is fine).
3. A real asyncpg session factory bound to loop A, used from loop B → the exact `Future attached to a
   different loop` error.

⇒ The loop-bound resource is the DB engine reached inside the tool, not the MCP server/client. The
prototyped `build_internal_mcp_server()` per-ToolExecutor change was reverted.

## The fix (as built)

1. `_shared._get_db()` resolves the thread-local `get_session_factory()` when `_db_factory is None`;
   `_db_factory` remains a TEST-ONLY override (tests inject a mock via `configure()`). The old
   `not configured` RuntimeError guard is dropped (no test relied on it).
2. Three production call sites pass `None` to `configure_tool_servers`: `routes_chat.py`, `run.py`,
   `routes_auth_oauth_integration.py`.
3. FastMCP `jarvis_tools` server + `ToolExecutor` are **untouched**.

## Regression test (has teeth)

`tests/test_internal_tool_db_dual_loop.py`:
- `test_get_db_uses_thread_local_factory_across_concurrent_loops` — two threads / two concurrent loops,
  `_db_factory=None`, both reach the DB via `_get_db()`. Fails RED on pre-fix (`not configured`);
  the buggy shared-global variant raises the cross-loop `Future` error. Skips if Postgres unreachable
  (`_db_reachable` pattern).
- `test_get_db_honors_injected_override` — a configured override is still used (test seam preserved).

## Gate
- New tests green + the 3 config-path tests (`test_discover_capabilities`, `test_runtime_wiring`,
  `test_intelligence_tools_contracts`) still green. Full non-e2e gate green; ruff clean; single head
  `1a2770a28c39`. Spec §4 + §10.4 corrected. Parallel spec-compliance + code-quality reviewers.
