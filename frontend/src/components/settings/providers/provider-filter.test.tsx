import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { ProviderFilter } from "./provider-filter";

test("renders all three segments", () => {
  render(<ProviderFilter value="all" onChange={vi.fn()} />);
  for (const label of ["All", "Connected", "Available"]) {
    expect(screen.getByRole("button", { name: label })).toBeTruthy();
  }
});

test("marks only the selected segment as pressed", () => {
  render(<ProviderFilter value="connected" onChange={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Connected" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByRole("button", { name: "Available" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

test("reports the chosen segment and never mutates its own selection", async () => {
  const onChange = vi.fn();
  render(<ProviderFilter value="all" onChange={onChange} />);
  await userEvent.click(screen.getByRole("button", { name: "Available" }));
  expect(onChange).toHaveBeenCalledWith("available");
  // Controlled: the value prop did not change, so neither did the pressed state.
  expect(screen.getByRole("button", { name: "All" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
