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

const ModelConfigContext = createContext<ModelConfigContextValue | undefined>(
  undefined,
);

/**
 * Split from the value above on purpose. The rail's `connected/total` suffix
 * changes only when the SAVED config does, while the model context's value
 * changes on every draft keystroke — subscribing the shell to the latter
 * re-rendered the header and all seven rail icons on every character typed
 * into a `max_tokens` field.
 *
 * Three-valued, and the two empties are NOT interchangeable: `undefined` is
 * "no provider above me" (a bug, and it throws), `null` is "loaded nothing
 * yet" (render no badge). Defaulting to `null` made a missing provider look
 * exactly like a pending fetch, forever.
 */
const ProviderCountsContext = createContext<ProviderCounts | null | undefined>(
  undefined,
);

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
  // goes there, so the CONFIG is fetched when the surface opens rather than
  // when the Model tab happens to mount.
  //
  // Deliberately `loadConfig` and not `load`: the catalog is 15-odd providers'
  // worth of models and credential schemas that only the Model and Providers
  // tabs render, and most Settings visits are someone changing a theme. It also
  // keeps this effect free of `setLoading(true)` — no synchronous setState, so
  // opening Settings costs no extra commit.
  //
  // A failure here is deliberately silent: the badge is simply absent, and
  // nobody asked for model data yet. The guard resets on failure, so the tabs
  // retry and toast when the user actually opens one.
  const { loadConfig } = models;
  useEffect(() => {
    loadConfig().catch(() => {});
  }, [loadConfig]);

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

/**
 * The full model surface — catalog included — and the credential mutations.
 *
 * Reaching for this IS the request for the catalog, so it starts the full load.
 * That is the seam: `useProviderCounts` reads the eagerly-fetched config and
 * pulls nothing extra, while every consumer that renders a binding or a
 * credential schema needs the catalog by definition and gets it by asking for
 * this. The alternative — each tab remembering to call `load()` in its own
 * mount effect — is a rule that holds only while everyone remembers it, and
 * `providers-tab.tsx` already consumes the catalog without one.
 *
 * The load is fired in an effect, never during render, and failures are
 * swallowed here: the consumer's own `load()` shares the same promise and owns
 * the toast.
 */
export function useModelConfigContext(): ModelConfigContextValue {
  const value = useContext(ModelConfigContext);

  // Hooks before the throw, or this one would be conditional.
  const load = value?.models.load;
  useEffect(() => {
    load?.().catch(() => {});
  }, [load]);

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
 *
 * Reads only the eagerly-fetched config; it never pulls the catalog.
 */
export function useProviderCounts(): ProviderCounts | null {
  const value = useContext(ProviderCountsContext);
  if (value === undefined) {
    throw new Error(
      "useProviderCounts must be used inside <ModelConfigProvider>",
    );
  }
  return value;
}
