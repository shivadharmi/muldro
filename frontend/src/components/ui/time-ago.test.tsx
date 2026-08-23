import { render } from "@testing-library/react";
import { test, expect, afterEach, vi } from "vitest";
import { TimeAgo } from "./time-ago";

// Every case below is stated relative to one fixed instant. A stamp is only
// past or future with respect to *now*, so a test that reads the wall clock
// would assert something different every month it runs.
const NOW = new Date("2026-08-23T12:00:00Z");

function at(offsetMs: number): string {
  return new Date(NOW.getTime() + offsetMs).toISOString();
}

function renderAt(date: string | null): HTMLElement {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  const { container } = render(<TimeAgo date={date} />);
  return container;
}

afterEach(() => {
  vi.useRealTimers();
});

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

test("a meeting two months out renders its datetime, not 'just now'", () => {
  const meeting = "2026-10-20T09:00:00Z";
  const text = renderAt(meeting).textContent ?? "";

  expect(text).not.toContain("just now");
  expect(text).not.toContain("ago");
  // Derived from Date accessors rather than from the formatter, so the
  // assertion holds in whatever timezone the suite runs in.
  const local = new Date(meeting);
  expect(text).toContain(String(local.getDate()));
  expect(text).toMatch(/\d{1,2}:\d{2}/);
});

test("the next scheduled meeting renders its datetime", () => {
  const text = renderAt("2026-08-25T09:00:00Z").textContent ?? "";
  expect(text).not.toContain("just now");
  expect(text).toContain(String(new Date("2026-08-25T09:00:00Z").getDate()));
});

test("a stamp thirty seconds ahead is clock skew, not a schedule", () => {
  expect(renderAt(at(30_000)).textContent).toBe("just now");
});

test("a stamp two minutes ahead is a schedule, not skew", () => {
  const text = renderAt(at(2 * MINUTE)).textContent ?? "";
  expect(text).not.toBe("just now");
  expect(text).toMatch(/\d{1,2}:\d{2}/);
});

test("a stamp seconds old reads 'just now'", () => {
  expect(renderAt(at(-10_000)).textContent).toBe("just now");
});

test("a stamp minutes old reads in minutes", () => {
  expect(renderAt(at(-5 * MINUTE)).textContent).toBe("5m ago");
});

test("a stamp hours old reads in hours", () => {
  expect(renderAt(at(-3 * HOUR)).textContent).toBe("3h ago");
});

test("a stamp days old reads in days", () => {
  expect(renderAt(at(-4 * DAY)).textContent).toBe("4d ago");
});

test("beyond thirty days it falls back to the plain date", () => {
  const old = at(-60 * DAY);
  expect(renderAt(old).textContent).toBe(new Date(old).toLocaleDateString());
});

test("a null date renders a dash and no time element", () => {
  const container = renderAt(null);
  expect(container.textContent).toBe("--");
  expect(container.querySelector("time")).toBeNull();
});

test("keeps the machine-readable stamp and the full title on a future date", () => {
  const meeting = "2026-10-20T09:00:00Z";
  const time = renderAt(meeting).querySelector("time");
  expect(time?.getAttribute("dateTime")).toBe(meeting);
  expect(time?.getAttribute("title")).toBe(new Date(meeting).toLocaleString());
});

test("keeps the machine-readable stamp and the full title on a past date", () => {
  const past = at(-3 * HOUR);
  const time = renderAt(past).querySelector("time");
  expect(time?.getAttribute("dateTime")).toBe(past);
  expect(time?.getAttribute("title")).toBe(new Date(past).toLocaleString());
});

test("applies the default tone class when tone is omitted", () => {
  const { container } = render(<TimeAgo date="2026-08-21T13:00:00Z" />);
  const time = container.querySelector("time");
  expect(time?.className).toContain("text-t-tertiary");
  expect(time?.className).not.toContain("text-t-muted");
});

test("applies the given tone class in place of the default", () => {
  const { container } = render(<TimeAgo date="2026-08-21T13:00:00Z" tone="text-t-muted" />);
  const time = container.querySelector("time");
  expect(time?.className).toContain("text-t-muted");
  expect(time?.className).not.toContain("text-t-tertiary");
});
