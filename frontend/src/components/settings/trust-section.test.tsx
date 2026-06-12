import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { TrustSection } from "./trust-section";

const noop = () => {};

function renderSection(trustByFamily = {}) {
  return render(
    <TrustSection
      trustByFamily={trustByFamily}
      loading={false}
      onExpand={vi.fn()}
      onCeilingChange={noop}
      onReset={noop}
      ceilingLoading={null}
      resetLoading={null}
    />,
  );
}

test("is collapsed by default — no trust content shown", () => {
  renderSection({ communication: [] });
  expect(screen.queryByText(/no trust data yet/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /per-capability trust/i })).toBeInTheDocument();
});

test("expanding with no data reveals the empty state", async () => {
  renderSection({});
  await userEvent.click(screen.getByRole("button", { name: /per-capability trust/i }));
  expect(screen.getByText(/no trust data yet/i)).toBeInTheDocument();
});

test("expanding calls onExpand (lazy load trigger)", async () => {
  const onExpand = vi.fn();
  render(
    <TrustSection
      trustByFamily={{}}
      loading={false}
      onExpand={onExpand}
      onCeilingChange={noop}
      onReset={noop}
      ceilingLoading={null}
      resetLoading={null}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: /per-capability trust/i }));
  expect(onExpand).toHaveBeenCalledTimes(1);
});
