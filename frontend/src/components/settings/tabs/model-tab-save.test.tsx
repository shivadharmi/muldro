/**
 * What the Model tab's save bar SAYS — the count, the names, and discard.
 *
 * Split from `model-tab.test.tsx`, which covers what the tab WIRES. The seam is
 * real rather than a line-count dodge: these tests never touch the picker, the
 * 422 path or the cross-tab intent, and the wiring tests never read the bar.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  fetchModelCatalog: vi.fn(),
  fetchModelConfig: vi.fn(),
  saveModelConfig: vi.fn(),
  saveProviderCredential: vi.fn(),
  testProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
}));

import {
  card,
  config,
  discardButton,
  nudge,
  openOverrides,
  override,
  renderTab,
  saveBar,
  saveButton,
} from "./model-tab-fixtures";

beforeEach(() => {
  vi.clearAllMocks();
});

test("F3: Save is inert until something changes, and counts what did", async () => {
  await renderTab();

  expect(within(saveBar()).getByText("No changes")).toBeInTheDocument();
  expect(saveButton()).toBeDisabled();
  expect(discardButton()).toBeDisabled();

  await nudge("Balanced");
  expect(saveButton()).toBeEnabled();
  expect(within(saveBar()).getByText(/1 unsaved change\b/)).toBeInTheDocument();

  await nudge("Fast");
  expect(within(saveBar()).getByText(/2 unsaved changes/)).toBeInTheDocument();
});

test("the save bar names the changed tiers, not just their number", async () => {
  await renderTab();
  await nudge("Fast");
  await nudge("Reasoning");

  // §9.6 substitutes a warned card's meta row, so the per-card "Changed — not
  // saved" marker cannot be relied on. The names live here instead.
  const bar = within(saveBar()).getByText(/2 unsaved changes/);
  expect(bar).toHaveTextContent("Reasoning");
  expect(bar).toHaveTextContent("Fast");
  expect(bar).not.toHaveTextContent("Balanced");
});

test("a REMOVED override is still named, though it is gone from the draft", async () => {
  // The pin for deriving `changed` from `dirtyKeys` rather than by walking
  // `draft.tiers` + `draft.agent_overrides` — the obvious shape, and the one
  // that reads more directly. A removal is dirty and is no longer IN the draft,
  // so a walk finds nothing to name: the founder deleting a saved Planner
  // override would see "1 unsaved change" with no name, press Save, and lose a
  // binding the bar never identified.
  await renderTab(config({ agent_overrides: [override("planner")] }));
  await openOverrides();

  await userEvent.click(
    within(card("Planner")).getByRole("button", { name: /^remove$/i }),
  );

  expect(screen.queryByRole("region", { name: "Planner" })).toBeNull();
  const bar = within(saveBar()).getByText(/1 unsaved change\b/);
  expect(bar).toHaveTextContent("Planner");
});

test("an EDITED override is named by its display name, not its slug", async () => {
  // **A3**: the bar reads the catalog's `display_name`, so a scope key that
  // happens to be a slug never reaches the screen.
  await renderTab(config({ agent_overrides: [override("planner")] }));
  await openOverrides();

  await userEvent.type(
    within(card("Planner")).getByLabelText("Max tokens"),
    "0",
  );

  const bar = within(saveBar()).getByText(/1 unsaved change\b/);
  expect(bar).toHaveTextContent("Planner");
  expect(bar).not.toHaveTextContent("planner");
});

test("Discard restores the saved values and empties the count", async () => {
  await renderTab();
  const maxTokens = within(card("Balanced")).getByLabelText("Max tokens");

  await nudge("Balanced");
  expect(maxTokens).toHaveValue(40960);

  await userEvent.click(discardButton());
  expect(maxTokens).toHaveValue(4096);
  expect(within(saveBar()).getByText("No changes")).toBeInTheDocument();
  expect(saveButton()).toBeDisabled();
});

test("Discard brings a removed override back, count and all", async () => {
  await renderTab(config({ agent_overrides: [override("planner")] }));
  await openOverrides();
  await userEvent.click(
    within(card("Planner")).getByRole("button", { name: /^remove$/i }),
  );

  await userEvent.click(discardButton());
  expect(within(saveBar()).getByText("No changes")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Planner" })).toBeInTheDocument();
});
