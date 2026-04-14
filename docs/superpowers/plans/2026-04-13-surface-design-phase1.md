# Surface Design Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all hardcoded Tailwind colors, inline HSL values, and inconsistent border-radius across ~50 frontend components with Jarvis design tokens, and add missing accessibility attributes (focus rings, aria labels, empty/loading states).

**Architecture:** Pattern-first extraction (shared `design-tokens.ts` utility, `StatusDot` component, `FOCUS_RING` constant) followed by a 7-batch file sweep applying 5 mechanical rules. Accessibility fixes happen inline during the sweep.

**Tech Stack:** React 19, Next.js 16, TypeScript 5, Tailwind CSS 4, Zustand

**Spec:** `docs/superpowers/specs/2026-04-13-surface-design-phase1-design.md`

---

### Task 1: Create shared design-tokens utility

**Files:**
- Create: `frontend/src/lib/design-tokens.ts`

- [ ] **Step 1: Create the design-tokens utility file**

```typescript
// frontend/src/lib/design-tokens.ts

/** Maps execution/task status to a Tailwind bg class */
export function statusColor(status: string): string {
  switch (status) {
    case "running":
    case "executing":
    case "in_progress":
      return "bg-j-info";
    case "completed":
    case "ok":
    case "approved":
    case "normal":
    case "healthy":
      return "bg-j-success";
    case "failed":
    case "rejected":
    case "error":
      return "bg-j-error";
    case "awaiting_approval":
    case "pending_approval":
    case "degraded":
      return "bg-j-warning";
    case "proposal":
      return "bg-j-secondary";
    case "pending":
    case "cancelled":
    case "paused":
    default:
      return "bg-t-muted";
  }
}

/** Maps execution/task status to a Tailwind text class */
export function statusTextColor(status: string): string {
  switch (status) {
    case "running":
    case "executing":
    case "in_progress":
      return "text-j-info";
    case "completed":
    case "ok":
    case "approved":
    case "normal":
    case "healthy":
      return "text-j-success";
    case "failed":
    case "rejected":
    case "error":
      return "text-j-error";
    case "awaiting_approval":
    case "pending_approval":
    case "degraded":
      return "text-j-warning";
    case "proposal":
    case "user_action":
      return "text-j-secondary";
    case "pending":
    case "cancelled":
    case "paused":
    default:
      return "text-t-muted";
  }
}

/** Maps execution phase to a Tailwind text class + optional pulse */
export function phaseTextColor(phase: string): { className: string; pulse: boolean } {
  switch (phase) {
    case "planning":
      return { className: "text-j-info", pulse: true };
    case "plan_ready":
      return { className: "text-j-info", pulse: false };
    case "executing":
      return { className: "text-j-info", pulse: true };
    case "approval_needed":
      return { className: "text-j-warning", pulse: true };
    case "completed":
      return { className: "text-j-success", pulse: false };
    case "failed":
      return { className: "text-j-error", pulse: false };
    case "partial":
      return { className: "text-j-warning", pulse: false };
    case "proposal":
      return { className: "text-j-secondary", pulse: true };
    default:
      return { className: "text-t-muted", pulse: false };
  }
}

/** Maps execution phase to a Tailwind bg class + optional pulse */
export function phaseBgColor(phase: string): { className: string; pulse: boolean } {
  switch (phase) {
    case "planning":
    case "executing":
      return { className: "bg-j-info", pulse: true };
    case "plan_ready":
      return { className: "bg-j-info", pulse: false };
    case "approval_needed":
      return { className: "bg-j-warning", pulse: true };
    case "completed":
      return { className: "bg-j-success", pulse: false };
    case "failed":
      return { className: "bg-j-error", pulse: false };
    case "partial":
      return { className: "bg-j-warning", pulse: false };
    case "proposal":
      return { className: "bg-j-secondary", pulse: true };
    default:
      return { className: "bg-t-muted", pulse: false };
  }
}

/** Maps risk level to a Tailwind bg class */
export function riskColor(risk: string): string {
  switch (risk) {
    case "low":
      return "bg-j-success";
    case "medium":
      return "bg-j-warning";
    case "high":
    case "critical":
      return "bg-j-error";
    default:
      return "bg-t-muted";
  }
}

/** Maps trust level to a Tailwind bg class */
export function trustLevelColor(level: string): string {
  switch (level) {
    case "first_use":
      return "bg-t-muted";
    case "learning":
      return "bg-j-info";
    case "trusted":
      return "bg-j-success";
    case "autonomous":
      return "bg-j-secondary";
    case "blocked":
      return "bg-j-error";
    default:
      return "bg-t-muted";
  }
}

/** Maps surface kind to badge styling (bg + text classes) */
export function kindStyle(kind: string): { bg: string; text: string } {
  switch (kind) {
    case "plan":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    case "approval":
      return { bg: "bg-j-warning-soft", text: "text-j-warning" };
    case "briefing":
      return { bg: "bg-j-success-soft", text: "text-j-success" };
    case "alert":
      return { bg: "bg-j-error-soft", text: "text-j-error" };
    case "proactive_insight":
    case "recommendation":
      return { bg: "bg-j-secondary-soft", text: "text-j-secondary" };
    case "execution":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    default:
      return { bg: "bg-surface-3", text: "text-t-secondary" };
  }
}

/** Maps priority to badge styling (bg + text classes) */
export function priorityStyle(priority: string): { bg: string; text: string } {
  switch (priority) {
    case "low":
      return { bg: "bg-surface-3", text: "text-t-secondary" };
    case "medium":
      return { bg: "bg-j-info-soft", text: "text-j-info" };
    case "high":
      return { bg: "bg-j-warning-soft", text: "text-j-warning" };
    case "critical":
      return { bg: "bg-j-error-soft", text: "text-j-error" };
    default:
      return { bg: "bg-surface-3", text: "text-t-secondary" };
  }
}

/** Maps search source DB to badge styling */
export function sourceDbStyle(db: string): string {
  switch (db) {
    case "qdrant":
      return "bg-j-info-soft text-j-info";
    case "postgres_fts":
      return "bg-j-success-soft text-j-success";
    case "neo4j":
      return "bg-j-secondary-soft text-j-secondary";
    default:
      return "bg-surface-2 text-t-tertiary";
  }
}

/** Human-readable labels for trust levels */
export const TRUST_LEVEL_LABELS: Record<string, string> = {
  first_use: "First Use",
  learning: "Learning",
  trusted: "Trusted",
  autonomous: "Autonomous",
  blocked: "Blocked",
};

/** Human-readable labels for surface kinds */
export const KIND_LABELS: Record<string, string> = {
  plan: "Plan",
  approval: "Approval",
  briefing: "Briefing",
  alert: "Alert",
  summary: "Summary",
  recommendation: "Rec",
  proactive_insight: "Insight",
  execution: "Execution",
  checklist: "Checklist",
  comparison: "Compare",
  timeline: "Timeline",
  table: "Table",
  activity: "Activity",
};
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20`
Expected: No errors related to `design-tokens.ts`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/design-tokens.ts
git commit -m "feat: add shared design-tokens utility for semantic color mappings"
```

---

### Task 2: Create StatusDot component and FOCUS_RING constant

**Files:**
- Create: `frontend/src/components/ui/status-dot.tsx`
- Create: `frontend/src/lib/focus-ring.ts`

- [ ] **Step 1: Create the FOCUS_RING constant**

```typescript
// frontend/src/lib/focus-ring.ts

/** Standard focus ring classes for interactive elements. Import instead of duplicating. */
export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface-0";
```

- [ ] **Step 2: Create the StatusDot component**

```typescript
// frontend/src/components/ui/status-dot.tsx

import { statusColor, phaseBgColor } from "@/lib/design-tokens";

interface StatusDotProps {
  /** Uses statusColor() to derive bg class */
  status?: string;
  /** Uses phaseBgColor() to derive bg class + pulse animation */
  phase?: string;
  /** Direct Tailwind bg class override (e.g. "bg-j-success") */
  color?: string;
  /** sm = w-1.5 h-1.5, md = w-2 h-2. Default: md */
  size?: "sm" | "md";
}

/** Tiny colored dot for status/phase indicators. Replaces inline <span> patterns. */
export function StatusDot({ status, phase, color, size = "md" }: StatusDotProps) {
  let bgClass: string;
  let pulse = false;

  if (phase) {
    const p = phaseBgColor(phase);
    bgClass = p.className;
    pulse = p.pulse;
  } else if (status) {
    bgClass = statusColor(status);
    pulse = status === "running" || status === "executing" || status === "in_progress";
  } else {
    bgClass = color ?? "bg-t-muted";
  }

  const sizeClass = size === "sm" ? "w-1.5 h-1.5" : "w-2 h-2";

  return (
    <span
      className={`inline-block rounded-full shrink-0 ${sizeClass} ${bgClass} ${pulse ? "animate-pulse-live" : ""}`}
      aria-hidden="true"
    />
  );
}
```

- [ ] **Step 3: Verify compilation**

Run: `cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/focus-ring.ts frontend/src/components/ui/status-dot.tsx
git commit -m "feat: add StatusDot component and shared FOCUS_RING constant"
```

---

### Task 3: Sweep Batch 3 — A2UI Complex Surfaces (hardcoded colors)

These are the highest-impact files with the most hardcoded colors. Token-swap only — no layout changes.

**Files:**
- Modify: `frontend/src/components/a2ui/components/step-list.tsx`
- Modify: `frontend/src/components/a2ui/components/execution-surface.tsx`
- Modify: `frontend/src/components/a2ui/components/insight-surface.tsx`
- Modify: `frontend/src/components/a2ui/components/inline-approval.tsx`

- [ ] **Step 1: Fix step-list.tsx**

Replace the hardcoded `statusIcon` color map with design-token imports:

```typescript
// Replace lines 10-17 in step-list.tsx
import { statusTextColor } from "@/lib/design-tokens";

const statusIcon: Record<string, { icon: string; className: string }> = {
  pending: { icon: "○", className: "text-t-tertiary" },
  executing: { icon: "◉", className: `${statusTextColor("executing")} animate-pulse` },
  completed: { icon: "✓", className: statusTextColor("completed") },
  failed: { icon: "✗", className: statusTextColor("failed") },
  approval_needed: { icon: "⚠", className: statusTextColor("awaiting_approval") },
  user_action: { icon: "👤", className: statusTextColor("user_action") },
};
```

Also replace:
- Line 29: `rounded` → `rounded-[var(--radius-sm)]`
- Line 44: `text-red-400` → `text-j-error`
- Line 70: `text-red-400` → `text-j-error`

- [ ] **Step 2: Fix execution-surface.tsx**

Replace the `phaseLabel` color map (lines 25-33):

```typescript
import { phaseTextColor } from "@/lib/design-tokens";

const phaseLabel: Record<string, string> = {
  planning: "Planning",
  plan_ready: "Plan Ready",
  executing: "Executing",
  approval_needed: "Approval Needed",
  completed: "Completed",
  failed: "Failed",
  partial: "Partially Completed",
};
```

Update usage (line 49):
```typescript
const labelText = phaseLabel[phase] ?? "Planning";
const { className: phaseClass } = phaseTextColor(phase);
// ...
<span className={`text-xs font-medium ${phaseClass}`}>{labelText}</span>
```

Replace all remaining hardcoded colors throughout the file:
- Line 55: `border-blue-400/30 border-t-blue-400` → `border-j-info/30 border-t-j-info`
- Line 72: `rounded-lg bg-green-500/5 border-green-500/20` → `rounded-[var(--radius-lg)] bg-j-success-soft border-j-success/20`
- Line 79: `text-green-400` → `text-j-success`
- Line 104: `text-blue-400` → `text-j-info`
- Line 116: `rounded-lg bg-red-500/5 border-red-500/20` → `rounded-[var(--radius-lg)] bg-j-error-soft border-j-error/20`
- Line 117: `text-red-400` → `text-j-error`
- Line 120: `text-red-400` → `text-j-error`
- Line 133: `bg-red-500` → `bg-j-error`, `bg-green-500` → `bg-j-success`, `bg-blue-500` → `bg-j-info`

- [ ] **Step 3: Fix insight-surface.tsx**

Replace all hardcoded colors:
- Line 54: `bg-violet-500/20 text-violet-400` → `bg-j-secondary-soft text-j-secondary`; `rounded` → `rounded-[var(--radius-sm)]`
- Line 58: `bg-amber-500/20 text-amber-400` → `bg-j-warning-soft text-j-warning`; `rounded` → `rounded-[var(--radius-sm)]`
- Line 82: `bg-blue-500/15 text-blue-400` → `bg-j-info-soft text-j-info`
- Line 99: `bg-violet-500/20 text-violet-300 hover:bg-violet-500/30` → `bg-j-secondary-soft text-j-secondary hover:bg-j-secondary/20`; `rounded-md` → `rounded-[var(--radius-md)]`

- [ ] **Step 4: Fix inline-approval.tsx**

Replace all hardcoded colors:
- Line 27: `rounded-lg border-amber-500/30 bg-amber-500/5` → `rounded-[var(--radius-lg)] border-j-warning/30 bg-j-warning-soft`
- Line 30: `text-amber-400` → `text-j-warning`
- Line 51: `text-blue-400/80` → `text-j-info/80`
- Line 61: `rounded-md bg-green-600 text-white hover:bg-green-500` → `rounded-[var(--radius-md)] bg-j-success text-white hover:bg-j-success/90`
- Line 68: `rounded-md` → `rounded-[var(--radius-md)]`
- Line 75: `rounded-md text-red-400 hover:bg-red-500/10` → `rounded-[var(--radius-md)] text-j-error hover:bg-j-error-soft`

- [ ] **Step 5: Build and verify**

Run: `cd frontend && npx next build 2>&1 | tail -5`
Expected: `✓ Compiled successfully`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/a2ui/components/step-list.tsx \
       frontend/src/components/a2ui/components/execution-surface.tsx \
       frontend/src/components/a2ui/components/insight-surface.tsx \
       frontend/src/components/a2ui/components/inline-approval.tsx
git commit -m "refactor: replace hardcoded colors with design tokens in A2UI complex surfaces"
```

---

### Task 4: Sweep Batch 5 — Search, Integrations, Primitives

**Files:**
- Modify: `frontend/src/components/feature/search/result-group-list.tsx`
- Modify: `frontend/src/components/feature/search/result-detail-pane.tsx`
- Modify: `frontend/src/components/search/search-bar.tsx`
- Modify: `frontend/src/components/a2ui/renderer.tsx`
- Modify: `frontend/src/app/integrations/page.tsx` (AdvancedMCPSection)
- Modify: `frontend/src/components/primitives/live-activity-feed.tsx`

- [ ] **Step 1: Fix result-group-list.tsx — replace hardcoded SOURCE_DB_COLORS + add empty state**

Replace the entire `SOURCE_DB_COLORS` map and add imports:

```typescript
import { sourceDbStyle } from "@/lib/design-tokens";
import { EmptyState } from "@/components/ui/empty-state";
```

Delete the `SOURCE_DB_COLORS` const (lines 5-9). Replace the empty state (lines 19-23):

```typescript
  if (entries.length === 0) {
    return <EmptyState title="No results found" description="Try adjusting your search query" />;
  }
```

Replace the badge usage (line 47):
```typescript
className={`shrink-0 px-1.5 py-0.5 text-[10px] font-medium rounded-[var(--radius-sm)] ${sourceDbStyle(r.source_db)}`}
```

Add focus ring and hover to result buttons (line 39):
```typescript
className="w-full text-left p-2 rounded-[var(--radius-sm)] hover:bg-surface-2 transition-colors duration-150 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface-0"
```

Add `aria-label` to group headings (line 31):
```typescript
<section key={type} aria-label={`${type} results`}>
```

- [ ] **Step 2: Fix result-detail-pane.tsx — add EmptyState**

Add import for `EmptyState` and replace the empty state text with:
```typescript
<EmptyState title="Select a result" description="Choose an item from the list to view details" />
```

- [ ] **Step 2b: Fix integration-detail-panel.tsx — add EmptyState**

Add import for `EmptyState` and replace the plain text empty state with:
```typescript
<EmptyState title="Select an integration" description="Choose a provider to manage its settings" />
```

- [ ] **Step 3: Fix search-bar.tsx — radius + focus ring**

Replace `rounded-lg` with `rounded-[var(--radius-lg)]`. Add focus ring to input and select elements.

- [ ] **Step 4: Fix renderer.tsx — replace hardcoded yellow border**

Line 50: `border-yellow-500/30 rounded` → `border-j-warning/30 rounded-[var(--radius-sm)]`

- [ ] **Step 5: Fix integrations/page.tsx AdvancedMCPSection — replace hardcoded colors**

In the MCP server installation list items:
- `bg-green-400` → `bg-j-success`
- `bg-yellow-400` → `bg-j-warning`
- `bg-red-400` → `bg-j-error`
- `text-green-400` → `text-j-success`

Add `aria-expanded={expanded}` to the toggle button:
```typescript
<button
  onClick={() => setExpanded(!expanded)}
  aria-expanded={expanded}
  className="..."
>
```

Replace providers loading state (line 302-304):
```typescript
import { SkeletonGrid } from "@/components/ui/skeleton";
// ...
{providers.length === 0 && <SkeletonGrid count={6} />}
```

- [ ] **Step 6: Fix live-activity-feed.tsx — add aria role**

Add `role="log"` and `aria-live="polite"` to the feed container element.

- [ ] **Step 7: Build and verify**

Run: `cd frontend && npx next build 2>&1 | tail -5`
Expected: `✓ Compiled successfully`

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/feature/search/result-group-list.tsx \
       frontend/src/components/feature/search/result-detail-pane.tsx \
       frontend/src/components/search/search-bar.tsx \
       frontend/src/components/a2ui/renderer.tsx \
       frontend/src/app/integrations/page.tsx \
       frontend/src/components/primitives/live-activity-feed.tsx \
       frontend/src/components/feature/integrations/integration-detail-panel.tsx
git commit -m "refactor: apply design tokens to search, integrations, and primitives"
```

---

### Task 5: Sweep Batch 1 — A2UI Small Components (border-radius only)

Most A2UI small components already use design tokens. They only need R3 (border-radius fixes).

**Files:**
- Modify: `frontend/src/components/a2ui/components/alert.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/button.tsx` — `rounded` → `rounded-[var(--radius-sm)]`; add FOCUS_RING import and apply to button
- Modify: `frontend/src/components/a2ui/components/text-field.tsx` — `rounded` → `rounded-[var(--radius-sm)]`; add FOCUS_RING to input
- Modify: `frontend/src/components/a2ui/components/select.tsx` — `rounded` → `rounded-[var(--radius-sm)]`; add FOCUS_RING to select
- Modify: `frontend/src/components/a2ui/components/metric.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/card.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/modal.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/form.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/entity-card.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/memory-card.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/code-block.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/toggle.tsx` — add FOCUS_RING to toggle switch

- [ ] **Step 1: Apply R3 to all 12 files listed above**

For each file, find `rounded-lg` and replace with `rounded-[var(--radius-lg)]`. Find bare `rounded` (not `rounded-full` or `rounded-[`) and replace with `rounded-[var(--radius-sm)]`.

For `button.tsx`, `text-field.tsx`, `select.tsx`, and `toggle.tsx`: also import `FOCUS_RING` from `@/lib/focus-ring` and add it to the interactive element's className.

- [ ] **Step 2: Build and verify**

Run: `cd frontend && npx next build 2>&1 | tail -5`
Expected: `✓ Compiled successfully`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/alert.tsx \
       frontend/src/components/a2ui/components/button.tsx \
       frontend/src/components/a2ui/components/text-field.tsx \
       frontend/src/components/a2ui/components/select.tsx \
       frontend/src/components/a2ui/components/metric.tsx \
       frontend/src/components/a2ui/components/card.tsx \
       frontend/src/components/a2ui/components/modal.tsx \
       frontend/src/components/a2ui/components/form.tsx \
       frontend/src/components/a2ui/components/entity-card.tsx \
       frontend/src/components/a2ui/components/memory-card.tsx \
       frontend/src/components/a2ui/components/code-block.tsx \
       frontend/src/components/a2ui/components/toggle.tsx
git commit -m "refactor: standardize border-radius tokens and focus rings in A2UI small components"
```

---

### Task 6: Sweep Batch 2 — A2UI Data Components (border-radius)

**Files:**
- Modify: `frontend/src/components/a2ui/components/a2ui-table.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`
- Modify: `frontend/src/components/a2ui/components/data-grid.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`; `rounded` → `rounded-[var(--radius-sm)]`
- Modify: `frontend/src/components/a2ui/components/kanban-board.tsx` — `rounded-lg` → `rounded-[var(--radius-lg)]`; `rounded` → `rounded-[var(--radius-sm)]`

- [ ] **Step 1: Apply R3 to all 3 files**

For each file, replace `rounded-lg` with `rounded-[var(--radius-lg)]` and bare `rounded` with `rounded-[var(--radius-sm)]`.

- [ ] **Step 2: Build and verify**

Run: `cd frontend && npx next build 2>&1 | tail -5`
Expected: `✓ Compiled successfully`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/a2ui/components/a2ui-table.tsx \
       frontend/src/components/a2ui/components/data-grid.tsx \
       frontend/src/components/a2ui/components/kanban-board.tsx
git commit -m "refactor: standardize border-radius tokens in A2UI data components"
```

---

### Task 7: Sweep Batch 4 — Knowledge Page Components (inline HSL + radius)

**Files:**
- Modify: `frontend/src/components/knowledge/graph-view.tsx`
- Modify: `frontend/src/components/knowledge/graph-detail-panel.tsx`
- Modify: `frontend/src/components/knowledge/graph-filters.tsx`
- Modify: `frontend/src/components/knowledge/graph-context-menu.tsx`
- Modify: `frontend/src/components/knowledge/knowledge-search.tsx`
- Modify: `frontend/src/components/knowledge/stats-view.tsx`
- Modify: `frontend/src/components/knowledge/community-card.tsx`
- Modify: `frontend/src/components/knowledge/memory-detail-panel.tsx`
- Modify: `frontend/src/components/knowledge/memory-row.tsx`
- Modify: `frontend/src/components/knowledge/memories-view.tsx`
- Modify: `frontend/src/components/knowledge/donut-chart.tsx`

- [ ] **Step 1: Fix graph-view.tsx — replace inline HSL with CSS custom properties**

The force-graph library requires raw color strings. Replace the hardcoded HSL color maps with a function that reads CSS custom properties at render time:

```typescript
// Replace the ENTITY_TYPE_COLORS object (lines 26-33) with:
const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "var(--jarvis-primary)",
  organization: "var(--jarvis-secondary)",
  project: "var(--jarvis-accent)",
  document: "var(--jarvis-warning)",
  repository: "var(--jarvis-error)",
};
const DEFAULT_NODE_COLOR = "var(--jarvis-text-muted)";
const LINK_HIGHLIGHT_COLOR = "var(--jarvis-primary)";
```

Note: `react-force-graph-2d` accepts CSS color strings including `var()` — test to confirm. If `var()` does not work with the canvas renderer, use `getComputedStyle(document.documentElement).getPropertyValue('--jarvis-primary')` at component mount time to resolve actual values.

Also replace the ad-hoc empty state with:
```typescript
import { EmptyState } from "@/components/ui/empty-state";
// In the empty graph branch:
<EmptyState title="No graph data" description="Connect sources and interact with Jarvis to build the knowledge graph" />
```

- [ ] **Step 2: Fix graph-detail-panel.tsx — replace inline HSL**

Replace `ENTITY_TYPE_COLORS` and `MEMORY_TYPE_COLORS` objects (lines 16-28) with CSS custom property references, same pattern as graph-view.

- [ ] **Step 3: Fix graph-filters.tsx — replace arbitrary Tailwind HSL**

Replace all `border-[hsl(...)]`, `text-[hsl(...)]`, `bg-[hsl(...)]` patterns (lines 8-29) with Jarvis token classes. Map entity types to semantic tokens using the same mapping from graph-view.

- [ ] **Step 4: Fix knowledge-search.tsx — replace arbitrary Tailwind HSL**

Lines 83, 92: `text-[hsl(193_100%_66%)]` → `text-j-primary`; `text-[hsl(247_92%_74%)]` → `text-j-secondary`.

- [ ] **Step 5: Fix stats-view.tsx — replace inline HSL + loading state**

Replace `ENTITY_TYPE_CHART_COLORS` (lines 16-20) and `CHART_PALETTE` (lines 24-28) with CSS custom property references: `"var(--jarvis-chart-1)"` through `"var(--jarvis-chart-5)"`. Replace custom loading skeleton with `<SkeletonGrid count={4} />`.

- [ ] **Step 6: Fix community-card.tsx — replace inline HSL**

Replace `SEED_TYPE_COLORS` (lines 7-11) and `DEFAULT_SEED_COLOR` (line 14) with CSS custom property references.

- [ ] **Step 7: Fix memory-detail-panel.tsx — replace inline HSL + border-radius**

Replace `ENTITY_TYPE_COLORS` (lines 30-38) with CSS custom property references. Replace `rounded-lg` → `rounded-[var(--radius-lg)]`, `rounded-md` → `rounded-[var(--radius-md)]`.

- [ ] **Step 8: Fix memory-row.tsx — border-radius + focus ring + hover**

Replace `rounded-lg` → `rounded-[var(--radius-lg)]`. Add `hover:bg-surface-2 transition-colors duration-150` to clickable row. Add `aria-label` with truncated content. Add FOCUS_RING import and apply.

- [ ] **Step 9: Fix memories-view.tsx — empty state**

Replace custom empty state with:
```typescript
import { EmptyState } from "@/components/ui/empty-state";
// ...
<EmptyState title="No memories found" description="Memories will appear as Jarvis learns from interactions" />
```

Add focus ring to filter/sort buttons.

- [ ] **Step 10: Fix graph-context-menu.tsx — radius + aria**

Replace `rounded-lg` → `rounded-[var(--radius-lg)]`. Add `role="menu"` to container and `role="menuitem"` to items.

- [ ] **Step 11: Fix donut-chart.tsx — HSL fallback**

Line 56: Replace `hsl(214 16% 18%)` → use CSS custom property as SVG fallback.

- [ ] **Step 12: Build and verify**

Run: `cd frontend && npx next build 2>&1 | tail -5`
Expected: `✓ Compiled successfully`

- [ ] **Step 13: Commit**

```bash
git add frontend/src/components/knowledge/
git commit -m "refactor: replace inline HSL and hardcoded colors with design tokens in knowledge components"
```

---

### Task 8: Sweep Batch 6 — Chat/Jarvis Components (radius + accessibility)

**Files:**
- Modify: `frontend/src/components/jarvis/chat-panel.tsx`
- Modify: `frontend/src/components/jarvis/session-sidebar.tsx`
- Modify: `frontend/src/components/jarvis/command-input.tsx`
- Modify: `frontend/src/components/jarvis/markdown-renderer.tsx`

- [ ] **Step 1: Fix chat-panel.tsx — border-radius**

Replace all `rounded-lg` → `rounded-[var(--radius-lg)]`.

- [ ] **Step 2: Fix session-sidebar.tsx — empty state + aria + hover**

Add import for `EmptyState`. Replace bare text empty state with:
```typescript
<EmptyState title="No conversations yet" description="Start a chat to see your history here" />
```

Add `aria-selected={isActive}` to conversation items. Add `hover:bg-surface-2 transition-colors duration-150` to conversation item buttons. Add FOCUS_RING.

- [ ] **Step 3: Fix command-input.tsx — border-radius**

Replace all `rounded-lg` → `rounded-[var(--radius-lg)]`.

- [ ] **Step 4: Fix markdown-renderer.tsx — border-radius**

Replace `rounded-t` → `rounded-t-[var(--radius-sm)]`, bare `rounded` → `rounded-[var(--radius-sm)]`.

- [ ] **Step 5: Build and verify**

Run: `cd frontend && npx next build 2>&1 | tail -5`
Expected: `✓ Compiled successfully`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/jarvis/
git commit -m "refactor: standardize border-radius tokens and add accessibility in chat components"
```

---

### Task 9: Sweep Batch 7 + final accessibility pass on evidence-panel

**Files:**
- Modify: `frontend/src/components/primitives/evidence-panel.tsx`

- [ ] **Step 1: Fix evidence-panel.tsx — aria labels**

Add `aria-label` to each evidence section (Entities, Memories, Sources):
```typescript
<section aria-label="Entities">
  ...
</section>
```

- [ ] **Step 2: Build and lint**

Run: `cd frontend && npx next build 2>&1 | tail -5 && npm run lint 2>&1 | tail -5`
Expected: Both pass cleanly

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/primitives/evidence-panel.tsx
git commit -m "refactor: add aria labels to evidence panel sections"
```

---

### Task 10: Verification — grep audit + final build

- [ ] **Step 1: Run hardcoded color audit**

Run: `rg "bg-(blue|green|red|amber|violet|purple|gray|orange|yellow|neutral)-" frontend/src/components/ --glob '!*.test.*' -l`

Expected: Zero files. If any remain, fix them.

**Exception:** Chart files (`chart.tsx`, `bar-chart.tsx`, `donut-chart.tsx`) may have data-category colors that map to `--jarvis-chart-*` — these are acceptable.

- [ ] **Step 2: Run border-radius audit**

Run: `rg "rounded-(lg|md|xl)\"" frontend/src/components/ --glob '!*.test.*' -l`

Expected: Zero files (all should use `rounded-[var(--radius-*)]`). `rounded-full` is allowed.

- [ ] **Step 3: Run final build + lint**

Run: `cd frontend && npx next build 2>&1 | tail -5 && npm run lint 2>&1 | tail -5`
Expected: Both pass cleanly

- [ ] **Step 4: Commit any stragglers**

If audits found remaining issues and you fixed them:
```bash
git add -A frontend/src/
git commit -m "refactor: fix remaining hardcoded colors and radius tokens found by audit"
```
