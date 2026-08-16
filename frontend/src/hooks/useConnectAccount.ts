"use client";

import { useCallback, useRef, useState } from "react";
import { beginConnection, confirmConnection } from "@/lib/api";
import { pollUntilActive, type PollResult } from "@/lib/connect-account";

type ConnectState = "idle" | "connecting" | "active" | "timeout" | "error";

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 150000; // ~2.5 min ceiling

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function useConnectAccount() {
  const [state, setState] = useState<ConnectState>("idle");
  const runningRef = useRef(false);

  const start = useCallback(async (provider: string, alias = "default") => {
    if (runningRef.current) return;
    runningRef.current = true;
    setState("connecting");
    try {
      const { authorization_url } = await beginConnection(provider, alias);
      const popup = window.open(authorization_url, "_blank", "width=520,height=680");
      const startedAt = Date.now();
      const result: PollResult = await pollUntilActive(
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
      setState(result === "active" ? "active" : result === "timeout" ? "timeout" : "idle");
    } catch {
      setState("error");
    } finally {
      runningRef.current = false;
    }
  }, []);

  return { state, start };
}
