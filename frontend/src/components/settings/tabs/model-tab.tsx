"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Card, CardBody } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { errorToMessage } from "@/lib/api-error";
import type {
  CatalogModel,
  ModelCatalog,
  ModelBinding,
  ProviderStatus,
} from "@/lib/types";
import { useModelConfigContext } from "../model-config-context";
import type { CredentialFields } from "../hooks/use-provider-credentials";

const TIER_ORDER = ["reasoning", "balanced", "fast"];
const EFFORT_OPTIONS = ["none", "low", "medium", "high"];

const INPUT_CLASS =
  "rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring transition-colors";
const SECTION_HEADER_CLASS =
  "text-[11px] uppercase text-t-muted font-medium mb-2.5 tracking-wider";
const PRIMARY_BTN_CLASS =
  "px-3.5 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover disabled:opacity-50 transition-colors cursor-pointer";
const GHOST_BTN_CLASS =
  "px-3.5 py-2 rounded-[var(--radius-md)] text-t-secondary text-[13px] hover:bg-surface-2 disabled:opacity-50 transition-colors cursor-pointer";

/** Bindings are identified by their full scope, so a tier and an agent of the same
 *  name can never be confused for one another. */
function keyOf(binding: ModelBinding): string {
  return `${binding.scope_type}:${binding.scope_key}`;
}

function findModel(
  catalog: ModelCatalog | null,
  provider: string,
  modelId: string,
): CatalogModel | undefined {
  return catalog?.models.find(
    (m) => m.provider === provider && m.model_id === modelId,
  );
}

function statusTone(status: string): string {
  if (status === "valid") return "text-j-success";
  if (status === "invalid" || status === "error") return "text-j-danger";
  return "text-t-muted";
}

interface BindingRowProps {
  binding: ModelBinding;
  catalog: ModelCatalog | null;
  configuredProviders: string[];
  onChange: (next: ModelBinding) => void;
  onRemove?: () => void;
}

/**
 * A single editable binding row (used for both tiers and per-agent overrides).
 * The provider list is restricted to configured providers; effort and
 * temperature controls only render when the selected model supports them.
 * `onRemove`, when provided, renders a remove control (used for overrides).
 */
function BindingRow({
  binding,
  catalog,
  configuredProviders,
  onChange,
  onRemove,
}: BindingRowProps) {
  const providerModels = (catalog?.models ?? []).filter(
    (m) => m.provider === binding.provider,
  );
  const selectedModel = findModel(catalog, binding.provider, binding.model_id);
  const showEffort = !!selectedModel && selectedModel.thinking_style !== "none";
  const showTemperature = !!selectedModel && selectedModel.accepts_temperature;

  // Always include the binding's current provider so a de-configured provider
  // never yields a blank/mismatched select.
  const providerOptions = Array.from(
    new Set([binding.provider, ...configuredProviders]),
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-t-secondary font-medium w-24 shrink-0 capitalize">
        {binding.scope_key}
      </span>

      <select
        aria-label={`${binding.scope_key} provider`}
        value={binding.provider}
        onChange={(e) =>
          onChange({ ...binding, provider: e.target.value, model_id: "" })
        }
        className={INPUT_CLASS}
      >
        {providerOptions.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>

      <select
        aria-label={`${binding.scope_key} model`}
        value={binding.model_id}
        onChange={(e) => onChange({ ...binding, model_id: e.target.value })}
        className={INPUT_CLASS}
      >
        <option value="">Select model…</option>
        {providerModels.map((m) => (
          <option key={m.model_id} value={m.model_id}>
            {m.display_name}
          </option>
        ))}
      </select>

      {showEffort && (
        <select
          aria-label={`${binding.scope_key} effort`}
          value={binding.effort}
          onChange={(e) =>
            onChange({
              ...binding,
              effort: e.target.value as ModelBinding["effort"],
            })
          }
          className={INPUT_CLASS}
        >
          {EFFORT_OPTIONS.map((eff) => (
            <option key={eff} value={eff}>
              {eff}
            </option>
          ))}
        </select>
      )}

      <input
        type="number"
        aria-label={`${binding.scope_key} max tokens`}
        min="1"
        value={binding.max_tokens}
        onChange={(e) =>
          // Never persist 0 — max_tokens=0 breaks every model call (backend rejects <1).
          onChange({
            ...binding,
            max_tokens: Math.max(1, Number(e.target.value) || 1),
          })
        }
        className={`${INPUT_CLASS} w-24`}
      />

      {showTemperature && (
        <input
          type="number"
          aria-label={`${binding.scope_key} temperature`}
          min="0"
          max="2"
          step="0.1"
          value={binding.temperature ?? ""}
          onChange={(e) =>
            onChange({
              ...binding,
              temperature:
                e.target.value === "" ? null : Number(e.target.value),
            })
          }
          className={`${INPUT_CLASS} w-20`}
        />
      )}

      {onRemove && (
        <button
          type="button"
          aria-label={`remove ${binding.scope_key} override`}
          onClick={onRemove}
          className="ml-auto px-2 py-1 rounded-[var(--radius-md)] text-t-muted hover:text-j-danger hover:bg-surface-2 transition-colors cursor-pointer"
        >
          ✕
        </button>
      )}
    </div>
  );
}

interface ProviderRowProps {
  provider: string;
  status: ProviderStatus | undefined;
  busy: boolean;
  onSaveKey: (provider: string, fields: CredentialFields) => void;
  onTest: (provider: string) => void;
  onDelete: (provider: string) => void;
}

/** Write-only credential row: the key input is never pre-filled. */
function ProviderRow({
  provider,
  status,
  busy,
  onSaveKey,
  onTest,
  onDelete,
}: ProviderRowProps) {
  const [apiKey, setApiKey] = useState("");
  // Prefill from the stored value so saving does not clear what the user did not retype.
  const [baseUrl, setBaseUrl] = useState(status?.base_url ?? "");

  return (
    <Card>
      <CardBody>
        <div className="space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-sm text-t-primary font-medium capitalize">
              {provider}
            </span>
            <div className="flex items-center gap-2">
              {status?.configured && (
                <span className="text-xs text-j-success font-medium">
                  Configured ✓
                </span>
              )}
              {/* An inherited credential is configured but not removable here, so say
                  where it comes from — otherwise the missing Remove button reads as a
                  bug rather than as "this isn't yours to delete". */}
              {status?.source === "default" && (
                <span className="text-xs text-t-secondary">
                  deployment default
                </span>
              )}
              {status?.source === "env" && (
                <span className="text-xs text-t-secondary">
                  from environment
                </span>
              )}
              {status?.status && (
                <span
                  className={`text-xs font-medium ${statusTone(status.status)}`}
                >
                  {status.status}
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <input
              type="password"
              aria-label={`${provider} api key`}
              placeholder="API key"
              value={apiKey}
              autoComplete="off"
              onChange={(e) => setApiKey(e.target.value)}
              className={`${INPUT_CLASS} flex-1 min-w-40`}
            />
            <input
              type="text"
              aria-label={`${provider} base url`}
              placeholder="Base URL (optional)"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className={`${INPUT_CLASS} flex-1 min-w-40`}
            />
            <button
              type="button"
              // ollama authenticates with a base URL alone — no key required.
              disabled={
                busy || (provider !== "ollama" && !status?.configured && !apiKey)
              }
              onClick={() =>
                onSaveKey(provider, {
                  ...(apiKey ? { api_key: apiKey } : {}),
                  ...(baseUrl !== (status?.base_url ?? "")
                    ? { base_url: baseUrl || null }
                    : {}),
                })
              }
              className={PRIMARY_BTN_CLASS}
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onTest(provider)}
              className={GHOST_BTN_CLASS}
            >
              Test
            </button>
            {/* Revoke a stored credential (e.g. a compromised key). Shown ONLY for a
                credential this workspace owns: DELETE removes the workspace row and
                nothing else, so offering it for a deployment-default row or an
                env-backed key is a button that appears to work and changes nothing. */}
            {status?.source === "workspace" && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onDelete(provider)}
                className={`${GHOST_BTN_CLASS} text-j-danger hover:text-j-danger`}
              >
                Remove
              </button>
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

export function ModelTab() {
  const { addToast } = useToast();
  const { models, credentials } = useModelConfigContext();
  const { catalog, config, draft, loading, saving: savingConfig } = models;
  const { load, updateBinding, upsertBinding, removeBinding, save } = models;

  const tiers = draft.tiers;
  const agentOverrides = draft.agent_overrides;
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [addAgent, setAddAgent] = useState("");

  useEffect(() => {
    load().catch((err) => addToast(errorToMessage(err), "error"));
  }, [load, addToast]);

  const configuredProviders = useMemo(
    () =>
      (config?.providers ?? [])
        .filter((p) => p.configured)
        .map((p) => p.provider),
    [config],
  );

  const statusByProvider = useMemo(() => {
    const map = new Map<string, ProviderStatus>();
    for (const p of config?.providers ?? []) map.set(p.provider, p);
    return map;
  }, [config]);

  const providerNames = useMemo(() => {
    const names = new Set<string>();
    for (const p of catalog?.providers ?? []) names.add(p.provider);
    for (const p of config?.providers ?? []) names.add(p.provider);
    return Array.from(names);
  }, [catalog, config]);

  const sortedTiers = useMemo(
    () =>
      [...tiers].sort(
        (a, b) => TIER_ORDER.indexOf(a.scope_key) - TIER_ORDER.indexOf(b.scope_key),
      ),
    [tiers],
  );

  // Both lists patch the same draft; the binding carries its own scope, so one
  // handler serves tiers and overrides alike.
  const changeBinding = useCallback(
    (next: ModelBinding) => {
      updateBinding(next.scope_type, next.scope_key, next);
    },
    [updateBinding],
  );

  // Agents that don't yet have an override — the candidates for the add selector.
  const overriddenAgents = useMemo(
    () => new Set(agentOverrides.map((o) => o.scope_key)),
    [agentOverrides],
  );
  const availableAgents = useMemo(
    () => (catalog?.agents ?? []).filter((a) => !overriddenAgents.has(a.name)),
    [catalog, overriddenAgents],
  );

  const addOverride = (agentName: string) => {
    const agent = catalog?.agents.find((a) => a.name === agentName);
    if (!agent) return;
    // Seed from the agent's default tier binding so the override starts valid.
    const tierBinding = tiers.find((t) => t.scope_key === agent.tier);
    const seed: ModelBinding = tierBinding
      ? { ...tierBinding, scope_type: "agent", scope_key: agentName }
      : {
          scope_type: "agent",
          scope_key: agentName,
          provider: configuredProviders[0] ?? "",
          model_id: "",
          effort: "none",
          max_tokens: 4096,
          temperature: null,
        };
    // `availableAgents` already excludes every agent that has an override, so
    // the upsert here can only ever be an append.
    upsertBinding(seed);
    setAddAgent("");
  };

  const handleSave = async () => {
    try {
      await save();
      addToast("Model configuration saved", "success");
    } catch (err) {
      addToast(errorToMessage(err), "error");
    }
  };

  const handleSaveKey = async (provider: string, fields: CredentialFields) => {
    try {
      await credentials.save(provider, fields);
      addToast(`${provider} credentials saved`, "success");
    } catch (err) {
      addToast(errorToMessage(err), "error");
    }
  };

  const handleTest = async (provider: string) => {
    try {
      const result = await credentials.test(provider);
      addToast(`${provider} test: ${result.status}`, "success");
    } catch (err) {
      addToast(errorToMessage(err), "error");
    }
  };

  const handleDelete = async (provider: string) => {
    try {
      const result = await credentials.remove(provider);
      // A revoke can orphan bindings that depended on this credential — surface
      // that consequence instead of reporting a plain success.
      if (result.orphaned_bindings.length > 0) {
        addToast(result.orphaned_bindings[0].message, "error");
      } else {
        addToast(`${provider} credentials removed`, "success");
      }
    } catch (err) {
      addToast(errorToMessage(err), "error");
    }
  };

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-16 rounded-[var(--radius-lg)] skeleton" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-t-tertiary leading-relaxed">
        Configure which model powers each reasoning tier, connect provider API
        keys, and override models per agent.
      </p>

      {/* Providers */}
      <div>
        <h3 className={SECTION_HEADER_CLASS}>Providers</h3>
        {providerNames.length === 0 ? (
          <Card>
            <CardBody>
              <p className="text-xs text-t-muted text-center py-2">
                No providers available.
              </p>
            </CardBody>
          </Card>
        ) : (
          <div className="space-y-2">
            {providerNames.map((provider) => (
              <ProviderRow
                key={provider}
                provider={provider}
                status={statusByProvider.get(provider)}
                busy={credentials.isBusy(provider)}
                onSaveKey={handleSaveKey}
                onTest={handleTest}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>

      {/* Tiers */}
      <div>
        <h3 className={SECTION_HEADER_CLASS}>Tiers</h3>
        {sortedTiers.length === 0 ? (
          <Card>
            <CardBody>
              <p className="text-xs text-t-muted text-center py-2">
                No tier bindings configured.
              </p>
            </CardBody>
          </Card>
        ) : (
          <Card>
            <CardBody>
              <div className="space-y-3">
                {sortedTiers.map((tier) => (
                  <BindingRow
                    key={keyOf(tier)}
                    binding={tier}
                    catalog={catalog}
                    configuredProviders={configuredProviders}
                    onChange={changeBinding}
                  />
                ))}
                <div className="flex justify-end pt-1">
                  <button
                    type="button"
                    disabled={savingConfig}
                    onClick={handleSave}
                    className={PRIMARY_BTN_CLASS}
                  >
                    {savingConfig ? "Saving…" : "Save"}
                  </button>
                </div>
              </div>
            </CardBody>
          </Card>
        )}
      </div>

      {/* Advanced (per-agent overrides) */}
      <div>
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          className="flex items-center gap-1.5 text-[11px] uppercase text-t-muted font-medium tracking-wider hover:text-t-secondary transition-colors cursor-pointer"
        >
          <span>{advancedOpen ? "▾" : "▸"}</span>
          Advanced — Per-Agent Overrides
        </button>
        {advancedOpen && (
          <div className="mt-2.5 space-y-2">
            {/* Add-override control: pick an agent without an override and seed it
                from that agent's default tier binding. */}
            {availableAgents.length > 0 && (
              <div className="flex items-center gap-2">
                <select
                  aria-label="agent to override"
                  value={addAgent}
                  onChange={(e) => setAddAgent(e.target.value)}
                  className={INPUT_CLASS}
                >
                  <option value="">Select agent…</option>
                  {availableAgents.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.display_name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  disabled={!addAgent}
                  onClick={() => addOverride(addAgent)}
                  className={GHOST_BTN_CLASS}
                >
                  + Add override
                </button>
              </div>
            )}

            {agentOverrides.length === 0 ? (
              <Card>
                <CardBody>
                  <p className="text-xs text-t-muted text-center py-2">
                    No per-agent overrides. Tier defaults apply to every agent.
                  </p>
                </CardBody>
              </Card>
            ) : (
              <Card>
                <CardBody>
                  <div className="space-y-3">
                    {agentOverrides.map((ov) => (
                      <BindingRow
                        key={keyOf(ov)}
                        binding={ov}
                        catalog={catalog}
                        configuredProviders={configuredProviders}
                        onChange={changeBinding}
                        onRemove={() => removeBinding("agent", ov.scope_key)}
                      />
                    ))}
                  </div>
                </CardBody>
              </Card>
            )}

            {/* Persists tiers + the full override set. The server replaces overrides
                wholesale, so a removed row is deleted on save. */}
            <div className="flex justify-end">
              <button
                type="button"
                disabled={savingConfig}
                onClick={handleSave}
                className={PRIMARY_BTN_CLASS}
              >
                {savingConfig ? "Saving…" : "Save overrides"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
