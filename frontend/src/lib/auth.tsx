"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";

export interface AuthUser {
  user_id: string;
  email: string;
  display_name: string | null;
}

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

const TOKEN_KEY = "jarvis_auth_token";
const USER_KEY = "jarvis_auth_user";
const EXPIRES_KEY = "jarvis_auth_expires";

function isTokenExpired(): boolean {
  if (typeof window === "undefined") return false;
  const expiresAt = localStorage.getItem(EXPIRES_KEY);
  if (!expiresAt) return false;
  return new Date(expiresAt).getTime() <= Date.now();
}

function clearStoredAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(EXPIRES_KEY);
}

function getInitialAuth(): { token: string | null; user: AuthUser | null } {
  if (typeof window === "undefined") return { token: null, user: null };
  try {
    if (isTokenExpired()) {
      clearStoredAuth();
      return { token: null, user: null };
    }
    const storedToken = localStorage.getItem(TOKEN_KEY);
    const storedUser = localStorage.getItem(USER_KEY);
    if (storedToken && storedUser) {
      return { token: storedToken, user: JSON.parse(storedUser) };
    }
  } catch {
    clearStoredAuth();
  }
  return { token: null, user: null };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // Lazy initializers — no useEffect + setState needed
  const [user, setUser] = useState<AuthUser | null>(() => getInitialAuth().user);
  const [token, setToken] = useState<string | null>(() => getInitialAuth().token);

  // Check token expiration periodically
  useEffect(() => {
    if (!token) return;
    const interval = setInterval(() => {
      if (isTokenExpired()) {
        clearStoredAuth();
        setToken(null);
        setUser(null);
      }
    }, 60_000);
    return () => clearInterval(interval);
  }, [token]);

  const login = useCallback(
    (newToken: string, newUser: AuthUser, expiresAt?: string) => {
      setToken(newToken);
      setUser(newUser);
      localStorage.setItem(TOKEN_KEY, newToken);
      localStorage.setItem(USER_KEY, JSON.stringify(newUser));
      if (expiresAt) {
        localStorage.setItem(EXPIRES_KEY, expiresAt);
      }
    },
    []
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    clearStoredAuth();
  }, []);

  return (
    <AuthContext
      value={{
        user,
        token,
        isAuthenticated: !!token && !!user,
        isLoading: false,
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

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  if (isTokenExpired()) {
    clearStoredAuth();
    return null;
  }
  return localStorage.getItem(TOKEN_KEY);
}
