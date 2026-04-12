# Phase 3: A2UI Complex Surface UX Redesigns

**Date:** 2026-04-13
**Branch:** `improve-surface-design-v1`
**Status:** Design approved, pending implementation
**Phase:** 3 of 3 (Foundation → Page Redesigns → **A2UI Complex Surfaces**)

## Context

Phases 1 and 2 established design system consistency (tokens, accessibility, shared primitives) and redesigned page-level layouts (Chat, Knowledge, Search, Integrations). The 4 large A2UI surface components — execution-surface (5,800 lines), insight-surface (3,765 lines), inline-approval (2,731 lines), and step-list (80 lines) — received token consistency fixes in Phase 1 but still have structural UX issues: weak typography hierarchy, flat information architecture, inconsistent spacing, and missing visual affordances.

**Scope:** Structural UX fixes only — typography, spacing, information flow, visual hierarchy, and interaction clarity. No API changes, no new state management patterns, no backend contract changes.

## Goals

1. **Clear information hierarchy** — Users immediately understand what's important on each surface
2. **Consistent typography scale** — 3-tier system (semibold headings, medium labels, normal body) across all surfaces
3. **Consistent spacing rhythm** — Standardized gap scale (4px/8px/12px/16px) across all sections
4. **Visual risk/trust communication** — Color-coded risk levels, trust badges, phase-appropriate styling
5. **Better action affordances** — Equal-weight approve/reject, primary/secondary action distinction, discoverable dismiss

## Non-Goals

- Step grouping/collapsing for long lists
- Elapsed time indicators on executing steps
- Approval timeout display
- Action preview tooltips
- Confirmation dialogs for reject/dismiss
- Phase transition animations
- Backend API changes

---

## Part 1: Execution Surface (`execution-surface.tsx`)

### 1.1 Typography & Spacing

**Changes:**
- Goal title: `text-sm font-medium` → `text-sm font-semibold` (stronger primary element)
- Root spacing: `space-y-3` → `space-y-4` (more breathing room)
- Progress label: `text-[10px]` → `text-[11px]` (slightly more readable)

### 1.2 Results Section Hierarchy

**Current state:** Key Findings, Artifacts, and Suggested Next all use identical `text-xs font-medium text-t-secondary mb-1` — visually flat.

**Changes:**
- Add uppercase section headers: `text-[11px] font-semibold text-t-muted uppercase tracking-wider mb-2`
- Add `border-t border-b-secondary pt-3` dividers between the 3 result sub-sections (not before the first)
- Artifacts: increase pill padding from `px-1.5 py-0.5` to `px-2 py-1` and use `rounded-[var(--radius-md)]`
- Overall results container: keep `bg-j-success-soft` but add `p-4` (from `p-3`) for more internal space

### 1.3 Failure Context Enhancement

**Current state:** Failed phase only shows the failed steps — no context of what succeeded before failure.

**Changes:**
- When `phase === "failed"`, render the full `<StepList>` (showing all steps including completed ones) ABOVE the failure detail box. This gives context of progress-before-failure.
- Failure box header: `text-sm font-semibold text-j-error` (from `text-xs font-medium`) — make failure prominent.
- Failure box: keep `bg-j-error-soft border border-j-error/20` but ensure the failed step text within uses `text-xs text-t-secondary` for the description (not just error color for everything).

### 1.4 Planning Spinner Polish

**Current state:** Planning phase shows a spinner with `py-4` — excessive whitespace.

**Changes:**
- Reduce vertical padding to `py-6` (centered but tighter — note: this is an increase from the current `py-4`, giving a clean centered feel without feeling empty).
- Add secondary text below spinner: `text-[11px] text-t-muted mt-2` with "This usually takes a few seconds".

---

## Part 2: Insight Surface (`insight-surface.tsx`)

### 2.1 Information Flow Reorder

**Current order:** Source badge → Signal summary → Relevance → Goals → Actions → Dismiss

**New order:**
1. **Signal summary** (headline — what this is about)
2. **Source + relevance** (compact metadata line)
3. **Relevance reasoning** (why it matters)
4. **Related goals** (with section label)
5. **Suggested actions + Dismiss** (action row)

**Implementation:** Reorder the JSX sections within the component. No prop changes needed.

### 2.2 Source Badge Simplification

**Current state:** Three elements (emoji + uppercase badge + conditional relevance badge) competing on one line.

**Changes:**
- Move source to a compact metadata line BELOW the signal summary:
  ```
  [icon] [source name] · High relevance
  ```
- Source name: `text-xs text-t-muted` (no uppercase, no badge background)
- Relevance indicator: show if `>= 0.7` (from `0.8`). Display as `text-xs text-j-warning font-medium` inline after source, separated by `·`
- Remove the `bg-j-secondary-soft` badge container from the source — it's now just plain text with icon

### 2.3 Action Button Hierarchy

**Current state:** All suggested action buttons use identical `bg-j-secondary-soft text-j-secondary` styling.

**Changes:**
- First action (index 0): `bg-j-primary text-j-primary-fg font-medium rounded-[var(--radius-md)] px-3 py-1.5` — primary CTA
- Remaining actions (index > 0): `bg-surface-2 text-t-secondary hover:bg-surface-3 rounded-[var(--radius-md)] px-3 py-1.5` — secondary style

### 2.4 Dismiss Inline with Actions

**Current state:** Dismiss is in a separate `border-t` section at the bottom in `text-[10px] text-t-tertiary`.

**Changes:**
- Move dismiss button into the same `flex flex-wrap gap-2` row as the action buttons, pushed to the right with `ml-auto`.
- Style: `text-xs text-t-muted hover:text-t-secondary transition-colors` — no border-t, no separate section.
- Keep the "Dismiss" text label (or "×" icon + "Dismiss").

### 2.5 Related Goals Section Label

**Current state:** Goal pills appear with no heading or context.

**Changes:**
- Add section label above the goals: `text-[11px] text-t-muted font-medium uppercase tracking-wider mb-1.5` with text "Related goals".

---

## Part 3: Inline Approval Card (`inline-approval.tsx`)

### 3.1 Step Description Promotion

**Current state:** Step description is `text-sm text-t-secondary` — secondary visual weight despite being the primary question ("What am I approving?").

**Changes:**
- Promote to `text-sm font-semibold text-t-primary` — now clearly the most important text after the header.
- Add `mb-1` below for spacing before the risk section.

### 3.2 Risk Level Color Coding

**Current state:** Risk assessment box uses neutral `bg-surface-1` regardless of risk level.

**Changes:**
- Add a left-border accent to the risk box based on risk level:
  - If `approval.risk_level` is available: use `riskColor()` from `design-tokens.ts` to derive border color
  - Low: `border-l-[3px] border-l-j-success`
  - Medium: `border-l-[3px] border-l-j-warning`
  - High/Critical: `border-l-[3px] border-l-j-error`
  - Fallback (no risk_level): `border-l-[3px] border-l-j-warning` (default to warning since approval was required)
- Add a risk level badge inline with the "Risk Assessment" header: `<span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-j-warning-soft text-j-warning font-medium uppercase">{risk_level}</span>` (use appropriate semantic color per level).
- Keep `bg-surface-1 rounded-[var(--radius-md)] p-3` for the box itself.

**Note:** Check `ApprovalContext` type in `frontend/src/lib/a2ui-types.ts` — if `risk_level` is not in the type, just use the border-l accent with warning color as default (no type changes needed).

### 3.3 Trust Context as Badge

**Current state:** `"Trust: " + freeform text` as inline `text-xs text-t-tertiary`.

**Changes:**
- Render trust as a structured row:
  ```
  [StatusDot status={trust_level}] [Trust Level Label]
  [trust_context text below in text-xs text-t-tertiary]
  ```
- Import `StatusDot` from `@/components/ui/status-dot` and `TRUST_LEVEL_LABELS` from `@/lib/design-tokens`.
- If `approval.trust_level` is available in the type, use it directly. If not, just improve the text styling: make "Trust:" a `text-xs font-medium text-t-secondary` label and the context value as `text-xs text-t-tertiary`.

### 3.4 Graduation Hint Callout

**Current state:** `text-xs text-j-info/80 italic` at the very bottom — users miss it.

**Changes:**
- Move above the action buttons.
- Wrap in a callout box: `bg-j-info-soft rounded-[var(--radius-md)] px-3 py-2 flex items-start gap-2`.
- Remove italic. Use `text-xs text-j-info`.
- Add an info icon (small SVG, `w-3.5 h-3.5 shrink-0 mt-0.5`).

### 3.5 Action Button Equal Weight

**Current state:** Approve is solid green, Edit is outlined neutral, Reject is text-only red — unequal.

**Changes:**
- **Approve:** `bg-j-success text-white px-4 py-2 text-xs font-medium rounded-[var(--radius-md)]` — solid green (keep strong)
- **Edit:** `bg-surface-2 text-t-secondary border border-b-secondary px-4 py-2 text-xs font-medium rounded-[var(--radius-md)]` — neutral secondary
- **Reject:** `bg-j-error-soft text-j-error border border-j-error/20 px-4 py-2 text-xs font-medium rounded-[var(--radius-md)]` — filled soft red (elevated from text-only)
- All three: same size `px-4 py-2`, same `rounded-[var(--radius-md)]`, equal touch targets.
- Increase gap from `gap-2` to `gap-2.5`.

---

## Part 4: Step List (`step-list.tsx`)

### 4.1 Current Step Highlight

**Current state:** Active step uses `bg-surface-1` — nearly invisible against page background.

**Changes:**
- Active step: `bg-j-primary-soft border-l-2 border-l-j-primary py-2 px-3` (from `bg-surface-1 py-1.5 px-2`)
- Non-active steps: keep `py-1.5 px-2` (no border-l)
- This creates a clear visual "you are here" indicator matching the sidebar nav-item active pattern.

### 4.2 Step Output Expand/Collapse

**Current state:** Output summaries are `line-clamp-2` with no way to see full text.

**Changes:**
- Add `expandedSteps` state: `const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());`
- For steps with `output_summary` longer than 120 characters:
  - Default: show with `line-clamp-2`
  - Show "Show more" button: `text-[11px] text-j-primary cursor-pointer hover:underline mt-0.5`
  - On click: toggle step_id in the set, remove line-clamp
  - When expanded: show "Show less" instead
- For short summaries (< 120 chars): no toggle, show full text.

### 4.3 Step Spacing

**Current state:** `space-y-1` (4px) between steps — dense.

**Changes:**
- Increase to `space-y-1.5` (6px) for better scanability.

### 4.4 Compact Step List Progress Bar

**Current state:** `StepListCompact` shows text counts only ("X/Y steps", "N failed").

**Changes:**
- Add `font-medium` to the failed count text.
- Add a tiny inline progress bar after the text:
  ```
  <div className="w-12 h-1 bg-surface-3 rounded-full overflow-hidden ml-1">
    <div
      className={`h-full rounded-full transition-all duration-300 ${failed > 0 ? "bg-j-error" : "bg-j-success"}`}
      style={{ width: `${(completed / total) * 100}%` }}
    />
  </div>
  ```

---

## Verification

After implementation, verify:

1. **`npx next build`** — no compilation errors
2. **`npm run lint`** — no ESLint warnings
3. **Visual spot-check at each phase:**
   - Execution surface: test all 6 phases (planning, plan_ready, executing, approval_needed, completed, failed)
   - Insight surface: test with/without actions, with/without goals, with dismiss
   - Approval card: verify risk colors, button sizing, graduation hint placement
   - Step list: test with current step, completed steps, failed steps, long output text
4. **Responsive check:** All surfaces render correctly in the workspace canvas grid and in the surface detail modal

## Risk Assessment

- **Medium risk:** execution-surface.tsx (5,800 lines) — changes are structural (JSX reorder, class changes) not logic changes, but the file is large. Search-and-replace carefully.
- **Medium risk:** insight-surface.tsx (3,765 lines) — JSX reorder requires moving entire sections. Test that the component still renders all conditional branches.
- **Low risk:** inline-approval.tsx (2,731 lines) — mostly class string changes + adding StatusDot/Badge imports.
- **Low risk:** step-list.tsx (80 lines) — small file, adding expand/collapse state is straightforward.

## Phasing Summary

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 1 | Token consistency + accessibility + interaction states | Complete |
| Phase 2 | Page-level redesigns — Chat, Knowledge, Search, Integrations | Complete |
| **Phase 3 (this spec)** | A2UI complex surface UX redesigns | Pending |
