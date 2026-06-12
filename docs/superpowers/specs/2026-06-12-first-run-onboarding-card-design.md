# First-run onboarding card + frontend test infrastructure

**Date:** 2026-06-12
**Branch:** `week2-first-run`
**Track:** Week 2 — first-run experience (Settings + onboarding UX, onboarding half)
**Roadmap:** `docs/superpowers/specs/2026-06-12-oss-release-audit-and-roadmap.md`
("Onboarding: first-load guided 'connect your first source' card.")

## Problem

A brand-new user lands on the workspace with zero connected sources. Today they
see `GreetingHero` + `BriefingGatheringCard` ("Gathering data for your first
briefing"), which secondarily links to `/integrations`. That card is
briefing-centric: it shows whenever no briefing surface exists, which is also
true transiently for established users. There is no dedicated, guided first-run
moment that makes connecting the first source the obvious next action.

Separately, the frontend has **no test infrastructure at all** — no test
runner, no React Testing Library, zero `.test.tsx` files — so new frontend
behavior currently ships unverified except by lint + build.

## Goals

1. A dedicated first-run onboarding card that guides the user to connect their
   first source, shown only when no source is connected.
2. Stand up reusable frontend test infrastructure (vitest + RTL) and TDD the
   card.

## Non-goals

- No manual dismiss / persisted "skip onboarding" flag. The card is purely a
  function of connection state; it disappears when a source connects.
- No redesign of the integrations page or the OAuth flow. The card reuses the
  existing `getAuthUrl` mechanism.
- No change to `BriefingGatheringCard` beyond it no longer being the 0-source
  state.

## Behavior — three mutually-exclusive first-load states

Keyed on `sourceCount` (already computed in `page.tsx` from
`system.observations`) and whether a briefing surface exists:

| State | Condition | Renders |
|-------|-----------|---------|
| **A — first run** | `sourceCount === 0` | `GreetingHero` + **OnboardingCard** (new) |
| **B — warming up** | `sourceCount > 0 && !briefing` | `GreetingHero` + `BriefingGatheringCard` (exists) |
| **C — active** | `briefing` exists | `GreetingHero` + `WorkspaceCanvas` |

State C takes precedence over A/B: once a briefing exists it renders regardless
of source count (an active user who later disconnects all sources should not be
thrown back to onboarding). Precedence order: **C, then A, then B.**

## Components

### Pure helper — `src/lib/first-run-state.ts`

The testable decision, extracted so it can be unit-tested without rendering and
so `page.tsx` stays declarative (replaces the ad-hoc `showBriefingGathering =
!briefing`).

```ts
export type FirstRunState = "onboarding" | "gathering" | "active";

export function resolveFirstRunState(
  sourceCount: number,
  hasBriefing: boolean,
): FirstRunState;
```

Logic: `hasBriefing` → `"active"`; else `sourceCount === 0` → `"onboarding"`;
else `"gathering"`.

### `src/components/dashboard/onboarding-card.tsx`

Presentational card following `BriefingGatheringCard`'s design tokens
(`bg-surface-1`, `border-b-secondary`, `rounded-[var(--radius-xl)]`, primary
button styling).

- A local `PRIMARY_SOURCES` constant lists the three first-run sources with the
  provider slug used by `getAuthUrl`, the logo component, and a one-line hint:
  - `google` — "Gmail + Calendar" — `GoogleLogo`
  - `github` — "repos" — `GitHubLogo`
  - `notion` — "docs" — `NotionLogo`
  Logos come from the existing `@/components/integrations/logos` set already
  used by the integrations page. **Slug note:** the backend `oauth_authorize`
  route (`routes_auth.py`) only implements `google`, `github`, `notion`,
  `atlassian` — Slack has no authorize branch and would 400, so it is not a
  first-run source. The Google slug is `google` (not the `google-workspace`
  UI logo-map key).
- `handleConnect(provider)`: `const { url } = await getAuthUrl(provider);
  window.location.assign(url)` — identical to the integrations page handler.
  This runs in a click handler, not the render body, so it does not violate the
  "no side effects during render" rule.
- Per-button `connecting` state disables the button and shows a spinner while
  the auth URL is fetched.
- On `getAuthUrl` rejection: surface an error via the existing `useToast`, clear
  the `connecting` state, do **not** redirect.
- A "See all integrations →" link to `/integrations`.

Layout:

```
┌────────────────────────────────────────────────────────┐
│  ◐ Connect your first source                           │
│  Jarvis gets sharper the more it can see.              │
│                                                        │
│  [ G  Google ]   [ ⌥ GitHub ]   [ # Slack ]            │
│    Gmail + Cal       repos        messages             │
│                                                        │
│  See all integrations →                                │
└────────────────────────────────────────────────────────┘
```

### `page.tsx` wiring

Replace `const showBriefingGathering = !briefing;` and the inline
`{showBriefingGathering && <BriefingGatheringCard />}` with a single
`firstRunState = resolveFirstRunState(sourceCount, Boolean(briefing))` switch
that renders `OnboardingCard`, `BriefingGatheringCard`, or neither (canvas only).

## Frontend test infrastructure

Dev dependencies (React 19 compatible):
`vitest`, `@vitejs/plugin-react`, `vite-tsconfig-paths`, `jsdom`,
`@testing-library/react` (v16), `@testing-library/jest-dom`,
`@testing-library/user-event`.

- `vitest.config.ts`: jsdom environment, `globals: true`, `react()` +
  `tsconfigPaths()` plugins (resolves the `@/` alias), `setupFiles:
  ['./vitest.setup.ts']`.
- `vitest.setup.ts`: `import "@testing-library/jest-dom";`
- `package.json` scripts: `"test": "vitest run"`, `"test:watch": "vitest"`.

## Test plan (TDD)

`src/lib/first-run-state.test.ts` (pure):
- 0 sources, no briefing → `"onboarding"`
- 2 sources, no briefing → `"gathering"`
- briefing present (any source count) → `"active"`
- 0 sources but briefing present → `"active"` (precedence)

`src/components/dashboard/onboarding-card.test.tsx` (RTL, mocking `getAuthUrl`
and `window.location.assign`):
- renders the three primary sources (Google, GitHub, Notion)
- clicking a source calls `getAuthUrl` with that provider slug (`google`,
  `github`, `notion`) and redirects to the returned URL
- `getAuthUrl` rejection surfaces a toast and does not redirect
- "See all integrations" links to `/integrations`

## Commits

1. `test(frontend): add vitest + React Testing Library harness` — infra
   (structural): deps, config, setup, scripts, one sanity test.
2. `feat(onboarding): first-run "connect your first source" card` — behavior:
   `first-run-state.ts` helper, `onboarding-card.tsx`, `page.tsx` wiring, and
   the two test files above.

Structural (harness) is separated from behavior (the card) per
`docs/engineering-standards.md`.

## Verification

- `npm test` green (new tests + sanity test).
- `npm run lint` and `npm run build` clean.
- Visual check of the three states.
