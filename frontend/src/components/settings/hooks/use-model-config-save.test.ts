import { beforeEach, expect, test, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

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

import type { ModelConfig } from "@/lib/types";
import { useModelConfig } from "./use-model-config";
import {
  CATALOG,
  bindRejectionError,
  makeConfig,
} from "./model-config-fixtures";

beforeEach(() => {
  catalogMock.mockReset().mockResolvedValue(CATALOG);
  configMock.mockReset().mockResolvedValue(makeConfig());
  saveMock.mockReset();
});

async function renderLoaded() {
  const view = renderHook(() => useModelConfig());
  await act(async () => {
    await view.result.current.load();
  });
  return view;
}

test("save submits the whole draft verbatim and adopts the response", async () => {
  const { result } = await renderLoaded();

  const saved = makeConfig();
  saved.tiers[1] = { ...saved.tiers[1], effort: "high" };
  saveMock.mockResolvedValue(saved);

  act(() => {
    result.current.updateBinding("tier", "fast", { effort: "high" });
  });
  const submitted = result.current.draft;

  await act(async () => {
    await result.current.save();
  });

  // Exact payload, not `arrayContaining` — a dropped tier or a mangled
  // agent_overrides list has to fail this.
  expect(saveMock).toHaveBeenCalledTimes(1);
  expect(saveMock).toHaveBeenCalledWith({
    tiers: submitted.tiers,
    agent_overrides: submitted.agent_overrides,
  });
  expect(result.current.dirtyCount).toBe(0);
  expect(result.current.config).toEqual(saved);
  expect(result.current.draft.tiers).toEqual(saved.tiers);
  expect(result.current.saving).toBe(false);
});

test("two concurrent save calls share one request and one promise", async () => {
  const { result } = await renderLoaded();

  let release: (config: ModelConfig) => void = () => {};
  saveMock.mockImplementation(
    () =>
      new Promise<ModelConfig>((resolve) => {
        release = resolve;
      }),
  );

  let first: Promise<void> | undefined;
  let second: Promise<void> | undefined;
  await act(async () => {
    first = result.current.save();
    second = result.current.save();
  });

  expect(second).toBe(first);
  expect(saveMock).toHaveBeenCalledTimes(1);
  expect(result.current.saving).toBe(true);

  await act(async () => {
    release(makeConfig());
    await first;
  });
  expect(result.current.saving).toBe(false);

  // The guard released, so a later save is a real request again.
  saveMock.mockResolvedValue(makeConfig());
  await act(async () => {
    await result.current.save();
  });
  expect(saveMock).toHaveBeenCalledTimes(2);
});

test("an edit made while the PUT is in flight survives the response", async () => {
  const { result } = await renderLoaded();

  let release: (config: ModelConfig) => void = () => {};
  saveMock.mockImplementation(
    () =>
      new Promise<ModelConfig>((resolve) => {
        release = resolve;
      }),
  );

  let pending: Promise<void> | undefined;
  await act(async () => {
    pending = result.current.save();
  });

  act(() => {
    result.current.updateBinding("tier", "reasoning", { effort: "low" });
  });

  await act(async () => {
    release(makeConfig());
    await pending;
  });

  // Rebasing unconditionally onto the response would drop this with no signal.
  expect(result.current.draft.tiers[0].effort).toBe("low");
  expect(result.current.dirtyKeys).toEqual(new Set(["tier:reasoning"]));
  // Bindings untouched during the flight still take the server's value.
  expect(result.current.draft.tiers[1]).toEqual(result.current.config!.tiers[1]);
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

test("editing a rejected binding drops only that binding's rejection", async () => {
  const { result } = await renderLoaded();

  saveMock.mockRejectedValueOnce(
    Object.assign(new Error("API 422"), {
      bindRejections: [
        bindRejectionError("fast").bindRejections[0],
        { ...bindRejectionError("reasoning").bindRejections[0] },
      ],
    }),
  );
  await act(async () => {
    await expect(result.current.save()).rejects.toThrow();
  });
  expect(result.current.rejections).toHaveLength(2);

  act(() => {
    result.current.updateBinding("tier", "fast", { provider: "google" });
  });

  expect(result.current.rejectionFor("tier", "fast")).toBeUndefined();
  expect(result.current.rejectionFor("tier", "reasoning")).toBeDefined();
});

test("discard and clearRejections both escape a stranded 422", async () => {
  const { result } = await renderLoaded();

  saveMock.mockRejectedValue(bindRejectionError("fast"));
  act(() => {
    result.current.updateBinding("tier", "fast", { provider: "openai" });
  });
  await act(async () => {
    await expect(result.current.save()).rejects.toThrow();
  });
  expect(result.current.rejections).toHaveLength(1);

  // Discarding removes the change the rejection was about — the card must not
  // keep showing an error the user has no edit left to fix.
  act(() => {
    result.current.discard();
  });
  expect(result.current.rejections).toEqual([]);
  expect(result.current.dirtyCount).toBe(0);

  act(() => {
    result.current.updateBinding("tier", "fast", { provider: "openai" });
  });
  await act(async () => {
    await expect(result.current.save()).rejects.toThrow();
  });
  expect(result.current.rejections).toHaveLength(1);

  act(() => {
    result.current.clearRejections();
  });
  expect(result.current.rejections).toEqual([]);
  // Still dirty — clearing a verdict is not discarding the edit.
  expect(result.current.dirtyCount).toBe(1);
});

test("applyServerConfig rebases clean bindings and preserves dirty ones", async () => {
  const { result } = await renderLoaded();

  act(() => {
    result.current.updateBinding("tier", "fast", { provider: "openai" });
  });

  const fromServer = makeConfig();
  fromServer.tiers[0] = { ...fromServer.tiers[0], model_id: "server-choice" };
  fromServer.tiers[1] = { ...fromServer.tiers[1], model_id: "server-choice" };
  fromServer.warnings = [
    {
      scope_type: "tier",
      scope_key: "reasoning",
      provider: "openai",
      code: "provider_not_configured",
      message: "reasoning has no runnable model.",
    },
  ];

  act(() => {
    result.current.applyServerConfig(fromServer);
  });

  // The whole point: a warning raised by a credential revoke reaches the cards.
  expect(result.current.warningFor("tier", "reasoning")?.message).toBe(
    "reasoning has no runnable model.",
  );
  expect(result.current.draft.tiers[0].model_id).toBe("server-choice");
  // The pending edit is not collateral damage.
  expect(result.current.draft.tiers[1].provider).toBe("openai");
  expect(result.current.dirtyKeys).toEqual(new Set(["tier:fast"]));
});

test("every action is identity-stable across draft edits", async () => {
  const { result } = await renderLoaded();
  const before = result.current;

  act(() => {
    result.current.updateBinding("tier", "fast", { max_tokens: 1 });
  });
  act(() => {
    result.current.updateBinding("tier", "fast", { max_tokens: 2 });
  });

  // A Cmd+S handler bound once in a mount-only effect must still submit what
  // is on screen, so `save` may not change identity per keystroke.
  expect(result.current.save).toBe(before.save);
  expect(result.current.load).toBe(before.load);
  expect(result.current.discard).toBe(before.discard);
  expect(result.current.updateBinding).toBe(before.updateBinding);
  expect(result.current.addBinding).toBe(before.addBinding);
  expect(result.current.removeBinding).toBe(before.removeBinding);
  expect(result.current.applyServerConfig).toBe(before.applyServerConfig);
  expect(result.current.clearRejections).toBe(before.clearRejections);

  saveMock.mockResolvedValue(makeConfig());
  await act(async () => {
    await before.save();
  });
  // The stale reference submitted the CURRENT draft, not the one at capture.
  expect(saveMock.mock.calls[0][0].tiers[1].max_tokens).toBe(2);
});
