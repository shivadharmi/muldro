"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { QueryProvider } from "@/lib/query-provider";
import { AuthProvider, useAuth } from "@/lib/auth";
import { Sidebar } from "@/components/layout/sidebar";

const PUBLIC_ROUTES = ["/login", "/auth/callback"];

function AuthGate({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const pathname = usePathname();

  // Public routes don't need auth
  if (PUBLIC_ROUTES.some((r) => pathname.startsWith(r))) {
    return <>{children}</>;
  }

  // Allow access if loading (prevents flash) or authenticated
  // In dev mode without auth configured, always allow
  if (isLoading || isAuthenticated || !process.env.NEXT_PUBLIC_REQUIRE_AUTH) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    );
  }

  // Redirect to login
  if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
  return null;
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <AuthProvider>
        <AuthGate>{children}</AuthGate>
      </AuthProvider>
    </QueryProvider>
  );
}
