import { beforeEach, expect, test, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

// The hook talks to the API module directly (no DI), so the module is mocked.
const { catalogMock, configMock, saveMock } = vi.hoisted(() => ({
  catalogMock: vi.fn(),
  configMock: vi.fn(),
  saveMock: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  fetchModelCatalog: catalogMock,
  fetchModelConfig: configMock,
  saveModelConfig: saveMock,
}));

import { useModelConfig } from "./use-model-config";
import { CATALOG, binding, makeConfig } from "./model-config-fixtures";

beforeEach(() => {
  catalogMock.mockReset().mockResolvedValue(CATALOG);
  configMock.mockReset().mockResolvedValue(makeConfig());
  saveMock.mockReset();
});

/** Renders the hook and completes its one-shot load. */
async function renderLoaded() {
  const view = renderHook(() => useModelConfig());
  await act(async () => {
    await view.result.current.load();
  });
  return view;
}

test("load fetches catalog + config once and seeds a clean draft", async () => {
  const { result } = await renderLoaded();

  expect(result.current.catalog).toEqual(CATALOG);
  expect(result.current.draft.tiers).toHaveLength(2);
  expect(result.current.loading).toBe(false);
  expect(result.current.dirtyCount).toBe(0);

  await act(async () => {
    await result.current.load();
  });
  expect(catalogMock).toHaveBeenCalledTimes(1);
  expect(configMock).toHaveBeenCalledTimes(1);
});

test("concurrent load callers share one request and one outcome", async () => {
  const { result } = renderHook(() => useModelConfig());

  let first: Promise<void> | undefined;
  let second: Promise<void> | undefined;
  await act(async () => {
    first = result.current.load();
    second = result.current.load();
    await Promise.all([first, second]);
  });

  // The second caller must get the SAME promise, not a bare `undefined` it
  // could `await` into a false "loaded" conclusion.
  expect(second).toBe(first);
  expect(catalogMock).toHaveBeenCalledTimes(1);
});

test("a concurrent second load caller observes the first one's failure", async () => {
  catalogMock.mockRejectedValueOnce(new Error("boom"));
  const { result } = renderHook(() => useModelConfig());

  // The assertion lives INSIDE act: letting the act callback itself reject
  // skips React's final flush, so the state set before the throw never lands.
  await act(async () => {
    const first = result.current.load();
    const second = result.current.load();
    await expect(first).rejects.toThrow("boom");
    await expect(second).rejects.toThrow("boom");
  });

  // ...and the guard reset, so a retry still works.
  await act(async () => {
    await result.current.load();
  });
  expect(catalogMock).toHaveBeenCalledTimes(2);
  expect(result.current.config).not.toBeNull();
});

test("editing one field of one binding makes exactly that binding dirty", async () => {
  const { result } = await renderLoaded();

  act(() => {
    expect(result.current.updateBinding("tier", "fast", { effort: "high" })).toBe(
      true,
    );
  });

  expect(result.current.dirtyCount).toBe(1);
  expect(result.current.dirtyKeys.has("tier:fast")).toBe(true);
  expect(result.current.dirtyKeys.has("tier:reasoning")).toBe(false);
});

test("re-setting a field to its saved value makes the binding clean again", async () => {
  const { result } = await renderLoaded();

  act(() => {
    result.current.updateBinding("tier", "fast", { max_tokens: 9999 });
  });
  expect(result.current.dirtyCount).toBe(1);

  act(() => {
    result.current.updateBinding("tier", "fast", { max_tokens: 2000 });
  });
  expect(result.current.dirtyCount).toBe(0);
});

test("updateBinding does not mutate the previous draft (immutability rule)", async () => {
  const { result } = await renderLoaded();

  const before = result.current.draft;
  const beforeTiers = before.tiers;
  const beforeSnapshot = { ...before.tiers[1] };
  const savedBinding = result.current.config!.tiers[1];

  act(() => {
    result.current.updateBinding("tier", "fast", {
      provider: "openai",
      model_id: "gpt-5-mini",
    });
  });

  // Previous draft object, its array, and its binding are all untouched.
  expect(result.current.draft).not.toBe(before);
  expect(before.tiers).toBe(beforeTiers);
  expect(before.tiers[1]).toEqual(beforeSnapshot);
  expect(before.tiers[1].provider).toBe("anthropic");
  // The saved config is a separate baseline and did not move either.
  expect(savedBinding.provider).toBe("anthropic");
  expect(result.current.draft.tiers[1].provider).toBe("openai");
  // Untouched siblings are carried over by reference.
  expect(result.current.draft.tiers[0]).toBe(before.tiers[0]);
});

test("a patch cannot re-key the binding it patches", async () => {
  const { result } = await renderLoaded();

  act(() => {
    result.current.updateBinding("tier", "fast", {
      scope_key: "reasoning",
      effort: "high",
    });
  });

  expect(result.current.draft.tiers[1].scope_key).toBe("fast");
  expect(result.current.dirtyKeys).toEqual(new Set(["tier:fast"]));
});

test("an unknown scope key returns false and warns instead of silently no-opping", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const { result } = await renderLoaded();

  act(() => {
    expect(result.current.updateBinding("tier", "typo", { effort: "high" })).toBe(
      false,
    );
    expect(result.current.removeBinding("agent", "typo")).toBe(false);
    // Right key, wrong scope type — still not a binding that exists.
    expect(result.current.updateBinding("agent", "fast", { effort: "low" })).toBe(
      false,
    );
  });

  expect(warn).toHaveBeenCalledTimes(3);
  expect(warn.mock.calls[0][0]).toContain("updateBinding");
  expect(result.current.dirtyCount).toBe(0);
  warn.mockRestore();
});

test("removeBinding makes the removal visible in dirtyKeys", async () => {
  const { result } = await renderLoaded();

  act(() => {
    expect(result.current.removeBinding("agent", "planner")).toBe(true);
  });

  expect(result.current.draft.agent_overrides).toEqual([]);
  // A draft-only walk would report 0 here and hide the save bar.
  expect(result.current.dirtyCount).toBe(1);
  expect(result.current.dirtyKeys.has("agent:planner")).toBe(true);
});

test("addBinding appends a new override and replaces an existing one", async () => {
  const { result } = await renderLoaded();

  act(() => {
    result.current.addBinding(binding("agent", "executor", { provider: "openai" }));
  });
  expect(result.current.draft.agent_overrides).toHaveLength(2);
  expect(result.current.dirtyKeys.has("agent:executor")).toBe(true);

  const previous = result.current.draft.agent_overrides;
  act(() => {
    result.current.addBinding(binding("agent", "executor", { provider: "google" }));
  });
  expect(result.current.draft.agent_overrides).toHaveLength(2);
  expect(result.current.draft.agent_overrides[1].provider).toBe("google");
  expect(result.current.draft.agent_overrides).not.toBe(previous);
  expect(previous[1].provider).toBe("openai");
});

test("discard restores the draft to the saved config", async () => {
  const { result } = await renderLoaded();

  act(() => {
    result.current.updateBinding("tier", "fast", { effort: "high" });
    result.current.removeBinding("agent", "planner");
  });
  expect(result.current.dirtyCount).toBe(2);

  act(() => {
    result.current.discard();
  });

  expect(result.current.dirtyCount).toBe(0);
  expect(result.current.draft.tiers).toEqual(result.current.config!.tiers);
  expect(result.current.draft.agent_overrides).toEqual(
    result.current.config!.agent_overrides,
  );
});

test("warningFor reads the server's warnings, not the rejections", async () => {
  const withWarning = makeConfig();
  withWarning.warnings = [
    {
      scope_type: "agent",
      scope_key: "planner",
      provider: "openai",
      code: "provider_not_configured",
      message: "OpenAI has no credential.",
    },
  ];
  configMock.mockResolvedValue(withWarning);

  const { result } = await renderLoaded();

  expect(result.current.warningFor("agent", "planner")?.message).toBe(
    "OpenAI has no credential.",
  );
  expect(result.current.warningFor("tier", "planner")).toBeUndefined();
  expect(result.current.rejectionFor("agent", "planner")).toBeUndefined();
});

test("the pre-load surface is safe and non-throwing", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const { result } = renderHook(() => useModelConfig());

  expect(result.current.warningFor("tier", "fast")).toBeUndefined();
  expect(result.current.dirtyCount).toBe(0);
  expect(result.current.draft.tiers).toEqual([]);
  act(() => {
    expect(result.current.updateBinding("tier", "fast", {})).toBe(false);
    result.current.discard();
  });
  expect(result.current.dirtyCount).toBe(0);
  warn.mockRestore();
});
