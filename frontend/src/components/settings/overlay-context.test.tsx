import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { expect, test } from "vitest";

import {
  SettingsOverlayProvider,
  useOverlayClaim,
  useOverlayClaims,
} from "./overlay-context";

/** Stands in for the model picker: claims while open, and can vanish. */
function Overlay({ open }: { open: boolean }) {
  useOverlayClaim(open);
  return <span>overlay</span>;
}

/**
 * The shell side, with the two ways an overlay goes away: closing it (`open`
 * false) and unmounting it outright, which is what the rail does by swapping
 * the tab out from under it.
 */
function Harness({ children }: { children?: ReactNode }) {
  const overlay = useOverlayClaims();
  return (
    <SettingsOverlayProvider value={overlay.value}>
      <output>{overlay.claimed ? "paused" : "trapping"}</output>
      {children}
    </SettingsOverlayProvider>
  );
}

function Scene() {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(true);
  return (
    <Harness>
      <button onClick={() => setOpen((v) => !v)}>toggle</button>
      <button onClick={() => setMounted(false)}>unmount</button>
      {mounted && <Overlay open={open} />}
    </Harness>
  );
}

const state = () => screen.getByRole("status").textContent;

test("a closed overlay claims nothing", () => {
  render(<Scene />);
  expect(state()).toBe("trapping");
});

test("an open overlay pauses the trap", async () => {
  render(<Scene />);
  await userEvent.click(screen.getByRole("button", { name: "toggle" }));
  expect(state()).toBe("paused");
});

test("closing the overlay releases the claim", async () => {
  render(<Scene />);
  await userEvent.click(screen.getByRole("button", { name: "toggle" }));
  await userEvent.click(screen.getByRole("button", { name: "toggle" }));
  expect(state()).toBe("trapping");
});

test("an overlay UNMOUNTED while open releases the claim", async () => {
  // The bug this shape exists for: with a raw `setPaused(true)` the picker was
  // swapped out by a rail click without running its own close path, and the
  // trap stayed disarmed for the rest of the dialog's life.
  render(<Scene />);
  await userEvent.click(screen.getByRole("button", { name: "toggle" }));
  expect(state()).toBe("paused");
  await userEvent.click(screen.getByRole("button", { name: "unmount" }));
  expect(state()).toBe("trapping");
});

test("two overlays: the trap re-arms only when the last one lets go", async () => {
  function TwoScene() {
    const [first, setFirst] = useState(true);
    const [second, setSecond] = useState(true);
    return (
      <Harness>
        <button onClick={() => setFirst(false)}>close first</button>
        <button onClick={() => setSecond(false)}>close second</button>
        <Overlay open={first} />
        <Overlay open={second} />
      </Harness>
    );
  }
  render(<TwoScene />);
  expect(state()).toBe("paused");
  // A boolean flag would have re-armed here, under an overlay still open.
  await userEvent.click(screen.getByRole("button", { name: "close first" }));
  expect(state()).toBe("paused");
  await userEvent.click(screen.getByRole("button", { name: "close second" }));
  expect(state()).toBe("trapping");
});

test("releasing twice does not decrement another overlay's claim", async () => {
  function DoubleReleaseScene() {
    const overlay = useOverlayClaims();
    // Claimed in an effect, not a lazy initialiser: `claim()` calls setState,
    // and doing that during render double-claims under StrictMode.
    const release = useRef<() => void>(() => {});
    useEffect(() => {
      release.current = overlay.value.claim();
      // Mount-only; `overlay.value` is stable.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <>
        <output>{overlay.claimed ? "paused" : "trapping"}</output>
        <button
          onClick={() => {
            release.current();
            release.current();
          }}
        >
          release twice
        </button>
        <SettingsOverlayProvider value={overlay.value}>
          <Overlay open />
        </SettingsOverlayProvider>
      </>
    );
  }
  render(<DoubleReleaseScene />);
  expect(state()).toBe("paused");
  await userEvent.click(screen.getByRole("button", { name: "release twice" }));
  // One claim released, one still held: a non-idempotent release would have
  // re-armed the trap under the overlay that is still open.
  expect(state()).toBe("paused");
});

test("a consumer outside the provider is a no-op, not a crash", () => {
  render(<Overlay open />);
  expect(screen.getByText("overlay")).toBeInTheDocument();
});
