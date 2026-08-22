import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchModelCatalog, fetchModelConfig, saveModelConfig } from "@/lib/api";
import type {
  ConfigWarning,
  ModelBinding,
  ModelCatalog,
  ModelConfig,
} from "@/lib/types";
import {
  EMPTY_DRAFT,
  type ModelDraft,
  dirtyKeysOf,
  draftFrom,
  indexOfBinding,
  listKeyFor,
  rebaseDraft,
} from "./model-draft";

export type { ModelDraft } from "./model-draft";
export { bindingKey } from "./model-draft";

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
 * The ENTRIES are trusted rather than re-parsed. `parseBindRejection`
 * (`@/lib/api-error`) already validated every field and filled a missing
 * `message` with `SAFE_FALLBACK_MESSAGE`; a second per-field parse here only
 * created a chance to disagree with it — which it did, filling a blank string.
 */
function extractBindRejections(err: unknown): ConfigWarning[] | null {
  if (!isRecord(err)) return null;
  const raw = err.bindRejections;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  return raw as ConfigWarning[];
}

function warnUnknownBinding(
  op: string,
  scopeType: ModelBinding["scope_type"],
  scopeKey: string,
): void {
  if (process.env.NODE_ENV !== "production") {
    console.warn(
      `useModelConfig.${op}: no ${scopeType} binding named "${scopeKey}" — ignored.`,
    );
  }
}

function findForScope<T extends { scope_type: string; scope_key: string }>(
  items: readonly T[],
  scopeType: ModelBinding["scope_type"],
  scopeKey: string,
): T | undefined {
  return items.find(
    (item) => item.scope_type === scopeType && item.scope_key === scopeKey,
  );
}

export interface UseModelConfigResult {
  catalog: ModelCatalog | null;
  /** The last config the server acknowledged — the baseline `draft` diffs against. */
  config: ModelConfig | null;
  loading: boolean;
  saving: boolean;
  draft: ModelDraft;
  /** Keys (`scope_type:scope_key`) whose draft binding differs from the saved
   *  one — edits, additions AND removals. Derived during render from `draft` +
   *  `config`, never stored, so it cannot drift out of sync with either. */
  dirtyKeys: Set<string>;
  dirtyCount: number;
  /** Per-binding rejections from the most recent 422. Replaced by a later 422,
   *  cleared by a successful save, by `discard()`, by `clearRejections()`, and
   *  per binding by any mutator that touches it. A non-422 failure leaves them
   *  alone (the caller toasts that; the cards keep the server's last verdict). */
  rejections: ConfigWarning[];
  /** Fetches the catalog and config exactly once. Concurrent callers get the
   *  SAME promise, so each observes the outcome; a failure resets the guard so
   *  a retry works, and re-throws so the caller can toast. */
  load: () => Promise<void>;
  /** Adopt a config fetched elsewhere (a credential mutation refetches it).
   *  Clean bindings rebase onto it; pending edits survive. */
  applyServerConfig: (next: ModelConfig) => void;
  /** `false` (with a dev warning) when no such binding exists. */
  updateBinding: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
    patch: Partial<ModelBinding>,
  ) => boolean;
  /** Appends, or replaces a binding already under that key. */
  addBinding: (binding: ModelBinding) => void;
  /** `false` (with a dev warning) when no such binding exists. */
  removeBinding: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
  ) => boolean;
  discard: () => void;
  /** Saves the draft. Re-entrant calls share the in-flight promise. On a 422 it
   *  records `rejections` and RE-THROWS — the caller still needs to know the
   *  save failed, and owns the toast for every non-per-binding error. */
  save: () => Promise<void>;
  clearRejections: () => void;
  rejectionFor: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
  ) => ConfigWarning | undefined;
  warningFor: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
  ) => ConfigWarning | undefined;
}

/**
 * Owns the model/provider configuration for the settings surface: the catalog,
 * the saved config, the editable draft laid over it, and the save lifecycle.
 *
 * Extracted so the settings shell owns no tab's data (defect L5). Every draft
 * mutation lives here so the components consuming it never re-derive it.
 */
export function useModelConfig(): UseModelConfigResult {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [draft, setDraft] = useState<ModelDraft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rejections, setRejections] = useState<ConfigWarning[]>([]);

  // Latest-value refs, so every callback below is identity-stable. A save bar
  // may therefore bind Cmd+S once, in a mount-only effect, and still submit
  // what is on screen. Assigned in effects only — never during render.
  const draftRef = useRef<ModelDraft>(EMPTY_DRAFT);
  const configRef = useRef<ModelConfig | null>(null);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  useEffect(() => {
    configRef.current = config;
  }, [config]);

  // In-flight promises double as the re-entrancy guards: a second caller gets
  // the first call's promise rather than a silent `undefined` or a duplicate
  // request whose completion order would decide which response wins.
  const loadPromiseRef = useRef<Promise<void> | null>(null);
  const savePromiseRef = useRef<Promise<void> | null>(null);
  const loadedOnce = useRef(false);

  const load = useCallback((): Promise<void> => {
    if (loadPromiseRef.current) return loadPromiseRef.current;
    if (loadedOnce.current) return Promise.resolve();
    loadedOnce.current = true;
    setLoading(true);

    const promise = (async () => {
      try {
        const [nextCatalog, nextConfig] = await Promise.all([
          fetchModelCatalog(),
          fetchModelConfig(),
        ]);
        setCatalog(nextCatalog);
        setConfig(nextConfig);
        setDraft(draftFrom(nextConfig));
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
  }, []);

  const dropRejectionFor = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) => {
      setRejections((prev) =>
        prev.some((r) => r.scope_type === scopeType && r.scope_key === scopeKey)
          ? prev.filter(
              (r) =>
                !(r.scope_type === scopeType && r.scope_key === scopeKey),
            )
          : prev,
      );
    },
    [],
  );

  const clearRejections = useCallback(() => {
    setRejections((prev) => (prev.length === 0 ? prev : []));
  }, []);

  const applyServerConfig = useCallback((next: ModelConfig) => {
    const baseline = draftFrom(configRef.current);
    setConfig(next);
    setDraft((prev) => rebaseDraft(baseline, prev, draftFrom(next)));
  }, []);

  const updateBinding = useCallback(
    (
      scopeType: ModelBinding["scope_type"],
      scopeKey: string,
      patch: Partial<ModelBinding>,
    ): boolean => {
      const listKey = listKeyFor(scopeType);
      if (indexOfBinding(draftRef.current[listKey], scopeType, scopeKey) < 0) {
        warnUnknownBinding("updateBinding", scopeType, scopeKey);
        return false;
      }
      setDraft((prev) => {
        const list = prev[listKey];
        const index = indexOfBinding(list, scopeType, scopeKey);
        if (index < 0) return prev;
        const next = [...list];
        // Identity is re-asserted last: a patch may never silently re-key the
        // binding it is patching.
        next[index] = {
          ...list[index],
          ...patch,
          scope_type: scopeType,
          scope_key: scopeKey,
        };
        return { ...prev, [listKey]: next };
      });
      // The user is fixing this binding — its stale verdict must not outlive it.
      dropRejectionFor(scopeType, scopeKey);
      return true;
    },
    [dropRejectionFor],
  );

  const addBinding = useCallback(
    (binding: ModelBinding) => {
      const listKey = listKeyFor(binding.scope_type);
      setDraft((prev) => {
        const list = prev[listKey];
        const index = indexOfBinding(
          list,
          binding.scope_type,
          binding.scope_key,
        );
        const next = [...list];
        if (index < 0) next.push(binding);
        else next[index] = binding;
        return { ...prev, [listKey]: next };
      });
      dropRejectionFor(binding.scope_type, binding.scope_key);
    },
    [dropRejectionFor],
  );

  const removeBinding = useCallback(
    (
      scopeType: ModelBinding["scope_type"],
      scopeKey: string,
    ): boolean => {
      const listKey = listKeyFor(scopeType);
      if (indexOfBinding(draftRef.current[listKey], scopeType, scopeKey) < 0) {
        warnUnknownBinding("removeBinding", scopeType, scopeKey);
        return false;
      }
      setDraft((prev) => ({
        ...prev,
        [listKey]: prev[listKey].filter(
          (b) => !(b.scope_type === scopeType && b.scope_key === scopeKey),
        ),
      }));
      dropRejectionFor(scopeType, scopeKey);
      return true;
    },
    [dropRejectionFor],
  );

  const discard = useCallback(() => {
    setDraft(draftFrom(configRef.current));
    // Rejections describe changes that no longer exist. Leaving them would
    // strand a card showing an error with no edit behind it and no way to clear.
    clearRejections();
  }, [clearRejections]);

  const save = useCallback((): Promise<void> => {
    if (savePromiseRef.current) return savePromiseRef.current;

    const submitted: ModelDraft = {
      tiers: [...draftRef.current.tiers],
      agent_overrides: [...draftRef.current.agent_overrides],
    };
    setSaving(true);

    const promise = (async () => {
      try {
        const updated = await saveModelConfig(submitted);
        setConfig(updated);
        // Rebase against what was SUBMITTED, so an edit made while the PUT was
        // in flight survives instead of vanishing with no signal at all.
        setDraft((prev) => rebaseDraft(submitted, prev, draftFrom(updated)));
        setRejections([]);
      } catch (err) {
        const rejected = extractBindRejections(err);
        if (rejected) setRejections(rejected);
        throw err;
      } finally {
        setSaving(false);
        savePromiseRef.current = null;
      }
    })();

    savePromiseRef.current = promise;
    return promise;
  }, []);

  const dirtyKeys = useMemo(
    () => dirtyKeysOf(draftFrom(config), draft),
    [config, draft],
  );

  const rejectionFor = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) =>
      findForScope(rejections, scopeType, scopeKey),
    [rejections],
  );

  const warningFor = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) =>
      config ? findForScope(config.warnings, scopeType, scopeKey) : undefined,
    [config],
  );

  return useMemo(
    () => ({
      catalog,
      config,
      loading,
      saving,
      draft,
      dirtyKeys,
      dirtyCount: dirtyKeys.size,
      rejections,
      load,
      applyServerConfig,
      updateBinding,
      addBinding,
      removeBinding,
      discard,
      save,
      clearRejections,
      rejectionFor,
      warningFor,
    }),
    [
      catalog,
      config,
      loading,
      saving,
      draft,
      dirtyKeys,
      rejections,
      load,
      applyServerConfig,
      updateBinding,
      addBinding,
      removeBinding,
      discard,
      save,
      clearRejections,
      rejectionFor,
      warningFor,
    ],
  );
}
