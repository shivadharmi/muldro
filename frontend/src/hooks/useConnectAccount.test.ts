import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

// The hook talks to the API module directly (no DI), so the module is mocked.
const { beginMock, confirmMock } = vi.hoisted(() => ({
  beginMock: vi.fn(),
  confirmMock: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  beginConnection: beginMock,
  confirmConnection: confirmMock,
}));

import { useConnectAccount, type ProviderOutcome } from "./useConnectAccount";

type Popup = { closed: boolean; close: () => void };

const openMock = vi.fn();

function makePopup(closed = false): Popup {
  const popup: Popup = {
    closed,
    close: () => {
      popup.closed = true;
    },
  };
  return popup;
}

/** Let queued microtasks (and any React state updates they cause) settle. */
async function flush() {
  await act(async () => {});
}

beforeEach(() => {
  beginMock.mockReset();
  confirmMock.mockReset();
  openMock.mockReset();
  openMock.mockImplementation(() => makePopup());
  vi.stubGlobal("open", openMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("walks the provider list in order, one popup at a time", async () => {
  beginMock.mockImplementation(async (provider: string) => ({
    authorization_url: `https://oc.test/${provider}`,
  }));

  // Hold gmail's first poll open so we can observe that googlecalendar has not
  // started while gmail is still in flight.
  let releaseGmail!: (v: { status: "active" }) => void;
  const gmailPoll = new Promise<{ status: "active" }>((resolve) => {
    releaseGmail = resolve;
  });
  confirmMock.mockImplementation((provider: string) =>
    provider === "gmail" ? gmailPoll : Promise.resolve({ status: "active" }),
  );

  const { result } = renderHook(() => useConnectAccount());

  let done!: Promise<Record<string, ProviderOutcome>>;
  act(() => {
    done = result.current.start(["gmail", "googlecalendar"]);
  });
  await flush();

  // Sequential: only gmail has been begun, and only one popup is open.
  expect(beginMock.mock.calls.map((c) => c[0])).toEqual(["gmail"]);
  expect(openMock).toHaveBeenCalledTimes(1);
  expect(openMock.mock.calls[0][0]).toBe("https://oc.test/gmail");
  expect(result.current.state).toBe("connecting");

  let outcomes!: Record<string, ProviderOutcome>;
  await act(async () => {
    releaseGmail({ status: "active" });
    outcomes = await done;
  });

  expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
    "gmail",
    "googlecalendar",
  ]);
  expect(openMock).toHaveBeenCalledTimes(2);
  expect(openMock.mock.calls[1][0]).toBe("https://oc.test/googlecalendar");
  expect(outcomes).toEqual({ gmail: "active", googlecalendar: "active" });
  expect(result.current.state).toBe("active");
});

test("reports a per-provider outcome so a partial connect is expressible", async () => {
  beginMock.mockResolvedValue({ authorization_url: "https://oc.test/x" });
  // The user closes the calendar consent popup instead of approving it.
  openMock.mockImplementationOnce(() => makePopup()).mockImplementationOnce(() =>
    makePopup(true),
  );
  confirmMock.mockImplementation(async (provider: string) => ({
    status: provider === "gmail" ? "active" : "pending",
  }));

  const { result } = renderHook(() => useConnectAccount());

  let outcomes!: Record<string, ProviderOutcome>;
  await act(async () => {
    outcomes = await result.current.start(["gmail", "googlecalendar"]);
  });

  expect(outcomes).toEqual({ gmail: "active", googlecalendar: "cancelled" });
  expect(result.current.results).toEqual(outcomes);
  // Not collapsed to a single boolean — the partial is visible to the UI.
  expect(result.current.state).toBe("partial");
});

test("a provider that times out does not collapse the successful one", async () => {
  vi.useFakeTimers();
  try {
    beginMock.mockResolvedValue({ authorization_url: "https://oc.test/x" });
    confirmMock.mockImplementation(async (provider: string) => ({
      status: provider === "gmail" ? "active" : "pending",
    }));

    const { result } = renderHook(() => useConnectAccount());

    let done!: Promise<Record<string, ProviderOutcome>>;
    act(() => {
      done = result.current.start(["gmail", "googlecalendar"]);
    });

    let outcomes!: Record<string, ProviderOutcome>;
    await act(async () => {
      // Past the ~2.5 min poll ceiling.
      await vi.advanceTimersByTimeAsync(200_000);
      outcomes = await done;
    });

    expect(outcomes).toEqual({ gmail: "active", googlecalendar: "timeout" });
    expect(result.current.state).toBe("partial");
  } finally {
    vi.useRealTimers();
  }
});

test("a failing provider does not abort the providers after it", async () => {
  beginMock.mockImplementation(async (provider: string) => {
    if (provider === "gmail") throw new Error("begin failed");
    return { authorization_url: `https://oc.test/${provider}` };
  });
  confirmMock.mockResolvedValue({ status: "active" });

  const { result } = renderHook(() => useConnectAccount());

  let outcomes!: Record<string, ProviderOutcome>;
  await act(async () => {
    outcomes = await result.current.start(["gmail", "googlecalendar"]);
  });

  // The walk continued past the failure and recorded it.
  expect(outcomes).toEqual({ gmail: "error", googlecalendar: "active" });
  expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
    "gmail",
    "googlecalendar",
  ]);
  expect(result.current.state).toBe("partial");
});

test("a second integration can be connected after a run finishes", async () => {
  beginMock.mockImplementation(async (provider: string) => ({
    authorization_url: `https://oc.test/${provider}`,
  }));
  confirmMock.mockResolvedValue({ status: "active" });

  const { result } = renderHook(() => useConnectAccount());

  await act(async () => {
    await result.current.start(["gmail", "googlecalendar"]);
  });

  let outcomes!: Record<string, ProviderOutcome>;
  await act(async () => {
    outcomes = await result.current.start(["github"]);
  });

  // The in-flight guard was released, so the github run was not a silent no-op.
  expect(outcomes).toEqual({ github: "active" });
  expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
    "gmail",
    "googlecalendar",
    "github",
  ]);
});

test("an empty provider list is a no-op", async () => {
  const { result } = renderHook(() => useConnectAccount());

  let outcomes!: Record<string, ProviderOutcome>;
  await act(async () => {
    outcomes = await result.current.start([]);
  });

  expect(outcomes).toEqual({});
  expect(beginMock).not.toHaveBeenCalled();
  expect(result.current.state).toBe("idle");
});
