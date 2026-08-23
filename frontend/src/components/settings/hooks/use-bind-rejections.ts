import { useCallback, useMemo, useState } from "react";

import type { ConfigWarning, ModelBinding } from "@/lib/types";
import { findByScope } from "./model-draft";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Pull the per-binding rejections off a `PUT /v1/model-config` 422.
 *
 * The gate is STRUCTURAL, not `instanceof ApiError`: the class lives in
 * `@/lib/api` (not `@/lib/api-error`, which only holds the `BindRejection`
 * shape and the parsers), and every consumer that mocks `@/lib/api` would lose
 * an `instanceof` check.
 *
 * Fields are NOT re-parsed — `parseBindRejection` (`@/lib/api-error`) already
 * validated them and filled a missing `message` with `SAFE_FALLBACK_MESSAGE`,
 * and a second per-field parse here only created a chance to disagree with it.
 * The shape check below is the minimum that keeps a malformed payload from
 * reaching JSX as `rejectionFor(...)!.message === undefined`; anything that
 * fails it falls back to the caller's generic toast.
 */
export function extractBindRejections(err: unknown): ConfigWarning[] | null {
  if (!isRecord(err)) return null;
  const raw = err.bindRejections;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  for (const entry of raw) {
    if (!isRecord(entry)) return null;
    if (typeof entry.scope_key !== "string") return null;
    if (typeof entry.message !== "string") return null;
  }
  return raw as ConfigWarning[];
}

export interface UseBindRejectionsResult {
  /** Per-binding rejections from the most recent 422, so a tier card can render
   *  its own verdict ON the card rather than as an anonymous toast. */
  rejections: ConfigWarning[];
  /** Records the rejections a 422 carries. A non-422 error leaves the existing
   *  ones alone — the caller toasts that, and the cards keep the server's last
   *  verdict rather than blanking on an unrelated network blip. */
  record: (err: unknown) => void;
  clear: () => void;
  /** Drops one binding's verdict — the user is editing it, so the verdict is
   *  about a value that no longer exists. */
  dropFor: (scopeType: ModelBinding["scope_type"], scopeKey: string) => void;
  rejectionFor: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
  ) => ConfigWarning | undefined;
}

/** The 422 bind-rejection half of the model config surface. Split out of
 *  `useModelConfig` because it is self-contained: its own state, its own
 *  lifecycle, and no dependency on the draft. */
export function useBindRejections(): UseBindRejectionsResult {
  const [rejections, setRejections] = useState<ConfigWarning[]>([]);

  const record = useCallback((err: unknown) => {
    const rejected = extractBindRejections(err);
    if (rejected) setRejections(rejected);
  }, []);

  const clear = useCallback(() => {
    setRejections((prev) => (prev.length === 0 ? prev : []));
  }, []);

  const dropFor = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) => {
      setRejections((prev) =>
        prev.some((r) => r.scope_type === scopeType && r.scope_key === scopeKey)
          ? prev.filter(
              (r) => !(r.scope_type === scopeType && r.scope_key === scopeKey),
            )
          : prev,
      );
    },
    [],
  );

  const rejectionFor = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) =>
      findByScope(rejections, scopeType, scopeKey),
    [rejections],
  );

  return useMemo(
    () => ({ rejections, record, clear, dropFor, rejectionFor }),
    [rejections, record, clear, dropFor, rejectionFor],
  );
}
