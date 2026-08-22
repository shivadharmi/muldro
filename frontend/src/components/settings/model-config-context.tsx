"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

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
import type { ProviderCounts } from "./settings-rail";

interface ModelConfigContextValue {
  models: UseModelConfigResult;
  credentials: UseProviderCredentialsResult;
}

const ModelConfigContext = createContext<ModelConfigContextValue | null>(null);

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

  const value = useMemo(
    () => ({ models, credentials }),
    [models, credentials],
  );

  return (
    <ModelConfigContext.Provider value={value}>
      {children}
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
  const { models } = useModelConfigContext();
  const providers = models.config?.providers;
  return useMemo(() => {
    if (!providers) return null;
    return {
      connected: providers.filter((p) => p.configured).length,
      total: providers.length,
    };
  }, [providers]);
}
