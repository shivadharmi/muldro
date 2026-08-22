"use client";

import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { logoutSession } from "@/lib/api";
import {
  clearStoredAuth,
  getStoredToken,
  isTokenExpired,
  TOKEN_KEY,
  USER_KEY,
  EXPIRES_KEY,
  type AuthUser,
} from "@/lib/auth-storage";

export type { AuthUser };
// Re-exported so every existing `from "@/lib/auth"` import keeps working.
export { getStoredToken };

interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (token: string, user: AuthUser, expiresAt?: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  token: null,
  isAuthenticated: false,
  isLoading: true,
  login: () => {},
  logout: () => {},
});

// ── External store for auth state (hydration-safe) ─────────────────
// useSyncExternalStore returns the server snapshot during SSR and hydration,
// then switches to the client snapshot. React handles this transition
// without hydration mismatch errors.

type AuthSnapshot = { token: string | null; user: AuthUser | null; hydrated: boolean };

let listeners: Array<() => void> = [];
let cachedSnapshot: AuthSnapshot | null = null;

const SERVER_SNAPSHOT: AuthSnapshot = { token: null, user: null, hydrated: false };

function readFromStorage(): AuthSnapshot {
  try {
    if (isTokenExpired()) {
      clearStoredAuth();
      return { token: null, user: null, hydrated: true };
    }
    const storedToken = localStorage.getItem(TOKEN_KEY);
    const storedUser = localStorage.getItem(USER_KEY);
    if (storedToken && storedUser) {
      return { token: storedToken, user: JSON.parse(storedUser), hydrated: true };
    }
  } catch {
    clearStoredAuth();
  }
  return { token: null, user: null, hydrated: true };
}

function getSnapshot(): AuthSnapshot {
  if (cachedSnapshot === null) {
    cachedSnapshot = readFromStorage();
  }
  return cachedSnapshot;
}

function getServerSnapshot(): AuthSnapshot {
  return SERVER_SNAPSHOT;
}

function subscribe(callback: () => void) {
  listeners.push(callback);
  return () => {
    listeners = listeners.filter((l) => l !== callback);
  };
}

function emitChange() {
  cachedSnapshot = readFromStorage();
  for (const listener of listeners) listener();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const { token, user, hydrated } = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // Check token expiration periodically
  useEffect(() => {
    if (!token) return;
    const interval = setInterval(() => {
      if (isTokenExpired()) {
        clearStoredAuth();
        emitChange();
      }
    }, 60_000);
    return () => clearInterval(interval);
  }, [token]);

  const login = useCallback(
    (newToken: string, newUser: AuthUser, expiresAt?: string) => {
      localStorage.setItem(TOKEN_KEY, newToken);
      localStorage.setItem(USER_KEY, JSON.stringify(newUser));
      if (expiresAt) {
        localStorage.setItem(EXPIRES_KEY, expiresAt);
      }
      emitChange();
    },
    []
  );

  const logout = useCallback(() => {
    // Revoke server-side FIRST. `api()` builds its Authorization header
    // synchronously while assembling the fetch arguments, so the request
    // already carries the token by the time the next line removes it —
    // clearing first would send an unauthenticated request and leave the
    // session alive for whoever still holds the value.
    //
    // Fire-and-forget, and local state is cleared either way: a network
    // failure must not trap someone in a session they asked to leave.
    void logoutSession().catch(() => undefined);
    clearStoredAuth();
    emitChange();
  }, []);

  return (
    <AuthContext
      value={{
        user,
        token,
        isAuthenticated: !!token && !!user,
        isLoading: !hydrated,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
