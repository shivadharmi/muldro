"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, CardBody } from "@/components/ui/card";
import type {
  CatalogModel,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
  TierBinding,
} from "@/lib/types";

interface ModelTabProps {
  open: boolean;
  loading: boolean;
  catalog: ModelCatalog | null;
  config: ModelConfig | null;
  onLoad: () => void;
  onSaveConfig?: (body: { tiers: TierBinding[]; agent_overrides: TierBinding[] }) => void;
  onSaveProviderKey?: (provider: string, apiKey: string, baseUrl?: string) => void;
  onTestProvider?: (provider: string) => void;
  savingConfig?: boolean;
  providerBusy?: string | null;
}

const TIER_ORDER = ["reasoning", "balanced", "fast"];
const EFFORT_OPTIONS = ["low", "medium", "high"];

const INPUT_CLASS =
  "rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring transition-colors";
const SECTION_HEADER_CLASS =
  "text-[11px] uppercase text-t-muted font-medium mb-2.5 tracking-wider";
const PRIMARY_BTN_CLASS =
  "px-3.5 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover disabled:opacity-50 transition-colors cursor-pointer";
const GHOST_BTN_CLASS =
  "px-3.5 py-2 rounded-[var(--radius-md)] text-t-secondary text-[13px] hover:bg-surface-2 disabled:opacity-50 transition-colors cursor-pointer";

function keyOf(binding: TierBinding): string {
  return binding.tier;
}

function findModel(
  catalog: ModelCatalog | null,
  provider: string,
  modelId: string,
): CatalogModel | undefined {
  return catalog?.providers[provider]?.find((m) => m.model_id === modelId);
}

function statusTone(status: string): string {
  if (status === "valid") return "text-j-success";
  if (status === "invalid" || status === "error") return "text-j-danger";
  return "text-t-muted";
}

interface BindingRowProps {
  binding: TierBinding;
  catalog: ModelCatalog | null;
  configuredProviders: string[];
  onChange: (next: TierBinding) => void;
}

/**
 * A single editable binding row (used for both tiers and per-agent overrides).
 * The provider list is restricted to configured providers; effort and
 * temperature controls only render when the selected model supports them.
 */
function BindingRow({ binding, catalog, configuredProviders, onChange }: BindingRowProps) {
  const providerModels = catalog?.providers[binding.provider] ?? [];
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
        {binding.tier}
      </span>

      <select
        aria-label={`${binding.tier} provider`}
        value={binding.provider}
        onChange={(e) => onChange({ ...binding, provider: e.target.value, model_id: "" })}
        className={INPUT_CLASS}
      >
        {providerOptions.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>

      <select
        aria-label={`${binding.tier} model`}
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
          aria-label={`${binding.tier} effort`}
          value={binding.effort}
          onChange={(e) => onChange({ ...binding, effort: e.target.value })}
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
        aria-label={`${binding.tier} max tokens`}
        min="1"
        value={binding.max_tokens}
        onChange={(e) =>
          onChange({ ...binding, max_tokens: Number(e.target.value) || 0 })
        }
        className={`${INPUT_CLASS} w-24`}
      />

      {showTemperature && (
        <input
          type="number"
          aria-label={`${binding.tier} temperature`}
          min="0"
          max="2"
          step="0.1"
          value={binding.temperature ?? ""}
          onChange={(e) =>
            onChange({
              ...binding,
              temperature: e.target.value === "" ? null : Number(e.target.value),
            })
          }
          className={`${INPUT_CLASS} w-20`}
        />
      )}
    </div>
  );
}

interface ProviderRowProps {
  provider: string;
  status: ProviderStatus | undefined;
  busy: boolean;
  onSaveKey?: (provider: string, apiKey: string, baseUrl?: string) => void;
  onTest?: (provider: string) => void;
}

/** Write-only credential row: the key input is never pre-filled. */
function ProviderRow({ provider, status, busy, onSaveKey, onTest }: ProviderRowProps) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

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
                <span className="text-xs text-j-success font-medium">Configured ✓</span>
              )}
              {status?.status && (
                <span className={`text-xs font-medium ${statusTone(status.status)}`}>
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
              disabled={busy || !apiKey}
              onClick={() => onSaveKey?.(provider, apiKey, baseUrl || undefined)}
              className={PRIMARY_BTN_CLASS}
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onTest?.(provider)}
              className={GHOST_BTN_CLASS}
            >
              Test
            </button>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

export function ModelTab({
  loading,
  catalog,
  config,
  onLoad,
  onSaveConfig,
  onSaveProviderKey,
  onTestProvider,
  savingConfig,
  providerBusy,
}: ModelTabProps) {
  const [tiers, setTiers] = useState<TierBinding[]>(config?.tiers ?? []);
  const [agentOverrides, setAgentOverrides] = useState<TierBinding[]>(
    config?.agent_overrides ?? [],
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Re-seed editable local state whenever a fresh config prop arrives. This is
  // React's "adjust state during render" pattern (keyed on the config
  // reference) — no setState in an effect, no side effect during render.
  const [seededConfig, setSeededConfig] = useState(config);
  if (config !== seededConfig) {
    setSeededConfig(config);
    setTiers(config?.tiers ?? []);
    setAgentOverrides(config?.agent_overrides ?? []);
  }

  useEffect(() => {
    onLoad();
  }, [onLoad]);

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
    for (const name of Object.keys(catalog?.providers ?? {})) names.add(name);
    for (const p of config?.providers ?? []) names.add(p.provider);
    return Array.from(names);
  }, [catalog, config]);

  const sortedTiers = useMemo(
    () =>
      [...tiers].sort(
        (a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier),
      ),
    [tiers],
  );

  const updateTier = (next: TierBinding) => {
    setTiers((prev) => prev.map((t) => (keyOf(t) === keyOf(next) ? next : t)));
  };

  const updateOverride = (next: TierBinding) => {
    setAgentOverrides((prev) =>
      prev.map((t) => (keyOf(t) === keyOf(next) ? next : t)),
    );
  };

  const handleSave = () => {
    onSaveConfig?.({ tiers, agent_overrides: agentOverrides });
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
                busy={providerBusy === provider}
                onSaveKey={onSaveProviderKey}
                onTest={onTestProvider}
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
                    key={tier.tier}
                    binding={tier}
                    catalog={catalog}
                    configuredProviders={configuredProviders}
                    onChange={updateTier}
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
          <div className="mt-2.5">
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
                        key={ov.tier}
                        binding={ov}
                        catalog={catalog}
                        configuredProviders={configuredProviders}
                        onChange={updateOverride}
                      />
                    ))}
                  </div>
                </CardBody>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
