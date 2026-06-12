# Settings consolidation (4 tabs → 3) + decomposition

**Date:** 2026-06-12
**Branch:** `week2-first-run`
**Track:** Week 2 — first-run experience (Settings + onboarding UX, settings half)
**Roadmap:** `docs/superpowers/specs/2026-06-12-oss-release-audit-and-roadmap.md`
("Settings collapsed to user-language (3 tabs; progressive disclosure of trust/policy).")

## Problem

The settings page (`frontend/src/app/settings/page.tsx`, 522 lines) exposes four
engineer-language tabs:

1. **Account** — email, display name, sign out
2. **Policy** — four global autonomy radio modes (Lockdown / Approval Required /
   Suggest Only / Full Auto)
3. **Trust** — per-capability trust levels grouped by family, each with a ceiling
   dropdown and reset (granular, technical)
4. **Budget** — daily token budget

Two problems:
- **Conceptual:** Policy and Trust are two altitudes of the same idea — "how much
  can Jarvis act on its own." Policy is the coarse global posture; Trust is the
  granular per-capability detail. Presenting them as sibling tabs makes the
  technical Trust surface as prominent as the simple Policy choice, which
  overwhelms a new user.
- **Structural:** the page is 522 lines, over the 400-line component cap in
  `docs/engineering-standards.md`. `TrustCapabilityCard` is defined inline.

(Note: the OSS roadmap described "5 tabs" including a "Preferences" tab. That is
inaccurate — there is no Preferences tab in the code. The real count is 4.)

## Goals

1. Collapse to **three** user-language tabs with the per-capability Trust list
   behind progressive disclosure.
2. Decompose the over-cap page into a slim shell + focused presentational tab
   components.

## Non-goals

- No change to the policy modes, trust mechanics, budget logic, or any API call.
  Behavior of each control is preserved exactly; only grouping, labels, and
  disclosure change.
- No URL/query-param tab routing (tabs stay `useState`, as today).
- Cheap-mode documentation / budget content changes are a separate roadmap item;
  "Spending" is a label rename only.
- No summary teaser on the collapsed trust expander (deferred; plain expander).

## Target structure — three tabs

| Tab | Contents |
|-----|----------|
| **Account** | email, display name, sign out (unchanged) |
| **How Jarvis acts** | global policy radios (headline) + connective copy + collapsed per-capability Trust expander |
| **Spending** | daily token budget (renamed from "Budget"; same content) |

### "How Jarvis acts" tab layout

```
How Jarvis acts
  [connective copy]
    "Your overall posture applies to everything; per-capability trust
     fine-tunes how much Jarvis can do on its own for each kind of action."

  Overall posture
    ● Approval required   — All actions need approval
    ○ Suggest only        — Jarvis suggests, never acts
    ○ Full auto           — Jarvis acts autonomously
    ○ Lockdown            — All actions blocked

  ▸ Per-capability trust            ← collapsed by default
      (expanded: grouped-by-family TrustCapabilityCard list,
       or the "No trust data yet" empty state)
```

- The policy radios are the existing `POLICY_MODES` control, unchanged.
- The Trust list is the existing grouped-by-family `TrustCapabilityCard` render,
  unchanged, relocated inside a collapsible `TrustSection`.
- **Default state: collapsed.** A new user (no trust data) sees only the global
  posture.

## Architecture & decomposition

The shell keeps all data-fetching and state; each tab is a presentational
component fed by props (same pattern as the onboarding card).

```
src/app/settings/page.tsx          shell: fetch policy/budget/trust, own state,
                                   render PageHeader + 3-tab <Tabs>, delegate to
                                   tab components by passing data + handlers
src/components/settings/
  account-tab.tsx                  email / display name / sign out
  how-jarvis-acts-tab.tsx          policy radios + connective copy + <TrustSection>
  spending-tab.tsx                 daily budget
  trust-section.tsx                collapsible "Per-capability trust" expander
                                   wrapping the grouped-by-family list
  trust-capability-card.tsx        moved out of page.tsx (currently inline)
```

Handlers (`handlePolicyChange`, budget edit/save, `handleCeilingChange`,
`handleResetTrust`) and the `trustByFamily` grouping stay in the shell and pass
down as props. Tab components are presentational and independently testable.
After extraction, `page.tsx` is well under the 400-line cap.

### Props (interfaces the tab components expose)

- `AccountTab`: `{ email: string | null; displayName: string | null; onSignOut: () => void }`
- `SpendingTab`: `{ budgetLimit: number | null; editing: boolean; input: string;
  onEditStart: () => void; onInputChange: (v: string) => void; onSave: () => void;
  saving: boolean }` (mirrors the current budget state/handlers in the shell)
- `HowJarvisActsTab`: `{ policyMode: string; policyModes: PolicyMode[];
  onPolicyChange: (value: string) => void; policyLoading: boolean;
  trust: TrustSectionProps }`
- `TrustSection` (`TrustSectionProps`): `{ trustByFamily: Record<string,
  TrustDashboardEntry[]>; loading: boolean; onCeilingChange: (...): void;
  onReset: (...): void; ceilingLoading: string | null; resetLoading: string |
  null }` — owns its own `useState` for collapsed/expanded.

(Exact prop shapes are finalized against the current handler signatures in
`page.tsx` during implementation; the planning step pins them down.)

## Commit strategy

Per `docs/engineering-standards.md` (characterization tests before risky
changes; structural and behavioral commits separated):

1. **`test(settings)` — characterization tests** (test-only). RTL tests capturing
   CURRENT behavior against the 4-tab page: all four tabs render; switching tabs
   shows the right content; selecting a policy mode calls `setPolicyMode`; budget
   edit calls `updateBudgetLimit`; trust ceiling change and reset call their APIs.
   Locks behavior before the refactor.

2. **`refactor(settings)` — structural extraction** (no behavior change). Move the
   four tab bodies and `TrustCapabilityCard` into `src/components/settings/*`;
   `page.tsx` becomes the slim shell. The characterization tests pass UNCHANGED —
   proof the extraction preserved behavior. Still four tabs at this point.

3. **`feat(settings)` — the consolidation** (behavior). Merge Policy + Trust into
   the "How Jarvis acts" tab (headline radios + connective copy + collapsed
   `TrustSection`), rename Budget → Spending, `TABS` 4 → 3. Update the
   characterization tests to the 3-tab structure and add `TrustSection` tests.

## Test plan

`src/components/settings/trust-section.test.tsx`:
- collapsed by default — the grouped family list / capability cards are not shown
- clicking the expander reveals the grouped families (with trust data) or the
  "No trust data yet" empty state (without)

Settings characterization/behavior tests (location: `src/app/settings/` or a
`settings.test.tsx`; finalized in the plan):
- after consolidation: exactly THREE tabs render with labels Account / How Jarvis
  acts / Spending
- the "How Jarvis acts" tab shows the connective copy and the policy radios
- selecting a policy mode still calls `setPolicyMode` (behavior preserved)
- the Spending tab shows the budget and editing it still calls `updateBudgetLimit`

All via the existing vitest + RTL harness, mocking `@/lib/api`.

## Verification

- `npm test` green (characterization + trust-section + updated settings tests).
- `npm run lint` clean; `npm run build` succeeds.
- `page.tsx` under the 400-line component cap after extraction.
- Manual: three tabs; "How Jarvis acts" shows posture + collapsed trust; expanding
  reveals the capability list; Account and Spending behave as before.
