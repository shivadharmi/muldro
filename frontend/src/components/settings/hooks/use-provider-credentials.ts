import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  deleteProviderKey,
  fetchModelConfig,
  saveProviderCredential,
  testProviderKey,
} from "@/lib/api";
import type {
  CredentialDeleteResult,
  ModelConfig,
  ProviderStatus,
} from "@/lib/types";

/** Partial credential update. `JSON.stringify` drops `undefined`, which is what
 *  makes omission expressible — pass `null` to clear a field deliberately. */
export interface CredentialFields {
  api_key?: string;
  base_url?: string | null;
  extra_config?: Record<string, unknown> | null;
}

export interface UseProviderCredentialsResult {
  /** The most recently started provider still in flight, or `null` when idle.
   *  Inherently lossy under overlap — use {@link isBusy} for per-row state. */
  busy: string | null;
  /** Every provider with a mutation in flight. Overlapping mutations each keep
   *  their own row spinning, and one finishing does not clear the others. */
  busyProviders: ReadonlySet<string>;
  isBusy: (provider: string) => boolean;
  /** The last post-mutation refetch failed, so the config on screen is older
   *  than the server's. The mutation itself still SUCCEEDED. Cleared by the
   *  next refetch that lands. */
  stale: boolean;
  save: (provider: string, fields: CredentialFields) => Promise<ProviderStatus>;
  test: (provider: string) => Promise<{ status: string }>;
  remove: (provider: string) => Promise<CredentialDeleteResult>;
}

/**
 * Owns the three provider-credential mutations. Every one of them refetches the
 * config afterwards (a credential change moves `ProviderStatus.configured` and
 * can add or clear `warnings`) and hands it to `onConfigRefreshed`.
 *
 * Each call RETURNS the API result rather than swallowing it, because the
 * consequence is not always "it worked": a revoke reports the bindings it
 * orphaned, and only the caller can render that.
 *
 * The mutation and the refetch are SEPARATE failure classes. A mutation error
 * propagates — the calling component owns that toast. A refetch error does not:
 * the write already succeeded, so reporting it as a failed mutation would tell
 * the user the opposite of the truth, and would throw away the result they most
 * need (which tiers a revoke just broke). It surfaces as `stale` plus the
 * optional `onRefreshFailed`, and is never swallowed silently.
 */
export function useProviderCredentials(
  onConfigRefreshed: (config: ModelConfig) => void,
  onRefreshFailed?: (err: unknown) => void,
): UseProviderCredentialsResult {
  const [busyProviders, setBusyProviders] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const [stale, setStale] = useState(false);

  // Held in refs so an inline callback does not re-create every action on
  // every render. Assigned in an effect, never during render.
  const refreshedRef = useRef(onConfigRefreshed);
  const refreshFailedRef = useRef(onRefreshFailed);
  useEffect(() => {
    refreshedRef.current = onConfigRefreshed;
  }, [onConfigRefreshed]);
  useEffect(() => {
    refreshFailedRef.current = onRefreshFailed;
  }, [onRefreshFailed]);

  const run = useCallback(
    async <T>(provider: string, action: () => Promise<T>): Promise<T> => {
      setBusyProviders((prev) => {
        const next = new Set(prev);
        next.add(provider);
        return next;
      });
      try {
        const result = await action();
        try {
          refreshedRef.current(await fetchModelConfig());
          setStale(false);
        } catch (err) {
          setStale(true);
          refreshFailedRef.current?.(err);
        }
        return result;
      } finally {
        setBusyProviders((prev) => {
          if (!prev.has(provider)) return prev;
          const next = new Set(prev);
          next.delete(provider);
          return next;
        });
      }
    },
    [],
  );

  const save = useCallback(
    (provider: string, fields: CredentialFields) =>
      run(provider, () => saveProviderCredential(provider, fields)),
    [run],
  );

  const test = useCallback(
    (provider: string) => run(provider, () => testProviderKey(provider)),
    [run],
  );

  const remove = useCallback(
    (provider: string) => run(provider, () => deleteProviderKey(provider)),
    [run],
  );

  const isBusy = useCallback(
    (provider: string) => busyProviders.has(provider),
    [busyProviders],
  );

  // Set preserves insertion order, so "last started" is deterministic.
  const busy = useMemo(() => {
    let latest: string | null = null;
    for (const provider of busyProviders) latest = provider;
    return latest;
  }, [busyProviders]);

  return useMemo(
    () => ({ busy, busyProviders, isBusy, stale, save, test, remove }),
    [busy, busyProviders, isBusy, stale, save, test, remove],
  );
}
