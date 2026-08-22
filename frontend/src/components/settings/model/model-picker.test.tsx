import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { ModelPicker } from "./model-picker";
import {
  ANTHROPIC_OPUS,
  GEMINI_PRO,
  MODELS,
  OPENAI_GPT,
  PROVIDERS,
  STATUSES,
} from "./model-picker-fixtures";

/**
 * Rendered rows, in the order the keyboard walks them.
 *
 * For `tier="reasoning"` that is: Suggested (Opus, GPT, Gemini), then Anthropic
 * (Opus, Sonnet), OpenAI (GPT), Google (Gemini) — a suggested model appears
 * twice by design, which is why row ids are group-scoped.
 */
function optionTexts(): string[] {
  return screen.getAllByRole("option").map((el) => el.textContent ?? "");
}

function renderPicker(
  over: { tier?: string; selectedProvider?: string | null; selectedModelId?: string | null } = {},
) {
  const onSelect = vi.fn();
  const onClose = vi.fn();
  const onBrowseProviders = vi.fn();
  render(
    <ModelPicker
      open
      tier={over.tier ?? "reasoning"}
      selectedProvider={over.selectedProvider === undefined ? "anthropic" : over.selectedProvider}
      selectedModelId={
        over.selectedModelId === undefined ? "claude-opus-4-5" : over.selectedModelId
      }
      models={MODELS}
      providers={PROVIDERS}
      providerStatuses={STATUSES}
      onSelect={onSelect}
      onClose={onClose}
      onBrowseProviders={onBrowseProviders}
    />,
  );
  return { onSelect, onClose, onBrowseProviders, user: userEvent.setup() };
}

// ── Search ─────────────────────────────────────────────────────────────────

test("a term matching another provider's model finds it across group boundaries", async () => {
  const { user } = renderPicker();
  expect(optionTexts().some((t) => t.includes("Claude Opus 4.5"))).toBe(true);

  await user.keyboard("gpt");

  const texts = optionTexts();
  // `every` on an empty list is vacuously true — the length assertion is what
  // makes this a filter test rather than a "renders nothing" test.
  expect(texts.length).toBeGreaterThan(0);
  expect(texts.every((t) => t.includes("GPT-5.2"))).toBe(true);
  expect(texts.some((t) => t.includes("Claude"))).toBe(false);
});

test("a multi-word query ANDs its terms rather than matching one string", async () => {
  const { user } = renderPicker();

  // "anthropic" only ever matches the provider column and "sonnet" only the
  // name — never adjacent, so a concatenated `includes` finds nothing here.
  await user.keyboard("anthropic sonnet");

  const texts = optionTexts();
  expect(texts.length).toBeGreaterThan(0);
  expect(texts.every((t) => t.includes("Claude Sonnet 4.6"))).toBe(true);

  await user.clear(screen.getByRole("combobox"));
  await user.keyboard("anthropic gpt");
  expect(screen.queryAllByRole("option")).toHaveLength(0);
});

test("numeric facts are searchable — context window and price", async () => {
  const { user } = renderPicker();

  await user.keyboard("1000000");
  expect(optionTexts().length).toBeGreaterThan(0);
  expect(optionTexts().every((t) => t.includes("Gemini 3 Pro"))).toBe(true);

  await user.clear(screen.getByRole("combobox"));
  await user.keyboard("$1.25");
  expect(optionTexts().length).toBeGreaterThan(0);
  expect(optionTexts().every((t) => t.includes("GPT-5.2"))).toBe(true);
});

test("the empty state is a status message OUTSIDE the listbox", async () => {
  const { user } = renderPicker();
  await user.keyboard("zzzz");

  const empty = screen.getByRole("status");
  expect(empty).toHaveTextContent("No models match.");
  // A listbox whose only child is prose announces as a broken list.
  expect(screen.getByRole("listbox")).not.toContainElement(empty);
});

// ── Footer: unconnected providers are surfaced, not filtered away ───────────

test("the footer NAMES the providers that are not connected and can route to them", async () => {
  const { onBrowseProviders, user } = renderPicker();

  // A count alone still leaves the founder guessing which models are missing.
  expect(screen.getByText("2 providers not connected: Mistral, Cohere")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Browse all providers" }));
  expect(onBrowseProviders).toHaveBeenCalledTimes(1);
});

// ── Keyboard ───────────────────────────────────────────────────────────────

test("arrow keys move the active row and Enter selects that model", async () => {
  const { onSelect, onClose, user } = renderPicker();

  const input = screen.getByRole("combobox");
  const rows = screen.getAllByRole("option");
  expect(input).toHaveAttribute("aria-activedescendant", rows[0].id);

  await user.keyboard("{ArrowDown}");
  expect(input).toHaveAttribute("aria-activedescendant", rows[1].id);

  await user.keyboard("{ArrowUp}{ArrowDown}{ArrowDown}");
  expect(input).toHaveAttribute("aria-activedescendant", rows[2].id);

  await user.keyboard("{Enter}");
  // Suggested group order is the catalog's: Opus, GPT, Gemini.
  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(onSelect).toHaveBeenCalledWith(GEMINI_PRO);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("the cursor opens ON the bound model, not at the top of the list", () => {
  renderPicker({ selectedProvider: "google_genai", selectedModelId: "gemini-3-pro" });

  const rows = screen.getAllByRole("option");
  // Gemini is third in the Suggested group; reopening a bound tier should put
  // the cursor where the check already is.
  expect(screen.getByRole("combobox")).toHaveAttribute("aria-activedescendant", rows[2].id);
  expect(rows[2]).toHaveAttribute("aria-selected", "true");
});

test("Enter on the footer button is left alone — it must not rebind the tier", () => {
  const { onSelect, onClose } = renderPicker();
  const browse = screen.getByRole("button", { name: "Browse all providers" });
  browse.focus();

  // The palette's key handler is document-wide, so an unscoped Enter branch
  // would both suppress the button's own activation and bind whatever row the
  // cursor sat on. `dispatchEvent` returns false when default was prevented.
  const delivered = fireEvent.keyDown(browse, { key: "Enter" });

  expect(delivered).toBe(true);
  expect(onSelect).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

test("Escape closes without touching the binding, and marks the event handled", async () => {
  // The settings shell closes the WHOLE dialog on an Escape it sees unhandled.
  // Registered before the palette mounts and in the bubble phase, as the shell
  // is — the palette only beats it to the event from the capture phase.
  const seen: boolean[] = [];
  const shell = (e: KeyboardEvent) => {
    if (e.key === "Escape") seen.push(e.defaultPrevented);
  };
  document.addEventListener("keydown", shell);
  try {
    const { onSelect, onClose, user } = renderPicker();
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
    expect(seen).toEqual([true]);
  } finally {
    document.removeEventListener("keydown", shell);
  }
});

// ── A3: display names only, never a raw slug ───────────────────────────────

test("no raw provider or thinking-style slug reaches the screen", () => {
  const { container } = render(
    <ModelPicker
      open
      tier="reasoning"
      selectedProvider="anthropic"
      selectedModelId="claude-opus-4-5"
      models={MODELS}
      providers={PROVIDERS}
      providerStatuses={STATUSES}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onBrowseProviders={vi.fn()}
    />,
  );
  const text = container.textContent ?? "";

  // Case-SENSITIVE on purpose: the slugs are lower-cased, the display names are
  // not, so `"Anthropic"` passes while `"anthropic"` fails. Lower-casing the
  // haystack first would make this assertion impossible to satisfy.
  expect(text).not.toContain("google_genai");
  expect(text).not.toContain("anthropic");
  expect(text).not.toContain("openai");
  expect(text).not.toContain("anthropic_adaptive");
  expect(text).not.toContain("openai_effort");

  expect(screen.getAllByText("Anthropic").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Google").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Adaptive").length).toBeGreaterThan(0);
});

// ── Listbox wiring ─────────────────────────────────────────────────────────

test("the listbox, its options and aria-activedescendant are wired to real nodes", () => {
  renderPicker();

  expect(screen.getByRole("listbox")).toBeInTheDocument();

  const options = screen.getAllByRole("option");
  expect(options.length).toBe(7);
  // `aria-selected` marks the BOUND model, not the keyboard cursor: Opus is
  // bound, and it renders in both the Suggested group and Anthropic's.
  expect(options.filter((el) => el.getAttribute("aria-selected") === "true")).toHaveLength(2);

  const activeId = screen.getByRole("combobox").getAttribute("aria-activedescendant");
  expect(activeId).toBeTruthy();
  const activeEl = activeId ? document.getElementById(activeId) : null;
  expect(activeEl).not.toBeNull();
  expect(activeEl).toHaveAttribute("role", "option");
});

// ── Columns ────────────────────────────────────────────────────────────────

test("a context window of 1M or more is coloured, and a smaller one is not", () => {
  renderPicker();

  for (const cell of screen.getAllByText("1M")) {
    expect(cell).toHaveClass("text-j-primary");
  }
  for (const cell of screen.getAllByText("200K")) {
    expect(cell).toHaveClass("text-t-muted");
  }
});

test("prices are fixed to two places so the column reads as a column", () => {
  renderPicker();
  // `$5 / $25` and `$1.25 / $10` in one tabular-nums column do not line up.
  expect(screen.getAllByText("$5.00 / $25.00").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$1.25 / $10.00").length).toBeGreaterThan(0);
});

test("clicking a row selects that exact model", async () => {
  const { onSelect, onClose, user } = renderPicker();
  const gpt = screen.getAllByRole("option").find((el) => el.textContent?.includes("GPT-5.2"));
  expect(gpt).toBeDefined();

  await user.click(gpt as HTMLElement);
  expect(onSelect).toHaveBeenCalledWith(OPENAI_GPT);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("the bound model carries the check glyph and the 2px rule", () => {
  renderPicker();
  const bound = screen
    .getAllByRole("option")
    .filter((el) => el.getAttribute("aria-selected") === "true");

  for (const row of bound) {
    expect(row).toHaveClass("border-l-2");
    expect(row).toHaveClass("bg-j-primary-soft");
    expect(row.textContent).toContain(ANTHROPIC_OPUS.display_name);
  }
});

// ── Closed is closed ───────────────────────────────────────────────────────

test("renders nothing while closed", () => {
  const { container } = render(
    <ModelPicker
      open={false}
      tier="reasoning"
      selectedProvider={null}
      selectedModelId={null}
      models={MODELS}
      providers={PROVIDERS}
      providerStatuses={STATUSES}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      onBrowseProviders={vi.fn()}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});
