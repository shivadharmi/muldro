import { beforeEach, expect, test, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

const { configMock, saveCredentialMock, testKeyMock, deleteKeyMock } =
  vi.hoisted(() => ({
    configMock: vi.fn(),
    saveCredentialMock: vi.fn(),
    testKeyMock: vi.fn(),
    deleteKeyMock: vi.fn(),
  }));
vi.mock("@/lib/api", () => ({
  fetchModelConfig: configMock,
  saveProviderCredential: saveCredentialMock,
  testProviderKey: testKeyMock,
  deleteProviderKey: deleteKeyMock,
}));

import type {
  ConfigWarning,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";
import { useProviderCredentials } from "./use-provider-credentials";

const STATUS: ProviderStatus = {
  provider: "openai",
  configured: true,
  status: "ok",
  source: "workspace",
  base_url: null,
  extra_config_public: {},
  extra_config_secret_keys: [],
  catalogued: true,
};

const ORPHANED: ConfigWarning[] = [
  {
    scope_type: "tier",
    scope_key: "reasoning",
    provider: "openai",
    code: "provider_not_configured",
    message: "reasoning now has no runnable model.",
  },
];

function makeConfig(): ModelConfig {
  return { tiers: [], agent_overrides: [], providers: [STATUS], warnings: [] };
}

beforeEach(() => {
  configMock.mockReset().mockResolvedValue(makeConfig());
  saveCredentialMock.mockReset().mockResolvedValue(STATUS);
  testKeyMock.mockReset().mockResolvedValue({ status: "ok" });
  deleteKeyMock.mockReset();
});

test("save posts the fields, refetches the config, and returns the status", async () => {
  const onRefreshed = vi.fn();
  const { result } = renderHook(() => useProviderCredentials(onRefreshed));

  let returned: ProviderStatus | undefined;
  await act(async () => {
    returned = await result.current.save("openai", { api_key: "sk-test" });
  });

  expect(saveCredentialMock).toHaveBeenCalledWith("openai", {
    api_key: "sk-test",
  });
  expect(configMock).toHaveBeenCalledTimes(1);
  expect(onRefreshed).toHaveBeenCalledWith(makeConfig());
  expect(returned).toEqual(STATUS);
  expect(result.current.busy).toBeNull();
});

test("test returns the probe result and refreshes the config", async () => {
  const onRefreshed = vi.fn();
  const { result } = renderHook(() => useProviderCredentials(onRefreshed));

  let returned: { status: string } | undefined;
  await act(async () => {
    returned = await result.current.test("openai");
  });

  expect(testKeyMock).toHaveBeenCalledWith("openai");
  expect(returned).toEqual({ status: "ok" });
  expect(onRefreshed).toHaveBeenCalledTimes(1);
});

test("remove returns the delete result including orphaned_bindings", async () => {
  deleteKeyMock.mockResolvedValue({
    status: { ...STATUS, configured: false, source: "none" },
    orphaned_bindings: ORPHANED,
  });
  const onRefreshed = vi.fn();
  const { result } = renderHook(() => useProviderCredentials(onRefreshed));

  let returned;
  await act(async () => {
    returned = await result.current.remove("openai");
  });

  expect(deleteKeyMock).toHaveBeenCalledWith("openai");
  expect(returned).toEqual({
    status: { ...STATUS, configured: false, source: "none" },
    orphaned_bindings: ORPHANED,
  });
  expect(returned!.orphaned_bindings[0].message).toBe(
    "reasoning now has no runnable model.",
  );
  // The revoke's consequence has to reach the config the UI renders.
  expect(configMock).toHaveBeenCalledTimes(1);
  expect(onRefreshed).toHaveBeenCalledWith(makeConfig());
});

test("busy names the provider in flight and clears afterwards", async () => {
  let release: (value: ProviderStatus) => void = () => {};
  saveCredentialMock.mockImplementation(
    () =>
      new Promise<ProviderStatus>((resolve) => {
        release = resolve;
      }),
  );
  const { result } = renderHook(() => useProviderCredentials(vi.fn()));

  let pending: Promise<ProviderStatus> | undefined;
  await act(async () => {
    pending = result.current.save("openai", { api_key: "sk" });
  });
  expect(result.current.busy).toBe("openai");

  await act(async () => {
    release(STATUS);
    await pending;
  });
  expect(result.current.busy).toBeNull();
});

test("errors propagate to the caller and busy still clears", async () => {
  deleteKeyMock.mockRejectedValue(new Error("revoke failed"));
  const onRefreshed = vi.fn();
  const { result } = renderHook(() => useProviderCredentials(onRefreshed));

  await act(async () => {
    await expect(result.current.remove("openai")).rejects.toThrow(
      "revoke failed",
    );
  });

  expect(result.current.busy).toBeNull();
  expect(onRefreshed).not.toHaveBeenCalled();
});

test("a re-rendered inline callback does not stale out the refresh", async () => {
  const first = vi.fn();
  const second = vi.fn();
  const { result, rerender } = renderHook(
    ({ cb }: { cb: (c: ModelConfig) => void }) => useProviderCredentials(cb),
    { initialProps: { cb: first } },
  );

  const saveBefore = result.current.save;
  rerender({ cb: second });
  expect(result.current.save).toBe(saveBefore);

  await act(async () => {
    await result.current.test("openai");
  });

  expect(second).toHaveBeenCalledTimes(1);
  expect(first).not.toHaveBeenCalled();
});
