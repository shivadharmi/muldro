"use client";

import { useCallback, useRef, useState } from "react";
import { beginConnection, confirmConnection } from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";
import { pollUntilActive, type PollResult } from "@/lib/connect-account";

/**
 * Outcome of one provider's popup+poll cycle.
 * - "blocked" = the browser refused to open the consent popup at all.
 * - "error"   = the begin/confirm call itself failed.
 */
export type ProviderOutcome = PollResult | "error" | "blocked";

export type ConnectState =
  | "idle"
  | "connecting"
  | "active"
  | "partial"
  | "blocked"
  | "cancelled"
  | "timeout"
  | "error";

/** Everything one `start()` walk produced. Returned so callers need no effect. */
export interface ConnectRun {
  /** Per-provider outcome, in walk order. */
  outcomes: Record<string, ProviderOutcome>;
  /** Client-safe failure message per provider that returned "error". */
  errors: Record<string, string>;
  /** The headline state these outcomes collapse to. */
  state: ConnectState;
}

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 150000; // ~2.5 min ceiling

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface ProviderResult {
  outcome: ProviderOutcome;
  /** Present only for "error" — the client-safe cause, never swallowed. */
  message?: string;
}

/** Run one provider's full popup + poll cycle. Never throws. */
async function connectProvider(
  provider: string,
  alias: string,
): Promise<ProviderResult> {
  try {
    const { authorization_url } = await beginConnection(provider, alias);
    const popup = window.open(authorization_url, "_blank", "width=520,height=680");
    if (!popup) {
      // window.open returns null when the browser blocked the popup. There is
      // no window to approve in, so polling would burn the full 2.5-min ceiling
      // and then lie about it as a "timeout". Report it as its own outcome so
      // the UI can offer a fresh click, which is the only way to recover:
      // popups need transient user activation, and it has already lapsed.
      return { outcome: "blocked" };
    }
    const startedAt = Date.now();
    const result = await pollUntilActive(
      () => confirmConnection(provider, alias),
      {
        intervalMs: POLL_INTERVAL_MS,
        timeoutMs: POLL_TIMEOUT_MS,
        sleep,
        elapsed: () => Date.now() - startedAt,
        shouldStop: () => popup.closed,
      },
    );
    if (!popup.closed) popup.close();
    return { outcome: result };
  } catch (err) {
    // Never swallow the cause: a 503 "connection service not configured"
    // (OpenConnector unset in local dev) must be distinguishable from a blip.
    return { outcome: "error", message: errorToMessage(err) };
  }
}

/**
 * Non-active outcomes ranked most-actionable-first. The headline state is the
 * highest-ranked outcome present, so a run never reports the *least* useful
 * thing that happened: a blocked popup (recoverable with one click) outranks a
 * deliberate cancel, which outranks an ambiguous 2.5-min timeout, which
 * outranks an opaque error. Without this, `{timeout, error}` read as a bland
 * "error" and hid that a provider had polled for two and a half minutes.
 */
const OUTCOME_PRECEDENCE = [
  "blocked",
  "cancelled",
  "timeout",
  "error",
] as const satisfies readonly Exclude<ProviderOutcome, "active">[];

/**
 * Collapse per-provider outcomes into one headline state. A mix of connected
 * and not-connected is "partial" — it must not read as a clean success or a
 * clean failure, because one installation can fan out to several providers.
 */
function aggregateState(
  outcomes: Record<string, ProviderOutcome>,
): ConnectState {
  const values = Object.values(outcomes);
  if (values.length === 0) return "idle";
  if (values.every((v) => v === "active")) return "active";
  if (values.some((v) => v === "active")) return "partial";
  for (const outcome of OUTCOME_PRECEDENCE) {
    if (values.includes(outcome)) return outcome;
  }
  return "idle";
}

export function useConnectAccount() {
  const [state, setState] = useState<ConnectState>("idle");
  const [results, setResults] = useState<Record<string, ProviderOutcome>>({});
  const runningRef = useRef(false);

  /**
   * Connect every provider of one installation, strictly in order: OC serves
   * one consent screen per connection, and a second window.open() while the
   * first is still pending would steal focus from a consent the user is midway
   * through. A provider that fails, times out, is blocked, or is cancelled is
   * recorded and the walk continues.
   *
   * Note the cost of sequencing: only the FIRST popup rides the click's
   * transient user activation. Every later provider opens long after that
   * ~5s window has lapsed (and consent happened in the popup, which does not
   * re-activate this opener), so provider >= 2 is the one most likely to come
   * back "blocked" — which is exactly why "blocked" is a first-class outcome.
   *
   * Returns null — NOT an empty run — when another walk already owns the flow,
   * so a caller can tell "rejected, nothing happened" from "ran, found nothing
   * to do" and leave the in-flight card's pending state alone.
   */
  const start = useCallback(
    async (
      providers: string[],
      alias = "default",
    ): Promise<ConnectRun | null> => {
      if (runningRef.current) return null;
      if (providers.length === 0) {
        return { outcomes: {}, errors: {}, state: "idle" };
      }
      runningRef.current = true;
      setState("connecting");
      setResults({});
      const outcomes: Record<string, ProviderOutcome> = {};
      const errors: Record<string, string> = {};
      try {
        for (const provider of providers) {
          const { outcome, message } = await connectProvider(provider, alias);
          outcomes[provider] = outcome;
          if (message) errors[provider] = message;
          setResults({ ...outcomes });
        }
        const finalState = aggregateState(outcomes);
        setState(finalState);
        return { outcomes, errors, state: finalState };
      } finally {
        // Always release the guard, so the next integration is not a no-op.
        runningRef.current = false;
      }
    },
    [],
  );

  return { state, results, start };
}
