"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { sendMagicLink, verifyMagicLink, getGoogleAuthUrl } from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";

function LoginForm() {
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [step, setStep] = useState<"email" | "verify">("email");
  const [error, setError] = useState("");
  const [devToken, setDevToken] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  const verifyWithToken = useCallback(
    async (tokenValue: string) => {
      setError("");
      setLoading(true);
      try {
        const result = await verifyMagicLink(tokenValue);
        login(result.access_token, {
          user_id: result.user.user_id,
          email: result.user.email,
          display_name: result.user.display_name,
        });
        router.push("/chat");
      } catch (err) {
        setError(errorToMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [login, router],
  );

  // Auto-verify when ?token= query param is present (from magic link email)
  useEffect(() => {
    const urlToken = searchParams.get("token");
    if (urlToken) {
      verifyWithToken(urlToken);
    }
  }, [searchParams, verifyWithToken]);

  async function handleSendLink(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const resp = await sendMagicLink(email);
      // In dev mode, the API returns the token directly
      if (resp.token) {
        setDevToken(resp.token);
        setToken(resp.token);
      }
      setStep("verify");
    } catch (err) {
      setError(errorToMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault();
    await verifyWithToken(token);
  }

  async function handleOAuth(provider: string) {
    setError("");
    setLoading(true);
    try {
      if (provider === "google") {
        const { url } = await getGoogleAuthUrl();
        window.location.assign(url);
      } else {
        setError(`OAuth for ${provider} not yet configured`);
      }
    } catch (err) {
      setError(errorToMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4 relative overflow-hidden">
      {/* Atmospheric background */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 50% 60% at 20% 30%, hsl(193 100% 62% / 0.04), transparent 60%), radial-gradient(ellipse 40% 50% at 80% 70%, hsl(247 80% 72% / 0.03), transparent 50%)",
        }}
      />
      <div className="w-full max-w-[400px] space-y-8 relative">
        <div className="text-center">
          <h1 className="text-3xl font-semibold tracking-tight brand-gradient-text inline-block">Jarvis</h1>
          <p className="mt-1.5 text-sm text-t-tertiary">Personal AI Operating System</p>
        </div>

        <div className="rounded-[var(--radius-xl)] border border-b-secondary bg-surface-1 p-6 space-y-6 shadow-[var(--shadow-md)]">
          {step === "email" ? (
            <form onSubmit={handleSendLink} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-t-primary mb-1">
                  Email address
                </label>
                <input
                  id="email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full rounded-[var(--radius-md)] border border-b-secondary bg-surface-2 px-3.5 py-2.5 text-sm text-t-primary placeholder-t-muted focus:border-j-primary focus:outline-none focus:ring-1 focus:ring-j-primary/30 transition-colors"
                  placeholder="you@company.com"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-[var(--radius-md)] bg-j-primary px-4 py-2.5 text-sm font-medium text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50 transition-colors shadow-[var(--shadow-sm)]"
              >
                {loading ? "Sending..." : "Send Magic Link"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleVerify} className="space-y-4">
              {devToken ? (
                <p className="text-sm text-j-success">
                  Dev mode: token auto-filled below. Click &quot;Verify &amp; Sign In&quot;.
                </p>
              ) : (
                <p className="text-sm text-t-secondary">
                  Check your email for the magic link token.
                </p>
              )}
              <div>
                <label htmlFor="token" className="block text-sm font-medium text-t-primary mb-1">
                  Verification token
                </label>
                <input
                  id="token"
                  type="text"
                  required
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className="w-full rounded-[var(--radius-md)] border border-b-secondary bg-surface-2 px-3.5 py-2.5 text-sm text-t-primary placeholder-t-muted focus:border-j-primary focus:outline-none focus:ring-1 focus:ring-j-primary/30 transition-colors"
                  placeholder="Paste your token here"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-[var(--radius-md)] bg-j-primary px-4 py-2.5 text-sm font-medium text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50 transition-colors shadow-[var(--shadow-sm)]"
              >
                {loading ? "Verifying..." : "Verify & Sign In"}
              </button>
              <button
                type="button"
                onClick={() => setStep("email")}
                className="w-full text-sm text-t-secondary hover:text-t-primary"
              >
                Back to email
              </button>
            </form>
          )}

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-b-primary" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="bg-surface-1 px-2 text-t-tertiary">Or continue with</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={() => handleOAuth("google")}
              className="flex items-center justify-center gap-2 rounded-[var(--radius-md)] border border-b-secondary px-4 py-2.5 text-sm font-medium text-t-primary hover:bg-surface-2 transition-colors"
            >
              Google
            </button>
            <button
              onClick={() => handleOAuth("github")}
              className="flex items-center justify-center gap-2 rounded-[var(--radius-md)] border border-b-secondary px-4 py-2.5 text-sm font-medium text-t-primary hover:bg-surface-2 transition-colors"
            >
              GitHub
            </button>
          </div>

          {error && (
            <div className="rounded-[var(--radius-md)] bg-j-error-soft border border-j-error/20 p-3 text-sm text-j-error animate-slide-in-up">
              {error}
            </div>
          )}

          {/* Demo Login — seeds the session directly */}
          {process.env.NODE_ENV === "development" && (
            <>
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-b-primary" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="bg-surface-1 px-2 text-t-tertiary">Dev mode</span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  login("demo-session-token-for-jarvis-ui-dev", {
                    user_id: "usr_01KM2EMPNB8WYN2E2S286DJ52J",
                    email: "founder@jarvis.dev",
                    display_name: "Demo Founder",
                  });
                  router.push("/");
                }}
                className="w-full rounded-[var(--radius-md)] border border-j-primary/20 bg-j-primary-soft px-4 py-2.5 text-sm font-medium text-j-primary hover:bg-j-primary/20 transition-colors"
              >
                Demo Login (seeded data)
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
