/**
 * Where the session actually lives: localStorage, not a cookie.
 *
 * Split out of `auth.tsx` so the API client can read the token without
 * importing the React provider — `auth.tsx` needs `logoutSession` from the
 * client, and the two importing each other is a cycle that only works by
 * accident of every reference being call-time.
 *
 * Note for anyone hunting an auth bug: clearing cookies does nothing here.
 * These three keys are the whole session as far as the browser is concerned.
 */

export interface AuthUser {
  user_id: string;
  email: string;
  display_name: string | null;
}

export const TOKEN_KEY = "muldro_auth_token";
export const USER_KEY = "muldro_auth_user";
export const EXPIRES_KEY = "muldro_auth_expires";

export function isTokenExpired(): boolean {
  if (typeof window === "undefined") return false;
  const expiresAt = localStorage.getItem(EXPIRES_KEY);
  if (!expiresAt) return false;
  return new Date(expiresAt).getTime() <= Date.now();
}

export function clearStoredAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(EXPIRES_KEY);
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  if (isTokenExpired()) {
    clearStoredAuth();
    return null;
  }
  return localStorage.getItem(TOKEN_KEY);
}
