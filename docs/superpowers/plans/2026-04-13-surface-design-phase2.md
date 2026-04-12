# Surface Design Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign 4 frontend pages (Chat, Knowledge, Search, Integrations) with improved layout, responsive patterns, typography hierarchy, and loading/error states.

**Architecture:** Two tiers — deep redesign for Chat + Knowledge (mobile drawers, segmented controls, skeleton loading), light polish for Search + Integrations (scope pills, toast migration, badge polish). Mobile patterns are inline per-page, not shared components.

**Tech Stack:** React 19, Next.js 16, TypeScript 5, Tailwind CSS 4, Zustand

**Spec:** `docs/superpowers/specs/2026-04-13-surface-design-phase2-design.md`

**Parallelization:** Tasks 1-2 (Chat) are sequential (1 builds layout, 2 fills it). Tasks 3-4 (Knowledge) are sequential. Tasks 5-6 (Search) are sequential. Tasks 7 (Integrations) is independent. Task 8 is verification. Waves: [1→2] parallel with [3→4], then [5→6] parallel with [7], then [8].

**Deferred from spec:** Knowledge graph detail mobile bottom sheet (spec 2.3 responsive pattern) — requires deep integration with `react-force-graph-2d` click handlers and the knowledge store. The empty state is included; the mobile bottom sheet is deferred to Phase 3 alongside the A2UI surface redesigns where similar overlay patterns will be needed.

---

### Task 1: Chat — Session Sidebar Responsive Drawer + Declutter

**Files:**
- Modify: `frontend/src/components/feature/command/command-workspace.tsx`
- Modify: `frontend/src/components/jarvis/session-sidebar.tsx`
- Modify: `frontend/src/app/chat/page.tsx`

- [ ] **Step 1: Add mobile drawer to CommandWorkspace**

Read `frontend/src/components/feature/command/command-workspace.tsx`. It currently has a `hidden lg:block` wrapper around `sessionRail`. Add mobile drawer support:

```typescript
"use client";

import { useState } from "react";

interface Props {
  sessionRail: React.ReactNode;
  commandPanel: React.ReactNode;
  surfaces?: React.ReactNode;
}

export function CommandWorkspace({ sessionRail, commandPanel, surfaces }: Props) {
  const [mobileSessionOpen, setMobileSessionOpen] = useState(false);

  return (
    <div className="flex h-full">
      {/* Mobile session drawer */}
      {mobileSessionOpen && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30 lg:hidden"
          onClick={() => setMobileSessionOpen(false)}
        />
      )}
      <div
        className={`
          fixed inset-y-0 left-0 z-40 w-72 transform transition-transform duration-200 ease-out lg:hidden
          ${mobileSessionOpen ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        {sessionRail}
      </div>

      {/* Desktop session rail */}
      <div className="w-64 shrink-0 border-r border-b-secondary overflow-y-auto hidden lg:block">
        {sessionRail}
      </div>

      {/* Chat panel */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile sessions toggle — injected before commandPanel */}
        <div className="lg:hidden flex items-center px-3 py-1.5 border-b border-b-secondary">
          <button
            onClick={() => setMobileSessionOpen(true)}
            className="p-1.5 rounded-[var(--radius-md)] text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer"
            aria-label="Open conversations"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M3 5h12M3 9h12M3 13h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <span className="text-xs text-t-muted ml-2">Conversations</span>
        </div>
        {commandPanel}
      </div>

      {/* Surfaces panel */}
      {surfaces && (
        <div className="w-[380px] shrink-0 border-l border-b-secondary bg-surface-0 overflow-y-auto transition-all duration-200 ease-in-out hidden md:block">
          {surfaces}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Declutter session sidebar conversation items**

Read `frontend/src/components/jarvis/session-sidebar.tsx`. Modify the conversation item metadata:

Replace the metadata section (lines 94-108) — remove message count and cost from default view, keep only relative time:

```typescript
              <div className="flex items-center gap-2 mt-0.5">
                {convo.last_active_at && (
                  <span className="text-[10px] text-t-muted">
                    {formatRelativeTime(convo.last_active_at)}
                  </span>
                )}
              </div>
```

Replace the delete button (lines 86-92) — make it always visible but subtle, use trash icon:

```typescript
                <button
                  onClick={(e) => handleDelete(e, convo.conversation_id)}
                  className="text-t-muted hover:text-j-error transition-colors shrink-0 cursor-pointer p-0.5 rounded-[var(--radius-sm)]"
                  title={`Delete${convo.message_count ? ` (${convo.message_count} msgs, $${convo.total_cost_usd.toFixed(2)})` : ""}`}
                  aria-label="Delete conversation"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                    <path d="M4 4h8M6 2h4M5 4v8.5a.5.5 0 00.5.5h5a.5.5 0 00.5-.5V4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                  </svg>
                </button>
```

This moves cost and message count into the delete button's tooltip, keeping the row clean.

- [ ] **Step 3: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/feature/command/command-workspace.tsx frontend/src/components/jarvis/session-sidebar.tsx
git commit -m "feat: add responsive session drawer and declutter conversation items in chat"
```

---

### Task 2: Chat — Connection Banner + Mode Selector + Surfaces Rail

**Files:**
- Modify: `frontend/src/app/chat/page.tsx`

- [ ] **Step 1: Add connection banner**

In the chat page, find the command header section (around lines 152-180). Add a connection warning bar below the mode selector, inside the `<div className="flex flex-col h-full">`:

After the command header div and before `<ChatPanel>`, add:

```typescript
            {/* Connection warning */}
            {!connected && (
              <div className="px-4 py-2 bg-j-warning-soft border-b border-j-warning/20 flex items-center gap-2 text-xs text-j-warning animate-fade-in">
                <span className="w-1.5 h-1.5 rounded-full bg-j-warning animate-pulse-live" />
                Connection lost — reconnecting...
              </div>
            )}
```

- [ ] **Step 2: Upgrade mode selector to segmented control**

Find the mode buttons section (around lines 154-169). Replace with a segmented control:

```typescript
              <div className="flex items-center gap-2">
                <div className="bg-surface-2 rounded-[var(--radius-lg)] p-1 inline-flex gap-0.5">
                  {MODES.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => setMode(m.value)}
                      className={`px-3.5 py-1.5 text-[13px] rounded-[var(--radius-md)] transition-all duration-150 cursor-pointer ${
                        mode === m.value
                          ? "bg-j-primary text-j-primary-fg font-medium"
                          : "text-t-muted hover:text-t-secondary"
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
```

- [ ] **Step 3: Upgrade connection status text**

Change the connection status from `text-[11px]` to `text-xs`:

```typescript
              <div className="flex items-center gap-1.5 text-xs">
```

- [ ] **Step 4: Polish surfaces rail**

Find the surfaces section (around lines 192-219). Update the header and add left border:

Replace the surfaces wrapper in the `surfaces` prop content:

```typescript
        surfaces={
          surfaces.length > 0 ? (
            <div className="p-4 space-y-3 border-l border-b-secondary">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold text-t-secondary">
                  Surfaces
                </span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-2 text-t-muted font-medium">
                  {surfaces.length}
                </span>
              </div>
```

Note: The surfaces `border-l` may need to be on the parent container in `CommandWorkspace` instead if the surfaces are inside a wrapper div. Check the actual structure and place accordingly.

- [ ] **Step 5: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/chat/page.tsx
git commit -m "feat: add connection banner, segmented mode control, and surfaces rail polish to chat"
```

---

### Task 3: Knowledge — Top Bar Responsive + Stats View Skeletons

**Files:**
- Modify: `frontend/src/app/knowledge/page.tsx`
- Modify: `frontend/src/components/knowledge/stats-view.tsx`

- [ ] **Step 1: Fix knowledge page top bar responsive layout**

Read `frontend/src/app/knowledge/page.tsx`. Find the top bar section (around lines 98-110). Modify the stats display:

Replace the stats span to hide on mobile entirely:

```typescript
          {(entityCount > 0 || relationshipCount > 0) && (
            <span className="text-xs text-t-muted whitespace-nowrap sm:ml-auto hidden sm:inline">
              {entityCount} entities &middot; {relationshipCount} relationships
            </span>
          )}
```

Remove the `<span className="sm:hidden">` abbreviated version entirely — stats are available in the Stats tab.

- [ ] **Step 2: Fix stats-view loading skeleton**

Read `frontend/src/components/knowledge/stats-view.tsx`. Find the loading branch. Replace custom `animate-pulse` skeleton divs with proper skeleton components:

```typescript
import { SkeletonGrid, Skeleton } from "@/components/ui/skeleton";

// In the loading branch:
if (isLoading) {
  return (
    <div className="p-4 sm:p-6 space-y-6">
      <SkeletonGrid count={4} />
      <Skeleton className="h-48 w-full" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}
```

- [ ] **Step 3: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/knowledge/page.tsx frontend/src/components/knowledge/stats-view.tsx
git commit -m "refactor: improve knowledge page responsive layout and stats loading skeleton"
```

---

### Task 4: Knowledge — Memories Filter Consolidation + Graph Detail Panel

**Files:**
- Modify: `frontend/src/components/knowledge/memories-view.tsx`
- Modify: `frontend/src/components/knowledge/graph-detail-panel.tsx`

- [ ] **Step 1: Replace memories sort pills with dropdown**

Read `frontend/src/components/knowledge/memories-view.tsx`. Find the sort options section (the row of pill buttons for sort options like "Recent", "Confidence", etc.).

Replace the row of sort pill buttons with a single dropdown button + popover menu:

```typescript
// Add state for sort dropdown
const [sortOpen, setSortOpen] = useState(false);

// In the JSX — replace the sort pills row with:
<div className="relative">
  <button
    onClick={() => setSortOpen(!sortOpen)}
    className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-[var(--radius-md)] border border-b-secondary hover:bg-surface-2 transition-colors cursor-pointer ${FOCUS_RING}`}
  >
    <span className="text-t-muted">Sort:</span>
    <span className="text-t-primary font-medium capitalize">{sortBy}</span>
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="text-t-muted">
      <path d="M3 4.5l3 3 3-3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  </button>
  {sortOpen && (
    <>
      <div className="fixed inset-0 z-10" onClick={() => setSortOpen(false)} />
      <div className="absolute top-full mt-1 right-0 z-20 bg-surface-1 border border-b-secondary rounded-[var(--radius-lg)] shadow-[var(--shadow-md)] py-1 min-w-[140px]">
        {SORT_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => { setSortBy(opt.value); setSortOpen(false); }}
            className={`w-full text-left px-3 py-1.5 text-xs transition-colors cursor-pointer ${
              sortBy === opt.value ? "text-j-primary bg-j-primary-soft" : "text-t-secondary hover:bg-surface-2"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </>
  )}
</div>
```

Where `SORT_OPTIONS` is extracted from the existing sort values (should be an array like `[{ value: "recent", label: "Recent" }, { value: "confidence", label: "Confidence" }, ...]`).

- [ ] **Step 2: Add skeleton loading for memories**

Find the loading state. Replace bare text with skeleton rows:

```typescript
import { Skeleton } from "@/components/ui/skeleton";

// In the loading branch:
<div className="space-y-2 p-4">
  {Array.from({ length: 5 }).map((_, i) => (
    <div key={i} className="flex gap-3 py-3">
      <Skeleton className="h-4 w-24" />
      <Skeleton className="h-4 flex-1" />
      <Skeleton className="h-4 w-16" />
    </div>
  ))}
</div>
```

- [ ] **Step 3: Add empty state CTA**

Find the `<EmptyState>` usage. Add an action prop:

```typescript
import Link from "next/link";

<EmptyState
  title="No memories found"
  description="Memories will appear as Jarvis learns from interactions"
  action={
    <Link
      href="/chat"
      className="inline-flex items-center px-3.5 py-2 text-[13px] font-medium rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover transition-colors"
    >
      Start a conversation
    </Link>
  }
/>
```

- [ ] **Step 4: Add graph detail panel empty state**

Read `frontend/src/components/knowledge/graph-detail-panel.tsx`. Find where it returns `null` or renders nothing when no entity is selected. Replace with:

```typescript
import { EmptyState } from "@/components/ui/empty-state";

// When no entity selected:
<EmptyState title="Select an entity" description="Click a node in the graph to see details" />
```

- [ ] **Step 5: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/knowledge/memories-view.tsx frontend/src/components/knowledge/graph-detail-panel.tsx
git commit -m "feat: consolidate memories sort into dropdown, add skeleton loading and graph panel empty state"
```

---

### Task 5: Search — Scope Pill Selector + Loading/Error States

**Files:**
- Modify: `frontend/src/components/search/search-bar.tsx`
- Modify: `frontend/src/app/search/page.tsx`

- [ ] **Step 1: Replace search scope select with pill selector**

Read `frontend/src/components/search/search-bar.tsx`. Replace the entire component:

```typescript
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";

const SCOPES = [
  { value: "all", label: "All" },
  { value: "memory", label: "Memories" },
  { value: "entities", label: "Entities" },
  { value: "events", label: "Events" },
];

export function SearchBar({
  onSearch,
}: {
  onSearch: (query: string, scope: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("all");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) onSearch(query.trim(), scope);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3 sm:space-y-0 sm:flex sm:items-center sm:gap-3">
      <div className="bg-surface-2 rounded-[var(--radius-lg)] p-1 inline-flex gap-0.5 shrink-0">
        {SCOPES.map((s) => (
          <button
            key={s.value}
            type="button"
            onClick={() => setScope(s.value)}
            className={`px-2.5 py-1 text-xs rounded-[var(--radius-md)] transition-all duration-150 cursor-pointer ${
              scope === s.value
                ? "bg-j-primary text-j-primary-fg font-medium"
                : "text-t-muted hover:text-t-secondary"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search memories, entities, events..."
        className="flex-1 bg-surface-2 border border-b-secondary rounded-[var(--radius-lg)] px-4 py-2 text-sm text-t-primary placeholder:text-t-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface-0"
      />
      <Button type="submit">Search</Button>
    </form>
  );
}
```

- [ ] **Step 2: Add loading skeleton, error state, and initial empty state to search page**

Read `frontend/src/app/search/page.tsx`. Replace the loading and results sections:

```typescript
import { SkeletonCard } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";

// In the results area:

        <div className="flex-1 overflow-y-auto p-4">
          {/* Initial state — no search yet */}
          {!data && !isLoading && (
            <EmptyState
              title="Search across everything"
              description="Find memories, entities, events, and documents"
              icon={
                <svg width="32" height="32" viewBox="0 0 32 32" fill="none" className="text-t-muted">
                  <circle cx="14" cy="14" r="8" stroke="currentColor" strokeWidth="2" />
                  <path d="M20 20l6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              }
            />
          )}

          {/* Loading */}
          {isLoading && (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          )}

          {/* Error */}
          {isError && (
            <EmptyState title="Search failed" description="Something went wrong. Please try again." />
          )}

          {/* Results */}
          {data && !isLoading && (
            <>
              <p className="text-[13px] text-t-secondary font-medium mb-3">
                {totalCount} result{totalCount !== 1 ? "s" : ""} found
              </p>
              <ResultGroupList
                groups={groups}
                onSelect={setSelectedResult}
              />
            </>
          )}
        </div>
```

Note: Add `isError` from the `useQuery` return: `const { data, isLoading, isError } = useQuery(...)`.

- [ ] **Step 3: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/search/search-bar.tsx frontend/src/app/search/page.tsx
git commit -m "feat: replace search scope select with pill selector, add skeleton loading and error states"
```

---

### Task 6: Search — Detail Pane Mobile Overlay + Badge Polish

**Files:**
- Modify: `frontend/src/components/feature/search/result-detail-pane.tsx`
- Modify: `frontend/src/app/search/page.tsx`

- [ ] **Step 1: Add mobile overlay for result detail**

In `frontend/src/app/search/page.tsx`, add a mobile bottom sheet that shows when a result is selected below `lg:` breakpoint:

After the existing `</div>` that closes the two-column layout, add:

```typescript
      {/* Mobile detail overlay */}
      {selectedResult && (
        <div className="lg:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={() => setSelectedResult(null)} />
          <div className="absolute inset-x-0 bottom-0 max-h-[80vh] rounded-t-[var(--radius-xl)] bg-surface-1 border-t border-b-secondary shadow-[var(--shadow-lg)] overflow-y-auto animate-slide-in-up">
            <div className="sticky top-0 flex items-center justify-between px-4 py-3 border-b border-b-secondary bg-surface-1">
              <span className="text-[13px] font-semibold text-t-primary">Result Details</span>
              <button
                onClick={() => setSelectedResult(null)}
                className="p-1 rounded-[var(--radius-sm)] text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer"
                aria-label="Close"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            <ResultDetailPane result={selectedResult} />
          </div>
        </div>
      )}
```

- [ ] **Step 2: Polish result-detail-pane badges**

Read `frontend/src/components/feature/search/result-detail-pane.tsx`. Replace the raw `<span>` badges with `<Badge>` component and simplify labels:

```typescript
import { Badge } from "@/components/ui/badge";

const SOURCE_DB_LABELS: Record<string, string> = {
  qdrant: "Vector",
  postgres_fts: "Keyword",
  neo4j: "Graph",
};

// Replace the badges section:
      <div className="flex flex-wrap gap-2">
        {result.source_db && (
          <Badge variant="info">
            {SOURCE_DB_LABELS[result.source_db] ?? result.source_db}
          </Badge>
        )}
        {result.score != null && (
          <Badge variant="default">
            Score: {result.score.toFixed(3)}
          </Badge>
        )}
      </div>
```

- [ ] **Step 3: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/feature/search/result-detail-pane.tsx frontend/src/app/search/page.tsx
git commit -m "feat: add mobile detail overlay and badge polish to search results"
```

---

### Task 7: Integrations — Flash→Toast + Test Badge + Button Sizing + Scope Badges

**Files:**
- Modify: `frontend/src/app/integrations/page.tsx`

- [ ] **Step 1: Replace flash banner with toast**

Read `frontend/src/app/integrations/page.tsx`. Find the flash state variables (`flash`, `setFlash`) and the flash banner JSX.

Remove `const [flash, setFlash] = useState<string | null>(null);`

Replace the `useEffect` that sets flash on OAuth callback (around lines 42-55):

```typescript
  useEffect(() => {
    const status = searchParams.get("status");
    const provider = searchParams.get("provider");
    const error = searchParams.get("error");
    if (status === "connected" && provider) {
      queryClient.invalidateQueries({ queryKey: ["installations"] });
      queryClient.invalidateQueries({ queryKey: ["auth-providers"] });
      addToast(`${provider} connected successfully`, "success");
      window.history.replaceState({}, "", "/integrations");
    } else if (error) {
      addToast(`Error: ${error}`, "error");
      window.history.replaceState({}, "", "/integrations");
    }
  }, [searchParams, queryClient, addToast]);
```

Delete the flash banner JSX (the `{flash && (...)}` block around lines 258-274).

- [ ] **Step 2: Replace test result text with Badge**

Find the test result display (inside `renderProviderCard`, the `{testResult[...] && (...)}` block). Replace:

```typescript
                {testResult[installation.install_id] && (
                  <Badge
                    variant={testResult[installation.install_id] === "healthy" ? "success" : "error"}
                  >
                    {testResult[installation.install_id]}
                  </Badge>
                )}
```

Add auto-clear: in the `handleTest` function, after setting the result, add a timeout:

```typescript
  async function handleTest(installId: string) {
    setTestingId(installId);
    try {
      const result = await checkInstallationHealth(installId);
      setTestResult((prev) => ({ ...prev, [installId]: result.health_status }));
      setTimeout(() => {
        setTestResult((prev) => {
          const next = { ...prev };
          delete next[installId];
          return next;
        });
      }, 5000);
    } catch {
      setTestResult((prev) => ({ ...prev, [installId]: "error" }));
    } finally {
      setTestingId(null);
    }
  }
```

- [ ] **Step 3: Reduce button padding + fix scope badges**

Find the action buttons in `renderProviderCard`. Reduce padding from `px-3 py-1.5` to `px-2.5 py-1` on all 3 buttons (Test, Reauthorize, Disconnect). Standardize radius to `rounded-[var(--radius-md)]`.

Find the scopes display section. Change:
- `text-[10px]` → `text-[11px]`
- `.slice(0, 3)` → `.slice(0, 2)` (show fewer scopes)
- Add `title={provider.scopes.join(", ")}` on the "+N more" span for tooltip.

- [ ] **Step 4: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/integrations/page.tsx
git commit -m "refactor: migrate flash to toast, add test result badges, reduce button sizing in integrations"
```

---

### Task 8: Final Verification

- [ ] **Step 1: Build + lint**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -15 && npm run lint 2>&1 | tail -10`
Expected: Both pass cleanly.

- [ ] **Step 2: Responsive spot-check**

Verify at 3 widths (375px, 768px, 1280px) in browser:
- `/chat` — mobile drawer opens/closes, desktop sidebar works, mode selector is segmented, connection banner shows when offline
- `/knowledge` — stats hidden on mobile, sort dropdown works, graph detail panel shows empty state
- `/search` — scope pills work, skeleton loading appears, mobile detail overlay opens/closes
- `/integrations` — toast notifications appear, test badge shows, buttons fit on one row

- [ ] **Step 3: Commit any remaining fixes**

```bash
git add -A frontend/src/
git commit -m "refactor: fix remaining Phase 2 issues found during verification"
```
