"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";

import { useToast } from "@/components/ui/toast";
import { errorToMessage } from "@/lib/api-error";
import {
  useModelConfig,
  type UseModelConfigResult,
} from "./hooks/use-model-config";
import {
  useProviderCredentials,
  type UseProviderCredentialsResult,
} from "./hooks/use-provider-credentials";

/** How many providers are connected, out of how many exist. */
export interface ProviderCounts {
  connected: number;
  total: number;
}

interface ModelConfigContextValue {
  models: UseModelConfigResult;
  credentials: UseProviderCredentialsResult;
}

const ModelConfigContext = createContext<ModelConfigContextValue | null>(null);

/**
 * Split from the value above on purpose. The rail's `connected/total` suffix
 * changes only when the SAVED config does, while the model context's value
 * changes on every draft keystroke — subscribing the shell to the latter
 * re-rendered the header and all seven rail icons on every character typed
 * into a `max_tokens` field.
 */
const ProviderCountsContext = createContext<ProviderCounts | null>(null);

/**
 * Holds the model/provider configuration for the whole settings surface.
 *
 * It exists because two consumers need one instance of that state: the Model
 * and Providers tabs, and the RAIL — which shows a connected/total suffix it
 * must not fetch for itself. Putting it back in `settings-modal.tsx` would
 * restore defect L5 (a shell owning every tab's data); a second `useModelConfig()`
 * call would be a second, silently divergent copy. Hoisting it one level above
 * both, in its own file, is neither.
 *
 * State lives here rather than in the Model tab so switching tabs does not
 * discard a loaded catalog and re-fetch it on return.
 */
export function ModelConfigProvider({ children }: { children: ReactNode }) {
  const { addToast } = useToast();
  const models = useModelConfig();
  // A post-mutation refetch failure is NOT a failed mutation -- the credential
  // change landed. Say what is actually wrong: the view is behind the server.
  const credentials = useProviderCredentials(models.applyServerConfig, (err) =>
    addToast(`Credentials saved, but the view is stale: ${errorToMessage(err)}`, "error"),
  );

  // The rail's badge exists to say "Providers needs attention" BEFORE the user
  // goes there, so the config is loaded when the surface opens rather than when
  // the Model tab happens to mount. `load()` is once-only and re-entrant, so
  // the Model tab's own call collapses into this one.
  //
  // A failure here is deliberately silent: the badge is simply absent, and
  // nobody asked for model data yet. `load()` clears its own guard on failure,
  // so the Model tab retries and toasts when the user actually opens it.
  const { load } = models;
  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  const value = useMemo(
    () => ({ models, credentials }),
    [models, credentials],
  );

  const providers = models.config?.providers;
  const counts = useMemo<ProviderCounts | null>(() => {
    if (!providers) return null;
    return {
      connected: providers.filter((p) => p.configured).length,
      total: providers.length,
    };
  }, [providers]);

  return (
    <ModelConfigContext.Provider value={value}>
      <ProviderCountsContext.Provider value={counts}>
        {children}
      </ProviderCountsContext.Provider>
    </ModelConfigContext.Provider>
  );
}

export function useModelConfigContext(): ModelConfigContextValue {
  const value = useContext(ModelConfigContext);
  if (!value) {
    throw new Error(
      "useModelConfigContext must be used inside <ModelConfigProvider>",
    );
  }
  return value;
}

/**
 * `connected/total` for the rail's Providers suffix, or `null` while the config
 * has not loaded — an unloaded config renders no suffix rather than `0/0`.
 */
export function useProviderCounts(): ProviderCounts | null {
  return useContext(ProviderCountsContext);
}
