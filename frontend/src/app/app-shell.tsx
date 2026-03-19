"use client";

import { useState, useEffect, useCallback, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { QueryProvider } from "@/lib/query-provider";
import { AuthProvider, useAuth } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";
import { ToastProvider } from "@/components/ui/toast";
import { ErrorBoundary } from "@/components/error-boundary";
import { Sidebar } from "@/components/layout/sidebar";

const PUBLIC_ROUTES = ["/login", "/auth/callback"];

function AuthGate({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  const [mobileOpenFor, setMobileOpenFor] = useState<string | null>(null);
  const mobileOpen = mobileOpenFor === pathname;
  const setMobileOpen = (open: boolean) => setMobileOpenFor(open ? pathname : null);

  const isPublic = PUBLIC_ROUTES.some((r) => pathname.startsWith(r));
  const hasAccess =
    isLoading || isAuthenticated || !process.env.NEXT_PUBLIC_REQUIRE_AUTH;

  useEffect(() => {
    if (!isPublic && !hasAccess) {
      router.replace("/login");
    }
  }, [isPublic, hasAccess, router]);

  const toggleSidebar = useCallback(() => {
    if (window.innerWidth < 768) {
      setMobileOpenFor((prev) => (prev === pathname ? null : pathname));
    } else {
      setSidebarCollapsed((p) => !p);
    }
  }, [pathname]);

  if (isPublic) return <>{children}</>;

  if (hasAccess) {
    return (
      <div className="flex h-screen relative">
        {/* Mobile hamburger */}
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="fixed top-3 left-3 z-50 p-2 rounded-[var(--radius-sm)] bg-surface-1 border border-b-primary text-t-secondary hover:text-t-primary md:hidden shadow-[var(--shadow-sm)] cursor-pointer"
          aria-label="Toggle navigation"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            {mobileOpen ? (
              <path
                d="M5 5l10 10M15 5L5 15"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            ) : (
              <path
                d="M3 5h14M3 10h14M3 15h14"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            )}
          </svg>
        </button>

        {/* Mobile backdrop */}
        {mobileOpen && (
          <div
            className="fixed inset-0 bg-black/50 z-30 md:hidden backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
        )}

        {/* Mobile sidebar — always expanded (full labels) */}
        <div
          className={`
            fixed inset-y-0 left-0 z-40 transform transition-transform duration-200 ease-in-out
            md:hidden
            ${mobileOpen ? "translate-x-0" : "-translate-x-full"}
          `}
        >
          <Sidebar collapsed={false} onToggle={() => setMobileOpen(false)} />
        </div>

        {/* Desktop sidebar — collapsible */}
        <div className="hidden md:block">
          <Sidebar collapsed={sidebarCollapsed} onToggle={toggleSidebar} />
        </div>

        <main className="flex-1 overflow-y-auto pl-14 md:pl-0">
          <ErrorBoundary>{children}</ErrorBoundary>
        </main>
      </div>
    );
  }

  return null;
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <AuthProvider>
        <ThemeProvider>
          <ToastProvider>
            <AuthGate>{children}</AuthGate>
          </ToastProvider>
        </ThemeProvider>
      </AuthProvider>
    </QueryProvider>
  );
}
