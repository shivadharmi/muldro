import { useCallback, useMemo, useRef, useState } from "react";

import { fetchModelCatalog, fetchModelConfig } from "@/lib/api";
import type { ModelCatalog, ModelConfig } from "@/lib/types";

export interface ModelLoads {
  catalog: ModelCatalog | null;
  /** True only during a {@link ModelLoads.load}. The eager config-only fetch
   *  deliberately does not raise it — nothing renders a spinner for a badge,
   *  and flipping it would cost every Settings open an extra commit. */
  loading: boolean;
  /** Fetches the catalog AND the config, exactly once. Concurrent callers get
   *  the SAME promise, so each observes the outcome; a failure resets the guard
   *  so a retry works, and re-throws so the caller can toast. Collapses into an
   *  in-flight or completed {@link ModelLoads.loadConfig} rather than
   *  re-fetching it.
   *
   *  For a surface that renders bindings or credential schemas — the Model and
   *  Providers tabs. The rail's badge must NOT use it: it would pull a catalog
   *  nobody has asked to look at. */
  load: () => Promise<void>;
  /** Fetches the config alone. This is the eager one: the rail's
   *  `connected/total` badge exists to flag Providers BEFORE the user goes
   *  there, and `config.providers` is all it needs. */
  loadConfig: () => Promise<void>;
}

/**
 * The fetch lifecycle behind `useModelConfig` — what has been asked for, what
 * is in flight, and what may be skipped — kept apart from the draft state it
 * feeds. The two answer different questions and change for different reasons.
 *
 * `adopt` is how a fetched config re-enters that state; it must be referentially
 * stable, since both loaders are memoised on it.
 */
export function useModelLoads(
  adopt: (config: ModelConfig) => void,
): ModelLoads {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [loading, setLoading] = useState(false);

  // In-flight promises double as the re-entrancy guards: a second caller gets
  // the first call's promise rather than a silent `undefined` or a duplicate
  // request whose completion order would decide which response wins.
  //
  // The two loads need SEPARATE guards. Sharing one would let the eager
  // config-only fetch mark the catalog as loaded, and a tab that needs the
  // catalog would then never fetch it.
  const loadPromiseRef = useRef<Promise<void> | null>(null);
  const configPromiseRef = useRef<Promise<void> | null>(null);
  const loadedOnce = useRef(false);
  const configLoadedOnce = useRef(false);

  const loadConfig = useCallback((): Promise<void> => {
    if (configPromiseRef.current) return configPromiseRef.current;
    if (configLoadedOnce.current) return Promise.resolve();

    const promise = (async () => {
      try {
        // Adopted, not assigned. A credential mutation can land its own refetch
        // while this one is still in flight, and the eager badge fetch must not
        // be the thing that discards an edit.
        adopt(await fetchModelConfig());
        configLoadedOnce.current = true;
      } finally {
        configPromiseRef.current = null;
      }
    })();

    configPromiseRef.current = promise;
    return promise;
  }, [adopt]);

  const load = useCallback((): Promise<void> => {
    if (loadPromiseRef.current) return loadPromiseRef.current;
    if (loadedOnce.current) return Promise.resolve();
    loadedOnce.current = true;
    setLoading(true);

    const promise = (async () => {
      try {
        // `loadConfig()` collapses into the eager fetch when there is one, so
        // opening the Model tab costs one GET, not two.
        const [nextCatalog] = await Promise.all([
          fetchModelCatalog(),
          loadConfig(),
        ]);
        setCatalog(nextCatalog);
      } catch (err) {
        loadedOnce.current = false;
        throw err;
      } finally {
        setLoading(false);
        loadPromiseRef.current = null;
      }
    })();

    loadPromiseRef.current = promise;
    return promise;
  }, [loadConfig]);

  return useMemo(
    () => ({ catalog, loading, load, loadConfig }),
    [catalog, loading, load, loadConfig],
  );
}
