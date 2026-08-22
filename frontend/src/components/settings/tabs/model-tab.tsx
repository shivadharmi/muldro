"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { useToast } from "@/components/ui/toast";
import { errorToMessage } from "@/lib/api-error";
import type { CatalogModel, ModelBinding } from "@/lib/types";
import { useSettingsModalStore } from "@/stores/settings-modal-store";
import { bindingKey } from "../hooks/use-model-config";
import { extractBindRejections } from "../hooks/use-bind-rejections";
import { useModelConfigContext } from "../model-config-context";
import { AgentOverrides } from "../model/agent-overrides";
import type { BindingPatch } from "../model/binding-fields";
import { ModelPicker } from "../model/model-picker";
import { SaveBar } from "../model/save-bar";
import { TierCard } from "../model/tier-card";

/** Render order. A tier the server adds later sorts after these rather than
 *  before them — `indexOf`'s `-1` would have put an unknown tier first. */
const TIER_ORDER: readonly string[] = ["reasoning", "balanced", "fast"];

/** §9.5's one-liner beside each tier name. It states what the tier is FOR, not
 *  which model is bound to it — the card below already says that, and a
 *  description that named a model would go stale on the next rebind. */
const TIER_DESCRIPTIONS: Record<string, string> = {
  reasoning: "Deepest thinking. Slowest and dearest.",
  balanced: "The everyday default. Most agents run here.",
  fast: "Cheap and quick — triage, classification, summaries.",
};

/** Sentence case, per **A3** — nothing on screen may be a raw slug. */
function sentence(slug: string): string {
  return slug.charAt(0).toUpperCase() + slug.slice(1);
}

function tierRank(scopeKey: string): number {
  const index = TIER_ORDER.indexOf(scopeKey);
  return index < 0 ? TIER_ORDER.length : index;
}

/** The scope a `dirtyKeys` entry names. Split on the FIRST colon only, since
 *  `bindingKey` joins with one and an agent name is free text. */
function parseScope(key: string): {
  scopeType: ModelBinding["scope_type"];
  scopeKey: string;
} {
  const sep = key.indexOf(":");
  const head = key.slice(0, sep);
  return {
    scopeType: head === "agent" ? "agent" : "tier",
    scopeKey: key.slice(sep + 1),
  };
}

/**
 * Which model powers each reasoning tier, and the per-agent exceptions.
 *
 * **Tiers only.** Provider credentials moved to the Providers tab: a tab that
 * both bound models and stored API keys had two save affordances with different
 * semantics — one PUTs a whole draft, the other writes a secret immediately —
 * sitting a few pixels apart. What survives here is the *consequence* of a
 * missing credential, on the card it breaks (§9.6), with a Connect button that
 * navigates to where the key is entered.
 *
 * **There is no provider control anywhere on this tab (defect F1).** The old
 * pair — a provider `<select>` beside a model `<select>` — could only stay
 * consistent by having one blank the other, so choosing a provider erased the
 * model and a half-finished pair was savable. The model is now chosen in the
 * picker, which returns a whole `CatalogModel`, so `provider` and `model_id`
 * are written together in a single `updateBinding` and cannot disagree.
 * `BindingFields` reinforces this: its patch type cannot express either key.
 */
export function ModelTab() {
  const { addToast } = useToast();
  const { models: modelState } = useModelConfigContext();
  const { catalog, config, draft, loading, saving, dirtyKeys } = modelState;
  const { load, updateBinding, upsertBinding, removeBinding } = modelState;
  const { discard, save, rejectionFor, warningFor } = modelState;

  const setActiveTab = useSettingsModalStore((s) => s.setActiveTab);
  const openProviderFor = useSettingsModalStore((s) => s.openProviderFor);

  /**
   * Which binding the picker is choosing a model for, or `null` when it is shut.
   *
   * A full scope, not a tier name: the same palette serves the three tier cards
   * and every per-agent override, and only the scope says which binding the
   * selection lands on. `tier` rides along because the picker's "Suggested for
   * …" group matches `suggested_tier`, which for an override is the tier the
   * agent rides on rather than its own name.
   */
  const [picker, setPicker] = useState<{
    scopeType: ModelBinding["scope_type"];
    scopeKey: string;
    tier: string;
  } | null>(null);

  // The context fires the same `load()` and swallows the failure — a shared
  // context cannot know which consumer is on screen to be told. This tab IS,
  // and both calls share one promise, so this is not a second request: it is
  // the one place that observes the outcome and can retry.
  const retryLoad = useCallback(
    () => load().catch((err) => addToast(errorToMessage(err), "error")),
    [load, addToast],
  );
  useEffect(() => {
    void retryLoad();
  }, [retryLoad]);

  const agents = useMemo(() => catalog?.agents ?? [], [catalog]);
  const catalogModels = useMemo(() => catalog?.models ?? [], [catalog]);
  const catalogProviders = useMemo(() => catalog?.providers ?? [], [catalog]);
  const providerStatuses = useMemo(() => config?.providers ?? [], [config]);

  const tiers = useMemo(
    () =>
      [...draft.tiers].sort(
        (a, b) => tierRank(a.scope_key) - tierRank(b.scope_key),
      ),
    [draft.tiers],
  );

  const isDirty = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) =>
      dirtyKeys.has(bindingKey(scopeType, scopeKey)),
    [dirtyKeys],
  );

  const labelFor = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) =>
      scopeType === "tier"
        ? sentence(scopeKey)
        : (agents.find((a) => a.name === scopeKey)?.display_name ??
          sentence(scopeKey)),
    [agents],
  );

  /**
   * The changed scopes, named and ordered for the save bar.
   *
   * Derived from `dirtyKeys` rather than by walking the draft, because a
   * REMOVED override is dirty and is no longer in the draft to be walked — a
   * pending deletion would otherwise count in the bar and stay anonymous.
   */
  const changed = useMemo(() => {
    const scopes = Array.from(dirtyKeys, parseScope);
    return scopes
      .map((scope) => ({
        rank:
          scope.scopeType === "tier"
            ? tierRank(scope.scopeKey)
            : TIER_ORDER.length + 1,
        label: labelFor(scope.scopeType, scope.scopeKey),
      }))
      .sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label))
      .map((entry) => entry.label);
  }, [dirtyKeys, labelFor]);

  const openPicker = useCallback(
    (scopeType: ModelBinding["scope_type"], scopeKey: string) => {
      const tier =
        scopeType === "tier"
          ? scopeKey
          : (agents.find((a) => a.name === scopeKey)?.tier ?? "balanced");
      setPicker({ scopeType, scopeKey, tier });
    },
    [agents],
  );

  /**
   * The picker's result, applied as ONE write that sets both keys (**F1**).
   *
   * `updateBinding` and not `BindingFields`' `onChange`: that patch type
   * deliberately cannot carry `provider` or `model_id`, which is what makes a
   * grid edit incapable of rewriting the model. The pair enters the draft here,
   * from a whole `CatalogModel`, or not at all.
   */
  const chooseModel = useCallback(
    (model: CatalogModel) => {
      if (!picker) return;
      updateBinding(picker.scopeType, picker.scopeKey, {
        provider: model.provider,
        model_id: model.model_id,
      });
    },
    [picker, updateBinding],
  );

  /**
   * The picker's footer link. It switches tabs and carries NO intent: there is
   * no provider in mind, and pre-expanding an arbitrary row — or chipping one
   * with a reason nobody asked for — would be an answer to a question the
   * founder did not ask.
   */
  const browseProviders = useCallback(() => {
    setPicker(null);
    setActiveTab("providers");
  }, [setActiveTab]);

  /**
   * §4.4's remediation, completed: switch to Providers with the offending
   * provider already open and the reason stated on the row.
   *
   * The sentence is composed HERE because only this tab knows what a tier is —
   * the Providers tab renders whatever string it is handed, and teaching it to
   * turn `fast` into "the Fast tier" would put tier vocabulary in a tab that
   * has none. Sentence-cased through the same `sentence` every other tier label
   * on this tab goes through (**A3**: nothing on screen is a raw slug).
   */
  const connectProvider = useCallback(
    (provider: string, scopeKey: string) => {
      setPicker(null);
      openProviderFor(provider, `Needed by the ${sentence(scopeKey)} tier`);
    },
    [openProviderFor],
  );

  const handleSave = useCallback(async () => {
    try {
      await save();
      addToast("Model configuration saved", "success");
    } catch (err) {
      // A 422's per-binding verdicts already render ON the refused cards (§4.4).
      // Toasting them as well would say the same thing a second time, anonymously
      // and away from the binding — which is the failure the card rendering exists
      // to fix. Every other error has nowhere else to go, so it toasts.
      if (extractBindRejections(err)) return;
      addToast(errorToMessage(err), "error");
    }
  }, [save, addToast]);

  const pickerBinding = useMemo(() => {
    if (!picker) return undefined;
    const list =
      picker.scopeType === "tier" ? draft.tiers : draft.agent_overrides;
    return list.find((b) => b.scope_key === picker.scopeKey);
  }, [picker, draft]);

  if (loading) {
    return (
      <div className="flex flex-col gap-[10px]">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-[126px] rounded-[var(--radius-lg)] skeleton" />
        ))}
      </div>
    );
  }

  return (
    <>
      {/* `-mb-[18px] sm:-mb-5` cancels the shell's bottom padding so the save
          bar can sit flush against the panel's lower edge. */}
      <div className="flex-1 min-h-0 flex flex-col gap-[18px] -mb-[18px] sm:-mb-5">
        {tiers.length === 0 ? (
          <p className="text-[12.5px] text-t-muted">
            No tiers are configured for this workspace.
          </p>
        ) : (
          <div className="flex flex-col gap-[10px]">
            {tiers.map((tier) => (
              <TierCard
                key={tier.scope_key}
                binding={tier}
                models={catalogModels}
                providers={catalogProviders}
                agents={agents}
                description={TIER_DESCRIPTIONS[tier.scope_key] ?? ""}
                dirty={isDirty("tier", tier.scope_key)}
                disabled={saving}
                warning={warningFor("tier", tier.scope_key)}
                rejection={rejectionFor("tier", tier.scope_key)}
                onChange={(patch: BindingPatch) =>
                  updateBinding("tier", tier.scope_key, patch)
                }
                onOpenPicker={() => openPicker("tier", tier.scope_key)}
                // The card supplies the SLUG it is warned about; the tier it
                // belongs to is this loop's, not the card's to repeat.
                onConnectProvider={(provider) =>
                  connectProvider(provider, tier.scope_key)
                }
              />
            ))}
          </div>
        )}

        <AgentOverrides
          overrides={draft.agent_overrides}
          agents={agents}
          tiers={draft.tiers}
          models={catalogModels}
          providers={catalogProviders}
          disabled={saving}
          dirty={(scopeKey) => isDirty("agent", scopeKey)}
          rejection={(scopeKey) => rejectionFor("agent", scopeKey)}
          onChange={(scopeKey, patch) =>
            updateBinding("agent", scopeKey, patch)
          }
          onAdd={upsertBinding}
          onRemove={(scopeKey) => removeBinding("agent", scopeKey)}
          onOpenPicker={(scopeKey) => openPicker("agent", scopeKey)}
        />

        <SaveBar
          changed={changed}
          saving={saving}
          onDiscard={discard}
          onSave={handleSave}
        />
      </div>

      <ModelPicker
        open={pickerBinding !== undefined}
        tier={picker?.tier ?? ""}
        selectedProvider={pickerBinding?.provider ?? null}
        selectedModelId={pickerBinding?.model_id ?? null}
        models={catalogModels}
        providers={catalogProviders}
        providerStatuses={providerStatuses}
        onSelect={chooseModel}
        onClose={() => setPicker(null)}
        onBrowseProviders={browseProviders}
      />
    </>
  );
}
