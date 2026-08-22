import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { SettingsOverlayProvider } from "../overlay-context";
import { ModelPicker } from "./model-picker";
import { MODELS, PROVIDERS, STATUSES } from "./model-picker-fixtures";

/**
 * The palette's focus contract, which its behaviour tests cannot see.
 *
 * Spec §5 names it: "Focus is trapped while open and restored on close (A1)".
 * It is also the half most able to fail silently — a released lease, an
 * un-inerted background and an Escape that never reaches its handler all leave
 * a palette that still looks and behaves correctly to a mouse.
 */

/** The picker beside the control that opens it, so restore has a real target. */
function Harness({ open }: { open: boolean }) {
  return (
    <div>
      <button data-testid="invoker">Model</button>
      <ModelPicker
        open={open}
        tier="reasoning"
        selectedProvider="anthropic"
        selectedModelId="claude-opus-4-5"
        models={MODELS}
        providers={PROVIDERS}
        providerStatuses={STATUSES}
        onSelect={vi.fn()}
        onClose={vi.fn()}
        onBrowseProviders={vi.fn()}
      />
    </div>
  );
}

function renderOpen(over: { onClose?: () => void } = {}) {
  const onClose = over.onClose ?? vi.fn();
  render(
    <ModelPicker
      open
      tier="reasoning"
      selectedProvider="anthropic"
      selectedModelId="claude-opus-4-5"
      models={MODELS}
      providers={PROVIDERS}
      providerStatuses={STATUSES}
      onSelect={vi.fn()}
      onClose={onClose}
      onBrowseProviders={vi.fn()}
    />,
  );
  return { onClose };
}

// ── Opening and closing ────────────────────────────────────────────────────

test("opening puts the caret in the search field, not on the panel", () => {
  renderOpen();
  expect(document.activeElement).toBe(screen.getByRole("combobox"));
});

test("closing returns focus to the control that opened it (A1)", () => {
  const { rerender } = render(<Harness open={false} />);
  const invoker = screen.getByTestId("invoker");
  invoker.focus();

  rerender(<Harness open />);
  expect(document.activeElement).toBe(screen.getByRole("combobox"));

  rerender(<Harness open={false} />);
  expect(document.activeElement).toBe(invoker);
});

// ── The overlay lease ──────────────────────────────────────────────────────

test("the shell's trap is leased on open and released on close", () => {
  const release = vi.fn();
  const claim = vi.fn(() => release);
  const value = { claim };

  const { rerender } = render(
    <SettingsOverlayProvider value={value}>
      <Harness open={false} />
    </SettingsOverlayProvider>,
  );
  expect(claim).not.toHaveBeenCalled();

  rerender(
    <SettingsOverlayProvider value={value}>
      <Harness open />
    </SettingsOverlayProvider>,
  );
  expect(claim).toHaveBeenCalledTimes(1);
  expect(release).not.toHaveBeenCalled();

  rerender(
    <SettingsOverlayProvider value={value}>
      <Harness open={false} />
    </SettingsOverlayProvider>,
  );
  // A lease held past close leaves the shell's trap disarmed for the rest of
  // the dialog's life, with nothing on screen looking wrong.
  expect(release).toHaveBeenCalledTimes(1);
});

// ── Isolation: the palette EARNS its aria-modal ────────────────────────────

test("everything outside the palette is inerted while open, and released on close", () => {
  const { rerender } = render(<Harness open={false} />);
  const invoker = screen.getByTestId("invoker");
  expect(invoker).not.toHaveAttribute("inert");

  rerender(<Harness open />);
  // `aria-modal` constrains focus; a screen reader's virtual cursor still walks
  // the page behind a dialog unless the background is actually marked.
  expect(invoker).toHaveAttribute("inert");
  expect(invoker).toHaveAttribute("aria-hidden", "true");

  rerender(<Harness open={false} />);
  expect(invoker).not.toHaveAttribute("inert");
  expect(invoker).not.toHaveAttribute("aria-hidden");
});

test("the backdrop is never inerted, and closes the palette on click", async () => {
  const { onClose } = renderOpen();
  const backdrop = screen.getByTestId("model-picker-backdrop");

  // It lives inside the isolate root, so it is not a marked sibling. Inerted,
  // its onClick is swallowed and click-outside dies with nothing looking wrong.
  expect(backdrop).not.toHaveAttribute("inert");
  expect(backdrop).not.toHaveAttribute("aria-hidden");

  await userEvent.click(backdrop);
  expect(onClose).toHaveBeenCalledTimes(1);
});

// ── Tab: the palette ships its own trap, because `paused` releases the key ──

const tab = (el: Element, shiftKey = false) => fireEvent.keyDown(el, { key: "Tab", shiftKey });

test("Tab wraps from the last stop back to the search field", () => {
  renderOpen();
  const input = screen.getByRole("combobox");
  const browse = screen.getByRole("button", { name: "Browse all providers" });
  browse.focus();

  tab(browse);
  expect(document.activeElement).toBe(input);
});

test("Shift+Tab wraps from the search field to the last stop", () => {
  renderOpen();
  const input = screen.getByRole("combobox");
  const browse = screen.getByRole("button", { name: "Browse all providers" });
  input.focus();

  tab(input, true);
  // Without a trap of its own the palette has none at all: the shell's is
  // PAUSED by the lease above, which releases the keyboard rather than
  // handing it over, so Tab would walk out of both.
  expect(document.activeElement).toBe(browse);
});

// ── The stray click ────────────────────────────────────────────────────────

test("Escape still closes when focus has been lost to the body", () => {
  const seen: boolean[] = [];
  const shell = (e: KeyboardEvent) => {
    if (e.key === "Escape") seen.push(e.defaultPrevented);
  };
  // Registered BEFORE the palette mounts and in the BUBBLE phase, exactly as
  // `settings-modal.tsx` does. Same node and same phase means registration
  // order, so a bubble-phase listener in the palette would run second — after
  // the shell had already torn down the dialog and every unsaved edit.
  document.addEventListener("keydown", shell);
  try {
    const { onClose } = renderOpen();

    // What a click on a group header, on whitespace or on the scrollbar leaves
    // behind. A React handler on the panel stops firing entirely from here.
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(seen).toEqual([true]);
  } finally {
    document.removeEventListener("keydown", shell);
  }
});

test("a mousedown on non-interactive chrome keeps the caret in the search field", () => {
  renderOpen();
  const input = screen.getByRole("combobox");

  // jsdom does not move focus on mousedown, so the mechanism is asserted
  // rather than its effect: `dispatchEvent` returns false when default was
  // prevented, which is what stops the browser blurring the input.
  expect(fireEvent.mouseDown(screen.getByText("navigate"))).toBe(false);
  expect(fireEvent.mouseDown(screen.getAllByRole("option")[0])).toBe(false);
  // The input itself must keep its default, or the caret cannot be placed.
  expect(fireEvent.mouseDown(input)).toBe(true);
});
