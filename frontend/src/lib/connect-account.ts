import type { ConfirmConnectionResponse } from "./api";

export type PollResult = "active" | "timeout" | "cancelled";

export interface PollOptions {
  intervalMs: number;
  timeoutMs: number;
  sleep: (ms: number) => Promise<void>;
  // Monotonic elapsed-ms source; injected so tests need no real clock.
  elapsed: () => number;
  // Optional early-abort (e.g. the consent popup was closed by the user).
  shouldStop?: () => boolean;
}

/**
 * Poll `confirm` until it reports "active", the timeout ceiling is reached, or
 * `shouldStop()` fires. Pure: all time + side effects are injected, so this is
 * unit-testable without a real clock or DOM. The React hook wraps it.
 *
 * When `shouldStop()` fires we confirm ONE more time before reporting a cancel.
 * The popup very often closes *because* the user just approved: the grant is
 * live at the gateway, but `/connections/confirm` is what activates our own
 * connection row. Returning "cancelled" without that last call would leave a
 * genuinely connected provider reading "Not connected" until a manual reload.
 */
export async function pollUntilActive(
  confirm: () => Promise<ConfirmConnectionResponse>,
  opts: PollOptions,
): Promise<PollResult> {
  for (;;) {
    if (opts.shouldStop?.()) {
      const { status } = await confirm();
      return status === "active" ? "active" : "cancelled";
    }
    const { status } = await confirm();
    if (status === "active") return "active";
    if (opts.elapsed() >= opts.timeoutMs) return "timeout";
    await opts.sleep(opts.intervalMs);
  }
}
