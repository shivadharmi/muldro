# Spec 1B-iv: Frontend Migration

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1B-ii (Orchestrator Core Switchover) — backend emits `plan` SSE event, uses perceiver agent name
**Builds toward:** Spec 2 (Trust), Spec 3 (Surfaces), Spec 4 (Perception)

## Problem Statement

After Specs 1B-ii and 1B-iii, the backend runs on capability-based routing with PlanOutput and the Perceiver agent. The frontend still has TypeScript types for PlannerOutput, parses `decision` SSE events, and renders observer/researcher agent names. This spec updates all frontend code to match the new backend contracts.

This spec is entirely frontend — no backend changes.

## Design

### Component 1: TypeScript Type Updates

**`lib/api.ts`:**
- Delete `PlannerOutput` type definition
- Add `PlanOutput` type:
  ```typescript
  interface PlanOutput {
    goal: string;
    reasoning: string;
    achievable: "full" | "partial" | "not_achievable";
    priority: "low" | "medium" | "high" | "critical";
    steps: PlanStep[];
    success_criteria: string;
    capability_gaps: CapabilityGap[];
    plan_id: string | null;
    requires_user_input: boolean;
  }

  interface PlanStep {
    step_id: string;
    description: string;
    actor: "jarvis" | "user";
    capability: string;
    input: Record<string, any>;
    depends_on: string[];
    risk: "none" | "low" | "medium" | "high";
    user_context: string | null;
  }

  interface CapabilityGap {
    description: string;
    resolution: string;
    workaround: string | null;
  }
  ```
- Update `ChatSSEEvent` — `decision` field removed, `plan` field added (type `PlanOutput`)
- Update `streamChat()` SSE parser to handle `plan` event type

**`lib/types.ts`:**
- Delete `Task`/`TaskDetail` types that reference `decision` field
- Update `ConversationMessage.metadata_` — `decision` field type changes from `PlannerOutput` to `PlanOutput`

**`lib/a2ui-types.ts`:**
- Update `WorkspaceSurfacePush` — remove `decision` field (surfaces keyed by capability, not decision type)

**`lib/types/runtime.ts`:**
- Update `RuntimeEventType` — remove decision-type-specific events (`route_selected`), add capability-based events (`plan_created`, `step_routed`)

### Component 2: Agent Config Update

**`lib/agent-config.ts`:**
- Delete `observer` entry (name, tools, color, description)
- Delete `researcher` entry
- Add `perceiver` entry:
  ```typescript
  {
    name: "perceiver",
    displayName: "Perceiver",
    description: "Gathers information from any source — email, calendar, Slack, GitHub, web, and internal knowledge",
    color: "#6366f1", // indigo (combines observer blue + researcher purple)
    model: "sonnet",
    tools: [...observerTools, ...researcherTools], // union of both
  }
  ```
- Update `governor` — mark as edge-case agent (reduced prominence in UI)

### Component 3: Chat Panel SSE Parsing

**`components/jarvis/chat-panel.tsx`:**
- Update SSE event handler:
  ```typescript
  // Old:
  if (event.type === "decision") {
    setDecision(event.decision as PlannerOutput);
  }
  
  // New:
  if (event.type === "plan") {
    setPlan(event.plan as PlanOutput);
  }
  ```
- Update agent step rendering — render `perceiver` agent name with its color/icon instead of `observer`/`researcher`
- Update plan display in chat — show capability-level steps instead of decision type label

### Component 4: Activity Feed Update

**`stores/activity-store.ts`:**
- Update event type parsing for new `RuntimeEventType` values
- Handle `plan_created` event (replaces `route_selected`)
- Handle `step_routed` event (new)

**`components/shell/activity-strip.tsx`:**
- Update event rendering for new event types
- Render capability names instead of decision types in activity entries

### Component 5: Documentation Updates

**`CLAUDE.md`:**
- Update Agent Boundaries table (observer/researcher → perceiver)
- Update Agent Routing & Execution section (capability-based, not decision-type)
- Update PlannerOutput references → PlanOutput
- Delete DEFAULT_ROUTES table
- Delete decision→pipeline mapping table
- Update Common Mistakes section

## Files Changed

### Modified Files (10)

| File | What changes | Risk |
|------|-------------|------|
| `frontend/src/lib/api.ts` | PlanOutput type, `plan` SSE event, streamChat parser | **HIGH** — SSE parsing is critical path |
| `frontend/src/lib/types.ts` | Delete decision-field types, update MessageMetadata | **MEDIUM** — type changes ripple |
| `frontend/src/lib/a2ui-types.ts` | Remove `decision` from WorkspaceSurfacePush | **LOW** — additive |
| `frontend/src/lib/types/runtime.ts` | New RuntimeEventType values | **LOW** — additive |
| `frontend/src/lib/agent-config.ts` | Delete 2 agents, add 1, demote 1 | **MEDIUM** — UI rendering |
| `frontend/src/components/jarvis/chat-panel.tsx` | Parse `plan` event, render perceiver | **HIGH** — main chat UI |
| `frontend/src/stores/activity-store.ts` | New event types | **LOW** — additive |
| `frontend/src/components/shell/activity-strip.tsx` | New event rendering | **LOW** — display only |
| `CLAUDE.md` | Update architecture documentation | **LOW** — docs |
| `docs/architecture/*.md` | Update flow diagrams, decisions, services | **LOW** — docs |

## Testing Strategy

- Frontend: build succeeds with no TypeScript errors
- Frontend: SSE parser handles `plan` event correctly
- Frontend: chat panel renders PlanOutput steps
- Frontend: activity feed shows new event types
- Frontend: agent-config has perceiver, no observer/researcher
- E2E: send message → see plan steps in chat → see perceiver agent step

## Success Criteria

1. Frontend TypeScript compiles with zero errors
2. Chat SSE parser handles `plan` event (not `decision`)
3. Agent steps render `perceiver` with correct color/icon
4. Activity feed shows capability-based events
5. No references to `PlannerOutput`, `observer`, `researcher` in frontend code
6. CLAUDE.md reflects new architecture

## Blast Radius

**Low-Medium — purely frontend, no backend changes.**

| File | Change | Risk |
|------|--------|------|
| `api.ts` | Type replacement + SSE parser | **HIGH** — SSE parsing critical |
| `chat-panel.tsx` | Event handler + rendering | **HIGH** — main UI |
| `types.ts` | Type deletion + update | **MEDIUM** — ripple to consumers |
| Other 5 files | Additive or display-only | **LOW** |

### Total: ~13 files (10 modified, 3 docs updated)
