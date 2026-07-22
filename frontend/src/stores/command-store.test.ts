import { test, expect, beforeEach } from "vitest";
import { useCommandStore } from "./command-store";

beforeEach(() => useCommandStore.setState({ permissionMode: "auto" }));

test("defaults permissionMode to auto", () => {
  expect(useCommandStore.getState().permissionMode).toBe("auto");
});

test("setPermissionMode updates the value", () => {
  useCommandStore.getState().setPermissionMode("bypass");
  expect(useCommandStore.getState().permissionMode).toBe("bypass");
});
