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

import type { ModelCatalog, ModelConfig } from "@/lib/types";
import { useModelConfig } from "./use-model-config";

const CATALOG: ModelCatalog = { providers: [], models: [], agents: [] };

function makeConfig(): ModelConfig {
  return {
    tiers: [
      {
        scope_type: "tier",
        scope_key: "reasoning",
        provider: "anthropic",
        model_id: "claude-opus",
        effort: "high",
        max_tokens: 8000,
        temperature: null,
      },
      {
        scope_type: "tier",
        scope_key: "fast",
        provider: "anthropic",
        model_id: "claude-haiku",
        effort: "low",
        max_tokens: 2000,
        temperature: null,
      },
    ],
    agent_overrides: [
      {
        scope_type: "agent",
        scope_key: "planner",
        provider: "openai",
        model_id: "gpt-5",
        effort: "medium",
        max_tokens: 4000,
        temperature: 0.2,
      },
    ],
    providers: [],
    warnings: [],
  };
}

/** An ApiError-shaped rejection: the hook detects `bindRejections` structurally,
 *  so the real class (which lives in `@/lib/api`, now mocked) is not needed. */
function bindRejectionError(scopeKey: string) {
  return Object.assign(new Error("API 422: no key"), {
    safeMessage: "no key",
    code: "error",
    correlationId: null,
    bindRejections: [
      {
        scope_type: "tier" as const,
        scope_key: scopeKey,
        provider: "openai",
        code: "provider_not_configured" as const,
        message: "OpenAI is not configured.",
      },
    ],
  });
}

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
  expect(result.current.config).not.toBeNull();
  expect(result.current.loading).toBe(false);
  expect(result.current.draft.tiers).toHaveLength(2);
  expect(result.current.dirtyCount).toBe(0);

  // Second call is a no-op — the ref guard prevents a double fetch.
  await act(async () => {
    await result.current.load();
  });
  expect(catalogMock).toHaveBeenCalledTimes(1);
  expect(configMock).toHaveBeenCalledTimes(1);
});

test("a failed load resets its guard so a retry is possible", async () => {
  catalogMock.mockRejectedValueOnce(new Error("boom"));
  const { result } = renderHook(() => useModelConfig());

  // The assertion lives INSIDE act: letting the act callback itself reject
  // skips React's final flush, so the state set before the throw never lands.
  await act(async () => {
    await expect(result.current.load()).rejects.toThrow("boom");
  });

  await act(async () => {
    await result.current.load();
  });

  expect(catalogMock).toHaveBeenCalledTimes(2);
  expect(result.current.config).not.toBeNull();
});

test("editing one field of one binding makes exactly that binding dirty", async () => {
  const { result } = await renderLoaded();
  expect(result.current.dirtyCount).toBe(0);

  act(() => {
    result.current.updateBinding("tier", "fast", { effort: "high" });
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
  const beforeBinding = before.tiers[1];
  const beforeSnapshot = { ...beforeBinding };
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
  expect(beforeBinding).toEqual(beforeSnapshot);
  expect(before.tiers[1].provider).toBe("anthropic");
  // The saved config is a separate baseline and did not move either.
  expect(savedBinding.provider).toBe("anthropic");
  // The new draft really did change.
  expect(result.current.draft.tiers[1].provider).toBe("openai");
  expect(result.current.draft.tiers[1].model_id).toBe("gpt-5-mini");
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

test("discard restores the draft to the saved config", async () => {
  const { result } = await renderLoaded();

  act(() => {
    result.current.updateBinding("tier", "fast", { effort: "high" });
    result.current.updateBinding("agent", "planner", { temperature: 0.9 });
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

test("save adopts the server's response as the new baseline", async () => {
  const { result } = await renderLoaded();

  const saved = makeConfig();
  saved.tiers[1] = { ...saved.tiers[1], effort: "high" };
  saveMock.mockResolvedValue(saved);

  act(() => {
    result.current.updateBinding("tier", "fast", { effort: "high" });
  });
  expect(result.current.dirtyCount).toBe(1);

  await act(async () => {
    await result.current.save();
  });

  expect(saveMock).toHaveBeenCalledWith({
    tiers: expect.arrayContaining([
      expect.objectContaining({ scope_key: "fast", effort: "high" }),
    ]),
    agent_overrides: expect.any(Array),
  });
  expect(result.current.dirtyCount).toBe(0);
  expect(result.current.config!.tiers[1].effort).toBe("high");
  expect(result.current.saving).toBe(false);
});

test("a 422 populates rejections, re-throws, and clears on the next good save", async () => {
  const { result } = await renderLoaded();

  saveMock.mockRejectedValueOnce(bindRejectionError("fast"));

  act(() => {
    result.current.updateBinding("tier", "fast", { provider: "openai" });
  });

  await act(async () => {
    await expect(result.current.save()).rejects.toThrow("API 422");
  });

  expect(result.current.rejections).toHaveLength(1);
  expect(result.current.rejectionFor("tier", "fast")?.message).toBe(
    "OpenAI is not configured.",
  );
  expect(result.current.rejectionFor("tier", "reasoning")).toBeUndefined();
  expect(result.current.rejectionFor("agent", "fast")).toBeUndefined();
  // The draft survives a rejection so the user can fix it in place.
  expect(result.current.dirtyCount).toBe(1);
  expect(result.current.saving).toBe(false);

  saveMock.mockResolvedValueOnce(makeConfig());
  await act(async () => {
    await result.current.save();
  });

  expect(result.current.rejections).toEqual([]);
  expect(result.current.rejectionFor("tier", "fast")).toBeUndefined();
  expect(result.current.dirtyCount).toBe(0);
});

test("a non-422 failure leaves existing rejections in place", async () => {
  const { result } = await renderLoaded();

  saveMock.mockRejectedValueOnce(bindRejectionError("fast"));
  await act(async () => {
    await expect(result.current.save()).rejects.toThrow();
  });
  expect(result.current.rejections).toHaveLength(1);

  saveMock.mockRejectedValueOnce(new Error("network down"));
  await act(async () => {
    await expect(result.current.save()).rejects.toThrow("network down");
  });

  expect(result.current.rejectionFor("tier", "fast")).toBeDefined();
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

test("warningFor is safe before the config has loaded", () => {
  const { result } = renderHook(() => useModelConfig());
  expect(result.current.warningFor("tier", "fast")).toBeUndefined();
  expect(result.current.dirtyCount).toBe(0);
});
