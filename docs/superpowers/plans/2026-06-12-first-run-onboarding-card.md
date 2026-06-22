# First-run Onboarding Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-run "connect your first source" onboarding card to the workspace page, shown only when no source is connected, backed by new frontend test infrastructure.

**Architecture:** A pure `resolveFirstRunState` helper decides which of three mutually-exclusive first-load states renders (onboarding / gathering / active). A presentational `OnboardingCard` offers inline OAuth connect buttons for Google, GitHub, and Notion via the existing `getAuthUrl` helper. `page.tsx` switches on the helper's result. Frontend tests run on a new vitest + React Testing Library harness.

**Tech Stack:** Next.js 16, React 19, TypeScript 5, vitest, @testing-library/react v16, jsdom.

**Spec:** `docs/superpowers/specs/2026-06-12-first-run-onboarding-card-design.md`

**Working directory:** `frontend/` (all paths below are relative to `frontend/` unless noted). Run from the repo root or `cd frontend` first.

---

## File Structure

- Create: `frontend/vitest.config.mts` — vitest config (jsdom, react, tsconfig paths). `.mts` (not `.ts`) because the project has no `"type": "module"`; matches the existing `.mjs` config convention.
- Create: `frontend/vitest.setup.mts` — imports jest-dom matchers
- Create: `frontend/src/test-harness.test.tsx` — smoke test proving the harness works
- Modify: `frontend/package.json` — add dev deps + `test` / `test:watch` scripts
- Create: `frontend/src/lib/first-run-state.ts` — pure state helper
- Create: `frontend/src/lib/first-run-state.test.ts` — helper unit tests
- Create: `frontend/src/components/dashboard/onboarding-card.tsx` — the card
- Create: `frontend/src/components/dashboard/onboarding-card.test.tsx` — card tests
- Modify: `frontend/src/app/page.tsx` — switch on `resolveFirstRunState`

**Commits:** (1) test harness [structural]; (2) helper + card + wiring [behavior]. Structural separated from behavior per `docs/engineering-standards.md`.

---

## Task 1: Frontend test harness (structural)

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/vitest.setup.ts`
- Create: `frontend/src/test-harness.test.tsx`

- [ ] **Step 1: Install dev dependencies**

Run (from `frontend/`):
```bash
npm install -D vitest @vitejs/plugin-react vite-tsconfig-paths jsdom \
  @testing-library/react @testing-library/jest-dom @testing-library/user-event
```
Expected: installs without peer-dep errors (RTL v16 supports React 19).

- [ ] **Step 2: Create `frontend/vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
});
```

- [ ] **Step 3: Create `frontend/vitest.setup.ts`**

```ts
import "@testing-library/jest-dom";
```

- [ ] **Step 4: Add scripts to `frontend/package.json`**

In the `"scripts"` block, add `test` and `test:watch` alongside the existing entries:
```json
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint",
    "test": "vitest run",
    "test:watch": "vitest"
  },
```

- [ ] **Step 5: Write the harness smoke test `frontend/src/test-harness.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";

test("test harness renders React and jest-dom matchers work", () => {
  render(<span>jarvis-harness-ok</span>);
  expect(screen.getByText("jarvis-harness-ok")).toBeInTheDocument();
});
```

- [ ] **Step 6: Run the smoke test to verify the harness works**

Run: `npm test`
Expected: 1 passed. This proves jsdom + React render + the `@/` alias plugin + jest-dom matchers are all wired. If `toBeInTheDocument` is unknown, the setup file isn't loading; if JSX fails, the react plugin isn't loading.

- [ ] **Step 7: Verify lint still passes**

Run: `npm run lint`
Expected: no errors (test file imports `test`/`expect` explicitly, so no undefined globals).

- [ ] **Step 8: Commit (structural)**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts \
  frontend/vitest.setup.ts frontend/src/test-harness.test.tsx
git commit -m "test(frontend): add vitest + React Testing Library harness"
```

---

## Task 2: `resolveFirstRunState` helper (behavior, TDD)

**Files:**
- Create: `frontend/src/lib/first-run-state.test.ts`
- Create: `frontend/src/lib/first-run-state.ts`

- [ ] **Step 1: Write the failing tests `frontend/src/lib/first-run-state.test.ts`**

```ts
import { test, expect } from "vitest";
import { resolveFirstRunState } from "./first-run-state";

test("no sources and no briefing -> onboarding", () => {
  expect(resolveFirstRunState(0, false)).toBe("onboarding");
});

test("sources connected but no briefing -> gathering", () => {
  expect(resolveFirstRunState(2, false)).toBe("gathering");
});

test("briefing present -> active", () => {
  expect(resolveFirstRunState(2, true)).toBe("active");
});

test("briefing present with zero sources -> active (precedence)", () => {
  expect(resolveFirstRunState(0, true)).toBe("active");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm test src/lib/first-run-state.test.ts`
Expected: FAIL — "Failed to resolve import './first-run-state'" (module does not exist yet).

- [ ] **Step 3: Write minimal implementation `frontend/src/lib/first-run-state.ts`**

```ts
export type FirstRunState = "onboarding" | "gathering" | "active";

/**
 * Decide which first-load state the workspace should render. Precedence:
 * an existing briefing always wins (an active user who disconnects all
 * sources is not thrown back to onboarding), then zero sources is the
 * first-run onboarding state, otherwise we are warming up.
 */
export function resolveFirstRunState(
  sourceCount: number,
  hasBriefing: boolean,
): FirstRunState {
  if (hasBriefing) return "active";
  if (sourceCount === 0) return "onboarding";
  return "gathering";
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm test src/lib/first-run-state.test.ts`
Expected: 4 passed.

(No separate commit — committed with Task 4 as the single behavior commit.)

---

## Task 3: `OnboardingCard` component (behavior, TDD)

**Files:**
- Create: `frontend/src/components/dashboard/onboarding-card.test.tsx`
- Create: `frontend/src/components/dashboard/onboarding-card.tsx`

Reference facts (verified against the codebase):
- `getAuthUrl(provider: string): Promise<{ url: string; provider: string }>` from `@/lib/api`.
- `useToast()` returns `{ addToast(message, variant) }`; variant union includes `"error"` (`@/components/ui/toast`).
- Logos `GoogleLogo`, `GitHubLogo`, `NotionLogo` from `@/components/integrations/logos` (each takes `{ className }`).
- OAuth slugs accepted by the backend authorize route: `google`, `github`, `notion`.

- [ ] **Step 1: Write the failing tests `frontend/src/components/dashboard/onboarding-card.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach, afterEach } from "vitest";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/api", () => ({ getAuthUrl: vi.fn() }));

import { OnboardingCard } from "./onboarding-card";
import { getAuthUrl } from "@/lib/api";

const mockedGetAuthUrl = vi.mocked(getAuthUrl);

beforeEach(() => {
  vi.spyOn(window.location, "assign").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  mockedGetAuthUrl.mockReset();
  addToast.mockReset();
});

test("renders the three primary sources", () => {
  render(<OnboardingCard />);
  expect(screen.getByRole("button", { name: /google/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /github/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /notion/i })).toBeInTheDocument();
});

test("clicking a source connects via getAuthUrl and redirects", async () => {
  mockedGetAuthUrl.mockResolvedValue({ url: "https://oauth.example/start", provider: "github" });
  render(<OnboardingCard />);
  await userEvent.click(screen.getByRole("button", { name: /github/i }));
  expect(mockedGetAuthUrl).toHaveBeenCalledWith("github");
  expect(window.location.assign).toHaveBeenCalledWith("https://oauth.example/start");
});

test("a failed getAuthUrl shows an error toast and does not redirect", async () => {
  mockedGetAuthUrl.mockRejectedValue(new Error("boom"));
  render(<OnboardingCard />);
  await userEvent.click(screen.getByRole("button", { name: /google/i }));
  expect(window.location.assign).not.toHaveBeenCalled();
  expect(addToast).toHaveBeenCalledWith(expect.stringMatching(/couldn't start connecting/i), "error");
});

test("links to all integrations", () => {
  render(<OnboardingCard />);
  expect(screen.getByRole("link", { name: /see all integrations/i })).toHaveAttribute(
    "href",
    "/integrations",
  );
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm test src/components/dashboard/onboarding-card.test.tsx`
Expected: FAIL — "Failed to resolve import './onboarding-card'" (component does not exist yet).

- [ ] **Step 3: Write the implementation `frontend/src/components/dashboard/onboarding-card.tsx`**

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { getAuthUrl } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { GoogleLogo, GitHubLogo, NotionLogo } from "@/components/integrations/logos";

type LogoComponent = React.FC<{ className?: string }>;

interface PrimarySource {
  provider: string;
  label: string;
  hint: string;
  Logo: LogoComponent;
}

// OAuth slugs must match the backend authorize route (routes_auth.py):
// google, github, notion. Slack has no authorize branch and is excluded.
const PRIMARY_SOURCES: PrimarySource[] = [
  { provider: "google", label: "Google", hint: "Gmail + Calendar", Logo: GoogleLogo },
  { provider: "github", label: "GitHub", hint: "repos", Logo: GitHubLogo },
  { provider: "notion", label: "Notion", hint: "docs", Logo: NotionLogo },
];

/**
 * First-run card shown when no source is connected. Guides the user to connect
 * their first source via inline OAuth buttons (the same getAuthUrl mechanism the
 * integrations page uses). Replaced by BriefingGatheringCard once a source
 * connects. See resolveFirstRunState in src/lib/first-run-state.ts.
 */
export function OnboardingCard() {
  const { addToast } = useToast();
  const [connecting, setConnecting] = useState<string | null>(null);

  async function handleConnect(provider: string) {
    setConnecting(provider);
    try {
      const { url } = await getAuthUrl(provider);
      window.location.assign(url);
    } catch {
      addToast(`Couldn't start connecting ${provider}. Please try again.`, "error");
      setConnecting(null);
    }
  }

  return (
    <div className="rounded-[var(--radius-xl)] border border-b-secondary bg-surface-1 p-8 sm:p-10">
      <div className="flex flex-col items-center text-center max-w-md mx-auto">
        <p className="text-[15px] text-t-primary font-medium mb-1">
          Connect your first source
        </p>
        <p className="text-sm text-t-tertiary leading-relaxed mb-6">
          Jarvis gets sharper the more it can see. Connect a source to begin —
          you can add more anytime.
        </p>
        <div className="flex flex-wrap justify-center gap-3 mb-6">
          {PRIMARY_SOURCES.map(({ provider, label, hint, Logo }) => (
            <button
              key={provider}
              type="button"
              onClick={() => handleConnect(provider)}
              disabled={connecting !== null}
              className="flex flex-col items-center gap-1 px-5 py-3 rounded-[var(--radius-md)] border border-b-secondary hover:bg-surface-2 disabled:opacity-50 transition-colors"
            >
              <Logo className="w-6 h-6" />
              <span className="text-[13px] font-medium text-t-primary">
                {connecting === provider ? "Connecting…" : label}
              </span>
              <span className="text-[11px] text-t-tertiary">{hint}</span>
            </button>
          ))}
        </div>
        <Link
          href="/integrations"
          className="text-[13px] text-t-secondary hover:text-t-primary underline underline-offset-2"
        >
          See all integrations →
        </Link>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm test src/components/dashboard/onboarding-card.test.tsx`
Expected: 4 passed.

(No separate commit — committed with Task 4.)

---

## Task 4: Wire into `page.tsx` and commit behavior

**Files:**
- Modify: `frontend/src/app/page.tsx`

- [ ] **Step 1: Add imports**

Near the other dashboard component imports (next to `BriefingGatheringCard`), add:
```tsx
import { OnboardingCard } from "@/components/dashboard/onboarding-card";
```
Near the other `@/lib` imports, add:
```tsx
import { resolveFirstRunState } from "@/lib/first-run-state";
```

- [ ] **Step 2: Replace the briefing-gathering flag with the state helper**

Find:
```tsx
  // The briefing schedule is enabled at workspace creation, so before the first
  // briefing runs we show a "gathering data" card instead of a generic empty state.
  const showBriefingGathering = !briefing;
```
Replace with:
```tsx
  // First-load state: onboarding (no source yet), gathering (source connected,
  // briefing pending), or active (briefing exists). See resolveFirstRunState.
  const firstRunState = resolveFirstRunState(sourceCount, Boolean(briefing));
```

- [ ] **Step 3: Replace the render branch**

Find:
```tsx
      {showBriefingGathering && <BriefingGatheringCard />}
```
Replace with:
```tsx
      {firstRunState === "onboarding" && <OnboardingCard />}
      {firstRunState === "gathering" && <BriefingGatheringCard />}
```

- [ ] **Step 4: Run the full frontend test suite**

Run (from `frontend/`): `npm test`
Expected: all passed (harness smoke + 4 helper + 4 card tests).

- [ ] **Step 5: Lint and build**

Run: `npm run lint` — expected: no errors.
Run: `npm run build` — expected: build succeeds (no type errors; `OnboardingCard` and `resolveFirstRunState` resolve).

- [ ] **Step 6: Commit (behavior)**

```bash
git add frontend/src/lib/first-run-state.ts frontend/src/lib/first-run-state.test.ts \
  frontend/src/components/dashboard/onboarding-card.tsx \
  frontend/src/components/dashboard/onboarding-card.test.tsx \
  frontend/src/app/page.tsx
git commit -m "feat(onboarding): first-run \"connect your first source\" card"
```

---

## Verification (whole feature)

- [ ] `npm test` green (9 tests: 1 harness + 4 helper + 4 card).
- [ ] `npm run lint` clean.
- [ ] `npm run build` succeeds.
- [ ] Manual: with 0 connected sources and no briefing, the onboarding card renders with Google/GitHub/Notion buttons; clicking one starts OAuth. After connecting a source (or when a briefing exists), the onboarding card is gone (replaced by the gathering card / canvas).
