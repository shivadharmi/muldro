/**
 * The save bar's STRUCTURE and geometry.
 *
 * Its behaviour is exercised through the tab (`tabs/model-tab-save.test.tsx`);
 * what that cannot see is the class strings. `text-j-danger` survived `tsc` and
 * `eslint` for months and rendered nothing at all, because a Tailwind class is
 * a string and no compiler reads it. The last test here is the direct guard
 * against that failure mode: every colour utility the bar renders is resolved
 * against `globals.css`, so a token that does not exist fails the suite instead
 * of silently rendering transparent.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { SaveBar } from "./save-bar";

function renderBar(changed: string[] = [], saving = false) {
  const onSave = vi.fn();
  const onDiscard = vi.fn();
  const view = render(
    <SaveBar
      changed={changed}
      saving={saving}
      onSave={onSave}
      onDiscard={onDiscard}
    />,
  );
  return { ...view, onSave, onDiscard };
}

const bar = () =>
  screen.getByRole("region", { name: /save model configuration/i });
const saveButton = () =>
  screen.getByRole("button", { name: /^save changes$|^saving/i });
const discardButton = () => screen.getByRole("button", { name: /^discard$/i });

/** The 6px §9.7 dot. Decorative, so it is `aria-hidden` and has no role to
 *  query by — its presence IS the "you have unsaved work" signal. */
const dot = (container: HTMLElement) =>
  container.querySelector('span[aria-hidden="true"]');

test("clean: says so, hides the dot, and disables both actions", () => {
  const { container } = renderBar([]);

  expect(screen.getByText("No changes")).toBeInTheDocument();
  expect(dot(container)).toBeNull();
  expect(saveButton()).toBeDisabled();
  expect(discardButton()).toBeDisabled();
});

test("dirty: the dot appears, and both actions come live", () => {
  const { container } = renderBar(["Reasoning"]);

  const marker = dot(container);
  expect(marker).not.toBeNull();
  expect(marker?.getAttribute("class")).toContain("bg-j-primary");
  expect(marker?.getAttribute("class")).toContain("w-[6px]");
  expect(saveButton()).toBeEnabled();
  expect(discardButton()).toBeEnabled();
});

test("the count agrees in number with the names beside it", () => {
  const { rerender } = renderBar(["Reasoning"]);
  expect(screen.getByText(/^1 unsaved change — Reasoning$/)).toBeInTheDocument();

  rerender(
    <SaveBar
      changed={["Reasoning", "Fast", "Planner"]}
      onSave={vi.fn()}
      onDiscard={vi.fn()}
    />,
  );
  expect(
    screen.getByText(/^3 unsaved changes — Reasoning, Fast, Planner$/),
  ).toBeInTheDocument();
});

test("saving locks both actions even while dirty, and says which is running", () => {
  renderBar(["Reasoning"], true);

  expect(screen.getByRole("button", { name: /^saving/i })).toBeDisabled();
  expect(discardButton()).toBeDisabled();
});

test("the two actions report to their own handlers", async () => {
  const { onSave, onDiscard } = renderBar(["Reasoning"]);

  await userEvent.click(saveButton());
  expect(onSave).toHaveBeenCalledTimes(1);
  expect(onDiscard).not.toHaveBeenCalled();

  await userEvent.click(discardButton());
  expect(onDiscard).toHaveBeenCalledTimes(1);
  expect(onSave).toHaveBeenCalledTimes(1);
});

test("§9.7's geometry is on the section itself, not on a wrapper", () => {
  renderBar(["Reasoning"]);
  const className = bar().getAttribute("class") ?? "";

  // The separator, the fill and the padding are the three things that make it
  // read as a footer rather than as the last card in the list. Two of the three
  // are breakpoint-split by §9.10, and the mobile half is asserted alongside the
  // desktop one — an unqualified `bg-surface-2/50` would still be "contained" by
  // `sm:bg-surface-2/50`, so the pairs are named in full.
  for (const utility of [
    "border-t",
    "border-b-secondary",
    "bg-surface-2 ",
    "sm:bg-surface-2/50",
    "px-[16px]",
    "sm:px-[24px]",
    "pt-[12px]",
    "pb-[26px]",
    "sm:pb-[12px]",
    "flex",
    "items-center",
    "gap-3",
  ]) {
    expect(className).toContain(utility);
  }
  // It has to survive the scroll, or it is not a save bar.
  expect(className).toContain("sticky");
  expect(className).toContain("bottom-0");
});

/** Tailwind resolves `bg-x` from `--color-x`; a utility naming an undefined
 *  token generates no rule at all. Variant prefixes (`hover:`, `sm:`) are
 *  stripped so a hover colour is checked too. */
function colourTokensIn(root: Element): string[] {
  const skip = new Set(["transparent", "t", "b", "l", "r", "x", "y"]);
  const tokens = new Set<string>();
  for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
    for (const raw of (el.getAttribute("class") ?? "").split(/\s+/)) {
      const utility = raw.split(":").pop() ?? "";
      const match = /^(?:bg|text|border)-([a-z][a-z0-9-]*?)(?:\/\d+)?$/.exec(
        utility,
      );
      if (match && !skip.has(match[1])) tokens.add(match[1]);
    }
  }
  return Array.from(tokens);
}

test("every colour token the bar renders is defined in globals.css", () => {
  renderBar(["Reasoning"]);
  const css = readFileSync(join(process.cwd(), "src/app/globals.css"), "utf8");

  const tokens = colourTokensIn(bar());
  // A guard that found nothing to check is not a guard.
  expect(tokens).toContain("j-primary");
  expect(tokens).toContain("surface-2");

  const undefinedTokens = tokens.filter(
    (token) => !css.includes(`--color-${token}:`),
  );
  expect(undefinedTokens).toEqual([]);
});
