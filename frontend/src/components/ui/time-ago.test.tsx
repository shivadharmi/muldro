import { render } from "@testing-library/react";
import { test, expect } from "vitest";
import { TimeAgo } from "./time-ago";

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
