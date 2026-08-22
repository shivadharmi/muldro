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
  /** Slug of the provider whose mutation is currently in flight, or `null`. */
  busy: string | null;
  save: (
    provider: string,
    fields: CredentialFields,
  ) => Promise<ProviderStatus>;
  test: (provider: string) => Promise<{ status: string }>;
  remove: (provider: string) => Promise<CredentialDeleteResult>;
}

/**
 * Owns the three provider-credential mutations. Every one of them refetches the
 * config afterwards (a credential change moves `ProviderStatus.configured` and
 * can add or clear `warnings`) and hands it to `onConfigRefreshed`.
 *
 * Each call RETURNS the API result rather than swallowing it, because the
 * consequence is not always "it worked": a delete reports the bindings it
 * orphaned, and only the caller can render that.
 *
 * Errors PROPAGATE — the calling component owns the toast.
 */
export function useProviderCredentials(
  onConfigRefreshed: (config: ModelConfig) => void,
): UseProviderCredentialsResult {
  const [busy, setBusy] = useState<string | null>(null);

  // Held in a ref so an inline callback does not re-create every action on
  // every render. Assigned in an effect, never during render.
  const callbackRef = useRef(onConfigRefreshed);
  useEffect(() => {
    callbackRef.current = onConfigRefreshed;
  }, [onConfigRefreshed]);

  const run = useCallback(
    async <T>(provider: string, action: () => Promise<T>): Promise<T> => {
      setBusy(provider);
      try {
        const result = await action();
        callbackRef.current(await fetchModelConfig());
        return result;
      } finally {
        setBusy(null);
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

  return useMemo(
    () => ({ busy, save, test, remove }),
    [busy, save, test, remove],
  );
}
