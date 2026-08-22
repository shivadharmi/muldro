/**
 * Signing out must do BOTH halves: forget the token locally and revoke the
 * session server-side. Clearing localStorage alone left the session valid
 * until natural expiry for anyone still holding the value.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const { logoutSession } = vi.hoisted(() => ({
  logoutSession: vi.fn(() => Promise.resolve({ status: "logged_out" })),
}));
vi.mock("@/lib/api", () => ({ logoutSession }));

import { clearStoredAuth, getStoredToken, TOKEN_KEY, USER_KEY } from "@/lib/auth-storage";

describe("auth storage", () => {
  beforeEach(() => {
    localStorage.clear();
    logoutSession.mockClear();
  });

  it("reads the token the API client sends", () => {
    localStorage.setItem(TOKEN_KEY, "tok_abc");
    expect(getStoredToken()).toBe("tok_abc");
  });

  it("clearStoredAuth removes every session key", () => {
    localStorage.setItem(TOKEN_KEY, "tok_abc");
    localStorage.setItem(USER_KEY, JSON.stringify({ user_id: "u" }));
    clearStoredAuth();
    expect(getStoredToken()).toBeNull();
    expect(localStorage.getItem(USER_KEY)).toBeNull();
  });

  it("an expired token reads as absent and is swept", () => {
    localStorage.setItem(TOKEN_KEY, "tok_abc");
    localStorage.setItem("muldro_auth_expires", new Date(Date.now() - 1000).toISOString());
    expect(getStoredToken()).toBeNull();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
