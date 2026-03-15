"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { sendMagicLink, verifyMagicLink, getGoogleAuthUrl } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [step, setStep] = useState<"email" | "verify">("email");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const router = useRouter();

  async function handleSendLink(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await sendMagicLink(email);
      setStep("verify");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send magic link");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await verifyMagicLink(token);
      login(result.access_token, {
        user_id: result.user.user_id,
        email: result.user.email,
        display_name: result.user.display_name,
      });
      router.push("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid or expired token");
    } finally {
      setLoading(false);
    }
  }

  async function handleOAuth(provider: string) {
    setError("");
    setLoading(true);
    try {
      if (provider === "google") {
        const { url } = await getGoogleAuthUrl();
        window.location.href = url;
      } else {
        setError(`OAuth for ${provider} not yet configured`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start OAuth");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold">Jarvis</h1>
          <p className="mt-2 text-neutral-400">Personal AI Operating System</p>
        </div>

        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-6 space-y-6">
          {step === "email" ? (
            <form onSubmit={handleSendLink} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-neutral-300 mb-1">
                  Email address
                </label>
                <input
                  id="email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-3 py-2 text-white placeholder-neutral-500 focus:border-blue-500 focus:outline-none"
                  placeholder="you@company.com"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {loading ? "Sending..." : "Send Magic Link"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleVerify} className="space-y-4">
              <p className="text-sm text-neutral-400">
                Check your email for the magic link token.
              </p>
              <div>
                <label htmlFor="token" className="block text-sm font-medium text-neutral-300 mb-1">
                  Verification token
                </label>
                <input
                  id="token"
                  type="text"
                  required
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-3 py-2 text-white placeholder-neutral-500 focus:border-blue-500 focus:outline-none"
                  placeholder="Paste your token here"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {loading ? "Verifying..." : "Verify & Sign In"}
              </button>
              <button
                type="button"
                onClick={() => setStep("email")}
                className="w-full text-sm text-neutral-400 hover:text-white"
              >
                Back to email
              </button>
            </form>
          )}

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-neutral-700" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="bg-neutral-900 px-2 text-neutral-500">Or continue with</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={() => handleOAuth("google")}
              className="flex items-center justify-center gap-2 rounded-lg border border-neutral-700 px-4 py-2 text-sm font-medium text-neutral-300 hover:bg-neutral-800"
            >
              Google
            </button>
            <button
              onClick={() => handleOAuth("github")}
              className="flex items-center justify-center gap-2 rounded-lg border border-neutral-700 px-4 py-2 text-sm font-medium text-neutral-300 hover:bg-neutral-800"
            >
              GitHub
            </button>
          </div>

          {error && (
            <div className="rounded-lg bg-red-900/20 border border-red-800 p-3 text-sm text-red-400">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
