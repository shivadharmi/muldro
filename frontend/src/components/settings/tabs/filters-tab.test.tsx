import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";
import type { FilterRule } from "@/lib/types";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/api", () => ({
  fetchFilterRules: vi.fn(),
  revokeFilterRule: vi.fn(),
}));

import { FiltersTab } from "./filters-tab";
import { fetchFilterRules, revokeFilterRule } from "@/lib/api";

const activeRule: FilterRule = {
  rule_id: "fr_active",
  source: "gmail",
  match_kind: "sender",
  match_value: "noreply@example.com",
  enabled: true,
  created_at: "2026-08-01T10:00:00Z",
  revoked_at: null,
  created_from_approval_id: "apr_1",
};

const revokedRule: FilterRule = {
  rule_id: "fr_revoked",
  source: "gmail",
  match_kind: "sender",
  match_value: "digest@example.com",
  enabled: false,
  created_at: "2026-07-01T10:00:00Z",
  revoked_at: "2026-07-20T10:00:00Z",
  created_from_approval_id: "apr_2",
};

function mockRules(rules: FilterRule[]) {
  vi.mocked(fetchFilterRules).mockResolvedValue({
    rules,
    count: rules.length,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

test("splits rules into Active and Revoked, and only active rules can be revoked", async () => {
  mockRules([activeRule, revokedRule]);
  render(<FiltersTab />);

  const active = await screen.findByRole("region", { name: "Active" });
  const revoked = screen.getByRole("region", { name: "Revoked" });

  expect(within(active).getByText("noreply@example.com")).toBeInTheDocument();
  expect(within(revoked).getByText("digest@example.com")).toBeInTheDocument();

  expect(
    within(revoked).queryByRole("button", { name: /revoke filter/i }),
  ).not.toBeInTheDocument();
  expect(
    within(active).getByRole("button", { name: /revoke filter/i }),
  ).toBeInTheDocument();
});

test("revoking moves the rule into Revoked without refetching the list", async () => {
  mockRules([activeRule]);
  vi.mocked(revokeFilterRule).mockResolvedValue({
    rule_id: "fr_active",
    released: 0,
  });
  render(<FiltersTab />);

  await screen.findByRole("region", { name: "Active" });
  await userEvent.click(screen.getByRole("button", { name: /revoke filter/i }));

  await waitFor(() =>
    expect(revokeFilterRule).toHaveBeenCalledWith("fr_active"),
  );
  const revoked = await screen.findByRole("region", { name: "Revoked" });
  expect(within(revoked).getByText("noreply@example.com")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Active" })).not.toBeInTheDocument();
  expect(fetchFilterRules).toHaveBeenCalledTimes(1);
});

test("the success toast reports how much mail the revoke released", async () => {
  mockRules([activeRule]);
  vi.mocked(revokeFilterRule).mockResolvedValue({
    rule_id: "fr_active",
    released: 12,
  });
  render(<FiltersTab />);

  await screen.findByRole("region", { name: "Active" });
  await userEvent.click(screen.getByRole("button", { name: /revoke filter/i }));

  await waitFor(() =>
    expect(addToast).toHaveBeenCalledWith(
      expect.stringMatching(/12 messages/),
      "success",
    ),
  );
});

test("the success toast carries no count when nothing was released", async () => {
  mockRules([activeRule]);
  vi.mocked(revokeFilterRule).mockResolvedValue({
    rule_id: "fr_active",
    released: 0,
  });
  render(<FiltersTab />);

  await screen.findByRole("region", { name: "Active" });
  await userEvent.click(screen.getByRole("button", { name: /revoke filter/i }));

  await waitFor(() =>
    expect(addToast).toHaveBeenCalledWith("Filter removed", "success"),
  );
});

test("a failed revoke reports the error and leaves the rule active", async () => {
  mockRules([activeRule]);
  vi.mocked(revokeFilterRule).mockRejectedValue(new Error("network down"));
  render(<FiltersTab />);

  await screen.findByRole("region", { name: "Active" });
  await userEvent.click(screen.getByRole("button", { name: /revoke filter/i }));

  await waitFor(() =>
    expect(addToast).toHaveBeenCalledWith(expect.any(String), "error"),
  );
  const active = screen.getByRole("region", { name: "Active" });
  expect(within(active).getByText("noreply@example.com")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Revoked" })).not.toBeInTheDocument();
});

test("says Muldro has proposed nothing when there are no rules", async () => {
  mockRules([]);
  render(<FiltersTab />);

  expect(await screen.findByText(/no filters yet/i)).toBeInTheDocument();
  expect(
    screen.getByText(/it will ask before filtering anything/i),
  ).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Active" })).not.toBeInTheDocument();
});
