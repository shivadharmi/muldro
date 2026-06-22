# Settings Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the settings page from four engineer-language tabs to three user-language tabs (Account / How Jarvis acts / Spending) with per-capability Trust behind a collapsed expander, while decomposing the 522-line page into a slim shell plus focused tab components.

**Architecture:** Three commits per the engineering standards — (1) characterization tests locking current behavior, (2) behavior-preserving structural extraction, (3) the behavioral consolidation. The shell (`page.tsx`) keeps all data fetching and state; tab components are presentational, fed by props.

**Tech Stack:** Next.js 16, React 19, TypeScript 5, vitest + React Testing Library (harness already in place from the onboarding card work).

**Spec:** `docs/superpowers/specs/2026-06-12-settings-consolidation-design.md`

**Working directory:** `frontend/` (paths below are relative to `frontend/`). Run commands from `frontend/`.

---

## Reference: current `src/app/settings/page.tsx` (522 lines)

- Lines 1-72: imports; `type SettingsTab = "account" | "policy" | "trust" | "budget"`; `TABS`; `POLICY_MODES`; `TRUST_LEVEL_COLORS`; `TRUST_LEVEL_LABELS`; `CEILING_OPTIONS`.
- Lines 73-195: `SettingsPage` — state, effects, handlers (`handlePolicyChange`, `handleBudgetSave`, `handleCeilingChange`, `handleResetTrust`), `trustByFamily` grouping.
- Lines 196-379: render — Account (208-235), Policy (237-272), Trust (274-317), Budget (319-376).
- Lines 381-522: inline `TrustCapabilityCard` component (uses `TRUST_LEVEL_COLORS`, `TRUST_LEVEL_LABELS`, `CEILING_OPTIONS`).

Shell data/handlers (keep in `page.tsx` throughout):
- State: `activeTab`, `policyMode`, `budgetLimit`, `editingBudget`, `budgetInput`, `trustEntries`, `trustLoading`, `policyLoading`, `budgetSaving`, `ceilingLoading`, `resetLoading`.
- `useAuth()` → `{ user, logout }`; `useToast()` → `{ addToast }`.
- `loadTrust()` (useCallback) fetches `fetchTrustDashboard()` → `setTrustEntries`.
- Handler signatures: `handlePolicyChange(mode: string)`, `handleBudgetSave()`, `handleCeilingChange(capability: string, maxLevel: string)`, `handleResetTrust(capability: string)`.
- `trustByFamily: Record<string, TrustDashboardEntry[]>` grouping by `entry.family || "unknown"`.

---

## File Structure (end state after all three commits)

- Modify: `src/app/settings/page.tsx` — slim shell (state, fetching, 3-tab `<Tabs>`, delegates)
- Create: `src/components/settings/trust-constants.ts` — `TRUST_LEVEL_COLORS`, `TRUST_LEVEL_LABELS`, `CEILING_OPTIONS`
- Create: `src/components/settings/trust-capability-card.tsx` — moved from page.tsx
- Create: `src/components/settings/trust-section.tsx` — collapsible per-capability trust expander
- Create: `src/components/settings/account-tab.tsx`
- Create: `src/components/settings/spending-tab.tsx`
- Create: `src/components/settings/how-jarvis-acts-tab.tsx` — policy radios + connective copy + `<TrustSection>`
- Create: `src/app/settings/page.test.tsx` — characterization → updated to 3-tab behavior
- Create: `src/components/settings/trust-section.test.tsx`

---

## Task 1: Characterization tests (commit 1, test-only)

Locks CURRENT 4-tab behavior before any refactor. Mocks `@/lib/api`, `@/lib/auth`, `@/components/ui/toast`.

**Files:**
- Create: `src/app/settings/page.test.tsx`

- [ ] **Step 1: Write the characterization test `src/app/settings/page.test.tsx`**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
const { logout } = vi.hoisted(() => ({ logout: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { email: "founder@example.com", display_name: "Founder" },
    logout,
  }),
}));
vi.mock("@/lib/api", () => ({
  fetchPolicyMode: vi.fn().mockResolvedValue({ mode: "approval_required" }),
  setPolicyMode: vi.fn().mockResolvedValue({}),
  fetchBudget: vi.fn().mockResolvedValue({ daily_limit_usd: 25 }),
  updateBudgetLimit: vi.fn().mockResolvedValue({ daily_limit_usd: 30 }),
  fetchTrustDashboard: vi.fn().mockResolvedValue({ capabilities: [] }),
  setTrustCeiling: vi.fn().mockResolvedValue({}),
  resetTrust: vi.fn().mockResolvedValue({}),
}));

import SettingsPage from "./page";
import {
  setPolicyMode,
  updateBudgetLimit,
  fetchTrustDashboard,
} from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

test("renders the four settings tabs", () => {
  render(<SettingsPage />);
  expect(screen.getByRole("tab", { name: /account/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /policy/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /trust/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /budget/i })).toBeInTheDocument();
});

test("account tab shows the user email by default", () => {
  render(<SettingsPage />);
  expect(screen.getByText("founder@example.com")).toBeInTheDocument();
});

test("selecting a policy mode calls setPolicyMode", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /policy/i }));
  await userEvent.click(screen.getByText("Full Auto"));
  await waitFor(() => expect(setPolicyMode).toHaveBeenCalledWith("full_auto"));
});

test("opening the trust tab loads the trust dashboard", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /trust/i }));
  await waitFor(() => expect(fetchTrustDashboard).toHaveBeenCalled());
});

test("editing the budget calls updateBudgetLimit", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /budget/i }));
  await userEvent.click(screen.getByRole("button", { name: /edit/i }));
  const input = screen.getByRole("spinbutton");
  await userEvent.clear(input);
  await userEvent.type(input, "30");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(updateBudgetLimit).toHaveBeenCalledWith(30));
});
```

- [ ] **Step 2: Run to verify the tests PASS against current behavior**

Run: `npm test src/app/settings/page.test.tsx`
Expected: 5 passed. (These are characterization tests — they describe what the page already does, so they pass immediately. If any fails, the mock setup or a selector is wrong — fix the test, not the page. Note: the `<Tabs>` component must expose `role="tab"`; if it does not, switch the selectors to `getByRole("button", { name })` or `getByText` to match how tabs are actually rendered — inspect `src/components/ui/tabs.tsx` first.)

- [ ] **Step 3: Commit (test-only)**

```bash
git add frontend/src/app/settings/page.test.tsx
git commit -m "test(settings): characterization tests for the 4-tab settings page"
```

---

## Task 2: Structural extraction (commit 2, refactor — no behavior change)

Extract the clearly-reusable pieces into `src/components/settings/*`. Leave the Policy and Trust RENDER blocks inline in `page.tsx` (they are merged in Task 3, so extracting them now would be throwaway). After this task the page still renders four tabs with identical behavior, and Task 1's tests pass UNCHANGED.

**Files:**
- Create: `src/components/settings/trust-constants.ts`
- Create: `src/components/settings/trust-capability-card.tsx`
- Create: `src/components/settings/account-tab.tsx`
- Create: `src/components/settings/budget-tab.tsx`
- Modify: `src/app/settings/page.tsx`

- [ ] **Step 1: Create `src/components/settings/trust-constants.ts`**

Move the three trust constants out of `page.tsx` (verbatim values from current lines 47-71):

```ts
export const TRUST_LEVEL_COLORS: Record<string, string> = {
  first_use: "bg-t-muted",
  learning: "bg-j-info",
  trusted: "bg-j-success",
  autonomous: "bg-j-secondary",
  blocked: "bg-j-error",
};

export const TRUST_LEVEL_LABELS: Record<string, string> = {
  first_use: "First Use",
  learning: "Learning",
  trusted: "Trusted",
  autonomous: "Autonomous",
  blocked: "Blocked",
};

export const CEILING_OPTIONS = [
  { value: "blocked", label: "Blocked" },
  { value: "first_use", label: "First Use" },
  { value: "learning", label: "Learning" },
  { value: "trusted", label: "Trusted" },
  { value: "autonomous", label: "Autonomous (no limit)" },
];
```

- [ ] **Step 2: Create `src/components/settings/trust-capability-card.tsx`**

Move the entire `TrustCapabilityCard` component and its props interface (current lines 381-522) into this file verbatim, adding a `"use client"` directive (it uses `useState`), the `TrustDashboardEntry` type import, and importing the constants from `./trust-constants`:

```tsx
"use client";

import { useState } from "react";
import type { TrustDashboardEntry } from "@/lib/types";
import { TRUST_LEVEL_COLORS, TRUST_LEVEL_LABELS, CEILING_OPTIONS } from "./trust-constants";

interface TrustCapabilityCardProps {
  entry: TrustDashboardEntry;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingDisabled?: boolean;
  resetDisabled?: boolean;
}

export function TrustCapabilityCard({
  entry,
  onCeilingChange,
  onReset,
  ceilingDisabled,
  resetDisabled,
}: TrustCapabilityCardProps) {
  // ... move the EXACT body from current page.tsx lines 398-521 unchanged ...
}
```
(The body — `const [expanded, setExpanded]`, `bestProgress`, and the full JSX from current lines 398-521 — moves verbatim. Only the constant references now resolve via the import.)

- [ ] **Step 3: Create `src/components/settings/account-tab.tsx`**

Extract the Account render block (current lines 208-235) into a presentational component:

```tsx
import { Card, CardBody } from "@/components/ui/card";

interface AccountTabProps {
  email: string | null;
  displayName: string | null;
  onSignOut: () => void;
}

export function AccountTab({ email, displayName, onSignOut }: AccountTabProps) {
  return (
    <Card>
      <CardBody>
        <div className="space-y-5">
          <div className="grid grid-cols-[120px_1fr] gap-y-4 gap-x-4 items-baseline">
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider">Email</p>
            <p className="text-sm text-t-primary">{email ?? "—"}</p>
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider">
              Display Name
            </p>
            <p className="text-sm text-t-primary">{displayName ?? "—"}</p>
          </div>
          <div className="pt-4 border-t border-b-secondary">
            <button
              onClick={onSignOut}
              className="px-4 py-2 rounded-[var(--radius-md)] border border-j-error/30 text-j-error text-[13px] font-medium hover:bg-j-error-soft transition-colors cursor-pointer"
            >
              Sign Out
            </button>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
```

- [ ] **Step 4: Create `src/components/settings/budget-tab.tsx`**

Extract the Budget render block (current lines 319-376) into a presentational component driven by props (mirrors the shell's budget state/handlers):

```tsx
import { Card, CardBody } from "@/components/ui/card";

interface BudgetTabProps {
  budgetLimit: number | null;
  editing: boolean;
  input: string;
  saving: boolean;
  onEditStart: () => void;
  onInputChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
}

export function BudgetTab({
  budgetLimit,
  editing,
  input,
  saving,
  onEditStart,
  onInputChange,
  onSave,
  onCancel,
}: BudgetTabProps) {
  return (
    <Card>
      <CardBody>
        <div className="space-y-4">
          <div>
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
              Daily Token Budget
            </p>
            {editing ? (
              <div className="flex items-center gap-2">
                <span className="text-t-secondary text-sm">$</span>
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={input}
                  onChange={(e) => onInputChange(e.target.value)}
                  className="w-32 rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring transition-colors"
                  autoFocus
                />
                <button
                  onClick={onSave}
                  disabled={saving}
                  className="px-3.5 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
                <button
                  onClick={onCancel}
                  className="px-3.5 py-2 rounded-[var(--radius-md)] text-t-secondary text-[13px] hover:bg-surface-2 transition-colors cursor-pointer"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <p className="text-2xl font-semibold text-t-primary tracking-tight">
                  ${budgetLimit?.toFixed(2) ?? "—"}
                  <span className="text-sm text-t-muted font-normal ml-1">/ day</span>
                </p>
                <button
                  onClick={onEditStart}
                  className="text-xs text-j-primary hover:text-j-primary-hover font-medium cursor-pointer"
                >
                  Edit
                </button>
              </div>
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
```

- [ ] **Step 5: Rewire `page.tsx` to use the extracted components (no behavior change)**

In `page.tsx`:
1. Delete the three trust constants (now in `trust-constants.ts`) and the inline `TrustCapabilityCard` (now in its own file).
2. Add imports:
```tsx
import { TrustCapabilityCard } from "@/components/settings/trust-capability-card";
import { TRUST_LEVEL_LABELS } from "@/components/settings/trust-constants";
import { AccountTab } from "@/components/settings/account-tab";
import { BudgetTab } from "@/components/settings/budget-tab";
```
   (`TRUST_LEVEL_LABELS` is still referenced by `handleCeilingChange`'s toast at current line 159 — keep that import.)
3. Replace the Account render block (lines 208-235) with:
```tsx
{activeTab === "account" && (
  <AccountTab
    email={user?.email ?? null}
    displayName={user?.display_name ?? null}
    onSignOut={logout}
  />
)}
```
4. Replace the Budget render block (lines 319-376) with:
```tsx
{activeTab === "budget" && (
  <BudgetTab
    budgetLimit={budgetLimit}
    editing={editingBudget}
    input={budgetInput}
    saving={budgetSaving}
    onEditStart={() => {
      setBudgetInput(String(budgetLimit ?? 5));
      setEditingBudget(true);
    }}
    onInputChange={setBudgetInput}
    onSave={handleBudgetSave}
    onCancel={() => setEditingBudget(false)}
  />
)}
```
5. Leave the Policy block (237-272) and the Trust block (274-317, which already renders `<TrustCapabilityCard .../>` — now imported) INLINE and unchanged. `TABS` and `SettingsTab` stay 4-valued.

- [ ] **Step 6: Run Task 1's characterization tests — they must pass UNCHANGED**

Run: `npm test src/app/settings/page.test.tsx`
Expected: 5 passed (identical to Task 1). This proves the extraction preserved behavior.

- [ ] **Step 7: Lint, build, and confirm the line-count drop**

Run: `npm run lint` (no new errors), `npm run build` (succeeds), and `wc -l src/app/settings/page.tsx` (expected: under 400).

- [ ] **Step 8: Commit (structural)**

```bash
git add frontend/src/components/settings/trust-constants.ts \
  frontend/src/components/settings/trust-capability-card.tsx \
  frontend/src/components/settings/account-tab.tsx \
  frontend/src/components/settings/budget-tab.tsx \
  frontend/src/app/settings/page.tsx
git commit -m "refactor(settings): extract account/budget tabs + trust card into components"
```

---

## Task 3: Consolidation (commit 3, behavior)

Merge Policy + Trust into a single "How Jarvis acts" tab (policy radios headline + connective copy + collapsed `TrustSection`), rename Budget → Spending, reduce `TABS` 4 → 3, and lazy-load trust on first expand instead of on tab open.

**Files:**
- Create: `src/components/settings/trust-section.tsx`
- Create: `src/components/settings/how-jarvis-acts-tab.tsx`
- Create: `src/components/settings/spending-tab.tsx` (renamed from budget-tab.tsx)
- Create: `src/components/settings/trust-section.test.tsx`
- Modify: `src/app/settings/page.tsx`
- Modify: `src/app/settings/page.test.tsx`
- Delete: `src/components/settings/budget-tab.tsx`

- [ ] **Step 1: Write the failing `TrustSection` test `src/components/settings/trust-section.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { TrustSection } from "./trust-section";

const noop = () => {};

function renderSection(trustByFamily = {}) {
  return render(
    <TrustSection
      trustByFamily={trustByFamily}
      loading={false}
      onExpand={vi.fn()}
      onCeilingChange={noop}
      onReset={noop}
      ceilingLoading={null}
      resetLoading={null}
    />,
  );
}

test("is collapsed by default — no trust content shown", () => {
  renderSection({ communication: [] });
  expect(screen.queryByText(/no trust data yet/i)).not.toBeInTheDocument();
  // The expander control itself is present.
  expect(screen.getByRole("button", { name: /per-capability trust/i })).toBeInTheDocument();
});

test("expanding with no data reveals the empty state", async () => {
  renderSection({});
  await userEvent.click(screen.getByRole("button", { name: /per-capability trust/i }));
  expect(screen.getByText(/no trust data yet/i)).toBeInTheDocument();
});

test("expanding calls onExpand (lazy load trigger)", async () => {
  const onExpand = vi.fn();
  render(
    <TrustSection
      trustByFamily={{}}
      loading={false}
      onExpand={onExpand}
      onCeilingChange={noop}
      onReset={noop}
      ceilingLoading={null}
      resetLoading={null}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: /per-capability trust/i }));
  expect(onExpand).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm test src/components/settings/trust-section.test.tsx`
Expected: FAIL — "Failed to resolve import './trust-section'".

- [ ] **Step 3: Implement `src/components/settings/trust-section.tsx`**

```tsx
"use client";

import { useState } from "react";
import { Card, CardBody } from "@/components/ui/card";
import type { TrustDashboardEntry } from "@/lib/types";
import { TrustCapabilityCard } from "./trust-capability-card";

interface TrustSectionProps {
  trustByFamily: Record<string, TrustDashboardEntry[]>;
  loading: boolean;
  onExpand: () => void;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingLoading: string | null;
  resetLoading: string | null;
}

/**
 * Progressive-disclosure wrapper for the per-capability trust list. Collapsed by
 * default so a new user sees only the global posture; the grouped-by-family
 * capability cards live behind the expander. Trust data is lazy-loaded the first
 * time the section is expanded (the parent fetches in onExpand).
 */
export function TrustSection({
  trustByFamily,
  loading,
  onExpand,
  onCeilingChange,
  onReset,
  ceilingLoading,
  resetLoading,
}: TrustSectionProps) {
  const [expanded, setExpanded] = useState(false);

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next) onExpand();
  }

  const families = Object.entries(trustByFamily);

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center gap-2 text-left cursor-pointer group py-2"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className={`text-t-muted group-hover:text-t-secondary transition-all duration-150 ${expanded ? "rotate-90" : ""}`}
        >
          <path d="M9 18l6-6-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="text-[13px] font-medium text-t-primary">Per-capability trust</span>
      </button>

      {expanded && (
        <div className="space-y-6 pt-2">
          {loading && (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-16 rounded-[var(--radius-lg)] skeleton" />
              ))}
            </div>
          )}

          {!loading && families.length === 0 && (
            <Card>
              <CardBody>
                <div className="text-center py-4">
                  <p className="text-sm text-t-secondary font-medium mb-1">No trust data yet</p>
                  <p className="text-xs text-t-muted">
                    Trust levels build as Jarvis performs actions and you approve or reject them.
                  </p>
                </div>
              </CardBody>
            </Card>
          )}

          {families.map(([family, entries]) => (
            <div key={family}>
              <h3 className="text-[11px] uppercase text-t-muted font-medium mb-2.5 tracking-wider">
                {family}
              </h3>
              <div className="space-y-2">
                {entries.map((entry) => (
                  <TrustCapabilityCard
                    key={entry.capability}
                    entry={entry}
                    onCeilingChange={onCeilingChange}
                    onReset={onReset}
                    ceilingDisabled={ceilingLoading === entry.capability}
                    resetDisabled={resetLoading === entry.capability}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run to verify the TrustSection tests pass**

Run: `npm test src/components/settings/trust-section.test.tsx`
Expected: 3 passed.

- [ ] **Step 5: Create `src/components/settings/how-jarvis-acts-tab.tsx`**

Combines the policy radios (moved from page.tsx lines 237-272), the connective copy, and `<TrustSection>`:

```tsx
import { TrustSection } from "./trust-section";
import type { TrustDashboardEntry } from "@/lib/types";

interface PolicyMode {
  value: string;
  label: string;
  description: string;
}

interface HowJarvisActsTabProps {
  policyMode: string;
  policyModes: PolicyMode[];
  policyLoading: boolean;
  onPolicyChange: (value: string) => void;
  trustByFamily: Record<string, TrustDashboardEntry[]>;
  trustLoading: boolean;
  onTrustExpand: () => void;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingLoading: string | null;
  resetLoading: string | null;
}

export function HowJarvisActsTab({
  policyMode,
  policyModes,
  policyLoading,
  onPolicyChange,
  trustByFamily,
  trustLoading,
  onTrustExpand,
  onCeilingChange,
  onReset,
  ceilingLoading,
  resetLoading,
}: HowJarvisActsTabProps) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-t-tertiary leading-relaxed">
        Your overall posture applies to everything; per-capability trust fine-tunes
        how much Jarvis can do on its own for each kind of action.
      </p>

      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Overall posture
        </p>
        <div className="space-y-2">
          {policyModes.map((pm) => {
            const isActive = policyMode === pm.value;
            return (
              <button
                key={pm.value}
                type="button"
                onClick={() => onPolicyChange(pm.value)}
                disabled={policyLoading}
                className={`w-full text-left rounded-[var(--radius-lg)] border p-4 transition-all duration-150 cursor-pointer ${
                  isActive
                    ? "border-j-primary/40 bg-j-primary-soft"
                    : "border-b-secondary bg-surface-1 hover:bg-surface-2"
                } disabled:opacity-50`}
              >
                <div className="flex items-center gap-3">
                  <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${
                    isActive ? "border-j-primary" : "border-b-strong"
                  }`}>
                    {isActive && <div className="w-2 h-2 rounded-full bg-j-primary" />}
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-t-primary">{pm.label}</p>
                    <p className="text-xs text-t-tertiary mt-0.5">{pm.description}</p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-b-secondary pt-2">
        <TrustSection
          trustByFamily={trustByFamily}
          loading={trustLoading}
          onExpand={onTrustExpand}
          onCeilingChange={onCeilingChange}
          onReset={onReset}
          ceilingLoading={ceilingLoading}
          resetLoading={resetLoading}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create `src/components/settings/spending-tab.tsx` and delete `budget-tab.tsx`**

Copy `budget-tab.tsx` to `spending-tab.tsx`, renaming the component `BudgetTab` → `SpendingTab` and the props interface `BudgetTabProps` → `SpendingTabProps`. The JSX body is unchanged (the heading stays "Daily Token Budget"; only the tab label becomes "Spending"). Then delete `budget-tab.tsx`.

Run: `git rm frontend/src/components/settings/budget-tab.tsx` after creating `spending-tab.tsx`.

- [ ] **Step 7: Rewire `page.tsx` to three tabs**

1. Change the tab type and list:
```tsx
type SettingsTab = "account" | "how_jarvis_acts" | "spending";

const TABS = [
  { key: "account", label: "Account" },
  { key: "how_jarvis_acts", label: "How Jarvis acts" },
  { key: "spending", label: "Spending" },
];
```
2. Update `useState<SettingsTab>("account")` (unchanged default).
3. Replace the trust-loading effect. DELETE:
```tsx
  useEffect(() => {
    if (activeTab === "trust") {
      loadTrust();
    }
  }, [activeTab, loadTrust]);
```
   Add a guard so the lazy load fetches at most once per mount:
```tsx
  const [trustLoadedOnce, setTrustLoadedOnce] = useState(false);

  const handleTrustExpand = useCallback(() => {
    if (trustLoadedOnce) return;
    setTrustLoadedOnce(true);
    loadTrust();
  }, [trustLoadedOnce, loadTrust]);
```
4. Update imports: remove `POLICY_MODES` usage from inline render (it moves as a prop value — keep the `POLICY_MODES` const in page.tsx and pass it down, OR move it into how-jarvis-acts-tab.tsx). Simplest: keep `POLICY_MODES` defined in page.tsx and pass it as `policyModes={POLICY_MODES}`. Add:
```tsx
import { HowJarvisActsTab } from "@/components/settings/how-jarvis-acts-tab";
import { SpendingTab } from "@/components/settings/spending-tab";
```
   Remove the now-unused `AccountTab`? No — keep it. Remove the `BudgetTab` import; add `SpendingTab`.
5. Replace the three render branches (account stays; policy + trust become one; budget → spending):
```tsx
{activeTab === "account" && (
  <AccountTab
    email={user?.email ?? null}
    displayName={user?.display_name ?? null}
    onSignOut={logout}
  />
)}

{activeTab === "how_jarvis_acts" && (
  <HowJarvisActsTab
    policyMode={policyMode}
    policyModes={POLICY_MODES}
    policyLoading={policyLoading}
    onPolicyChange={handlePolicyChange}
    trustByFamily={trustByFamily}
    trustLoading={trustLoading}
    onTrustExpand={handleTrustExpand}
    onCeilingChange={handleCeilingChange}
    onReset={handleResetTrust}
    ceilingLoading={ceilingLoading}
    resetLoading={resetLoading}
  />
)}

{activeTab === "spending" && (
  <SpendingTab
    budgetLimit={budgetLimit}
    editing={editingBudget}
    input={budgetInput}
    saving={budgetSaving}
    onEditStart={() => {
      setBudgetInput(String(budgetLimit ?? 5));
      setEditingBudget(true);
    }}
    onInputChange={setBudgetInput}
    onSave={handleBudgetSave}
    onCancel={() => setEditingBudget(false)}
  />
)}
```
6. Delete the now-removed inline Policy block, Trust block, and the old `TrustCapabilityCard` import if the page no longer references it directly (it now lives only inside `TrustSection`). Update the `PageHeader` subtitle to drop "policies, trust levels" wording, e.g. `subtitle="Manage your account, autonomy, and spending"`.

- [ ] **Step 8: Update the characterization tests in `page.test.tsx` for the 3-tab structure**

Replace the four-tab tests with the consolidated behavior. Change the "renders four tabs" test to assert THREE, update the policy test to open the "How Jarvis acts" tab, and change the trust test to load on expand:

```tsx
test("renders the three settings tabs", () => {
  render(<SettingsPage />);
  expect(screen.getByRole("tab", { name: /account/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /how jarvis acts/i })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /spending/i })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /^trust$/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /^policy$/i })).not.toBeInTheDocument();
});

test("how-jarvis-acts tab shows the connective copy and policy modes", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /how jarvis acts/i }));
  expect(screen.getByText(/per-capability trust fine-tunes/i)).toBeInTheDocument();
  expect(screen.getByText("Full Auto")).toBeInTheDocument();
});

test("selecting a policy mode calls setPolicyMode", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /how jarvis acts/i }));
  await userEvent.click(screen.getByText("Full Auto"));
  await waitFor(() => expect(setPolicyMode).toHaveBeenCalledWith("full_auto"));
});

test("trust loads only after the per-capability section is expanded", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /how jarvis acts/i }));
  expect(fetchTrustDashboard).not.toHaveBeenCalled();
  // Use the button role to disambiguate from the connective copy paragraph,
  // which also contains the phrase "per-capability trust".
  await userEvent.click(screen.getByRole("button", { name: /per-capability trust/i }));
  await waitFor(() => expect(fetchTrustDashboard).toHaveBeenCalled());
});

test("editing the budget on the spending tab calls updateBudgetLimit", async () => {
  render(<SettingsPage />);
  await userEvent.click(screen.getByRole("tab", { name: /spending/i }));
  await userEvent.click(screen.getByRole("button", { name: /edit/i }));
  const input = screen.getByRole("spinbutton");
  await userEvent.clear(input);
  await userEvent.type(input, "30");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(updateBudgetLimit).toHaveBeenCalledWith(30));
});
```
Keep the existing "account tab shows the user email" test (unchanged).

- [ ] **Step 9: Run the full settings test suite**

Run: `npm test src/app/settings/page.test.tsx src/components/settings/trust-section.test.tsx`
Expected: all pass (5 settings + 3 trust-section).

- [ ] **Step 10: Full verify**

Run (from `frontend/`): `npm test` (whole suite green), `npm run lint` (no new errors), `npm run build` (succeeds), `wc -l src/app/settings/page.tsx` (under 400).

- [ ] **Step 11: Commit (behavior)**

```bash
git add frontend/src/components/settings/trust-section.tsx \
  frontend/src/components/settings/trust-section.test.tsx \
  frontend/src/components/settings/how-jarvis-acts-tab.tsx \
  frontend/src/components/settings/spending-tab.tsx \
  frontend/src/app/settings/page.tsx \
  frontend/src/app/settings/page.test.tsx
git rm frontend/src/components/settings/budget-tab.tsx
git commit -m "feat(settings): collapse to 3 user-language tabs with trust progressive disclosure"
```

---

## Verification (whole feature)

- [ ] `npm test` green (settings characterization updated to 3 tabs + trust-section tests + all prior tests).
- [ ] `npm run lint` clean; `npm run build` succeeds.
- [ ] `src/app/settings/page.tsx` under the 400-line component cap.
- [ ] Manual: three tabs (Account / How Jarvis acts / Spending); "How Jarvis acts" shows connective copy + posture radios + collapsed "Per-capability trust"; expanding loads and reveals the capability list (or empty state); changing a policy mode and editing the budget still work; Account unchanged.
