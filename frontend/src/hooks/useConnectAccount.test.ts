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

import { useConnectAccount, type ConnectRun } from "./useConnectAccount";

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

  let done!: Promise<ConnectRun | null>;
  act(() => {
    done = result.current.start(["gmail", "googlecalendar"]);
  });
  await flush();

  // Sequential: only gmail has been begun, and only one popup is open.
  expect(beginMock.mock.calls.map((c) => c[0])).toEqual(["gmail"]);
  expect(openMock).toHaveBeenCalledTimes(1);
  expect(openMock.mock.calls[0][0]).toBe("https://oc.test/gmail");
  expect(result.current.state).toBe("connecting");

  let run!: ConnectRun | null;
  await act(async () => {
    releaseGmail({ status: "active" });
    run = await done;
  });

  expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
    "gmail",
    "googlecalendar",
  ]);
  expect(openMock).toHaveBeenCalledTimes(2);
  expect(openMock.mock.calls[1][0]).toBe("https://oc.test/googlecalendar");
  expect(run!.outcomes).toEqual({ gmail: "active", googlecalendar: "active" });
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

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["gmail", "googlecalendar"]);
  });

  expect(run!.outcomes).toEqual({ gmail: "active", googlecalendar: "cancelled" });
  expect(result.current.results).toEqual(run!.outcomes);
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

    let done!: Promise<ConnectRun | null>;
    act(() => {
      done = result.current.start(["gmail", "googlecalendar"]);
    });

    let run!: ConnectRun | null;
    await act(async () => {
      // Past the ~2.5 min poll ceiling.
      await vi.advanceTimersByTimeAsync(200_000);
      run = await done;
    });

    expect(run!.outcomes).toEqual({ gmail: "active", googlecalendar: "timeout" });
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

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["gmail", "googlecalendar"]);
  });

  // The walk continued past the failure and recorded it.
  expect(run!.outcomes).toEqual({ gmail: "error", googlecalendar: "active" });
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

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["github"]);
  });

  // The in-flight guard was released, so the github run was not a silent no-op.
  expect(run!.outcomes).toEqual({ github: "active" });
  expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
    "gmail",
    "googlecalendar",
    "github",
  ]);
});

test("an empty provider list is a no-op", async () => {
  const { result } = renderHook(() => useConnectAccount());

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start([]);
  });

  // A completed run that found nothing to do — NOT a rejected one.
  expect(run).toEqual({ outcomes: {}, errors: {}, state: "idle" });
  expect(beginMock).not.toHaveBeenCalled();
  expect(result.current.state).toBe("idle");
});

// ── FIX 1: a blocked popup must not masquerade as a timeout ──────────────

test("a blocked popup is reported as blocked without burning the poll ceiling", async () => {
  beginMock.mockImplementation(async (provider: string) => ({
    authorization_url: `https://oc.test/${provider}`,
  }));
  confirmMock.mockResolvedValue({ status: "active" });
  // Provider 2 is the realistic victim: gmail's popup consumed the click's
  // transient user activation, so the browser refuses calendar's and returns
  // null. Real timers here — if the hook polled, this test would hang out to
  // the 150s ceiling instead of finishing immediately.
  openMock
    .mockImplementationOnce(() => makePopup())
    .mockImplementationOnce(() => null);

  const { result } = renderHook(() => useConnectAccount());

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["gmail", "googlecalendar"]);
  });

  expect(run!.outcomes).toEqual({ gmail: "active", googlecalendar: "blocked" });
  // Not a single confirm was spent on the window that never opened.
  expect(confirmMock.mock.calls.map((c) => c[0])).toEqual(["gmail"]);
  expect(result.current.state).toBe("partial");
});

test("a blocked popup still lets the providers after it be attempted", async () => {
  beginMock.mockImplementation(async (provider: string) => ({
    authorization_url: `https://oc.test/${provider}`,
  }));
  confirmMock.mockResolvedValue({ status: "active" });
  openMock
    .mockImplementationOnce(() => null)
    .mockImplementationOnce(() => makePopup());

  const { result } = renderHook(() => useConnectAccount());

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["gmail", "googlecalendar"]);
  });

  expect(run!.outcomes).toEqual({ gmail: "blocked", googlecalendar: "active" });
  expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
    "gmail",
    "googlecalendar",
  ]);
});

test("an all-blocked run aggregates to blocked, not timeout", async () => {
  beginMock.mockResolvedValue({ authorization_url: "https://oc.test/x" });
  openMock.mockImplementation(() => null);

  const { result } = renderHook(() => useConnectAccount());

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["gmail", "googlecalendar"]);
  });

  expect(run!.state).toBe("blocked");
  expect(result.current.state).toBe("blocked");
  expect(confirmMock).not.toHaveBeenCalled();
});

// ── FIX 3: the cause of a failure must reach the caller ──────────────────

test("a failed call exposes its client-safe cause per provider", async () => {
  // What the backend returns when OpenConnector's admin URL/token are unset.
  beginMock.mockRejectedValue({
    safeMessage: "connection service not configured",
    code: "service_unavailable",
    correlationId: "req_abc123",
  });

  const { result } = renderHook(() => useConnectAccount());

  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["gmail"]);
  });

  expect(run!.outcomes).toEqual({ gmail: "error" });
  expect(run!.errors.gmail).toBe(
    "connection service not configured — reference: req_abc123",
  );
  expect(run!.state).toBe("error");
});

// ── FIX 5: aggregate precedence ─────────────────────────────────────────

test("aggregate precedence prefers the most actionable outcome", async () => {
  beginMock.mockImplementation(async (provider: string) => {
    if (provider === "err") throw new Error("nope");
    return { authorization_url: `https://oc.test/${provider}` };
  });
  // "cancel" sees an already-closed popup; "stall" never goes active.
  openMock.mockImplementation((url: string) =>
    url.endsWith("/cancel") ? makePopup(true) : makePopup(),
  );
  confirmMock.mockResolvedValue({ status: "pending" });

  const { result } = renderHook(() => useConnectAccount());

  // A 2.5-min poll (timeout) must not be reported as a bland "error".
  vi.useFakeTimers();
  try {
    let done!: Promise<ConnectRun | null>;
    act(() => {
      done = result.current.start(["stall", "err"]);
    });
    let run!: ConnectRun | null;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200_000);
      run = await done;
    });
    expect(run!.outcomes).toEqual({ stall: "timeout", err: "error" });
    expect(run!.state).toBe("timeout");
  } finally {
    vi.useRealTimers();
  }

  // A deliberate cancel outranks an ambiguous timeout.
  vi.useFakeTimers();
  try {
    let done!: Promise<ConnectRun | null>;
    act(() => {
      done = result.current.start(["cancel", "stall"]);
    });
    let run!: ConnectRun | null;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200_000);
      run = await done;
    });
    expect(run!.outcomes).toEqual({ cancel: "cancelled", stall: "timeout" });
    expect(run!.state).toBe("cancelled");
  } finally {
    vi.useRealTimers();
  }

  // A blocked popup outranks everything — it is the one the user can fix.
  openMock.mockImplementation((url: string) =>
    url.endsWith("/blocked") ? null : makePopup(true),
  );
  let run!: ConnectRun | null;
  await act(async () => {
    run = await result.current.start(["cancel", "blocked", "err"]);
  });
  expect(run!.outcomes).toEqual({
    cancel: "cancelled",
    blocked: "blocked",
    err: "error",
  });
  expect(run!.state).toBe("blocked");
});

// ── FIX 8: a rejected start is distinguishable from an empty run ─────────

test("start() returns null when another walk already owns the flow", async () => {
  beginMock.mockImplementation(async (provider: string) => ({
    authorization_url: `https://oc.test/${provider}`,
  }));
  let releaseGmail!: (v: { status: "active" }) => void;
  const gmailPoll = new Promise<{ status: "active" }>((resolve) => {
    releaseGmail = resolve;
  });
  confirmMock.mockImplementation((provider: string) =>
    provider === "gmail" ? gmailPoll : Promise.resolve({ status: "active" }),
  );

  const { result } = renderHook(() => useConnectAccount());

  let first!: Promise<ConnectRun | null>;
  act(() => {
    first = result.current.start(["gmail"]);
  });
  await flush();

  // A second card is clicked mid-walk.
  let rejected!: ConnectRun | null;
  await act(async () => {
    rejected = await result.current.start(["github"]);
  });

  // null, not {} — the caller can tell "nothing ran" from "ran, found nothing".
  expect(rejected).toBeNull();
  expect(beginMock.mock.calls.map((c) => c[0])).toEqual(["gmail"]);

  await act(async () => {
    releaseGmail({ status: "active" });
    await first;
  });
});
