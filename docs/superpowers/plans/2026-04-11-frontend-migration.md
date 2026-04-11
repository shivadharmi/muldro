# Spec 1B-iv: Frontend Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update all frontend TypeScript types, SSE parsing, agent config, and activity feed to match the post-switchover backend (PlanOutput, perceiver agent, capability-based events).

**Architecture:** Pure frontend migration — no backend changes. Replace `PlannerOutput` type with `PlanOutput`/`PlanStep`/`CapabilityGap`, change SSE event handler from `"decision"` to `"plan"`, swap observer/researcher agent entries for `perceiver`, and update runtime event types. Verify with `npm run build` (zero TS errors) and grep for zero stale references.

**Tech Stack:** Next.js 14, TypeScript, React, Zustand

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `frontend/src/lib/api.ts` | Modify | Delete `PlannerOutput`, add `PlanOutput`/`PlanStep`/`CapabilityGap`, rename `ChatSSEEvent.decision` → `plan`, update `MessageMetadata` |
| `frontend/src/lib/agent-config.ts` | Modify | Delete `observer`+`researcher`, add `perceiver`, demote `governor` |
| `frontend/src/components/jarvis/chat-panel.tsx` | Modify | Update import, state, SSE handler (`"plan"` event), decision badge → plan badge |
| `frontend/src/lib/types/runtime.ts` | Modify | Remove `route_selected`, add `step_routed` |
| `frontend/src/stores/activity-store.ts` | Modify | Replace `route_selected` with `step_routed` in SSE listener list |
| `frontend/src/lib/a2ui-types.ts` | Modify | Remove `decision` field from `WorkspaceSurfacePush` |
| `frontend/src/lib/types.ts` | Modify | Remove `decision` field from `Task` interface |
| `frontend/src/lib/api.ts` (workspace response) | Modify | Remove `decision` from `WorkspaceSurfaceResponse` |
| `CLAUDE.md` | Modify | Update Agent Boundaries, remove DEFAULT_ROUTES table, PlannerOutput→PlanOutput refs |

---

### Task 1: Replace PlannerOutput with PlanOutput types in api.ts

**Files:**
- Modify: `frontend/src/lib/api.ts:140-166` (ChatSSEEvent)
- Modify: `frontend/src/lib/api.ts:469-478` (PlannerOutput definition)
- Modify: `frontend/src/lib/api.ts:480-484` (MessageMetadata)
- Modify: `frontend/src/lib/api.ts:393-402` (WorkspaceSurfaceResponse)

- [ ] **Step 1: Delete PlannerOutput, add PlanOutput + PlanStep + CapabilityGap**

Replace the `PlannerOutput` interface block (lines 469-478) with:

```typescript
export interface PlanStep {
  step_id: string;
  description: string;
  actor: "jarvis" | "user";
  capability: string;
  input: Record<string, unknown>;
  depends_on: string[];
  risk: "none" | "low" | "medium" | "high";
  user_context: string | null;
}

export interface CapabilityGap {
  description: string;
  resolution: string;
  workaround: string | null;
}

export interface PlanOutput {
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
```

- [ ] **Step 2: Update ChatSSEEvent — rename `decision` field to `plan`**

In `ChatSSEEvent` (line 152), change:

```typescript
// Old:
  decision?: PlannerOutput;
// New:
  plan?: PlanOutput;
```

- [ ] **Step 3: Update MessageMetadata — change decision type**

In `MessageMetadata` (lines 480-484), change:

```typescript
// Old:
export interface MessageMetadata {
  trace_id: string | null;
  decision: PlannerOutput | null;
  agent_steps: MessageAgentStep[];
}
// New:
export interface MessageMetadata {
  trace_id: string | null;
  plan: PlanOutput | null;
  agent_steps: MessageAgentStep[];
}
```

- [ ] **Step 4: Remove `decision` from WorkspaceSurfaceResponse**

In `WorkspaceSurfaceResponse` (lines 393-402), delete the `decision` field:

```typescript
// Old:
interface WorkspaceSurfaceResponse {
  id: string;
  kind: string;
  preview: import("@/lib/a2ui-types").SurfacePreview;
  detail_config: import("@/lib/a2ui-types").DetailConfig | null;
  decision?: string | null;
  source_run_id?: string | null;
  response_preview?: string | null;
  created_at?: string | null;
}
// New:
interface WorkspaceSurfaceResponse {
  id: string;
  kind: string;
  preview: import("@/lib/a2ui-types").SurfacePreview;
  detail_config: import("@/lib/a2ui-types").DetailConfig | null;
  source_run_id?: string | null;
  response_preview?: string | null;
  created_at?: string | null;
}
```

- [ ] **Step 5: Verify no remaining PlannerOutput references in api.ts**

Run: `grep -n "PlannerOutput" frontend/src/lib/api.ts`
Expected: zero matches

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(spec1b-iv): replace PlannerOutput with PlanOutput types in api.ts"
```

---

### Task 2: Update agent-config.ts — delete observer/researcher, add perceiver

**Files:**
- Modify: `frontend/src/lib/agent-config.ts` (full file rewrite)

- [ ] **Step 1: Replace observer and researcher entries with perceiver, demote governor**

Replace the entire `AGENT_CONFIGS` array with:

```typescript
/** Static agent configuration — mirrors backend/src/orchestrator/agents.py */

export interface AgentConfig {
  name: string;
  model_tier: string;
  max_tokens: number;
  temperature: number;
  tools: string[];
}

export const AGENT_CONFIGS: AgentConfig[] = [
  {
    name: "perceiver",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: [
      "gmail_list", "gmail_read", "gmail_search",
      "calendar_list", "calendar_get",
      "drive_list", "drive_search",
      "slack_list_channels", "slack_get_messages", "slack_search",
      "ingest_event", "report_observation",
      "get_observation_cursor", "update_observation_cursor",
      "search", "perplexity_search",
      "playwright_navigate", "playwright_screenshot", "playwright_get_text",
    ],
  },
  {
    name: "librarian",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: ["update_entity", "get_entities", "search"],
  },
  {
    name: "planner",
    model_tier: "opus",
    max_tokens: 8192,
    temperature: 0.3,
    tools: ["get_active_plans", "search"],
  },
  {
    name: "governor",
    model_tier: "haiku",
    max_tokens: 2048,
    temperature: 0.1,
    tools: ["evaluate_policy", "report_governor_verdict"],
  },
  {
    name: "operator",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: [
      "gmail_send", "gmail_send_email", "gmail_draft", "gmail_create_draft", "gmail_reply",
      "calendar_create", "calendar_create_event", "calendar_update", "calendar_update_event",
      "slack_post_message", "slack_send_message",
      "github_comment", "github_create_issue", "github_create_pr",
      "update_execution",
    ],
  },
  {
    name: "presenter",
    model_tier: "sonnet",
    max_tokens: 4096,
    temperature: 0.3,
    tools: [
      "get_briefing", "search",
      "send_telegram", "send_approval_prompt", "push_ui_update",
    ],
  },
  {
    name: "persona",
    model_tier: "haiku",
    max_tokens: 4096,
    temperature: 0.3,
    tools: ["search", "extract_preferences"],
  },
];
```

Key changes:
- `observer` + `researcher` deleted, replaced by `perceiver` (union of both tool sets)
- `governor` demoted to `haiku` model, `max_tokens` 2048, tools updated (`approve_action` → `report_governor_verdict`)
- `librarian`/`presenter`/`persona` tools: `search_memory`/`get_entities` → `search` (matches backend's unified search tool)

- [ ] **Step 2: Verify no observer/researcher references remain**

Run: `grep -n "observer\|researcher" frontend/src/lib/agent-config.ts`
Expected: zero matches

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/agent-config.ts
git commit -m "feat(spec1b-iv): replace observer/researcher with perceiver in agent-config"
```

---

### Task 3: Update chat-panel.tsx — SSE handler + plan state

**Files:**
- Modify: `frontend/src/components/jarvis/chat-panel.tsx:4` (import)
- Modify: `frontend/src/components/jarvis/chat-panel.tsx:39` (ChatMessage type)
- Modify: `frontend/src/components/jarvis/chat-panel.tsx:86` (backendMessagesToChat)
- Modify: `frontend/src/components/jarvis/chat-panel.tsx:280` (SSE case "decision")
- Modify: `frontend/src/components/jarvis/chat-panel.tsx:462-470` (decision badge rendering)

- [ ] **Step 1: Update import — PlannerOutput → PlanOutput**

Line 4, change:

```typescript
// Old:
import { streamChat, type ChatSSEEvent, type ConversationMessage, type PlannerOutput } from "@/lib/api";
// New:
import { streamChat, type ChatSSEEvent, type ConversationMessage, type PlanOutput } from "@/lib/api";
```

- [ ] **Step 2: Update ChatMessage interface — decision → plan**

Line 39, change:

```typescript
// Old:
  decision?: PlannerOutput;
// New:
  plan?: PlanOutput;
```

- [ ] **Step 3: Update backendMessagesToChat — decision → plan**

In the `backendMessagesToChat` function (around line 86), change:

```typescript
// Old:
        decision: m.metadata_?.decision ?? undefined,
// New:
        plan: m.metadata_?.plan ?? undefined,
```

- [ ] **Step 4: Update SSE handler — case "decision" → case "plan"**

Lines 280-285, change:

```typescript
// Old:
            case "decision":
              updateAssistant((m) => ({
                ...m,
                decision: event.decision,
              }));
              break;
// New:
            case "plan":
              updateAssistant((m) => ({
                ...m,
                plan: event.plan,
              }));
              break;
```

- [ ] **Step 5: Update plan badge rendering in AssistantMessage**

Lines 462-470, change:

```typescript
// Old:
        {msg.decision && (
          <div className="flex items-center gap-2 px-2">
            <span className="text-[10px] uppercase tracking-wider text-t-tertiary">
              Decision
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-j-secondary-soft text-j-secondary border border-j-secondary/30">
              {msg.decision.decision}
            </span>
          </div>
        )}
// New:
        {msg.plan && (
          <div className="flex items-center gap-2 px-2">
            <span className="text-[10px] uppercase tracking-wider text-t-tertiary">
              Plan
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-j-secondary-soft text-j-secondary border border-j-secondary/30">
              {msg.plan.goal}
            </span>
            {msg.plan.steps.length > 0 && (
              <span className="text-[10px] text-t-muted">
                {msg.plan.steps.length} step{msg.plan.steps.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
        )}
```

- [ ] **Step 6: Verify no remaining PlannerOutput or "decision" event references**

Run: `grep -n "PlannerOutput\|case \"decision\"" frontend/src/components/jarvis/chat-panel.tsx`
Expected: zero matches

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/jarvis/chat-panel.tsx
git commit -m "feat(spec1b-iv): update chat panel SSE handler for plan events"
```

---

### Task 4: Update runtime.ts — remove route_selected, add step_routed

**Files:**
- Modify: `frontend/src/lib/types/runtime.ts:34-54`

- [ ] **Step 1: Update RuntimeEventType union**

Replace the type (lines 34-54) with:

```typescript
export type RuntimeEventType =
  | "command_received"
  | "plan_created"
  | "step_routed"
  | "run_created"
  | "agent_started"
  | "agent_completed"
  | "step_started"
  | "step_completed"
  | "step_failed"
  | "tool_call_started"
  | "tool_call_completed"
  | "tool_call_failed"
  | "approval_requested"
  | "approval_resolved"
  | "artifact_created"
  | "surface_created"
  | "fallback_triggered"
  | "run_completed"
  | "run_failed"
  | "run_cancelled";
```

Changes: `route_selected` removed, `step_routed` added.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/types/runtime.ts
git commit -m "feat(spec1b-iv): update RuntimeEventType — route_selected → step_routed"
```

---

### Task 5: Update activity-store.ts — SSE event type list

**Files:**
- Modify: `frontend/src/stores/activity-store.ts:93-101`

- [ ] **Step 1: Replace route_selected with step_routed in runtimeTypes array**

Lines 93-101, change:

```typescript
// Old:
    const runtimeTypes = [
      "command_received", "route_selected", "plan_created", "run_created",
      "step_started", "step_completed", "step_failed",
      "approval_requested", "approval_resolved",
      "tool_call_started", "tool_call_completed", "tool_call_failed",
      "artifact_created", "surface_created",
      "agent_started", "agent_completed",
      "run_completed", "run_failed", "run_cancelled",
    ];
// New:
    const runtimeTypes = [
      "command_received", "plan_created", "step_routed", "run_created",
      "step_started", "step_completed", "step_failed",
      "approval_requested", "approval_resolved",
      "tool_call_started", "tool_call_completed", "tool_call_failed",
      "artifact_created", "surface_created",
      "agent_started", "agent_completed",
      "run_completed", "run_failed", "run_cancelled",
    ];
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/activity-store.ts
git commit -m "feat(spec1b-iv): update activity store SSE types — route_selected → step_routed"
```

---

### Task 6: Update a2ui-types.ts — remove decision from WorkspaceSurfacePush

**Files:**
- Modify: `frontend/src/lib/a2ui-types.ts:76-87`

- [ ] **Step 1: Remove decision field from WorkspaceSurfacePush**

Replace lines 76-87 with:

```typescript
/** New two-layer surface push from backend (preview + detail_config). */
export interface WorkspaceSurfacePush {
  type: "surface";
  id: string;
  kind: string;
  preview: SurfacePreview;
  detail_config: DetailConfig | null;
  source_run_id: string | null;
  response_preview: string | null;
  created_at: string;
  ttl_hours: number;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/a2ui-types.ts
git commit -m "feat(spec1b-iv): remove decision field from WorkspaceSurfacePush"
```

---

### Task 7: Clean up types.ts — remove decision from Task interface

**Files:**
- Modify: `frontend/src/lib/types.ts:136-145`

- [ ] **Step 1: Remove decision field from Task interface**

Replace lines 136-145 with:

```typescript
export interface Task {
  task_id: string;
  goal: string;
  priority: string;
  status: string;
  created_at: string | null;
}
```

- [ ] **Step 2: Remove decision field from TaskDetail interface**

Replace lines 153-164 with:

```typescript
export interface TaskDetail {
  task_id: string;
  goal: string;
  priority: string;
  status: string;
  risk_level: string;
  reasoning_summary: string | null;
  steps: TaskStep[];
  execution_status: string | null;
  created_at: string | null;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts
git commit -m "feat(spec1b-iv): remove decision field from Task/TaskDetail types"
```

---

### Task 8: Build verification + stale reference sweep

**Files:** None (verification only)

- [ ] **Step 1: Run TypeScript build**

Run from `frontend/`:

```bash
npm run build
```

Expected: Build succeeds with zero TypeScript errors.

- [ ] **Step 2: Grep for stale references**

```bash
grep -rn "PlannerOutput\|\"observer\"\|\"researcher\"\|route_selected" frontend/src/
```

Expected: zero matches.

- [ ] **Step 3: Grep for stale decision field references that should now be plan**

```bash
grep -rn "\.decision" frontend/src/lib/api.ts frontend/src/components/jarvis/chat-panel.tsx
```

Expected: zero matches (the `decision` field in `Task`/`TaskDetail` in types.ts was removed in Task 7; the only remaining `decision` references should be in unrelated files like approval types where `decision_reason` is a different concept).

- [ ] **Step 4: Fix any build errors found**

If `npm run build` fails, fix the errors — they will be downstream consumers of the changed types. Common fixes:
- Any component accessing `msg.decision` needs to change to `msg.plan`
- Any component accessing `event.decision` needs to change to `event.plan`
- Any reference to `observer` or `researcher` agent names needs to use `perceiver`

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix(spec1b-iv): resolve build errors from type migration"
```

---

### Task 9: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update Agent Boundaries table**

Find the "Agent Boundaries" table and replace `Observer` + `Researcher` rows with `Perceiver`:

```markdown
| Agent | Role | Write Scope |
|-------|------|-------------|
| Perceiver | Gather information from any source — email, calendar, Slack, GitHub, web, internal knowledge | normalized_events |
| Librarian | Extract entities, update world model, store memories | entities, relationships, memories |
| Planner | Produce capability-based plans (structured PlanOutput JSON) | plans, plan_tasks, goal memories |
| Governor | Evaluate policies, gate approvals, verify plans | policy decisions, approvals |
| Operator | Execute approved plans via tools (reads context first) | task_runs, task_steps |
| Presenter | Generate user-facing output | briefings, A2UI surfaces (via SurfaceService + renderer.py) |
| Persona | Learn and store preferences | memories (preference type) |
```

Note: 7 agents now (Perceiver replaces Observer + Researcher).

- [ ] **Step 2: Update "Routes to:" line in Architecture section**

Change:
```
Routes to: Observer, Librarian, Planner, Governor,
           Operator, Presenter, Researcher, Persona
```
To:
```
Routes to: Perceiver, Librarian, Planner, Governor,
           Operator, Presenter, Persona
```

- [ ] **Step 3: Delete the Agent Routing & Execution section**

Delete the entire "Agent Routing & Execution" section that contains `RouteResolver`, `DEFAULT_ROUTES`, and the Decision→Pipeline mapping table. These are all deleted from the backend.

- [ ] **Step 4: Update PlannerOutput references → PlanOutput**

Search CLAUDE.md for `PlannerOutput` and replace with `PlanOutput`. Update the contracts description:

```markdown
- Runtime contracts: `backend/src/orchestrator/contracts.py` (PlanOutput, PolicyDecision, StepResult, ToolCallRequest, DomainEvent, WorkspaceSurfaceMetadata, WorkspaceSurfacePush)
```

- [ ] **Step 5: Update Common Mistakes section**

Remove these lines that reference deleted concepts:
- "Do not add new PlannerOutput decision types without a matching route in `DEFAULT_ROUTES`"
- "Do not use `has_key` condition for plan_id checks — use `has_truthy_key`"
- "Do not put `<decision_framework>` in non-Planner agent prompts"

Add:
```markdown
- Do not reference `PlannerOutput` — it was replaced by `PlanOutput` (no decision field, uses steps/capability_gaps)
- Do not reference `observer` or `researcher` agents — they were merged into `perceiver`
- Do not reference `RouteResolver` or `DEFAULT_ROUTES` — capability-based routing replaced decision-type routing
```

- [ ] **Step 6: Update "8 sub-agents" references to "7 sub-agents"**

Search for "8 sub-agents" or "8 agents" and change to "7 sub-agents" / "7 agents".

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(spec1b-iv): update CLAUDE.md for perceiver agent and PlanOutput contracts"
```

---

### Task 10: Final verification

- [ ] **Step 1: Full build**

```bash
cd frontend && npm run build
```

Expected: zero errors.

- [ ] **Step 2: Full stale reference sweep**

```bash
grep -rn "PlannerOutput\|\"observer\"\|\"researcher\"\|route_selected\|RouteResolver\|DEFAULT_ROUTES" frontend/src/ CLAUDE.md
```

Expected: zero matches.

- [ ] **Step 3: Verify agent count in CLAUDE.md**

```bash
grep -n "8 sub-agent\|8 agent" CLAUDE.md
```

Expected: zero matches (should all be 7 now).
