import { useCallback, useMemo, useRef, useState } from "react";

import { fetchModelCatalog, fetchModelConfig, saveModelConfig } from "@/lib/api";
import type {
  ConfigWarning,
  ModelBinding,
  ModelCatalog,
  ModelConfig,
} from "@/lib/types";

/** The editable half of a {@link ModelConfig}. `providers` and `warnings` are
 *  server-owned and therefore never part of the draft. */
export interface ModelDraft {
  tiers: ModelBinding[];
  agent_overrides: ModelBinding[];
}

const EMPTY_DRAFT: ModelDraft = { tiers: [], agent_overrides: [] };

/** Stable identity of one binding across the draft, the saved config, the
 *  server's `warnings`, and a 422's bind rejections. */
export function bindingKey(
  scopeType: ModelBinding["scope_type"],
  scopeKey: string,
): string {
  return `${scopeType}:${scopeKey}`;
}

/** Structural comparison of the seven fields a binding actually carries.
 *  Deliberately explicit rather than `JSON.stringify` — key order in a spread
 *  copy is not guaranteed to match the server's, and a stringify diff would
 *  then report a clean binding as dirty. */
function bindingsEqual(a: ModelBinding, b: ModelBinding): boolean {
  return (
    a.scope_type === b.scope_type &&
    a.scope_key === b.scope_key &&
    a.provider === b.provider &&
    a.model_id === b.model_id &&
    a.effort === b.effort &&
    a.max_tokens === b.max_tokens &&
    a.temperature === b.temperature
  );
}

/** A fresh draft off a saved config. New arrays every time, so the returned
 *  draft shares no mutable structure with the config it came from. */
function draftFrom(config: ModelConfig | null): ModelDraft {
  if (!config) return EMPTY_DRAFT;
  return {
    tiers: [...config.tiers],
    agent_overrides: [...config.agent_overrides],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Pull the per-binding rejections off a `PUT /v1/model-config` 422.
 *
 * The check is STRUCTURAL, not `instanceof ApiError`: the class lives in
 * `@/lib/api` (not `@/lib/api-error`, which only holds the `BindRejection`
 * shape and the parsers), and every consumer that mocks `@/lib/api` would lose
 * an `instanceof` check. Returns `null` for every other error, including a
 * partially-shaped `bindRejections`, so a caller can fall back to a toast.
 */
function extractBindRejections(err: unknown): ConfigWarning[] | null {
  if (!isRecord(err)) return null;
  const raw = err.bindRejections;
  if (!Array.isArray(raw) || raw.length === 0) return null;

  const rejections: ConfigWarning[] = [];
  for (const entry of raw) {
    if (!isRecord(entry)) return null;
    if (entry.scope_type !== "tier" && entry.scope_type !== "agent") return null;
    if (typeof entry.scope_key !== "string") return null;
    rejections.push({
      scope_type: entry.scope_type,
      scope_key: entry.scope_key,
      provider: typeof entry.provider === "string" ? entry.provider : "",
      code: "provider_not_configured",
      message: typeof entry.message === "string" ? entry.message : "",
    });
  }
  return rejections;
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
   *  one. Derived during render from `draft` + `config`, never stored, so it
   *  cannot drift out of sync with either. */
  dirtyKeys: Set<string>;
  dirtyCount: number;
  /** Per-binding rejections from the most recent 422. Replaced by a later 422,
   *  cleared by a successful save, and left untouched by a non-422 failure (the
   *  caller toasts that; the cards keep showing what the server last rejected). */
  rejections: ConfigWarning[];
  /** Fetches the catalog and config exactly once. THROWS on failure after
   *  resetting its own guard, so the caller can toast AND retry. */
  load: () => Promise<void>;
  updateBinding: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
    patch: Partial<ModelBinding>,
  ) => void;
  discard: () => void;
  /** Saves the draft. On a 422 it records `rejections` and RE-THROWS — the
   *  caller still needs to know the save failed, and owns the toast for every
   *  error that is not a per-binding rejection. */
  save: () => Promise<void>;
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
 * Extracted so the settings shell owns no tab's data (defect L5).
 */
export function useModelConfig(): UseModelConfigResult {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [draft, setDraft] = useState<ModelDraft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rejections, setRejections] = useState<ConfigWarning[]>([]);

  // Guards against the double fetch two tab mounts would otherwise cause. It
  // RESETS on failure, so a failed load is retryable rather than permanent.
  const loadedOnce = useRef(false);

  const load = useCallback(async () => {
    if (loadedOnce.current) return;
    loadedOnce.current = true;
    setLoading(true);
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
    }
  }, []);

  const updateBinding = useCallback(
    (
      scopeType: ModelBinding["scope_type"],
      scopeKey: string,
      patch: Partial<ModelBinding>,
    ) => {
      setDraft((prev) => {
        const listKey = scopeType === "tier" ? "tiers" : "agent_overrides";
        const list = prev[listKey];
        const index = list.findIndex(
          (b) => b.scope_type === scopeType && b.scope_key === scopeKey,
        );
        if (index === -1) return prev;

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
    },
    [],
  );

  const discard = useCallback(() => {
    setDraft(draftFrom(config));
  }, [config]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const updated = await saveModelConfig({
        tiers: draft.tiers,
        agent_overrides: draft.agent_overrides,
      });
      setConfig(updated);
      setDraft(draftFrom(updated));
      setRejections([]);
    } catch (err) {
      const rejected = extractBindRejections(err);
      if (rejected) setRejections(rejected);
      throw err;
    } finally {
      setSaving(false);
    }
  }, [draft]);

  const dirtyKeys = useMemo(() => {
    const keys = new Set<string>();
    const saved = new Map<string, ModelBinding>();
    if (config) {
      for (const b of [...config.tiers, ...config.agent_overrides]) {
        saved.set(bindingKey(b.scope_type, b.scope_key), b);
      }
    }
    for (const b of [...draft.tiers, ...draft.agent_overrides]) {
      const key = bindingKey(b.scope_type, b.scope_key);
      const before = saved.get(key);
      if (!before || !bindingsEqual(before, b)) keys.add(key);
    }
    return keys;
  }, [config, draft]);

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
      updateBinding,
      discard,
      save,
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
      updateBinding,
      discard,
      save,
      rejectionFor,
      warningFor,
    ],
  );
}
