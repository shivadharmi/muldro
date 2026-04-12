# Phase 2: Page-Level Redesigns — Chat, Knowledge, Search, Integrations

**Date:** 2026-04-13
**Branch:** `improve-surface-design-v1`
**Status:** Design approved, pending implementation
**Phase:** 2 of 3 (Foundation → **Page Redesigns** → A2UI Complex Surfaces)

## Context

Phase 1 established design system consistency: all ~50 component files now use Jarvis design tokens, shared color mapping primitives (`design-tokens.ts`, `StatusDot`, `FOCUS_RING`), and baseline accessibility attributes. The visual foundation (globals.css, sidebar, TopBar, workspace home, login, settings) was refined in the v1 design pass.

Phase 2 addresses **page-level layout, structure, and UX issues** identified in the audit. The 4 remaining pages (Chat, Knowledge, Search, Integrations) have consistent tokens but still suffer from weak typography hierarchy, poor mobile responsiveness, cramped information density, and missing loading/error states.

**Approach:** Two tiers of effort:
- **2A (Deep redesign):** Chat + Knowledge — highest-traffic pages, most structural issues
- **2B (Light polish):** Search + Integrations — fewer issues, targeted fixes

## Goals

1. **Responsive layouts** — Mobile/tablet users can access all content via drawer/overlay patterns
2. **Typography hierarchy** — Clear visual levels (heading → section title → body → caption) on every page
3. **Information density** — Declutter cramped metadata, consolidate redundant controls
4. **Loading/error states** — Skeleton loaders and error boundaries replace bare text placeholders
5. **Interaction clarity** — Primary actions visually distinct, secondary actions discoverable but de-emphasized

## Non-Goals

- A2UI complex surface UX redesigns (Phase 3)
- New feature development (search syntax, advanced filters)
- Backend API changes
- Internationalization

---

## Part 1: Chat Page Redesign (2A — Deep)

### 1.1 Session Sidebar — Responsive Drawer + Declutter

**Problem:** Sidebar is `hidden lg:block` — mobile/tablet users cannot switch conversations. Conversation items show dense metadata (cost to 4 decimal places, message count, time) all in `text-[10px]`.

**Files:**
- Modify: `frontend/src/components/jarvis/session-sidebar.tsx`
- Modify: `frontend/src/app/chat/page.tsx`
- Modify: `frontend/src/components/feature/command/command-workspace.tsx`

**Changes:**
1. **Mobile drawer:** Add a slide-over drawer triggered by a sessions icon button in the command header bar. Implementation:
   - Add `sidebarOpen` / `setSidebarOpen` state to the chat page (or lift into commandStore if needed cross-component).
   - Render `SessionSidebar` in a `fixed inset-y-0 left-0 z-40 transform transition-transform` wrapper (same pattern as the app-shell mobile sidebar).
   - Add backdrop (`fixed inset-0 bg-black/50 backdrop-blur-sm z-30`) when open.
   - Add a sessions toggle button in the command header bar (left side, before mode selector). Icon: a sidebar/list icon. Only visible below `lg:` breakpoint.
   - Keep the existing `hidden lg:block` desktop sidebar — the drawer is mobile-only.

2. **Conversation item declutter:**
   - Primary line: conversation title (truncated) + relative time.
   - Remove `total_cost_usd` and `message_count` from the default view.
   - Show cost and message count in a tooltip on hover (using `title` attribute or a custom tooltip).
   - Delete button: replace hover-only `×` with a persistent `trash` icon button — `text-t-muted hover:text-j-error transition-colors`, always visible but subtle.

### 1.2 Connection Status — Banner Pattern

**Problem:** Connection status is `text-[11px]` — invisible when offline.

**Files:**
- Modify: `frontend/src/app/chat/page.tsx` (the command header section)

**Changes:**
1. When `connected === false`, render a warning bar below the mode selector:
   ```
   bg-j-warning-soft border-b border-j-warning/20 px-4 py-2 text-xs text-j-warning
   "Connection lost — reconnecting..."
   ```
   With a subtle pulse dot (`StatusDot` with `color="bg-j-warning"` and manual pulse).
2. Auto-dismiss when `connected` flips back to `true`.
3. Keep the compact dot + "Connected" text in the header when connected (no change to happy path). Increase from `text-[11px]` to `text-xs`.

### 1.3 Mode Selector — Segmented Control

**Problem:** Ask/Plan/Execute pills look like filter chips, not a primary mode selector.

**Files:**
- Modify: `frontend/src/app/chat/page.tsx` (the mode buttons section)

**Changes:**
1. Wrap the 3 mode pills in a segmented control container:
   ```
   bg-surface-2 rounded-[var(--radius-lg)] p-1 inline-flex gap-0.5
   ```
2. Increase individual pill sizing: `px-3.5 py-1.5 text-[13px]` (from `px-3 py-1.5 text-xs`).
3. Active pill: `bg-j-primary text-j-primary-fg font-medium rounded-[var(--radius-md)]`.
4. Inactive pill: `text-t-muted hover:text-t-secondary rounded-[var(--radius-md)]`.

### 1.4 Surfaces Rail Polish

**Problem:** Flat list with plain "Surfaces (N)" label, no visual separation from chat.

**Files:**
- Modify: `frontend/src/app/chat/page.tsx` (the surfaces section)

**Changes:**
1. Section header: replace plain text with `text-[13px] font-semibold text-t-secondary` + count in a `<Badge variant="default">`.
2. Add `border-l border-b-secondary` to the surfaces rail container for visual separation.
3. Add `p-4` padding (from `p-3`) for breathing room.

---

## Part 2: Knowledge Page Redesign (2A — Deep)

### 2.1 Top Bar — Responsive Stacking

**Problem:** PageHeader + KnowledgeSearch + Stats in one row — wraps awkwardly at tablet widths. Stats abbreviate to `ent`/`rel` on mobile.

**Files:**
- Modify: `frontend/src/app/knowledge/page.tsx`

**Changes:**
1. Desktop (`sm:+`): Keep horizontal layout but move stats into badge-style indicators next to the title (not after search).
2. Mobile (below `sm:`): Stack vertically — title row, then search (full width), then tabs. Hide entity/relationship counts (available in Stats tab).
3. Remove the `<span className="sm:hidden">` abbreviated mobile text. Replace with `hidden sm:inline` on the stats span — just hide entirely on mobile.

### 2.2 Memories View — Filter Consolidation + Skeletons

**Problem:** Two rows of controls (type filter chips + sort pills) are cramped. Sort pills look like filter chips. Loading shows bare text.

**Files:**
- Modify: `frontend/src/components/knowledge/memories-view.tsx`

**Changes:**
1. **Sort control:** Replace the row of sort pill buttons with a single dropdown button: `Sort: Recent ▾`. Implementation: a `<button>` that toggles a local `sortOpen` boolean state. When open, render an absolutely-positioned menu (`absolute top-full mt-1 right-0 z-20 bg-surface-1 border border-b-secondary rounded-[var(--radius-lg)] shadow-[var(--shadow-md)] py-1`) with clickable options (`px-3 py-1.5 text-xs hover:bg-surface-2`). Close on click-outside via a backdrop div or `useEffect` with document click listener. Frees up one full row of vertical space.
2. **Loading state:** Replace "Loading memories..." text with 5 skeleton rows. Each skeleton row: `<div className="flex gap-3 py-3"><Skeleton className="h-4 w-24" /><Skeleton className="h-4 flex-1" /><Skeleton className="h-4 w-16" /></div>`.
3. **Empty state CTA:** Add an action button to the EmptyState: `<EmptyState ... action={<Link href="/chat">Start a conversation</Link>} />`.

### 2.3 Graph Detail Panel — Empty State + Responsive

**Problem:** Panel takes right-side space when nothing selected. Hidden on mobile with no fallback.

**Files:**
- Modify: `frontend/src/components/knowledge/graph-detail-panel.tsx`
- Modify: `frontend/src/app/knowledge/page.tsx` (graph tab layout)

**Changes:**
1. **Empty state:** When no entity is selected, show `<EmptyState title="Select an entity" description="Click a node in the graph to see details" />` inside the panel.
2. **Mobile pattern:** Below `lg:` breakpoint, hide the persistent right panel. When a node is clicked, show entity details in a bottom sheet overlay:
   - Fixed positioning: `fixed inset-x-0 bottom-0 z-40 max-h-[70vh] rounded-t-[var(--radius-xl)]`
   - Backdrop: same pattern as mobile drawer
   - Close button at top-right of the sheet
   - Content: same `GraphDetailPanel` content, scrollable
3. **Load more:** When the related memories list is capped at 5, show a "Load more" text button below.

### 2.4 Stats View — Skeleton Upgrade

**Problem:** Loading uses generic `animate-pulse` divs.

**Files:**
- Modify: `frontend/src/components/knowledge/stats-view.tsx`

**Changes:**
1. Replace custom loading skeleton divs with `<SkeletonGrid count={4} />` for stat cards.
2. For chart loading sections, use `<Skeleton className="h-48 w-full" />` as chart placeholder.
3. Imports for `SkeletonGrid` and `Skeleton` were already added in Phase 1 — just wire them into the loading conditional branches.

---

## Part 3: Search Page Polish (2B — Light)

### 3.1 Search Bar — Scope Pill Selector

**Problem:** Scope filter uses native `<select>` — browser-default, doesn't match design system.

**Files:**
- Modify: `frontend/src/components/search/search-bar.tsx`

**Changes:**
1. Replace the `<select>` with a pill-style segmented control (same pattern as chat mode selector):
   ```
   Container: bg-surface-2 rounded-[var(--radius-lg)] p-1 inline-flex gap-0.5
   Active pill: bg-j-primary text-j-primary-fg text-xs font-medium rounded-[var(--radius-md)] px-2.5 py-1
   Inactive pill: text-t-muted hover:text-t-secondary text-xs rounded-[var(--radius-md)] px-2.5 py-1
   ```
2. Options: All, Memories, Entities, Events.
3. Layout: pills left of the input on desktop (`flex items-center gap-3`), stacked above on mobile (`flex-col sm:flex-row`).

### 3.2 Result Loading + Error States

**Problem:** Bare "Searching..." text while loading. No error handling.

**Files:**
- Modify: `frontend/src/app/search/page.tsx`

**Changes:**
1. Replace `<p>Searching...</p>` with 5 `<SkeletonCard />` rows.
2. Add error handling: wrap `searchAll` query with `onError` or check `isError` from useQuery. Show `<EmptyState title="Search failed" description="Something went wrong. Please try again." />`.
3. Upgrade result count: `text-[13px] text-t-secondary font-medium` (from `text-xs text-t-tertiary`).

### 3.3 Detail Pane — Mobile Overlay + Badge Polish

**Problem:** Detail pane hidden below `lg:` with no fallback. Source DB labels are raw technical names.

**Files:**
- Modify: `frontend/src/components/feature/search/result-detail-pane.tsx`
- Modify: `frontend/src/app/search/page.tsx`

**Changes:**
1. **Mobile overlay:** When a result is selected below `lg:` breakpoint, show the detail pane as a fixed bottom sheet:
   - `fixed inset-x-0 bottom-0 z-40 max-h-[80vh] rounded-t-[var(--radius-xl)] bg-surface-1 border-t border-b-secondary`
   - Backdrop + close button
   - Same `ResultDetailPane` content, scrollable
   - Add `onClose` prop to `ResultDetailPane` for the mobile overlay close button.
2. **Source DB labels:** Replace raw badge text with human-readable labels:
   - `qdrant` → "Vector"
   - `postgres_fts` → "Keyword"
   - `neo4j` → "Graph"
   Use `<Badge>` component for consistency.

### 3.4 Empty State Enhancement

**Problem:** No guidance before the user searches.

**Files:**
- Modify: `frontend/src/app/search/page.tsx`

**Changes:**
1. Before first search (`!data && !isLoading`), show:
   ```typescript
   <EmptyState
     title="Search across everything"
     description="Find memories, entities, events, and documents"
     icon={<SearchIcon />}
   />
   ```
   where `SearchIcon` is an inline SVG (magnifying glass, same as sidebar icon).

---

## Part 4: Integrations Page Polish (2B — Light)

### 4.1 Flash Message → Toast

**Problem:** Flash banner at top of page after OAuth — may be missed, has a weak "dismiss" link.

**Files:**
- Modify: `frontend/src/app/integrations/page.tsx`

**Changes:**
1. Replace `flash`/`setFlash` state with `addToast()` calls:
   - Success: `addToast(\`${provider} connected successfully\`, "success")`
   - Error: `addToast(\`Error: ${error}\`, "error")`
2. Remove the flash banner JSX entirely.
3. Remove `flash` and `setFlash` state variables.

### 4.2 Test Result Badge

**Problem:** Test result appears as bare text at the end of the button row.

**Files:**
- Modify: `frontend/src/app/integrations/page.tsx`

**Changes:**
1. Replace the bare `<span>` test result with `<Badge variant={result === "healthy" ? "success" : "error"}>{result}</Badge>`.
2. Auto-clear the test result after 5 seconds using `setTimeout` in the `handleTest` function.

### 4.3 Provider Card Button Sizing

**Problem:** 3 action buttons (Test, Reauthorize, Disconnect) can wrap to 2 rows on narrow cards.

**Files:**
- Modify: `frontend/src/app/integrations/page.tsx`

**Changes:**
1. Reduce button padding: `px-2.5 py-1` (from `px-3 py-1.5`). Keeps all 3 on one row at 3-column grid.
2. Standardize border-radius on all buttons: `rounded-[var(--radius-md)]` (from `rounded-md`).

### 4.4 Scope Badge Readability

**Problem:** Scope badges are `text-[10px]` with long truncated strings.

**Files:**
- Modify: `frontend/src/app/integrations/page.tsx`

**Changes:**
1. Increase scope badge font size to `text-[11px]`.
2. Limit visible scopes to 2 (from 3). Show `+N more` as remainder.
3. Add `title` attribute with full scope list for tooltip.

---

## Shared Patterns Introduced

### Mobile Drawer/Bottom Sheet Pattern

Used by: Chat session sidebar (drawer), Knowledge graph detail (bottom sheet), Search result detail (bottom sheet).

**Pattern:**
```
Backdrop: fixed inset-0 bg-black/50 backdrop-blur-sm z-30
Panel:    fixed [position] z-40 bg-surface-1 border border-b-secondary shadow-[var(--shadow-lg)]
          rounded-[appropriate corners] transform transition-transform duration-200
Content:  overflow-y-auto, close button at top-right
```

Implementation is inline in each page — no shared component needed since the positioning differs (left drawer vs bottom sheet). If a third use case appears in Phase 3, extract a shared `<Drawer>` component.

### Segmented Control Pattern

Used by: Chat mode selector, Search scope selector.

**Pattern:**
```
Container: bg-surface-2 rounded-[var(--radius-lg)] p-1 inline-flex gap-0.5
Active:    bg-j-primary text-j-primary-fg font-medium rounded-[var(--radius-md)]
Inactive:  text-t-muted hover:text-t-secondary rounded-[var(--radius-md)]
```

Implementation is inline — 2 use cases doesn't justify a shared component. If it appears again, extract.

---

## Verification

After implementation, verify:

1. **`npx next build`** — no compilation errors
2. **`npm run lint`** — no ESLint warnings
3. **Responsive check** — test all 4 pages at 3 viewport widths:
   - Mobile (375px): drawers/sheets work, panels don't overflow
   - Tablet (768px): layout transitions are smooth
   - Desktop (1280px): full layout with sidebars/detail panes
4. **Loading states** — disable network in devtools, verify skeletons appear on every page
5. **Error states** — force API failure, verify error UI appears
6. **Keyboard navigation** — tab through each page, verify focus rings and drawer close on Escape

## Risk Assessment

- **Low risk:** Parts 3, 4 (Search, Integrations) — small targeted changes
- **Medium risk:** Part 2 (Knowledge) — responsive bottom sheet adds new interaction pattern
- **High risk:** Part 1 (Chat) — mobile drawer touches the layout shell (`command-workspace.tsx`), mode selector refactor affects the primary chat interaction. Mitigated by keeping all changes additive (not replacing existing desktop layout).

## Phasing Summary

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 1 | Token consistency + accessibility + interaction states | Complete |
| **Phase 2 (this spec)** | Page-level redesigns — Chat, Knowledge, Search, Integrations | Pending |
| Phase 3 (future) | A2UI complex surface UX redesigns | TBD |
