import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/api", () => ({
  fetchPolicyMode: vi.fn().mockResolvedValue({ mode: "approval_required" }),
  setPolicyMode: vi.fn().mockResolvedValue({}),
  fetchWorkspaceDefaultPermissionMode: vi
    .fn()
    .mockResolvedValue({ default_permission_mode: "auto" }),
  setWorkspaceDefaultPermissionMode: vi
    .fn()
    .mockResolvedValue({ default_permission_mode: "ask" }),
}));

import { PolicyTab } from "./policy-tab";
import {
  fetchPolicyMode,
  setPolicyMode,
  setWorkspaceDefaultPermissionMode,
} from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

test("loads its own posture — the shell hands it nothing", async () => {
  render(<PolicyTab />);
  await waitFor(() => expect(fetchPolicyMode).toHaveBeenCalled());
});

test("renders both mode lists", () => {
  render(<PolicyTab />);
  expect(screen.getByText("Full Auto")).toBeTruthy();
  expect(screen.getByText("Bypass")).toBeTruthy();
  expect(screen.getByText("Confirm every write")).toBeTruthy();
});

test("saves the chosen posture", async () => {
  render(<PolicyTab />);
  await userEvent.click(screen.getByText("Full Auto"));
  await waitFor(() => expect(setPolicyMode).toHaveBeenCalledWith("full_auto"));
});

test("saves the chosen default permission mode", async () => {
  render(<PolicyTab />);
  await userEvent.click(screen.getByText("Bypass"));
  await waitFor(() =>
    expect(setWorkspaceDefaultPermissionMode).toHaveBeenCalledWith("bypass"),
  );
});
