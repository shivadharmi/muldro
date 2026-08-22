import { afterEach, beforeEach, describe, expect, it, test, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Token always present so onopen sends the auth frame.
vi.mock("@/lib/auth", () => ({ getStoredToken: () => "tok" }));

import { dispatchMuldroMessage, useMuldroWs } from "./use-muldro-ws";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  url: string;
  readyState: number = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}

function deliver(ws: MockWebSocket, payload: unknown) {
  ws.onmessage?.({ data: JSON.stringify(payload) });
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("auth_error halts the reconnect loop instead of retrying forever", () => {
  renderHook(() => useMuldroWs({ userId: "u1", enabled: true }));
  expect(MockWebSocket.instances.length).toBe(1);
  const ws = MockWebSocket.instances[0];

  act(() => {
    ws.readyState = MockWebSocket.OPEN;
    ws.onopen?.();
    // Server rejects auth (e.g. token user != path user).
    deliver(ws, { type: "auth_error", message: "User mismatch" });
  });

  // Advance well past the 3s reconnect delay.
  act(() => {
    vi.advanceTimersByTime(10_000);
  });

  // No reconnect storm: exactly one socket was ever created.
  expect(MockWebSocket.instances.length).toBe(1);
});

test("auth_error surfaces the failure via onError", () => {
  const onError = vi.fn();
  renderHook(() => useMuldroWs({ userId: "u1", enabled: true, onError }));
  const ws = MockWebSocket.instances[0];

  act(() => {
    ws.readyState = MockWebSocket.OPEN;
    ws.onopen?.();
    deliver(ws, { type: "auth_error", message: "User mismatch" });
  });

  expect(onError).toHaveBeenCalledTimes(1);
});

test("a transient close (not auth) still reconnects", () => {
  renderHook(() => useMuldroWs({ userId: "u1", enabled: true }));
  const ws = MockWebSocket.instances[0];

  act(() => {
    ws.readyState = MockWebSocket.OPEN;
    ws.onopen?.();
    deliver(ws, { type: "auth_ok" });
    // Network drop, no auth error — the loop should recover.
    ws.onclose?.();
  });

  act(() => {
    vi.advanceTimersByTime(3_000);
  });

  expect(MockWebSocket.instances.length).toBe(2);
});

/**
 * The hook dispatches a `unit` frame to onUnitPush, and guards on identity
 * first.
 *
 * The guard is not defensive noise. `render_surface` emitted `surface_id`
 * where this hook read `id`, so `msg.surface?.id` dropped every surface it
 * ever sent — silently, because the tool returned {status: "published"}
 * regardless (spec §1). The guard stays; the publisher states the field.
 */

const UNIT = {
  frame: {
    key: "gmail:email_thread:t1",
    group_key: null,
    kind: "proposal",
    status: "needs_you",
    headline: "Sarah Chen - Series A term sheet",
    source: "gmail",
    entity_type: "email_thread",
    occurred_at: "2026-08-22T12:00:00Z",
    updated_at: "2026-08-22T12:00:00Z",
    importance: 0,
    event_count: 3,
    affordances: [],
  },
  body: "",
  quotes: [],
};

describe("dispatchMuldroMessage", () => {
  it("routes a unit frame to onUnitPush", () => {
    const onUnitPush = vi.fn();
    dispatchMuldroMessage({ type: "unit", key: UNIT.frame.key, unit: UNIT }, { onUnitPush });
    expect(onUnitPush).toHaveBeenCalledWith(UNIT);
  });

  it("drops a unit frame with no key", () => {
    const onUnitPush = vi.fn();
    dispatchMuldroMessage({ type: "unit", unit: UNIT }, { onUnitPush });
    expect(onUnitPush).not.toHaveBeenCalled();
  });

  it("drops a unit frame with no unit", () => {
    const onUnitPush = vi.fn();
    dispatchMuldroMessage({ type: "unit", key: "a:b:c" }, { onUnitPush });
    expect(onUnitPush).not.toHaveBeenCalled();
  });

  it("still routes surface_update — the history page reads it", () => {
    const onSurfaceUpdate = vi.fn();
    dispatchMuldroMessage(
      { type: "surface_update", surface_id: "run_1", phase: "executing" },
      { onSurfaceUpdate }
    );
    expect(onSurfaceUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ surface_id: "run_1", phase: "executing" })
    );
  });

  it("treats an unknown type as a notification", () => {
    const onNotification = vi.fn();
    dispatchMuldroMessage({ type: "notification", title: "hi" }, { onNotification });
    expect(onNotification).toHaveBeenCalled();
  });

  it("does not throw on a heartbeat", () => {
    expect(() => dispatchMuldroMessage({ type: "heartbeat" }, {})).not.toThrow();
  });
});
