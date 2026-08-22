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
  CredentialDeleteResult,
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

const DELETED: CredentialDeleteResult = {
  status: { ...STATUS, configured: false, source: "none" },
  orphaned_bindings: ORPHANED,
};

function makeConfig(): ModelConfig {
  return { tiers: [], agent_overrides: [], providers: [STATUS], warnings: [] };
}

beforeEach(() => {
  configMock.mockReset().mockResolvedValue(makeConfig());
  saveCredentialMock.mockReset().mockResolvedValue(STATUS);
  testKeyMock.mockReset().mockResolvedValue({ status: "ok" });
  deleteKeyMock.mockReset().mockResolvedValue(DELETED);
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
  expect(result.current.stale).toBe(false);
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
  const onRefreshed = vi.fn();
  const { result } = renderHook(() => useProviderCredentials(onRefreshed));

  let returned: CredentialDeleteResult | undefined;
  await act(async () => {
    returned = await result.current.remove("openai");
  });

  expect(deleteKeyMock).toHaveBeenCalledWith("openai");
  expect(returned).toEqual(DELETED);
  expect(returned!.orphaned_bindings[0].message).toBe(
    "reasoning now has no runnable model.",
  );
  // The revoke's consequence has to reach the config the UI renders.
  expect(configMock).toHaveBeenCalledTimes(1);
  expect(onRefreshed).toHaveBeenCalledWith(makeConfig());
});

test("a failed refetch does not turn a successful mutation into a failure", async () => {
  configMock.mockRejectedValue(new Error("refetch exploded"));
  const onRefreshed = vi.fn();
  const onRefreshFailed = vi.fn();
  const { result } = renderHook(() =>
    useProviderCredentials(onRefreshed, onRefreshFailed),
  );

  let returned: CredentialDeleteResult | undefined;
  await act(async () => {
    // Must NOT reject: the DELETE succeeded. Reporting "remove failed" here
    // invites the user to retry a revoke that already happened.
    returned = await result.current.remove("openai");
  });

  // The whole reason the hook returns a result must survive the hiccup.
  expect(returned).toEqual(DELETED);
  expect(returned!.orphaned_bindings).toHaveLength(1);
  expect(onRefreshed).not.toHaveBeenCalled();
  // Weaker signal, but never silent.
  expect(onRefreshFailed).toHaveBeenCalledTimes(1);
  expect(result.current.stale).toBe(true);
  expect(result.current.busy).toBeNull();

  // A later refetch that lands clears the staleness.
  configMock.mockResolvedValue(makeConfig());
  await act(async () => {
    await result.current.test("openai");
  });
  expect(result.current.stale).toBe(false);
});

test("a refetch failure is tolerated with no onRefreshFailed supplied", async () => {
  configMock.mockRejectedValue(new Error("refetch exploded"));
  const { result } = renderHook(() => useProviderCredentials(vi.fn()));

  let returned: ProviderStatus | undefined;
  await act(async () => {
    returned = await result.current.save("openai", { api_key: "sk" });
  });

  expect(returned).toEqual(STATUS);
  expect(result.current.stale).toBe(true);
});

test("overlapping providers each keep their own row busy", async () => {
  let releaseTest: (value: { status: string }) => void = () => {};
  let releaseSave: (value: ProviderStatus) => void = () => {};
  testKeyMock.mockImplementation(
    () => new Promise<{ status: string }>((r) => (releaseTest = r)),
  );
  saveCredentialMock.mockImplementation(
    () => new Promise<ProviderStatus>((r) => (releaseSave = r)),
  );

  const { result } = renderHook(() => useProviderCredentials(vi.fn()));

  let slow: Promise<{ status: string }> | undefined;
  let fast: Promise<ProviderStatus> | undefined;
  await act(async () => {
    slow = result.current.test("anthropic");
    fast = result.current.save("openai", { api_key: "sk" });
  });

  expect(result.current.isBusy("anthropic")).toBe(true);
  expect(result.current.isBusy("openai")).toBe(true);
  expect(result.current.busyProviders.size).toBe(2);

  // The second mutation finishing must not clear the first one's spinner.
  await act(async () => {
    releaseSave(STATUS);
    await fast;
  });
  expect(result.current.isBusy("openai")).toBe(false);
  expect(result.current.isBusy("anthropic")).toBe(true);
  expect(result.current.busy).toBe("anthropic");

  await act(async () => {
    releaseTest({ status: "ok" });
    await slow;
  });
  expect(result.current.busyProviders.size).toBe(0);
  expect(result.current.busy).toBeNull();
});

test("busy names the provider in flight and clears afterwards", async () => {
  let release: (value: ProviderStatus) => void = () => {};
  saveCredentialMock.mockImplementation(
    () => new Promise<ProviderStatus>((r) => (release = r)),
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

test("a mutation error propagates and busy still clears", async () => {
  deleteKeyMock.mockRejectedValue(new Error("revoke failed"));
  const onRefreshed = vi.fn();
  const { result } = renderHook(() => useProviderCredentials(onRefreshed));

  await act(async () => {
    await expect(result.current.remove("openai")).rejects.toThrow(
      "revoke failed",
    );
  });

  expect(result.current.busy).toBeNull();
  expect(result.current.busyProviders.size).toBe(0);
  expect(onRefreshed).not.toHaveBeenCalled();
  // The mutation never happened, so nothing is out of date.
  expect(result.current.stale).toBe(false);
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
