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

test("a malformed bindRejections payload falls back instead of reaching JSX", async () => {
  const { result } = await renderLoaded();

  saveMock.mockRejectedValueOnce(
    Object.assign(new Error("API 422"), {
      bindRejections: [{ scope_type: "tier", scope_key: 7 }],
    }),
  );
  await act(async () => {
    await expect(result.current.save()).rejects.toThrow();
  });

  // A `rejectionFor(...)!.message` of `undefined` must never render.
  expect(result.current.rejections).toEqual([]);
  expect(result.current.rejectionFor("tier", "fast")).toBeUndefined();
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

