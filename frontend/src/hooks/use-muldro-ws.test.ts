import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Token always present so onopen sends the auth frame.
vi.mock("@/lib/auth", () => ({ getStoredToken: () => "tok" }));

import { useMuldroWs } from "./use-muldro-ws";

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
