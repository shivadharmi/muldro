# Remove Intelligence-Layer Truncation & Simplify Surface Generation

**Date:** 2026-04-14
**Branch:** `improve-surface-design-v1`

## Problem

1. **Content loss between agents:** The Perceiver reads content (e.g., a markdown file) but downstream agents (Operator, Presenter) receive truncated versions. A 15K-char file gets chopped to 3K chars. The Operator can't complete tasks like "copy this file to Notion" because it never sees the full content.

2. **196+ arbitrary truncation points:** Hardcoded char limits throughout the codebase silently destroy information that feeds agent decision-making. Context packs capped at 12K chars, tool results at 2K, plan analysis at 2K, conversation history at 8K — all with no logging, no user indication, no intelligence about what to keep.

3. **Programmatic surface generation duplicates agent work:** `SurfaceService` and 29 detail tab builders construct rigid A2UI component trees from DB queries. The Presenter agent already generates richer, context-aware responses. The programmatic path truncates content (80-200 chars), ignores relevance, and can't make judgment calls about emphasis.

## Principles

1. **Never truncate content that feeds agent decision-making.** Let Claude's context window and the BudgetTracker be the natural limits.
2. **Inform agents of display constraints instead of backend truncation.** The Presenter produces better 80-char titles than Python's `[:80]`.
3. **Surfaces are for autonomous/proactive system actions.** User-initiated queries get responses through chat — no duplicate surface card.
4. **System-state surfaces stay programmatic.** Approval cards, execution progress, and blocked-task alerts show DB state accurately — they don't need agent interpretation.

---

## Part 1: Remove Intelligence-Layer Truncation

### Category A — REMOVE limits entirely (agent-to-agent content flow)

These truncations actively destroy information that agents need:

| File | Line(s) | Current limit | Content truncated | Change |
|------|---------|--------------|-------------------|--------|
| `jarvis.py` | 85 | `_STEP_OUTPUT_CHAR_LIMIT = 30_000` | Per-step output to downstream agents | **Delete constant, remove all usages** |
| `jarvis.py` | 804, 848, 1141, 1195 | `[:_STEP_OUTPUT_CHAR_LIMIT]` | Step outputs injected into downstream agents and Presenter | **Remove truncation** |
| `jarvis.py` | 865, 1213 | `plan_text[:2000]` | Plan analysis passed to Presenter | **Remove truncation** |
| `jarvis.py` | 2560 | `message[:500]` | Query to context builder | **Remove truncation** |
| `context_builder.py` | 325 | `max_tokens=3000` (12K chars) | Full context pack (goals, entities, preferences, memories) | **Remove hard cap** |
| `context_builder.py` | 414 | `result[:max_chars]` | Rendered context after assembly | **Remove hard truncation** |
| `context_builder.py` | 482-484 | `result[:max_chars]` after compression | Compressed context | **Remove hard truncation** |
| `context_builder.py` | 509 | `text[:1200]` | Haiku summarization fallback | **Remove truncation** |
| `graph_executor.py` | 33 | `_STEP_OUTPUT_CHAR_LIMIT = 30_000` | Constant | **Delete** |
| `graph_executor.py` | 1393 | `[:_STEP_OUTPUT_CHAR_LIMIT]` | Prior step outputs injected into operator | **Remove truncation** |
| `graph_executor.py` | 307 | `plan.goal[:500]` | Query to context builder | **Remove truncation** |
| `graph_executor.py` | 1504 | `query[:500]` | Query to context builder | **Remove truncation** |
| `graph_executor.py` | 63 | `[:200]` | Output summary in step state | **Remove truncation** |
| `graph_executor.py` | 649 | `[:100]` | Key findings extraction | **Remove truncation** |
| `graph_executor.py` | 1611 | `[:500]` | Output summary in checkpoint | **Remove truncation** |
| `graph_executor.py` | 1647-1648 | `[:200]` per step | Recent step JSON in checkpoint context | **Remove truncation** |

### Category B — REMOVE limits (persistence/audit — store full content)

These truncations make debugging and outcome learning impossible:

| File | Line(s) | Current limit | Content truncated | Change |
|------|---------|--------------|-------------------|--------|
| `agent_loop.py` | 479-483 | 2,000 chars | Tool results stored in spans | **Remove truncation** |
| `agent_loop.py` | 576 | 5,000 chars | Extended thinking traces | **Remove truncation** |
| `hooks.py` | 124-125 | 500 chars each | Tool input/output in audit log | **Remove truncation** |
| `jarvis.py` | ~2400 | 8K total, 1K/snippet | Conversation history loaded for context | **Remove per-snippet cap, increase total to 20K** |
| `jarvis.py` | 2509 | `summary[:500]` | Conversation summary for vector store | **Remove truncation** |
| `jarvis.py` | 2525 | `[:500]` | History summary fallback | **Remove truncation** |
| `graph_executor.py` | 448, 455, 546, 1121, 1134, 1136, 1143 | 500 chars | Error messages stored in run/step | **Remove truncation** |

### Category C — REMOVE limits (query/search parameters)

These truncations silently drop parts of user queries:

| File | Line(s) | Current limit | Content truncated | Change |
|------|---------|--------------|-------------------|--------|
| `context_builder.py` | 477 | `query[:500]` | Query for semantic search | **Remove truncation** |
| `intent_classifier.py` | 152, 164 | `[:200]` | Goal extracted from intent | **Remove truncation** |

### Category D — KEEP as-is (logging, display, external API constraints)

These truncations serve real purposes and do not affect agent intelligence:

| File | Line(s) | Limit | Why keep |
|------|---------|-------|----------|
| `agent_loop.py` | 384, 451 | 200 chars | Log line readability (tool input/output summaries) |
| `jarvis.py` | 482-487 | 500 chars | InteractionLog preview fields (audit metadata, not intelligence) |
| `jarvis.py` | 666, 731, 979 | 100-500 chars | Event payload previews (logging) |
| `jarvis.py` | 933, 1286, 3256, 3344 | 200 chars | Error messages in log/event payloads |
| `jarvis.py` | 3033 | 80 chars | Memory text in log line |
| `jarvis.py` | 1584 | `prefs[:10]` | Preference list limit (relevance filtering) |
| `intent_classifier.py` | 251 | `max_tokens=150` | Haiku generation budget (appropriate) |
| `intent_classifier.py` | 281 | `message[:80]` | Log line preview |
| `hooks.py` | 24 | 100 chars | Slack approval display text |
| All connectors | Various | 200 chars | HTTP error messages (display only, full error in server logs) |
| `telegram.py` | 159, 218 | 4,000 chars | Telegram API hard limit — **change to split messages** instead of truncating |
| `surface_detail_builders.py` | Various | 60-200 chars | System-state surface display (see Part 2) |
| `contracts.py` | 315, 320 | 80/120 chars | Surface title/subtitle validators — **keep as safety net**, Presenter informed of constraints |
| `routes_chat.py` | 229, 237, 263 | 500-2000 chars | SSE streaming previews (wire format, full content available separately) |
| `dead_letter.py` | 46 | 2,000 chars | DLQ error storage (adequate for error messages) |

### Category E — Inform agents of display constraints

Instead of backend truncation, tell agents the constraint:

| Constraint | Where to inform | How |
|------------|----------------|-----|
| Telegram 4,096 char limit | Presenter prompt when `surface == "telegram"` | Add to presenter message: "Keep under 3500 chars. If long, prioritize action items." |
| Surface title 80 chars | PRESENTER_PROMPT | Add: "Surface titles must be under 80 characters." |
| Surface subtitle 120 chars | PRESENTER_PROMPT | Add: "Surface subtitles must be under 120 characters." |

---

## Part 2: Simplify Surface Generation

### What stays (system-state surfaces — programmatic)

These show accurate DB state and don't need agent interpretation:

| Builder | Surface kind | Why keep |
|---------|-------------|----------|
| `SurfaceService._build_approval_surfaces` | `approval` | Shows pending approvals from TrustEngine — needs exact tool name, risk level, blast radius from DB |
| `SurfaceService._build_active_execution_surfaces` | `plan` | Shows running plans with step counts — real-time DB state |
| `SurfaceService._build_priority_surfaces` | `alert` | Shows blocked/awaiting tasks — system state |
| `SurfaceService._load_persisted_surfaces` | Any | Serves previously-pushed (agent-generated) surfaces on page refresh |
| GraphExecutor `SurfaceUpdate` emissions (6 points) | Execution state | Live status updates (plan_ready, executing, approval_needed, completed, failed) |
| Detail tab builders for system-state surfaces | Various | `build_plan_overview`, `build_plan_context`, `build_plan_execution`, `build_approval_request`, `build_approval_risk`, `build_approval_history`, `build_alert_overview`, `build_alert_diagnostics` |

### What gets removed (content surfaces — Presenter already generates)

These duplicate what the Presenter agent produces:

| Builder | Surface kind | Why remove |
|---------|-------------|-----------|
| `SurfaceService._build_briefing_surface` | `briefing` | Presenter generates briefing via `/brief` command |
| `SurfaceService._build_recommendation_surfaces` | `recommendation` | Presenter generates recommendations in response |
| `SurfaceService._build_insight_surfaces` | `proactive_insight` | Already pushed via `_push_insight_surface` — programmatic builder just re-reads stored rows |
| `_push_workspace_surface` (fake PlanOutput wrapping) | `briefing` | Wraps Presenter output in truncated fake PlanOutput — Presenter should use `json:surface` block instead |
| Notifier approval surface push (`notifier.py:499`) | `approval` | Duplicates `SurfaceService._build_approval_surfaces` |
| `build_briefing_priorities` | briefing tab | Presenter generates this content |
| `build_briefing_events` | briefing tab | Presenter generates this content |
| `build_briefing_actions` | briefing tab | Presenter generates this content |
| `build_summary_overview` | summary tab | Presenter generates this content |
| `build_summary_sources` | summary tab | Presenter generates this content |
| `build_summary_context` | summary tab | Presenter generates this content |
| `build_recommendation_overview` | recommendation tab | Presenter generates this content |
| `build_recommendation_context` | recommendation tab | Presenter generates this content |
| `build_recommendation_evidence` | recommendation tab | Presenter generates this content |
| `build_comparison_options` | comparison tab | Presenter generates this content |
| `build_comparison_criteria` | comparison tab | Presenter generates this content |
| `build_timeline_events` | timeline tab | Presenter generates this content |
| `build_timeline_context` | timeline tab | Presenter generates this content |
| `build_table_data` | table tab | Presenter generates this content |
| `build_table_sources` | table tab | Presenter generates this content |
| `build_checklist_items` | checklist tab | Presenter generates this content |
| `build_checklist_context` | checklist tab | Presenter generates this content |
| `build_activity_runs` | activity tab | Presenter generates this content |
| `build_activity_stats` | activity tab | Presenter generates this content |
| `build_insight_signal` | insight tab | Already in the pushed insight data |
| `build_insight_actions` | insight tab | Already in the pushed insight data |
| `build_insight_context` | insight tab | Already in the pushed insight data |

### Surface kind simplification

**Before:** 13 surface kinds (summary, briefing, plan, checklist, approval, comparison, alert, timeline, table, recommendation, activity, execution, proactive_insight)

**After:** 5 surface kinds
- `plan` / `execution` — system-state (programmatic)
- `approval` — system-state (programmatic)
- `alert` — system-state (programmatic)
- `proactive_insight` — agent-driven (Perceiver + Relevance Assessor push)
- `summary` — agent-driven (Presenter `json:surface` block, catch-all for any Presenter-generated surface)

The Presenter can still generate surfaces for any response it deems surface-worthy via the `json:surface` mechanism. The `kind` field becomes an agent choice, not a programmatic category.

---

## Files to modify

### Part 1 (truncation removal):
1. `backend/src/orchestrator/jarvis.py` — Remove `_STEP_OUTPUT_CHAR_LIMIT`, all `[:N]` on step outputs, plan analysis, queries, history
2. `backend/src/services/context_builder.py` — Remove fixed 3K-token cap, remove hard truncation fallback
3. `backend/src/orchestrator/agent_loop.py` — Remove tool result and thinking truncation
4. `backend/src/orchestrator/hooks.py` — Remove audit log truncation
5. `backend/src/services/graph_executor.py` — Remove `_STEP_OUTPUT_CHAR_LIMIT`, all `[:N]` on outputs/errors/queries
6. `backend/src/orchestrator/intent_classifier.py` — Remove goal truncation
7. `backend/src/orchestrator/prompts.py` — Add display constraint guidance to PRESENTER_PROMPT
8. `backend/src/interface/telegram.py` — Change from truncation to message splitting

### Part 2 (surface simplification):
9. `backend/src/services/surface_builder.py` — Remove `_build_briefing_surface`, `_build_recommendation_surfaces`, `_build_insight_surfaces`
10. `backend/src/services/surface_detail_builders.py` — Remove 21 content tab builders, keep 8 system-state builders, update `TAB_BUILDERS` registry
11. `backend/src/orchestrator/jarvis.py` — Remove `_push_workspace_surface` method and its call from `generate_briefing`
12. `backend/src/services/notifier.py` — Remove programmatic approval surface push from `_deliver_to_web`
13. `frontend/src/lib/types/surfaces.ts` — Simplify surface kind type
14. `frontend/src/components/workspace/surface-card.tsx` — Remove unused kind labels/colors
15. `frontend/src/lib/design-tokens.ts` — Remove unused kind tokens

### Tests:
16. `backend/tests/test_graph_executor.py` — Update assertions for removed truncation
17. Any tests for removed surface builders

---

## What we are NOT changing

- List/item count limits (`[:10]`, `[:5]`) — these are relevance filtering, not truncation
- Log line truncation — readability
- SSE streaming previews — wire format optimization
- InteractionLog preview fields — audit metadata
- Connector HTTP error truncation — display only
- DLQ error storage limit — adequate for error messages
- Agent `max_tokens` configuration — generation budget, not truncation
- Pydantic validators on `SurfaceSpec.title`/`.subtitle` — kept as safety net (agents informed of constraints)
