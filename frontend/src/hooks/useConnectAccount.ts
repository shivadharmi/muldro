"use client";

import { useCallback, useRef, useState } from "react";
import { beginConnection, confirmConnection } from "@/lib/api";
import { pollUntilActive, type PollResult } from "@/lib/connect-account";

/** Outcome of one provider's popup+poll cycle. "error" = the call itself failed. */
export type ProviderOutcome = PollResult | "error";

export type ConnectState =
  | "idle"
  | "connecting"
  | "active"
  | "partial"
  | "timeout"
  | "error";

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 150000; // ~2.5 min ceiling

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Run one provider's full popup + poll cycle. Never throws. */
async function connectProvider(
  provider: string,
  alias: string,
): Promise<ProviderOutcome> {
  try {
    const { authorization_url } = await beginConnection(provider, alias);
    const popup = window.open(authorization_url, "_blank", "width=520,height=680");
    const startedAt = Date.now();
    const result = await pollUntilActive(
      () => confirmConnection(provider, alias),
      {
        intervalMs: POLL_INTERVAL_MS,
        timeoutMs: POLL_TIMEOUT_MS,
        sleep,
        elapsed: () => Date.now() - startedAt,
        shouldStop: () => !!popup && popup.closed,
      },
    );
    if (popup && !popup.closed) popup.close();
    return result;
  } catch {
    return "error";
  }
}

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
  if (values.some((v) => v === "error")) return "error";
  if (values.some((v) => v === "timeout")) return "timeout";
  return "idle"; // every provider was cancelled by the user
}

export function useConnectAccount() {
  const [state, setState] = useState<ConnectState>("idle");
  const [results, setResults] = useState<Record<string, ProviderOutcome>>({});
  const runningRef = useRef(false);

  /**
   * Connect every provider of one installation, strictly in order: OC shows one
   * consent screen at a time, so a second window.open() while the first is
   * still pending would be popup-blocked or silently stolen focus. A provider
   * that fails, times out, or is cancelled is recorded and the walk continues.
   */
  const start = useCallback(
    async (
      providers: string[],
      alias = "default",
    ): Promise<Record<string, ProviderOutcome>> => {
      if (runningRef.current || providers.length === 0) return {};
      runningRef.current = true;
      setState("connecting");
      setResults({});
      const outcomes: Record<string, ProviderOutcome> = {};
      try {
        for (const provider of providers) {
          outcomes[provider] = await connectProvider(provider, alias);
          setResults({ ...outcomes });
        }
        setState(aggregateState(outcomes));
        return outcomes;
      } finally {
        // Always release the guard, so the next integration is not a no-op.
        runningRef.current = false;
      }
    },
    [],
  );

  return { state, results, start };
}
