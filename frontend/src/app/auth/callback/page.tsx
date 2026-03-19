"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";

function CallbackHandler() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { login } = useAuth();

  const token = searchParams.get("token");
  const userId = searchParams.get("user_id");
  const email = searchParams.get("email");
  const displayName = searchParams.get("display_name");
  const errorParam = searchParams.get("error");

  const hasCredentials = !!(token && userId && email);
  const error = hasCredentials
    ? ""
    : errorParam || "Authentication failed — missing token or user info.";

  useEffect(() => {
    if (hasCredentials) {
      login(token, {
        user_id: userId,
        email,
        display_name: displayName,
      });
      router.replace("/chat");
    }
  }, [hasCredentials, token, userId, email, displayName, login, router]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-md rounded-xl border border-red-800 bg-neutral-900 p-6 text-center space-y-4">
          <h2 className="text-lg font-semibold text-red-400">Login Failed</h2>
          <p className="text-sm text-neutral-400">{error}</p>
          <button
            onClick={() => router.push("/login")}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Back to Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-neutral-400">Signing you in...</p>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <p className="text-neutral-400">Loading...</p>
        </div>
      }
    >
      <CallbackHandler />
    </Suspense>
  );
}
