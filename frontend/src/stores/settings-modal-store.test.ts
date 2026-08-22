import { beforeEach, expect, test } from "vitest";

import { useSettingsModalStore } from "./settings-modal-store";

/** The store is a module singleton, so every test starts from the shipped
 *  initial state rather than from whatever the previous one left. */
beforeEach(() => {
  useSettingsModalStore.setState({
    open: false,
    activeTab: "account",
    pendingProvider: null,
  });
});

const state = () => useSettingsModalStore.getState();

test("openProviderFor sets the tab, the intent and the modal in one write", () => {
  state().openProviderFor("groq", "Needed by the Fast tier");

  const after = state();
  expect(after.activeTab).toBe("providers");
  expect(after.pendingProvider).toEqual({
    provider: "groq",
    reason: "Needed by the Fast tier",
  });
  // Not needed by today's only caller, which is already inside the modal — but
  // a caller from outside it would otherwise have to reach for `openSettings`,
  // which drops the intent it had just set.
  expect(after.open).toBe(true);
});

test("an intent may carry no reason", () => {
  state().openProviderFor("groq");
  expect(state().pendingProvider?.provider).toBe("groq");
  expect(state().pendingProvider?.reason).toBeUndefined();
});

/**
 * What makes the intent ONE-SHOT is that every other navigation drops it.
 *
 * Each of these is a separate object literal handed to `set`, and `set` merges:
 * a field added to one of them without re-stating `pendingProvider: null` leaves
 * a sticky intent that re-opens a row on some later, unrelated visit. Nothing
 * else would catch that — every other test in the suite seeds the store
 * directly instead of navigating to it.
 */
test.each([
  ["openSettings", () => state().openSettings()],
  ["openSettings with a tab", () => state().openSettings("trust")],
  ["closeSettings", () => state().closeSettings()],
  ["setActiveTab", () => state().setActiveTab("model")],
  ["clearPendingProvider", () => state().clearPendingProvider()],
])("%s drops a standing intent", (_name, navigate) => {
  state().openProviderFor("groq", "Needed by the Fast tier");
  expect(state().pendingProvider).not.toBeNull();

  navigate();

  expect(state().pendingProvider).toBeNull();
});

// The guard exists so a tab mounting with no intent does not notify every
// subscriber of a null→null "change".
test("clearing an already-clear intent is a no-op, not a write", () => {
  const before = state();
  before.clearPendingProvider();
  expect(state()).toBe(before);
});
