import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { expect, test } from "vitest";

import { useFocusTrap } from "./use-focus-trap";

/**
 * A container whose middle element is a programmatically-focusable anchor —
 * `tabIndex={-1}`, so it is NOT a tab stop. Roving-tabindex lists and every
 * "restore focus to the row you came from" flow produce exactly this shape.
 */
function Trapped() {
  const isolate = useRef<HTMLDivElement | null>(null);
  const panelRef = useFocusTrap<HTMLDivElement>({ isolate });
  return (
    <div ref={isolate}>
      <div ref={panelRef} tabIndex={-1}>
        <button>before</button>
        <a href="#row" tabIndex={-1} data-testid="anchor">
          row
        </a>
        <button>after</button>
        <button>last</button>
      </div>
    </div>
  );
}

const tab = (el: Element, shiftKey = false) =>
  fireEvent.keyDown(el, { key: "Tab", shiftKey });

test("Tab from a non-tabbable anchor goes to the NEXT control, not the first", () => {
  render(<Trapped />);
  const anchor = screen.getByTestId("anchor");
  anchor.focus();

  tab(anchor);
  // Wrapping to "before" here is the WCAG 2.4.3 failure: closing a dialog
  // restores focus to the row it was opened from, and the next Tab must
  // continue from that row rather than jump to the top of the panel.
  expect(document.activeElement).toBe(screen.getByText("after"));
});

test("Shift+Tab from a non-tabbable anchor goes to the PREVIOUS control", () => {
  render(<Trapped />);
  const anchor = screen.getByTestId("anchor");
  anchor.focus();

  tab(anchor, true);
  expect(document.activeElement).toBe(screen.getByText("before"));
});

test("an anchor before every tab stop still wraps to the last on Shift+Tab", () => {
  function Edge() {
    const panelRef = useFocusTrap<HTMLDivElement>();
    return (
      <div ref={panelRef} tabIndex={-1}>
        <a href="#row" tabIndex={-1} data-testid="anchor">
          row
        </a>
        <button>only</button>
        <button>final</button>
      </div>
    );
  }
  render(<Edge />);
  const anchor = screen.getByTestId("anchor");
  anchor.focus();

  tab(anchor, true);
  expect(document.activeElement).toBe(screen.getByText("final"));
});

test("focus genuinely outside the container is still pulled back", () => {
  // The other reading of `index === -1`: not a roving anchor but an escape.
  // Pulling it to an end is right, and must survive the fix above.
  const outside = document.createElement("button");
  document.body.appendChild(outside);
  render(<Trapped />);

  outside.focus();
  tab(outside);
  expect(document.activeElement).toBe(screen.getByText("before"));

  outside.focus();
  tab(outside, true);
  expect(document.activeElement).toBe(screen.getByText("last"));

  outside.remove();
});

test("the cycle still wraps at both ends for ordinary tab stops", () => {
  render(<Trapped />);
  const before = screen.getByText("before");
  const last = screen.getByText("last");

  last.focus();
  tab(last);
  expect(document.activeElement).toBe(before);

  tab(before, true);
  expect(document.activeElement).toBe(last);
});
