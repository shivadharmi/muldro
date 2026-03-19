"use client";

import { useState, useEffect, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { QueryProvider } from "@/lib/query-provider";
import { AuthProvider, useAuth } from "@/lib/auth";
import { ToastProvider } from "@/components/ui/toast";
import { Sidebar } from "@/components/layout/sidebar";

const PUBLIC_ROUTES = ["/login", "/auth/callback"];

function AuthGate({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [sidebarOpenFor, setSidebarOpenFor] = useState<string | null>(null);

  const isPublic = PUBLIC_ROUTES.some((r) => pathname.startsWith(r));
  const hasAccess = isLoading || isAuthenticated || !process.env.NEXT_PUBLIC_REQUIRE_AUTH;

  // Sidebar auto-closes on navigation: only open when pathname matches
  const sidebarOpen = sidebarOpenFor === pathname;
  const setSidebarOpen = (open: boolean) =>
    setSidebarOpenFor(open ? pathname : null);

  // Redirect to login when not authenticated
  useEffect(() => {
    if (!isPublic && !hasAccess) {
      router.replace("/login");
    }
  }, [isPublic, hasAccess, router]);

  // Public routes don't need auth
  if (isPublic) {
    return <>{children}</>;
  }

  // Allow access if loading (prevents flash) or authenticated
  // In dev mode without auth configured, always allow
  if (hasAccess) {
    return (
      <div className="flex h-screen relative">
        {/* Mobile hamburger button */}
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="fixed top-3 left-3 z-50 p-2 rounded-lg bg-neutral-900 border border-neutral-700 text-neutral-300 hover:text-white md:hidden"
          aria-label="Toggle navigation"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            {sidebarOpen ? (
              <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            ) : (
              <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            )}
          </svg>
        </button>

        {/* Backdrop overlay for mobile */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-black/50 z-30 md:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Sidebar: hidden on mobile unless open */}
        <div className={`
          fixed inset-y-0 left-0 z-40 transform transition-transform duration-200 ease-in-out
          md:relative md:translate-x-0
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}>
          <Sidebar />
        </div>

        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    );
  }

  // Waiting for redirect
  return null;
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <AuthProvider>
        <ToastProvider>
          <AuthGate>{children}</AuthGate>
        </ToastProvider>
      </AuthProvider>
    </QueryProvider>
  );
}
