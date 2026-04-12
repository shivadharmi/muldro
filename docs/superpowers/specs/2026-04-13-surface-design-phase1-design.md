# Phase 1: Frontend Design System Consistency, Accessibility & Interaction States

**Date:** 2026-04-13
**Branch:** `improve-surface-design-v1`
**Status:** Design approved, pending implementation
**Phase:** 1 of 3 (Foundation → Page Redesigns → A2UI Complex Surfaces)

## Context

A comprehensive UI/UX audit of the Jarvis frontend identified issues across ~50 untouched component files after a v1 design pass that improved the design foundation (globals.css), sidebar, TopBar, workspace home, login page, settings page, and core UI primitives (Button, Card, Modal, Badge, Tabs, Toast, EmptyState, Skeleton).

The v1 pass established a refined token system (HSL-based CSS custom properties, Tailwind v4 theme mapping, consistent border-radius/shadow scales). However, the majority of the codebase still uses hardcoded Tailwind colors, inconsistent border-radius, ad-hoc status color mappings, and missing accessibility attributes.

**This spec covers Phase 1** — the foundation layer that makes every subsequent phase (Phase 2: page redesigns, Phase 3: complex A2UI surface redesigns) automatically consistent.

## Goals

1. **Full token consistency** — Every component uses Jarvis design tokens (`bg-j-*`, `text-j-*`, `bg-surface-*`, `rounded-[var(--radius-*)]`) instead of hardcoded colors and raw Tailwind utilities
2. **Shared color mapping primitives** — Eliminate 8+ duplicated status→color, phase→color, risk→color, trust→color maps across the codebase
3. **Accessibility baseline** — Visible focus indicators, semantic aria attributes, and keyboard navigability on all interactive elements
4. **Consistent loading/empty states** — All data-loading views use shared `EmptyState` and `Skeleton` components instead of ad-hoc implementations

## Non-Goals

- Full page layout redesigns (Phase 2)
- A2UI complex surface UX redesigns — layout, interaction patterns, information architecture (Phase 3)
- Internationalization / i18n setup
- Performance optimization (memoization, virtualization)
- New feature development

## Architecture

### Approach: Pattern-First Extraction + File Sweep

**Step 1 — Extract shared primitives** (3-4 small files)
Create reusable utilities and micro-components that eliminate the repeated patterns found across 50+ files. These are consumed during the sweep.

**Step 2 — File sweep** (7 batches, ~50 files)
Systematic pass through every untouched component applying 5 mechanical rules. Accessibility fixes happen inline during this sweep (not a separate pass).

---

## Part 1: Shared Primitives

### 1.1 `lib/design-tokens.ts` — Semantic color mapping utility

Canonical source for all domain-value → design-token mappings. Replaces ~8 duplicated `const` maps scattered across components.

**Exports:**

```typescript
/** Maps execution/task status to a Tailwind bg class */
export function statusColor(status: string): string
// pending → "bg-t-muted"
// running/executing/in_progress → "bg-j-info"
// completed/ok/approved → "bg-j-success"
// failed/rejected/error → "bg-j-error"
// awaiting_approval/pending_approval → "bg-j-warning"
// cancelled/paused → "bg-t-muted"
// proposal → "bg-j-secondary"
// unknown → "bg-t-muted"

/** Maps execution/task status to a Tailwind text class */
export function statusTextColor(status: string): string
// Same mapping as statusColor but returns text-j-* classes

/** Maps risk level to a Tailwind bg class */
export function riskColor(risk: string): string
// low → "bg-j-success"
// medium → "bg-j-warning"
// high/critical → "bg-j-error"
// unknown → "bg-t-muted"

/** Maps execution phase to a Tailwind bg class + optional pulse */
export function phaseColor(phase: string): { bg: string; pulse: boolean }
// planning → { bg: "bg-j-info", pulse: true }
// plan_ready → { bg: "bg-j-info", pulse: false }
// executing → { bg: "bg-j-info", pulse: true }
// approval_needed → { bg: "bg-j-warning", pulse: true }
// completed → { bg: "bg-j-success", pulse: false }
// failed → { bg: "bg-j-error", pulse: false }
// partial → { bg: "bg-j-warning", pulse: false }
// proposal → { bg: "bg-j-secondary", pulse: true }
// unknown → { bg: "bg-t-muted", pulse: false }

/** Maps trust level to a Tailwind bg class */
export function trustLevelColor(level: string): string
// first_use → "bg-t-muted"
// learning → "bg-j-info"
// trusted → "bg-j-success"
// autonomous → "bg-j-secondary"
// blocked → "bg-j-error"
// unknown → "bg-t-muted"

/** Maps surface kind to badge styling (bg + text classes) */
export function kindStyle(kind: string): { bg: string; text: string }
// plan → { bg: "bg-j-info-soft", text: "text-j-info" }
// approval → { bg: "bg-j-warning-soft", text: "text-j-warning" }
// briefing → { bg: "bg-j-success-soft", text: "text-j-success" }
// alert → { bg: "bg-j-error-soft", text: "text-j-error" }
// proactive_insight → { bg: "bg-j-secondary-soft", text: "text-j-secondary" }
// recommendation → { bg: "bg-j-secondary-soft", text: "text-j-secondary" }
// execution → { bg: "bg-j-info-soft", text: "text-j-info" }
// summary/default → { bg: "bg-surface-3", text: "text-t-secondary" }

/** Maps priority to badge styling (bg + text classes) */
export function priorityStyle(priority: string): { bg: string; text: string }
// low → { bg: "bg-surface-3", text: "text-t-secondary" }
// medium → { bg: "bg-j-info-soft", text: "text-j-info" }
// high → { bg: "bg-j-warning-soft", text: "text-j-warning" }
// critical → { bg: "bg-j-error-soft", text: "text-j-error" }
// unknown → { bg: "bg-surface-3", text: "text-t-secondary" }

/** Human-readable labels for trust levels */
export const TRUST_LEVEL_LABELS: Record<string, string>
// first_use → "First Use", learning → "Learning", trusted → "Trusted",
// autonomous → "Autonomous", blocked → "Blocked"

/** Human-readable labels for surface kinds */
export const KIND_LABELS: Record<string, string>
// plan → "Plan", approval → "Approval", briefing → "Briefing", etc.
```

**Design decisions:**
- Functions (not const maps) so we can handle unknown values with a fallback instead of `undefined`
- Return class strings (not HSL values) so consumers just spread into `className`
- Separate `statusColor` (bg) and `statusTextColor` (text) because some contexts need background dots, others need colored text
- `phaseColor` returns `{ bg, pulse }` because phases often pair with animation

### 1.2 `components/ui/status-dot.tsx` — Micro status indicator

```typescript
interface StatusDotProps {
  status?: string;     // Uses statusColor() to derive color
  phase?: string;      // Uses phaseColor() to derive color + pulse
  color?: string;      // Direct Tailwind class override
  size?: "sm" | "md";  // sm=1.5, md=2 (w/h units)
}
```

Replaces ~15 inline `<span className="w-2 h-2 rounded-full bg-...">` instances across the codebase. Renders a single `<span>` with the appropriate color and optional `animate-pulse-live` class.

**Priority:** `phase` > `status` > `color` (first defined wins).

### 1.3 `lib/focus-ring.ts` — Shared focus utility constant

```typescript
/** Standard focus ring classes for interactive elements */
export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface-0";
```

Already used in `components/ui/button.tsx` as an inline string. Extracting to a shared constant so all ~20 interactive components import the same value. If the ring style changes, one edit propagates everywhere.

### 1.4 Existing Primitives (no changes needed)

`components/ui/empty-state.tsx` and `components/ui/skeleton.tsx` were already improved in v1. The sweep phase uses them as-is — no modifications to these files.

---

## Part 2: Token Consistency Sweep

### Sweep Rules

Every file in scope gets checked against these 5 rules:

| ID | Rule | Before | After |
|----|------|--------|-------|
| R1 | Hardcoded Tailwind colors → Jarvis tokens | `bg-blue-500`, `text-green-400`, `bg-gray-500/20` | `bg-j-info`, `text-j-success`, `bg-surface-3` |
| R2 | Inline HSL values → CSS custom properties | `color: hsl(193 100% 62%)` | `color: var(--jarvis-primary)` |
| R3 | Border-radius → token variables | `rounded-lg`, `rounded-md`, `rounded` | `rounded-[var(--radius-lg)]`, `rounded-[var(--radius-md)]`, `rounded-[var(--radius-sm)]` |
| R4 | Ad-hoc color maps → `design-tokens.ts` utility | `const phaseDotColor = { planning: "bg-blue-400 animate-pulse", ... }` | `import { phaseColor } from "@/lib/design-tokens"` |
| R5 | Ad-hoc empty/loading states → shared components | `<p className="text-sm text-t-tertiary">No data...</p>` | `<EmptyState title="No data" />` |

### Color Mapping Reference

For rule R1, this is the canonical mapping from hardcoded colors to tokens:

| Hardcoded | Token |
|-----------|-------|
| `bg-blue-400`, `bg-blue-500`, `text-blue-400`, `bg-blue-500/15`, `bg-blue-500/20` | `bg-j-info`, `text-j-info`, `bg-j-info-soft` |
| `bg-green-400`, `bg-green-500`, `text-green-400`, `bg-green-500/5`, `bg-green-500/10` | `bg-j-success`, `text-j-success`, `bg-j-success-soft` |
| `bg-amber-400`, `bg-amber-500`, `text-amber-400`, `bg-amber-500/5`, `bg-amber-500/20` | `bg-j-warning`, `text-j-warning`, `bg-j-warning-soft` |
| `bg-red-400`, `bg-red-500`, `text-red-400`, `bg-red-500/5`, `bg-red-500/10` | `bg-j-error`, `text-j-error`, `bg-j-error-soft` |
| `bg-violet-400`, `bg-violet-500`, `text-violet-400`, `bg-violet-500/20` | `bg-j-secondary`, `text-j-secondary`, `bg-j-secondary-soft` |
| `bg-purple-400`, `bg-purple-500`, `text-purple-400` | `bg-j-secondary`, `text-j-secondary` |
| `bg-gray-400`, `bg-gray-500`, `text-gray-400`, `bg-gray-500/20` | `bg-t-muted`, `text-t-muted`, `bg-surface-3` |
| `bg-neutral-400`, `text-neutral-400` | `bg-t-muted`, `text-t-muted` |
| `bg-orange-400`, `text-orange-400` | `bg-j-warning`, `text-j-warning` |
| `bg-yellow-400`, `text-yellow-400` | `bg-j-warning`, `text-j-warning` |
| `bg-green-600` | `bg-j-success` |
| `text-white` (on colored bg) | `text-j-primary-fg` (on primary bg), or keep `text-white` on semantic bgs |

### Border-Radius Reference

For rule R3:

| Hardcoded | Token |
|-----------|-------|
| `rounded` (4px default) | `rounded-[var(--radius-sm)]` (6px) |
| `rounded-md` | `rounded-[var(--radius-md)]` (8px) |
| `rounded-lg` | `rounded-[var(--radius-lg)]` (12px) |
| `rounded-xl` | `rounded-[var(--radius-xl)]` (16px) |
| `rounded-full` | Keep as-is (pills, dots) |

### Batch Inventory

#### Batch 1 — A2UI Small Components (~15 files, low risk)

| File | Rules | Notes |
|------|-------|-------|
| `a2ui/components/badge.tsx` | R1 | Color variants may use hardcoded colors |
| `a2ui/components/alert.tsx` | R1, R3 | Semantic alert colors |
| `a2ui/components/status-indicator.tsx` | R1, R4 | Status color map → design-tokens |
| `a2ui/components/button.tsx` | R1, R3 | Check variant colors use tokens, add FOCUS_RING |
| `a2ui/components/text-field.tsx` | R1, R3 | Focus ring, border tokens |
| `a2ui/components/select.tsx` | R1, R3 | Focus ring, border tokens |
| `a2ui/components/toggle.tsx` | R1 | Active/inactive colors |
| `a2ui/components/progress.tsx` | R1 | Bar fill color |
| `a2ui/components/metric.tsx` | R1 | Variant colors (success/warning/danger) |
| `a2ui/components/timeline.tsx` | R1, R3 | Event dot colors, line colors |
| `a2ui/components/tabs.tsx` | R1, R3 | Active tab color |
| `a2ui/components/card.tsx` | R3 | Border-radius |
| `a2ui/components/divider.tsx` | R1 | Border color |
| `a2ui/components/modal.tsx` | R1, R3 | Backdrop, radius |
| `a2ui/components/form.tsx` | R3 | Input styling consistency |

#### Batch 2 — A2UI Data Components (~7 files, medium risk)

| File | Rules | Notes |
|------|-------|-------|
| `a2ui/components/a2ui-table.tsx` | R1, R3 | 2,157 lines. Status cell colors, header styling, sort indicators |
| `a2ui/components/data-grid.tsx` | R1, R3 | Grid cell colors |
| `a2ui/components/chart.tsx` | R1 | Check if chart colors use CSS vars |
| `a2ui/components/kanban-board.tsx` | R1, R3 | Column header colors, card borders |
| `a2ui/components/code-block.tsx` | R1, R3 | Syntax highlight bg, copy button |
| `a2ui/components/entity-card.tsx` | R1, R3, R4 | Entity type color mapping |
| `a2ui/components/memory-card.tsx` | R1, R3, R4 | Memory type color mapping |

#### Batch 3 — A2UI Complex Surfaces (5 files, high risk)

| File | Lines | Rules | Notes |
|------|-------|-------|-------|
| `execution-surface.tsx` | 5,799 | R1, R2, R4 | Phase color map (line 25-32), result status colors (line 72-111), step status indicators throughout. Replace with `phaseColor()`, `statusColor()` |
| `insight-surface.tsx` | 3,765 | R1, R4 | Category color map (line 54-86), relevance indicators, action button variants. Replace with `kindStyle()` |
| `inline-approval.tsx` | 2,731 | R1, R4 | Risk color map (line 27-79), trust level colors, approve/reject button colors. Replace with `riskColor()`, `trustLevelColor()` |
| `step-list.tsx` | ~200 | R1, R4 | Step status icons and colors |
| `execution-trace.tsx` | ~300 | R1, R4 | Trace step colors |

**Risk mitigation for Batch 3:** These are functionally critical A2UI surfaces. Changes are limited to color token swaps and color-map imports — no layout, logic, or interaction changes. Each file gets a targeted search-and-replace, not a rewrite.

#### Batch 4 — Knowledge Page Components (~13 files, medium risk)

| File | Rules | Notes |
|------|-------|-------|
| `graph-view.tsx` | R1, R2, R3 | Node/edge colors use raw HSL. Convert to CSS custom properties for theme support |
| `graph-detail-panel.tsx` | R1, R2, R3 | Inline `color`/`backgroundColor` computed from HSL → CSS vars |
| `graph-filters.tsx` | R1, R3 | Filter chip active/inactive colors |
| `graph-context-menu.tsx` | R1, R3 | Menu item hover states |
| `memories-view.tsx` | R1, R3, R5 | Filter chip colors, empty state replacement |
| `memory-row.tsx` | R1, R3, R4 | Confidence bar color, entity chip styling, type badge |
| `memory-detail-panel.tsx` | R1, R3 | Detail field styling |
| `stats-view.tsx` | R1, R2, R5 | Inline `text-[hsl(...)]` → tokens, loading skeleton replacement |
| `stat-card.tsx` | R1, R3 | Metric colors |
| `knowledge-search.tsx` | R1, R3 | Input styling |
| `bar-chart.tsx` | R1 | Bar fill colors |
| `donut-chart.tsx` | R1 | Segment colors |
| `community-card.tsx` | R1, R3 | Card styling |

#### Batch 5 — Search, Integrations, Primitives (~8 files, low-medium risk)

| File | Rules | Notes |
|------|-------|-------|
| `search/search-bar.tsx` | R1, R3 | `rounded-lg` → token, add focus ring to input and select |
| `search/search-results.tsx` | R1 | Result type colors |
| `feature/search/result-group-list.tsx` | R1, R4, R5 | Hardcoded `bg-blue-500/15`, `text-blue-400`, type color map → design-tokens. Empty state. |
| `feature/search/result-detail-pane.tsx` | R1, R5 | Empty state replacement |
| `feature/integrations/integration-detail-panel.tsx` | R1, R5 | Badge styling, empty state replacement |
| `integrations/page.tsx` (AdvancedMCPSection only) | R1 | `bg-green-400`, `bg-yellow-400`, `bg-red-400`, `text-green-400` → tokens |
| `primitives/evidence-panel.tsx` | R1, R3 | Section styling |
| `primitives/live-activity-feed.tsx` | R1, R4 | Event dot colors → statusColor() |

#### Batch 6 — Chat/Jarvis Components (~6 files, medium risk)

| File | Rules | Notes |
|------|-------|-------|
| `jarvis/chat-panel.tsx` | R1, R3 | Message bubble colors, streaming indicator |
| `jarvis/session-sidebar.tsx` | R1, R3, R5 | Conversation item styling, empty state replacement |
| `jarvis/command-input.tsx` | R1, R3 | Mention styling, input border |
| `jarvis/markdown-renderer.tsx` | R1, R3 | Code block bg, link colors, heading styles |
| `feature/command/command-workspace.tsx` | R1, R3 | Panel border/bg tokens |
| `feature/command/command-composer.tsx` | R1, R3 | Composer styling |

#### Batch 7 — A2UI Renderer (1 file, low risk)

| File | Rules | Notes |
|------|-------|-------|
| `a2ui/renderer.tsx` | R3 | Wrapper div border-radius consistency |

---

## Part 3: Accessibility & Interaction States

These fixes happen **inline during the sweep** — when a file is touched for token consistency, its accessibility gaps are fixed in the same edit.

### 3.1 Focus States

**Requirement:** Every interactive element must have a visible focus indicator using the shared `FOCUS_RING` constant from `lib/focus-ring.ts`.

**Files requiring focus ring addition:**

| File | Element(s) |
|------|------------|
| `search/search-bar.tsx` | `<input>` and `<select>` |
| `integrations/page.tsx` | Provider card action buttons |
| `memories-view.tsx` | Sort and filter buttons |
| `result-group-list.tsx` | Result item buttons |
| `memory-row.tsx` | Clickable row element |
| `session-sidebar.tsx` | Conversation item buttons |
| `a2ui/components/button.tsx` | A2UI button variants |
| `a2ui/components/text-field.tsx` | Text input |
| `a2ui/components/select.tsx` | Select dropdown |
| `a2ui/components/toggle.tsx` | Toggle switch |

### 3.2 Aria Attributes

**Specific fixes (one per file, applied during sweep):**

| File | Fix |
|------|-----|
| `session-sidebar.tsx` | Add `aria-selected={isActive}` to conversation item |
| `integrations/page.tsx` (AdvancedMCPSection) | Add `aria-expanded={expanded}` to toggle button |
| `live-activity-feed.tsx` | Add `role="log"` and `aria-live="polite"` to feed container |
| `evidence-panel.tsx` | Add `aria-label` to each evidence section |
| `graph-context-menu.tsx` | Add `role="menu"` to container, `role="menuitem"` to items |
| `memory-row.tsx` | Add `aria-label={memory.content.slice(0, 60)}` to clickable row |
| `result-group-list.tsx` | Add `aria-label` to result type group headings |

### 3.3 Empty & Loading State Replacements

**Replace ad-hoc implementations with shared components:**

| File | Current implementation | Replacement |
|------|----------------------|-------------|
| `session-sidebar.tsx` | `<p>No conversations yet</p>` | `<EmptyState title="No conversations yet" description="Start a chat to see your history here" />` |
| `result-detail-pane.tsx` | `<p>Select a result to see details</p>` | `<EmptyState title="Select a result" description="Choose an item from the list to view details" />` |
| `integration-detail-panel.tsx` | Plain text empty state | `<EmptyState title="Select an integration" description="Choose a provider to manage its settings" />` |
| `memories-view.tsx` | Custom empty UI with SVG | `<EmptyState title="No memories found" description="Memories will appear as Jarvis learns from interactions" icon={...} />` |
| `graph-view.tsx` | Custom `text-sm text-t-tertiary` div | `<EmptyState title="No graph data" description="Connect sources and interact with Jarvis to build the knowledge graph" />` |
| `integrations/page.tsx` | `<p>Loading providers...</p>` | `<SkeletonGrid count={6} />` |
| `stats-view.tsx` | Custom `animate-pulse` divs | `<SkeletonGrid count={4} />` for cards, `<SkeletonTable rows={5} />` for tables |

### 3.4 Hover & Active State Consistency

**Requirement:** Interactive cards and rows use consistent hover treatment:

| Pattern | Classes |
|---------|---------|
| Clickable card | `surface-card cursor-pointer` (CSS class from globals.css — adds hover shadow + translateY) |
| Clickable list row | `hover:bg-surface-2 transition-colors duration-150 cursor-pointer` |
| Clickable group heading | `hover:bg-surface-2 transition-colors duration-150 cursor-pointer` |

**Files:**
- `memory-row.tsx` — clickable row → add `hover:bg-surface-2 transition-colors duration-150`
- `result-group-list.tsx` — result buttons → add consistent hover
- `session-sidebar.tsx` — conversation items → add `hover:bg-surface-2`
- `integration-detail-panel.tsx` — capability rows → add hover treatment

---

## Phasing Summary

| Phase | Scope | Spec |
|-------|-------|------|
| **Phase 1 (this spec)** | Token consistency + accessibility + interaction states | `2026-04-13-surface-design-phase1-design.md` |
| **Phase 2 (future)** | Chat experience redesign, knowledge page redesign, integrations page redesign, search page redesign | TBD |
| **Phase 3 (future)** | A2UI complex surface UX redesigns (execution-surface, insight-surface, inline-approval layout/interaction/information architecture) | TBD |

## Verification

After implementation, verify:

1. **`npx next build`** — no compilation errors
2. **`npm run lint`** — no ESLint warnings
3. **Grep audit** — `rg "bg-(blue|green|red|amber|violet|purple|gray|orange|yellow|neutral)-" frontend/src/components/` returns zero matches (excluding chart color arrays that intentionally use distinct colors for data visualization)
4. **Grep audit** — `rg "rounded-(lg|md|xl)(?!\[)" frontend/src/components/` returns zero matches (all should use token variables), excluding `rounded-full`
5. **Visual spot-check** — workspace, chat, knowledge, integrations, and settings pages render correctly in both light and dark themes
6. **Focus check** — tab through every page, confirm visible focus rings on all interactive elements

## Exclusions

The following files were already updated in the v1 design pass and are **out of scope** for this spec:
- `app/globals.css`, `app/layout.tsx`, `app/app-shell.tsx`, `app/page.tsx`, `app/chat/page.tsx`, `app/login/page.tsx`, `app/settings/page.tsx`
- `components/dashboard/greeting-hero.tsx`
- `components/layout/nav-item.tsx`, `page-header.tsx`, `sidebar.tsx`
- `components/shell/activity-strip.tsx`, `command-launcher.tsx`, `context-sidebar.tsx`, `top-bar.tsx`
- `components/ui/button.tsx`, `card.tsx`, `empty-state.tsx`, `modal.tsx`, `tabs.tsx`, `toast.tsx`, `badge.tsx`, `skeleton.tsx`, `table.tsx`, `time-ago.tsx`
- `components/workspace/surface-card.tsx`, `surface-detail-modal.tsx`, `workspace-canvas.tsx`, `workspace-status-bar.tsx`

**Chart color exception:** `chart.tsx`, `bar-chart.tsx`, and `donut-chart.tsx` may use distinct Tailwind colors for data visualization segments (e.g., 5 categorical colors in a pie chart). These are acceptable if they map to the `--jarvis-chart-*` CSS custom properties. Only replace colors that represent semantic status/state (not data categories).

**`graph-view.tsx` HSL exception:** The force-graph library (`react-force-graph-2d`) requires raw color strings for node/edge rendering (not CSS classes). These inline HSL values should reference CSS custom properties via `getComputedStyle()` at initialization, not Tailwind classes. This is the only file where R2 requires a different approach.

## Risk Assessment

- **Low risk:** Batches 1, 5, 7 — small files, mechanical changes
- **Medium risk:** Batches 2, 4, 6 — larger files, more color instances to find
- **High risk:** Batch 3 — 12,000+ lines across 3 critical A2UI surfaces. Mitigated by limiting changes to color tokens only (no layout/logic changes) and verifying surface rendering after each file
