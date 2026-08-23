/**
 * §9.10 — the settings surface below and above the `sm` breakpoint.
 *
 * **What these tests are.** They pin the CLASS CONTRACT that produces §9.10's
 * metrics; they do not render at 390px or at 1024px, and they do not measure
 * anything. jsdom has no layout engine and loads no stylesheet, so
 * `getBoundingClientRect()` is all zeros and no media query is ever evaluated —
 * an assertion on a computed pixel height would pass against a control carrying
 * no height class at all. **The pixel result is verified in a browser**; what is
 * verified here is that the responsive classes are present, on the right
 * elements, in the right direction (mobile unprefixed, desktop on `sm:`).
 *
 * The direction is the part worth a test. Every §9.10 override is the UNPREFIXED
 * utility with the desktop value on `sm:`, so the single likeliest regression —
 * writing `sm:h-[44px]` and calling the touch target done — is a mistake that
 * looks correct in a diff and is invisible in jsdom. `expectTouchTarget` refuses
 * a `sm:`-prefixed match for exactly that reason.
 *
 * The shell's own chrome — the pushed header, the body padding, the full sheet —
 * needs the whole mocked settings modal and lives in `responsive-shell.test.tsx`.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import type { CatalogProvider, ProviderStatus } from "@/lib/types";
import { AgentOverrides } from "./model/agent-overrides";
import { ProviderCredentialForm } from "./providers/provider-credential-form";
import { ProviderRow } from "./providers/provider-row";
import { RemoveConfirmation } from "./providers/remove-confirmation";
import { SaveBar } from "./model/save-bar";
import { SettingsRail } from "./settings-rail";
import { renderCard } from "./model/tier-card-fixtures";
import {
  DESKTOP_WIDTH,
  MOBILE_WIDTH,
  columnsAt,
  expectAllTouchTargets,
  expectBaseClass,
  expectSmClass,
  expectTouchTarget,
  spanOf,
} from "./responsive-fixtures";

/** The binding grid inside whatever was just rendered. It is the only `grid`
 *  on the card, and it is addressed by that rather than by a test id so the
 *  assertion is about the element the founder actually sees. */
function bindingGrid(container: HTMLElement): HTMLElement {
  const grid = container.querySelector<HTMLElement>("div.grid");
  expect(grid, "the tier card rendered no binding grid").not.toBeNull();
  return grid as HTMLElement;
}

/** The four cells, in §9.5's order: Model, Effort, Max tokens, Temperature. */
function cells(grid: HTMLElement): HTMLElement[] {
  return Array.from(grid.children) as HTMLElement[];
}

// ── At 390px (below `sm`) ──────────────────────────────────────────────────

test(`at ${MOBILE_WIDTH}px the field grid is two columns, and Model and Temperature span both`, () => {
  const { container } = renderCard();
  const grid = bindingGrid(container);

  expect(columnsAt(grid, "base")).toBe(2);
  expectBaseClass(grid, "gap-[10px]", "the mobile grid gap (§9.10)");

  const [model, effort, maxTokens, temperature] = cells(grid);
  expect(cells(grid)).toHaveLength(4);

  // The two wide fields take a whole row each; the two numeric ones pair up.
  // 2 + 1 + 1 + 2 = 6 tracks over 2 columns = exactly three full rows, so
  // nothing is left half-width against an empty cell.
  expect(spanOf(model, "base")).toBe(2);
  expect(spanOf(effort, "base")).toBe(1);
  expect(spanOf(maxTokens, "base")).toBe(1);
  expect(spanOf(temperature, "base")).toBe(2);
  const tracks = cells(grid).reduce((n, cell) => n + spanOf(cell, "base"), 0);
  expect(tracks % columnsAt(grid, "base")).toBe(0);
});

test(`at ${MOBILE_WIDTH}px every control in the binding grid is a 44px touch target`, () => {
  const { container } = renderCard();
  const grid = bindingGrid(container);

  // Four cells, four controls — the grid DISABLES an unsupported control rather
  // than unmounting it (F4), so the count is four whatever the model supports.
  expectAllTouchTargets(grid, "a binding-grid control");

  // And the desktop half of the pair is on `sm:`, so the 44px above is the
  // mobile value and not a control that simply got taller everywhere.
  for (const control of grid.querySelectorAll("button, input, select")) {
    expectSmClass(control, "h-[36px]", "the desktop control height (§9.3)");
  }
});

/** The card itself — `TierCard`'s root, and the only `<section>` `renderCard`
 *  mounts. */
function tierCard(container: HTMLElement): HTMLElement {
  const card = container.querySelector<HTMLElement>("section");
  expect(card, "renderCard mounted no card").not.toBeNull();
  return card as HTMLElement;
}

test(`at ${MOBILE_WIDTH}px the tier card is padded 14/16/12, not 13/20/11`, () => {
  const card = tierCard(renderCard().container);

  // The horizontal half is the one that costs something. The shell's body
  // gutter drops to 16px below `sm`, so a card that kept `px-[20px]` there
  // would sit its controls 36px from a 390px screen edge — a tenth of the
  // grid's width spent on inset, at the width that has least to spare.
  expectBaseClass(card, "px-[16px]", "the tier card's mobile gutter");
  expectSmClass(card, "px-[20px]", "the tier card's desktop gutter");
  expectBaseClass(card, "pt-[14px]", "the tier card's mobile top padding");
  expectSmClass(card, "pt-[13px]", "the tier card's desktop top padding");
  expectBaseClass(card, "pb-[12px]", "the tier card's mobile bottom padding");
  expectSmClass(card, "pb-[11px]", "the tier card's desktop bottom padding");
});

test(`at ${MOBILE_WIDTH}px the meta row's line-height opens up, and only below \`sm\``, () => {
  // The row the card ends on: context · price · thinking style. It is the only
  // `border-t` inside the card, which is also what makes it the meta row —
  // §9.6 SUBSTITUTES its contents rather than adding a second rule.
  const meta =
    tierCard(renderCard().container).querySelector<HTMLElement>(".border-t");
  expect(meta, "the card rendered no meta row").not.toBeNull();
  const el = meta as HTMLElement;

  // At 390px those three facts do not fit on one line, so the row's spans
  // compress and their text wraps INSIDE each span — at the desktop
  // line-height two wrapped lines of 11.5px text read as one smudge.
  expectBaseClass(el, "leading-[1.6]", "the meta row's mobile line-height");

  // `inherit` and not a literal: the row carried NO line-height before this
  // pass, so any other value would silently restyle the desktop rendering
  // while claiming to be a mobile-only override.
  expectSmClass(el, "leading-[inherit]", "the meta row deferring at `sm`");
});

test(`at ${MOBILE_WIDTH}px the override card is padded like the tier card`, async () => {
  // The two sit in ONE stack, so a 20px inset here against a 16px one there
  // steps the left edge in and out down the column. Asserted against the same
  // literals rather than against `tierCard()`'s classes: comparing the two
  // components to each other would let them drift from §9.10 together while
  // still agreeing, which is the one failure this cannot afford to miss.
  render(
    <AgentOverrides
      agents={[{ name: "planner", display_name: "Planner", tier: "reasoning" }]}
      overrides={[
        {
          scope_type: "agent",
          scope_key: "planner",
          provider: "anthropic",
          model_id: "claude-opus-4-5",
          effort: "high",
          max_tokens: 8192,
          temperature: null,
        },
      ]}
      tiers={[]}
      models={[]}
      providers={[]}
      dirty={() => false}
      rejection={() => undefined}
      onAdd={vi.fn()}
      onChange={vi.fn()}
      onRemove={vi.fn()}
      onOpenPicker={vi.fn()}
    />,
  );
  // Collapsed by default — the override is the exception, not the default view.
  await userEvent.click(screen.getByRole("button", { name: /per-agent overrides/i }));

  const card = screen.getByRole("region", { name: "Planner" });
  expectBaseClass(card, "px-[16px]", "the override card's mobile gutter");
  expectSmClass(card, "px-[20px]", "the override card's desktop gutter");
  expectBaseClass(card, "pt-[14px]", "the override card's mobile top padding");
  expectBaseClass(card, "pb-[12px]", "the override card's mobile bottom padding");
  expectSmClass(card, "pb-[13px]", "the override card's desktop bottom padding");
});

test(`at ${MOBILE_WIDTH}px the overrides disclosure is a 44px touch target`, () => {
  // FOUND IN A BROWSER, NOT HERE. This control declares no height, so it was
  // sized by its own 12.5px line box and measured 19px at 390px — while every
  // test in this file passed. `expectTouchTarget` reads a DECLARED height (see
  // its note in responsive-fixtures.ts) and jsdom computes no layout, so a
  // control that is too small purely by OMISSION is invisible to both. The
  // assertion below only works because the fix declares a floor; if someone
  // removes the class and reverts to implicit sizing, this fails — which is the
  // only shape of guard available for this class of defect.
  render(
    <AgentOverrides
      agents={[{ name: "planner", display_name: "Planner", tier: "reasoning" }]}
      overrides={[]}
      tiers={[]}
      models={[]}
      providers={[]}
      dirty={() => false}
      rejection={() => undefined}
      onAdd={vi.fn()}
      onChange={vi.fn()}
      onRemove={vi.fn()}
      onOpenPicker={vi.fn()}
    />,
  );
  expectTouchTarget(
    screen.getByRole("button", { name: /per-agent overrides/i }),
    "the per-agent overrides disclosure",
  );
});

test(`at ${MOBILE_WIDTH}px the tier card's own actions are 44px touch targets`, () => {
  // A warned card — its Connect button is the card's only chrome-level control.
  const { container } = renderCard({
    binding: { scope_key: "fast", provider: "groq", model_id: "llama-3.3-70b" },
    warning: {
      scope_type: "tier",
      scope_key: "fast",
      provider: "groq",
      code: "provider_not_configured",
      message: "Groq is not connected.",
    },
  });
  expectAllTouchTargets(container, "a tier-card control");
});

test(`at ${MOBILE_WIDTH}px the save bar's buttons are 44px, and the bar clears the home indicator`, () => {
  const { container } = render(
    <SaveBar changed={["Reasoning"]} onDiscard={vi.fn()} onSave={vi.fn()} />,
  );
  expectAllTouchTargets(container, "a save-bar button");

  const bar = screen.getByRole("region", { name: /save model configuration/i });
  // The bottom padding is the whole point of the row: on a gesture-navigation
  // phone the home indicator is drawn OVER the layout, so 12px would put
  // `Save changes` under the founder's own swipe target.
  expectBaseClass(bar, "pb-[26px]", "the save bar's home-indicator clearance");
  expectSmClass(bar, "pb-[12px]", "the save bar's desktop bottom padding");
  expectBaseClass(bar, "px-[16px]", "the save bar's mobile gutter");
  // Opaque below `sm`, translucent above: over a full-bleed sheet a 50% fill
  // lets the scrolling cards read through the bar they scroll under.
  expectBaseClass(bar, "bg-surface-2", "the save bar's mobile fill");
  expectSmClass(bar, "bg-surface-2/50", "the save bar's desktop fill");
});

test(`at ${MOBILE_WIDTH}px every rail row is a 44px touch target`, () => {
  const { container } = render(
    <SettingsRail activeTab="model" onSelect={vi.fn()} providerCounts={null} />,
  );
  // Below `sm` the rail IS the sheet's root view, so each row is the only way
  // into a tab — a 33px row would be the one unreachable control on the surface.
  expectAllTouchTargets(container, "a settings-rail row");
});

// ── The migrated provider controls ─────────────────────────────────────────

const ANTHROPIC: CatalogProvider = {
  provider: "anthropic",
  display_name: "Anthropic",
  auth_kind: "api_key",
  credential_fields: [
    { key: "api_key", label: "API key", kind: "secret", required: true, placeholder: null },
    { key: "base_url", label: "Base URL", kind: "url", required: false, placeholder: null },
  ],
  model_count: 4,
  docs_url: null,
};

const CONFIGURED: ProviderStatus = {
  provider: "anthropic",
  configured: true,
  status: "valid",
  source: "workspace",
  base_url: null,
  extra_config_public: {},
  extra_config_secret_keys: [],
  catalogued: true,
};

test(`at ${MOBILE_WIDTH}px the provider row's actions are 44px touch targets`, () => {
  const { container } = render(
    <ProviderRow
      status={CONFIGURED}
      catalog={ANTHROPIC}
      expanded={false}
      onToggle={vi.fn()}
      onTest={vi.fn()}
      onRemove={vi.fn()}
    />,
  );
  // Test, Edit and Remove — the three that used to carry a hand-rolled copy of
  // §9.3 rather than importing it.
  expect(container.querySelectorAll("button")).toHaveLength(3);
  expectAllTouchTargets(container, "a provider-row action");
});

test(`at ${MOBILE_WIDTH}px the credential form's fields and Save are 44px touch targets`, () => {
  const { container } = render(
    <ProviderCredentialForm
      provider={ANTHROPIC}
      status={null}
      busy={false}
      onSubmit={vi.fn()}
    />,
  );
  expectAllTouchTargets(container, "a credential-form control");
});

test(`at ${MOBILE_WIDTH}px both removal answers are 44px touch targets`, () => {
  const { container } = render(
    <RemoveConfirmation
      pending={{ provider: "anthropic", name: "Anthropic", prompt: "Remove?" }}
      onCancel={vi.fn()}
      onConfirm={vi.fn()}
    />,
  );
  // The destructive answer especially: it is the one a mis-tap cannot undo.
  expectAllTouchTargets(container, "a removal answer");
  expectTouchTarget(
    screen.getByRole("button", { name: /remove anyway/i }),
    "the destructive removal answer",
  );
});

// ── At 1024px (`sm` and up) ────────────────────────────────────────────────

test(`at ${DESKTOP_WIDTH}px the field grid is four columns and nothing wraps (L1)`, () => {
  const { container } = renderCard();
  const grid = bindingGrid(container);

  // §9.5's four tracks — Model wide, then Effort, Max tokens, Temperature.
  expect(columnsAt(grid, "sm")).toBe(4);

  // L1 is "the row does not wrap", and with a CSS grid that is arithmetic: four
  // cells occupying one track each is exactly the four tracks available. A cell
  // that kept its mobile `col-span-2` would make five, push the last field onto
  // a second row, and reintroduce the wrap the redesign removed.
  const spans = cells(grid).map((cell) => spanOf(cell, "sm"));
  expect(spans).toEqual([1, 1, 1, 1]);
  expect(spans.reduce((a, b) => a + b, 0)).toBe(columnsAt(grid, "sm"));

  // A `flex-wrap` anywhere on the row would wrap regardless of the tracks —
  // this is the layout the grid REPLACED, so its absence is worth stating.
  expect(grid.className).not.toContain("flex");
  expectSmClass(grid, "gap-[12px]", "the desktop grid gap");
});

test(`at ${DESKTOP_WIDTH}px the controls shrink back to §9.3's dense sizes`, () => {
  const { container } = renderCard();
  for (const control of bindingGrid(container).querySelectorAll("button, input, select")) {
    // Each pair is asserted whole: a control carrying only the 44px half would
    // be 44px on a laptop too, which is not §9.3 and reads as a broken row.
    // The HEIGHT pair is on `ctl`'s shared base, so it holds for every control
    // in the grid whatever state it is in.
    expectBaseClass(control, "h-[44px]", "the mobile control height");
    expectSmClass(control, "h-[36px]", "the desktop control height");

    // The TYPE SIZE does not, and deliberately: `ctl-off` — the disabled
    // rendering of a capability the selected model does not support (F4) — is a
    // single 12px size at both breakpoints, because it holds a two-word
    // placeholder rather than an editable value. Asserting one pair across both
    // states would either fail here or force `ctl-off` to grow a size it has no
    // use for, so the branch is named instead of flattened.
    const classes = control.getAttribute("class") ?? "";
    if (classes.includes("text-[12px]")) continue;
    expectBaseClass(control, "text-[15px]", "the mobile control type size");
    expectSmClass(control, "text-[14px]", "the desktop control type size");
  }
});
