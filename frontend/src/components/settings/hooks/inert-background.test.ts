import { afterEach, expect, test } from "vitest";

import { inertBackground } from "./inert-background";

const added: Element[] = [];

function bodyChild<T extends Element>(el: T): T {
  document.body.appendChild(el);
  added.push(el);
  return el;
}

function div(): HTMLDivElement {
  return bodyChild(document.createElement("div"));
}

afterEach(() => {
  for (const el of added.splice(0)) el.remove();
});

test("marks the dialog's siblings and releases them again", () => {
  const background = div();
  const dialog = div();

  const release = inertBackground(dialog);
  expect(background).toHaveAttribute("inert");
  expect(background).toHaveAttribute("aria-hidden", "true");
  expect(dialog).not.toHaveAttribute("inert");

  release();
  expect(background).not.toHaveAttribute("inert");
  expect(background).not.toHaveAttribute("aria-hidden");
});

test("walks up, so a sibling of an ANCESTOR is covered too", () => {
  const background = div();
  const wrapper = div();
  const dialog = document.createElement("div");
  wrapper.appendChild(dialog);

  const release = inertBackground(dialog);
  // The dialog's own parent is on the path and stays reachable...
  expect(wrapper).not.toHaveAttribute("inert");
  // ...while the parent's sibling, one level up, does not.
  expect(background).toHaveAttribute("inert");
  release();
});

test("an SVG sibling gets aria-hidden but not inert", () => {
  // `inert` is an HTML-only attribute — a body-level sprite sheet would keep
  // its contents in the virtual cursor if `aria-hidden` were skipped with it.
  const sprite = bodyChild(
    document.createElementNS("http://www.w3.org/2000/svg", "svg"),
  );
  const dialog = div();

  const release = inertBackground(dialog);
  expect(sprite.hasAttribute("aria-hidden")).toBe(true);
  expect(sprite.hasAttribute("inert")).toBe(false);

  release();
  expect(sprite.hasAttribute("aria-hidden")).toBe(false);
});

test("live regions stay announceable", () => {
  // The settings surface raises its own toasts; inerting the toast container
  // would silence exactly the messages the open dialog produces.
  const toasts = div();
  toasts.setAttribute("role", "status");
  const polite = div();
  polite.setAttribute("aria-live", "polite");
  const dialog = div();

  const release = inertBackground(dialog);
  expect(toasts).not.toHaveAttribute("inert");
  expect(polite).not.toHaveAttribute("inert");
  release();
});

test("non-rendered siblings are left alone", () => {
  const script = bodyChild(document.createElement("script"));
  const dialog = div();

  const release = inertBackground(dialog);
  expect(script.hasAttribute("aria-hidden")).toBe(false);
  release();
});

test("an element already hidden by someone else is not un-hidden on release", () => {
  const theirs = div();
  theirs.setAttribute("aria-hidden", "true");
  const dialog = div();

  const release = inertBackground(dialog);
  release();
  // Never clear a state this function did not set.
  expect(theirs).toHaveAttribute("aria-hidden", "true");
});

test("a null root isolates nothing", () => {
  const background = div();
  inertBackground(null)();
  expect(background).not.toHaveAttribute("inert");
});
