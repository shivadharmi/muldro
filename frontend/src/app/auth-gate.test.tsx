/**
 * The gate must actually refuse. Its condition once ended in
 * `|| !process.env.NEXT_PUBLIC_REQUIRE_AUTH`, a variable set nowhere in the
 * repo, so it was permanently `undefined`, the term was permanently true, and
 * no reader was ever redirected. Signing out cleared the token and left the
 * founder looking at the full workspace.
 *
 * This renders the REAL AppShell rather than a copy of its condition — a test
 * that re-implements the rule passes no matter what the component does.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { test, expect, vi, beforeEach } from "vitest";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
const { authState } = vi.hoisted(() => ({
  authState: { current: { isAuthenticated: false, isLoading: false } },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace, push: vi.fn(), prefetch: vi.fn() }),
}));
vi.mock("@/lib/auth", () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => authState.current,
}));
vi.mock("@/lib/query-provider", () => ({
  QueryProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/lib/theme", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useTheme: () => ({ theme: "system", resolved: "light", setTheme: vi.fn() }),
}));
vi.mock("@/components/ui/toast", () => ({
  ToastProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useToast: () => ({ addToast: vi.fn() }),
}));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => <nav /> }));
vi.mock("@/components/shell/top-bar", () => ({ TopBar: () => <header /> }));
vi.mock("@/components/shell/context-sidebar", () => ({ ContextSidebar: () => <aside /> }));
vi.mock("@/components/shell/activity-strip", () => ({ ActivityStrip: () => <div /> }));
vi.mock("@/components/shell/command-launcher", () => ({ CommandLauncher: () => <div /> }));
vi.mock("@/components/settings/settings-modal", () => ({ SettingsModal: () => <div /> }));

import { AppShell } from "./app-shell";

beforeEach(() => {
  replace.mockClear();
  delete process.env.NEXT_PUBLIC_REQUIRE_AUTH;
});

test("a signed-out reader is sent to /login and shown no workspace", async () => {
  authState.current = { isAuthenticated: false, isLoading: false };
  render(<AppShell>
    <div>workspace content</div>
  </AppShell>);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  expect(screen.queryByText("workspace content")).toBeNull();
});

test("an authenticated reader is admitted", async () => {
  authState.current = { isAuthenticated: true, isLoading: false };
  render(<AppShell>
    <div>workspace content</div>
  </AppShell>);
  await waitFor(() => expect(screen.getByText("workspace content")).toBeTruthy());
  expect(replace).not.toHaveBeenCalled();
});

test("hydration is not treated as a signed-out state", async () => {
  // The token lives in localStorage and is unreadable during SSR, so
  // redirecting before hydration would bounce every signed-in reader.
  authState.current = { isAuthenticated: false, isLoading: true };
  render(<AppShell>
    <div>workspace content</div>
  </AppShell>);
  await waitFor(() => expect(screen.getByText("workspace content")).toBeTruthy());
  expect(replace).not.toHaveBeenCalled();
});

test("an unset opt-out env var cannot admit a signed-out reader", async () => {
  // The exact shape of the original defect: the variable is undefined here,
  // as it was in every environment including production.
  authState.current = { isAuthenticated: false, isLoading: false };
  render(<AppShell>
    <div>workspace content</div>
  </AppShell>);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  expect(screen.queryByText("workspace content")).toBeNull();
});
