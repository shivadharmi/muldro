/**
 * §9.5 — the tier card in its healthy state: header, meta row, pass-through.
 *
 * The notice states (§9.6 warning, §4.4 rejection) are a separate spec file,
 * `tier-card-notices.test.tsx`; both share `tier-card-fixtures.tsx`.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect } from "vitest";

import { ANTHROPIC, FAST_BINDING, model, renderCard } from "./tier-card-fixtures";

// ── §9.5 meta row: right slot is exclusive ────────────────────────────────
// The hint states a fact about the model, the marker states a fact about the
// founder's edit, and the edit is the newer of the two.

test("a dirty tier shows the changed marker instead of the capability hint", () => {
  renderCard({ dirty: true });
  expect(screen.getByText("Changed — not saved")).toBeInTheDocument();
  expect(screen.queryByText(/do not accept temperature/)).toBeNull();
});

test("a clean tier shows the capability hint instead of the changed marker", () => {
  renderCard();
  expect(
    screen.getByText("Adaptive-thinking models do not accept temperature."),
  ).toBeInTheDocument();
  expect(screen.queryByText("Changed — not saved")).toBeNull();
});

// The hint is a claim about `accepts_temperature`, not about the thinking
// style — a model that DOES accept temperature must never carry it.
test("a model that accepts temperature carries no capability hint", () => {
  renderCard({ binding: FAST_BINDING });
  expect(screen.queryByText(/do not accept temperature/)).toBeNull();
});

// ── §9.5 meta row: sourced from the selected model ────────────────────────

test("the meta row renders context, per-Mtok costs and thinking style", () => {
  renderCard();
  expect(screen.getByText("200K context")).toBeInTheDocument();
  // The catalog stores per-1k; the row states per-Mtok, so 0.005 → $5.
  expect(screen.getByText("$5 / $25 per Mtok")).toBeInTheDocument();
  expect(screen.getByText("Adaptive thinking")).toBeInTheDocument();
});

test("sub-cent per-1k costs survive the ×1000 without rounding to zero", () => {
  renderCard({ binding: FAST_BINDING });
  expect(screen.getByText("128K context")).toBeInTheDocument();
  expect(screen.getByText("$0.59 / $0.79 per Mtok")).toBeInTheDocument();
  expect(screen.getByText("No thinking")).toBeInTheDocument();
});

// The meta label must name the provider, not the internal enum. This one is
// not latent — it renders on both Gemini models today.
test("the thinking-style label never renders an internal enum name", () => {
  const gemini = model({
    provider: "google_genai",
    model_id: "gemini-3-pro",
    display_name: "Gemini 3 Pro",
    thinking_style: "gemini",
  });
  renderCard({
    binding: { provider: "google_genai", model_id: "gemini-3-pro" },
    models: [gemini],
    providers: [{ ...ANTHROPIC, provider: "google_genai", display_name: "Google" }],
  });
  expect(screen.getByText("Gemini thinking")).toBeInTheDocument();
  expect(screen.queryByText(/Provider thinking/)).toBeNull();
  expect(
    screen.getByText("Gemini-thinking models do not accept temperature."),
  ).toBeInTheDocument();
});

// An unmapped style is shown raw rather than mislabelled, and its hint drops
// to a claim that is true of any model.
test("an unknown thinking style asserts nothing it cannot support", () => {
  renderCard({ models: [model({ thinking_style: "brand_new_style" })] });
  expect(screen.getByText("brand_new_style")).toBeInTheDocument();
  expect(
    screen.getByText("These models do not accept temperature."),
  ).toBeInTheDocument();
});

// A retired model has no price to print. Printing a zero would assert one.
test("a model absent from the catalog says so rather than printing zeroes", () => {
  renderCard({ binding: { model_id: "retired-model" } });
  expect(screen.getByText(/no longer in the catalog/)).toBeInTheDocument();
  expect(screen.queryByText(/per Mtok/)).toBeNull();
});

// ── Header ─────────────────────────────────────────────────────────────────

test("the agent chips list exactly the agents on this tier", () => {
  renderCard({ binding: { scope_key: "balanced" } });
  const chips = screen.getByRole("list", { name: "Agents on Balanced" });
  expect(
    Array.from(chips.querySelectorAll("li")).map((li) => li.textContent),
  ).toEqual(["Perceiver", "Librarian"]);
  expect(screen.queryByText("Planner")).toBeNull();
  expect(screen.queryByText("Persona")).toBeNull();
});

test("a tier with no agents renders no empty chip list", () => {
  renderCard({ binding: { scope_key: "reasoning" }, agents: [] });
  expect(screen.queryByRole("list")).toBeNull();
});

// A3: the heading is sentence case, and it comes from the binding itself — a
// separate `tier` prop could name a tier the grid below is not editing.
test("the card is headed by its own binding's tier, in sentence case", () => {
  renderCard({ binding: FAST_BINDING });
  expect(screen.getByRole("heading", { name: "Fast" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Fast" })).toBeInTheDocument();
});

test("the description renders beside the tier name", () => {
  renderCard({ description: "Cheap and quick. Classification and triage." });
  expect(
    screen.getByText("Cheap and quick. Classification and triage."),
  ).toBeInTheDocument();
});

// ── Pass-through ───────────────────────────────────────────────────────────

test("the card edits nothing itself — the grid's patches pass straight through", async () => {
  const { value, onChange, onOpenPicker } = renderCard();
  const before = { ...value };
  await userEvent.click(screen.getByLabelText(/^Model/));
  expect(onOpenPicker).toHaveBeenCalledTimes(1);
  await userEvent.selectOptions(screen.getByLabelText("Effort"), "low");
  expect(onChange).toHaveBeenCalledWith({ effort: "low" });
  expect(value).toEqual(before);
});

test("disabled turns off every editable control without unmounting any", () => {
  renderCard({ disabled: true });
  expect(screen.getByLabelText(/^Model/)).toBeDisabled();
  expect(screen.getByLabelText("Effort")).toBeDisabled();
  expect(screen.getByLabelText("Max tokens")).toBeDisabled();
  expect(screen.getByLabelText("Temperature")).toBeDisabled();
});
