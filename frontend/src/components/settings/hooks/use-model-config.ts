import { useCallback, useMemo, useRef, useState } from "react";

import { saveModelConfig } from "@/lib/api";
import type { ConfigWarning, ModelBinding, ModelConfig } from "@/lib/types";
import {
  EMPTY_DRAFT,
  type ModelDraft,
  dirtyKeysOf,
  draftFrom,
  findByScope,
  indexOfBinding,
  listKeyFor,
  rebaseDraft,
} from "./model-draft";
import { useBindRejections } from "./use-bind-rejections";
import { useModelLoads, type ModelLoads } from "./use-model-loads";

export type { ModelDraft } from "./model-draft";
export { bindingKey } from "./model-draft";

/**
 * The saved config and the draft laid over it are ONE state, because every
 * write to either is defined in terms of the other: the draft rebases onto a
 * new config, and `dirtyKeys` is their difference. Held apart, two refetches
 * batched into one React commit would let the second read the pre-first config
 * as its baseline, mark everything the first changed as dirty, and discard the
 * second response for those bindings.
 */
interface ModelConfigState {
  config: ModelConfig | null;
  draft: ModelDraft;
}

const INITIAL_STATE: ModelConfigState = Object.freeze({
  config: null,
  draft: EMPTY_DRAFT,
});

function warnDev(message: string): void {
  if (process.env.NODE_ENV !== "production") console.warn(message);
}

function warnUnknownBinding(
  op: string,
  scopeType: ModelBinding["scope_type"],
  scopeKey: string,
): void {
  warnDev(
    `useModelConfig.${op}: no ${scopeType} binding named "${scopeKey}" — ignored.`,
  );
}

/**
 * The fetch lifecycle ({@link ModelLoads}) plus the draft state laid over it.
 * Inherited rather than restated, so the two loaders are documented once.
 */
export interface UseModelConfigResult extends ModelLoads {
  /** The last config the server acknowledged — the baseline `draft` diffs against. */
  config: ModelConfig | null;
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
  /** Adopt a config fetched elsewhere (a credential mutation refetches it).
   *  Clean bindings rebase onto it; pending edits survive. */
  applyServerConfig: (next: ModelConfig) => void;
  /** `false` (with a dev warning) when no such binding exists. */
  updateBinding: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
    patch: Partial<ModelBinding>,
  ) => boolean;
  /** Appends, or REPLACES a binding already under that key — named `upsert`
   *  because an "add override" flow must check for the existing one itself
   *  rather than discover it has silently overwritten it. */
  upsertBinding: (binding: ModelBinding) => void;
  /** `false` (with a dev warning) when no such binding exists. */
  removeBinding: (
    scopeType: ModelBinding["scope_type"],
    scopeKey: string,
  ) => boolean;
  discard: () => void;
  /** Saves the draft. Re-entrant calls share the in-flight promise. A no-op
   *  before `load()` resolves (see the guard's comment). On a 422 it records
   *  `rejections` and RE-THROWS — the caller still needs to know the save
   *  failed, and owns the toast for every non-per-binding error. */
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
  const [state, setState] = useState<ModelConfigState>(INITIAL_STATE);
  const [saving, setSaving] = useState(false);
  const bindRejections = useBindRejections();

  // WRITE-THROUGH cache, not a post-commit echo of `state`. Every write goes
  // through `commit`, which assigns the ref BEFORE queueing the render, so a
  // second mutation in the same event handler — `upsertBinding(x)` then
  // `updateBinding(x, ...)` — sees the first. An effect-assigned ref would
  // still hold the pre-handler draft there, so the existence check would fail
  // and the second edit would vanish (silently, since the dev warning compiles
  // out in production). It is also what makes two batched `applyServerConfig`
  // calls read a current baseline.
  const stateRef = useRef<ModelConfigState>(INITIAL_STATE);
  const commit = useCallback((next: ModelConfigState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const savePromiseRef = useRef<Promise<void> | null>(null);

  // The one way a server config enters this state: rebase the draft onto it so
  // clean bindings adopt it and pending edits survive. Both the eager
  // `loadConfig` and `applyServerConfig` are this, and they must stay one
  // implementation — a second, plainer assignment somewhere would be the bug
  // where a background refetch silently drops what the user typed.
  const adoptConfig = useCallback(
    (next: ModelConfig) => {
      const prev = stateRef.current;
      commit({
        config: next,
        draft: rebaseDraft(draftFrom(prev.config), prev.draft, draftFrom(next)),
      });
    },
    [commit],
  );

  const { catalog, loading, load, loadConfig } = useModelLoads(adoptConfig);


  // Destructured, not used through `bindRejections`: that object is memoised on
  // `rejections`, so reaching through it inside a callback would re-create the
  // callback on every 422 and cost `save` its identity stability. These three
  // are stable.
  const {
    dropFor: dropRejectionFor,
    clear: clearRejections,
    record: recordRejections,
  } = bindRejections;

  /** @see adoptConfig — the same operation, named for the caller that adopts a
   *  config a credential mutation fetched. */
  const applyServerConfig = adoptConfig;

  const updateBinding = useCallback(
    (
      scopeType: ModelBinding["scope_type"],
      scopeKey: string,
      patch: Partial<ModelBinding>,
    ): boolean => {
      const prev = stateRef.current;
      const listKey = listKeyFor(scopeType);
      const index = indexOfBinding(prev.draft[listKey], scopeType, scopeKey);
      if (index < 0) {
        warnUnknownBinding("updateBinding", scopeType, scopeKey);
        return false;
      }
      const list = [...prev.draft[listKey]];
      // Identity is re-asserted last: a patch may never silently re-key the
      // binding it is patching.
      list[index] = {
        ...list[index],
        ...patch,
        scope_type: scopeType,
        scope_key: scopeKey,
      };
      commit({ ...prev, draft: { ...prev.draft, [listKey]: list } });
      // The user is fixing this binding — its stale verdict must not outlive it.
      dropRejectionFor(scopeType, scopeKey);
      return true;
    },
    [commit, dropRejectionFor],
  );

  const upsertBinding = useCallback(
    (binding: ModelBinding) => {
      const prev = stateRef.current;
      const listKey = listKeyFor(binding.scope_type);
      const index = indexOfBinding(
        prev.draft[listKey],
        binding.scope_type,
        binding.scope_key,
      );
      const list = [...prev.draft[listKey]];
      if (index < 0) list.push(binding);
      else list[index] = binding;
      commit({ ...prev, draft: { ...prev.draft, [listKey]: list } });
      dropRejectionFor(binding.scope_type, binding.scope_key);
    },
    [commit, dropRejectionFor],
  );

  const removeBinding = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string): boolean => {
      const prev = stateRef.current;
      const listKey = listKeyFor(scopeType);
      if (indexOfBinding(prev.draft[listKey], scopeType, scopeKey) < 0) {
        warnUnknownBinding("removeBinding", scopeType, scopeKey);
        return false;
      }
      const list = prev.draft[listKey].filter(
        (b) => !(b.scope_type === scopeType && b.scope_key === scopeKey),
      );
      commit({ ...prev, draft: { ...prev.draft, [listKey]: list } });
      dropRejectionFor(scopeType, scopeKey);
      return true;
    },
    [commit, dropRejectionFor],
  );

  const discard = useCallback(() => {
    const prev = stateRef.current;
    commit({ ...prev, draft: draftFrom(prev.config) });
    // Rejections describe changes that no longer exist. Leaving them would
    // strand a card showing an error with no edit behind it and no way to clear.
    clearRejections();
  }, [commit, clearRejections]);

  const save = useCallback((): Promise<void> => {
    if (savePromiseRef.current) return savePromiseRef.current;

    // `PUT /v1/model-config` is THREE-valued: an absent key leaves that scope
    // untouched, and any list — INCLUDING `[]` — is a complete replacement
    // (`model_config_service.put_config` prunes with `keep=set()`, which drops
    // every agent override in the workspace). Before `load()` resolves the
    // draft is EMPTY_DRAFT, so saving here would silently delete them all: no
    // 422, no warning, and `dirtyCount === 0` afterwards. Reachable from a save
    // bar binding Cmd+S in a mount-only effect, and open permanently whenever
    // `load()` failed. This guard is the only thing standing in the way — do
    // not "simplify" it into a truthiness check on the draft.
    if (stateRef.current.config === null) {
      warnDev("useModelConfig.save: ignored — the config has not loaded yet.");
      return Promise.resolve();
    }

    const submitted: ModelDraft = {
      tiers: [...stateRef.current.draft.tiers],
      agent_overrides: [...stateRef.current.draft.agent_overrides],
    };
    setSaving(true);

    const promise = (async () => {
      try {
        const updated = await saveModelConfig(submitted);
        const prev = stateRef.current;
        // Rebase against what was SUBMITTED, so an edit made while the PUT was
        // in flight survives, while a binding the server normalised on the way
        // through (a clamped `max_tokens`, say) adopts the server's value.
        commit({
          config: updated,
          draft: rebaseDraft(submitted, prev.draft, draftFrom(updated)),
        });
        clearRejections();
      } catch (err) {
        recordRejections(err);
        throw err;
      } finally {
        setSaving(false);
        savePromiseRef.current = null;
      }
    })();

    savePromiseRef.current = promise;
    return promise;
  }, [commit, clearRejections, recordRejections]);

  const dirtyKeys = useMemo(
    () => dirtyKeysOf(draftFrom(state.config), state.draft),
    [state],
  );

  const warningFor = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) =>
      state.config
        ? findByScope(state.config.warnings, scopeType, scopeKey)
        : undefined,
    [state],
  );

  return useMemo(
    () => ({
      catalog,
      config: state.config,
      loading,
      saving,
      draft: state.draft,
      dirtyKeys,
      dirtyCount: dirtyKeys.size,
      rejections: bindRejections.rejections,
      load,
      loadConfig,
      applyServerConfig,
      updateBinding,
      upsertBinding,
      removeBinding,
      discard,
      save,
      clearRejections,
      rejectionFor: bindRejections.rejectionFor,
      warningFor,
    }),
    [
      catalog,
      state,
      loading,
      saving,
      dirtyKeys,
      bindRejections,
      load,
      loadConfig,
      applyServerConfig,
      updateBinding,
      upsertBinding,
      removeBinding,
      discard,
      save,
      clearRejections,
      warningFor,
    ],
  );
}
