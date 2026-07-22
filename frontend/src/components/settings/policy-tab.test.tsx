import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { PolicyTab } from "./policy-tab";

const PERMISSION_MODES = [
  { value: "auto", label: "Auto", description: "Confirm only risky writes" },
  { value: "ask", label: "Ask", description: "Confirm every write" },
  { value: "bypass", label: "Bypass", description: "Never confirm" },
];

function renderTab(onChange = vi.fn()) {
  render(
    <PolicyTab
      policyMode="approval_required"
      policyModes={[{ value: "approval_required", label: "Approval Required", description: "" }]}
      policyLoading={false}
      onPolicyChange={() => {}}
      defaultPermissionMode="auto"
      permissionModes={PERMISSION_MODES}
      permissionLoading={false}
      onDefaultPermissionModeChange={onChange}
    />,
  );
  return onChange;
}

test("renders the default-permission-mode options", () => {
  renderTab();
  expect(screen.getByText("Bypass")).toBeTruthy();
  expect(screen.getByText("Confirm every write")).toBeTruthy();
});

test("fires the change callback with the chosen mode", async () => {
  const onChange = renderTab();
  await userEvent.click(screen.getByText("Bypass"));
  expect(onChange).toHaveBeenCalledWith("bypass");
});
