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
import { CATALOG, makeConfig } from "./model-config-fixtures";

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

test("save before load resolves is a no-op, not a wipe of every override", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const { result } = renderHook(() => useModelConfig());

  // `PUT /v1/model-config` treats `[]` as a COMPLETE REPLACEMENT, so an
  // unloaded EMPTY_DRAFT would delete every agent override in the workspace
  // with no 422 and nothing on screen looking wrong. Reachable from a save bar
  // binding Cmd+S in a mount-only effect, before the load round-trip lands.
  await act(async () => {
    await result.current.save();
  });
  expect(saveMock).not.toHaveBeenCalled();
  expect(warn).toHaveBeenCalledTimes(1);

  // Still shut after a FAILED load — `config` stays null behind the retry toast.
  catalogMock.mockRejectedValueOnce(new Error("boom"));
  await act(async () => {
    await expect(result.current.load()).rejects.toThrow("boom");
  });
  await act(async () => {
    await result.current.save();
  });
  expect(saveMock).not.toHaveBeenCalled();

  // ...and open again once a config actually exists.
  saveMock.mockResolvedValue(makeConfig());
  await act(async () => {
    await result.current.load();
  });
  await act(async () => {
    await result.current.save();
  });
  expect(saveMock).toHaveBeenCalledTimes(1);
  warn.mockRestore();
});

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

test("a binding dirty at submit time adopts the server's normalisation", async () => {
  const { result } = await renderLoaded();

  // Dirty when the PUT fires, and the server clamps it on the way through.
  act(() => {
    result.current.updateBinding("tier", "fast", { max_tokens: 999999 });
  });

  const clamped = makeConfig();
  clamped.tiers[1] = { ...clamped.tiers[1], max_tokens: 32000 };
  saveMock.mockResolvedValue(clamped);

  await act(async () => {
    await result.current.save();
  });

  // Only `baseline = submitted` gets this right. Baselined on the PRE-SAVE
  // config the binding still reads as dirty, so the draft would keep 999999
  // and the card would show a value the server has already refused.
  expect(result.current.draft.tiers[1].max_tokens).toBe(32000);
  expect(result.current.dirtyCount).toBe(0);
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

test("two applyServerConfig calls batched into one commit both land", async () => {
  const { result } = await renderLoaded();

  // Both responses touch the SAME binding — otherwise a stale baseline still
  // happens to produce the right answer and the test proves nothing.
  const first = makeConfig();
  first.tiers[0] = { ...first.tiers[0], model_id: "from-first" };
  const second = makeConfig();
  second.tiers[0] = { ...second.tiers[0], model_id: "from-second" };

  // Two overlapping credential mutations resolving in the same macrotask —
  // exactly what `busyProviders` being a Set exists to allow. With config and
  // draft held apart, the second reads the PRE-FIRST config as its baseline,
  // so `tier:reasoning` looks dirty (the user did not touch it — the first
  // response did) and rebaseDraft keeps `from-first`, discarding `from-second`.
  act(() => {
    result.current.applyServerConfig(first);
    result.current.applyServerConfig(second);
  });

  expect(result.current.config).toEqual(second);
  expect(result.current.draft.tiers[0].model_id).toBe("from-second");
  expect(result.current.dirtyCount).toBe(0);
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
  expect(result.current.upsertBinding).toBe(before.upsertBinding);
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
