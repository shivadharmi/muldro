"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchPolicyMode,
  setPolicyMode,
  fetchWorkspaceDefaultPermissionMode,
  setWorkspaceDefaultPermissionMode,
  fetchBudget,
  updateBudgetLimit,
  fetchTrustDashboard,
  setTrustCeiling,
  resetTrust,
  fetchModelCatalog,
  fetchModelConfig,
  saveModelConfig,
  saveProviderCredential,
  testProviderKey,
  deleteProviderKey,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";
import { errorToMessage } from "@/lib/api-error";
import type {
  TrustDashboardEntry,
  ModelCatalog,
  ModelConfig,
  ModelBinding,
} from "@/lib/types";
import { TRUST_LEVEL_LABELS } from "@/components/settings/trust-constants";
import {
  useSettingsModalStore,
  type SettingsTab,
} from "@/stores/settings-modal-store";
import { AccountTab } from "./account-tab";
import { PreferencesTab } from "./preferences-tab";
import { PolicyTab } from "./policy-tab";
import { TrustTab } from "./trust-tab";
import { FiltersTab } from "./filters-tab";
import { SpendingTab } from "./spending-tab";
import { ModelTab } from "./model-tab";

const TABS: Array<{ key: SettingsTab; label: string }> = [
  { key: "account", label: "Account" },
  { key: "preferences", label: "Preferences" },
  { key: "policy", label: "Policy" },
  { key: "budget", label: "Budget" },
  { key: "trust", label: "Trust" },
  { key: "filters", label: "Filters" },
  { key: "model", label: "Model" },
];

/**
 * Inline stroke-SVG icon per settings tab. Matches the design iconography:
 * 16px, viewBox 0 0 16 16, 1.4px strokes, currentColor, round caps. No icon library.
 */
function TabIcon({ tab }: { tab: SettingsTab }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (tab) {
    case "account": // person
      return (
        <svg {...common}>
          <circle cx="8" cy="5" r="2.5" />
          <path d="M3 13c0-2.5 2.2-4 5-4s5 1.5 5 4" />
        </svg>
      );
    case "preferences": // sliders
      return (
        <svg {...common}>
          <path d="M3 5h6M11 5h2M3 11h2M7 11h6" />
          <circle cx="10" cy="5" r="1.4" />
          <circle cx="6" cy="11" r="1.4" />
        </svg>
      );
    case "policy": // shield
      return (
        <svg {...common}>
          <path d="M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4l5-2z" />
        </svg>
      );
    case "budget": // coin / dollar
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6" />
          <path d="M8 4.5v7M9.8 6c-.4-.7-1.1-1-1.8-1-1 0-1.7.6-1.7 1.4 0 1.9 3.5 1 3.5 2.9 0 .8-.8 1.4-1.8 1.4-.8 0-1.5-.3-1.9-1" />
        </svg>
      );
    case "trust": // verified badge (check in circle)
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6" />
          <path d="M5.5 8.2l1.7 1.7 3.3-3.6" />
        </svg>
      );
    case "filters": // funnel
      return (
        <svg {...common}>
          <path d="M2.5 3.5h11l-4.3 5v4.3l-2.4 1.2V8.5l-4.3-5z" />
        </svg>
      );
    case "model": // chip / CPU
      return (
        <svg {...common}>
          <rect x="5" y="5" width="6" height="6" rx="1" />
          <path d="M6.5 2.5v2M9.5 2.5v2M6.5 11.5v2M9.5 11.5v2M2.5 6.5h2M2.5 9.5h2M11.5 6.5h2M11.5 9.5h2" />
        </svg>
      );
    default:
      return null;
  }
}

const POLICY_MODES = [
  { value: "lockdown", label: "Lockdown", description: "All actions blocked" },
  {
    value: "approval_required",
    label: "Approval Required",
    description: "All actions need approval",
  },
  {
    value: "suggest_only",
    label: "Suggest Only",
    description: "Muldro suggests, never acts",
  },
  {
    value: "full_auto",
    label: "Full Auto",
    description: "Muldro acts autonomously",
  },
];

const PERMISSION_MODES = [
  { value: "auto", label: "Auto", description: "Confirm only risky writes" },
  { value: "ask", label: "Ask", description: "Confirm every write" },
  {
    value: "bypass",
    label: "Bypass",
    description: "Never confirm (requires workspace entitlement)",
  },
];

export function SettingsModal() {
  const open = useSettingsModalStore((s) => s.open);
  const activeTab = useSettingsModalStore((s) => s.activeTab);
  const setActiveTab = useSettingsModalStore((s) => s.setActiveTab);
  const closeSettings = useSettingsModalStore((s) => s.closeSettings);

  const { user, logout } = useAuth();
  const { addToast } = useToast();

  const [policyMode, setPolicyModeState] = useState("approval_required");
  const [budgetLimit, setBudgetLimit] = useState<number | null>(null);
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetInput, setBudgetInput] = useState("");
  const [trustEntries, setTrustEntries] = useState<TrustDashboardEntry[]>([]);
  const [trustLoading, setTrustLoading] = useState(false);
  const [policyLoading, setPolicyLoading] = useState(false);
  const [defaultPermissionMode, setDefaultPermissionModeState] = useState("auto");
  const [permissionLoading, setPermissionLoading] = useState(false);
  const [budgetSaving, setBudgetSaving] = useState(false);
  const [ceilingLoading, setCeilingLoading] = useState<string | null>(null);
  const [resetLoading, setResetLoading] = useState<string | null>(null);
  const trustLoadedOnce = useRef(false);
  const [modelCatalog, setModelCatalog] = useState<ModelCatalog | null>(null);
  const [modelConfig, setModelConfig] = useState<ModelConfig | null>(null);
  const [modelLoading, setModelLoading] = useState(false);
  const [savingModelConfig, setSavingModelConfig] = useState(false);
  const [providerBusy, setProviderBusy] = useState<string | null>(null);
  const modelLoadedOnce = useRef(false);

  // Load policy + budget when the modal first opens.
  useEffect(() => {
    if (!open) return;
    fetchPolicyMode()
      .then((r) => setPolicyModeState(r.mode))
      .catch(() => {});
    fetchWorkspaceDefaultPermissionMode()
      .then((r) => setDefaultPermissionModeState(r.default_permission_mode))
      .catch(() => {});
    fetchBudget()
      .then((r) => setBudgetLimit(r.daily_limit_usd))
      .catch(() => {});
  }, [open]);

  // Esc to close.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeSettings();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, closeSettings]);

  const loadTrust = useCallback(async () => {
    setTrustLoading(true);
    try {
      const data = await fetchTrustDashboard();
      setTrustEntries(data.capabilities);
    } catch {
      addToast("Failed to load trust data", "error");
    } finally {
      setTrustLoading(false);
    }
  }, [addToast]);

  const handleTrustLoad = useCallback(() => {
    if (trustLoadedOnce.current) return;
    trustLoadedOnce.current = true;
    loadTrust();
  }, [loadTrust]);

  const handlePolicyChange = useCallback(
    async (mode: string) => {
      setPolicyLoading(true);
      try {
        await setPolicyMode(mode);
        setPolicyModeState(mode);
        addToast("Policy mode updated", "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setPolicyLoading(false);
      }
    },
    [addToast],
  );

  const handleDefaultPermissionModeChange = useCallback(
    async (mode: string) => {
      setPermissionLoading(true);
      try {
        await setWorkspaceDefaultPermissionMode(mode);
        setDefaultPermissionModeState(mode);
        addToast("Default permission mode updated", "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setPermissionLoading(false);
      }
    },
    [addToast],
  );

  const handleBudgetSave = useCallback(async () => {
    const value = parseFloat(budgetInput);
    if (isNaN(value) || value <= 0) return;
    setBudgetSaving(true);
    try {
      const res = await updateBudgetLimit(value);
      setBudgetLimit(res.daily_limit_usd);
      setEditingBudget(false);
      addToast("Budget updated", "success");
    } catch (err) {
      addToast(errorToMessage(err), "error");
    } finally {
      setBudgetSaving(false);
    }
  }, [budgetInput, addToast]);

  const handleCeilingChange = useCallback(
    async (capability: string, maxLevel: string) => {
      setCeilingLoading(capability);
      try {
        await setTrustCeiling(capability, maxLevel);
        setTrustEntries((prev) =>
          prev.map((e) =>
            e.capability === capability ? { ...e, ceiling: maxLevel } : e,
          ),
        );
        addToast(
          `Ceiling set to ${TRUST_LEVEL_LABELS[maxLevel] ?? maxLevel}`,
          "success",
        );
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setCeilingLoading(null);
      }
    },
    [addToast],
  );

  const handleResetTrust = useCallback(
    async (capability: string) => {
      setResetLoading(capability);
      try {
        await resetTrust(capability);
        await loadTrust();
        addToast(`Trust reset for ${capability}`, "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setResetLoading(null);
      }
    },
    [loadTrust, addToast],
  );

  const handleModelLoad = useCallback(async () => {
    if (modelLoadedOnce.current) return;
    modelLoadedOnce.current = true;
    setModelLoading(true);
    try {
      const [catalog, config] = await Promise.all([
        fetchModelCatalog(),
        fetchModelConfig(),
      ]);
      setModelCatalog(catalog);
      setModelConfig(config);
    } catch (err) {
      modelLoadedOnce.current = false;
      addToast(errorToMessage(err), "error");
    } finally {
      setModelLoading(false);
    }
  }, [addToast]);

  const handleSaveModelConfig = useCallback(
    async (body: { tiers: ModelBinding[]; agent_overrides: ModelBinding[] }) => {
      setSavingModelConfig(true);
      try {
        const updated = await saveModelConfig(body);
        setModelConfig(updated);
        addToast("Model configuration saved", "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setSavingModelConfig(false);
      }
    },
    [addToast],
  );

  const handleSaveProviderKey = useCallback(
    async (
      provider: string,
      fields: { api_key?: string; base_url?: string | null },
    ) => {
      setProviderBusy(provider);
      try {
        await saveProviderCredential(provider, fields);
        const config = await fetchModelConfig();
        setModelConfig(config);
        addToast(`${provider} credentials saved`, "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setProviderBusy(null);
      }
    },
    [addToast],
  );

  const handleTestProvider = useCallback(
    async (provider: string) => {
      setProviderBusy(provider);
      try {
        const result = await testProviderKey(provider);
        const config = await fetchModelConfig();
        setModelConfig(config);
        addToast(`${provider} test: ${result.status}`, "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setProviderBusy(null);
      }
    },
    [addToast],
  );

  const handleDeleteProviderKey = useCallback(
    async (provider: string) => {
      setProviderBusy(provider);
      try {
        const result = await deleteProviderKey(provider);
        const config = await fetchModelConfig();
        setModelConfig(config);
        // A revoke can orphan bindings that depended on this credential -- surface
        // that consequence instead of reporting a plain success.
        if (result.orphaned_bindings.length > 0) {
          addToast(result.orphaned_bindings[0].message, "error");
        } else {
          addToast(`${provider} credentials removed`, "success");
        }
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setProviderBusy(null);
      }
    },
    [addToast],
  );

  if (!open) return null;

  // Group trust entries by family.
  const trustByFamily: Record<string, TrustDashboardEntry[]> = {};
  for (const entry of trustEntries) {
    const family = entry.family || "unknown";
    if (!trustByFamily[family]) trustByFamily[family] = [];
    trustByFamily[family].push(entry);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
    >
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={closeSettings}
      />
      <div className="relative bg-surface-1 border border-b-secondary rounded-[var(--radius-xl)] shadow-[var(--shadow-lg)] w-full max-w-3xl mx-4 h-[600px] max-h-[calc(100vh-4rem)] flex flex-col sm:flex-row overflow-hidden animate-scale-in">
        {/* Left tab rail */}
        <nav className="flex sm:flex-col gap-1 shrink-0 border-b sm:border-b-0 sm:border-r border-b-secondary bg-surface-2/40 p-2 sm:w-44 overflow-x-auto sm:overflow-x-visible">
          <div className="hidden sm:block px-2 py-2 mb-1">
            <h2 className="text-[15px] font-semibold text-t-primary">Settings</h2>
          </div>
          {TABS.map((tab) => {
            const isActive = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                aria-current={isActive ? "true" : undefined}
                className={`flex items-center gap-2.5 text-left rounded-[var(--radius-md)] px-3 py-2 text-[13px] whitespace-nowrap transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring ${
                  isActive
                    ? "bg-j-primary-soft text-j-primary font-medium"
                    : "text-t-tertiary hover:text-t-primary hover:bg-surface-2"
                }`}
              >
                <span className="shrink-0">
                  <TabIcon tab={tab.key} />
                </span>
                {tab.label}
              </button>
            );
          })}
        </nav>

        {/* Content pane */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex items-center justify-end px-4 py-3 border-b border-b-secondary shrink-0">
            <button
              onClick={closeSettings}
              className="p-1 rounded-[var(--radius-sm)] text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring"
              aria-label="Close settings"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path
                  d="M4 4l8 8M12 4l-8 8"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-5">
            {activeTab === "account" && (
              <AccountTab
                email={user?.email ?? null}
                displayName={user?.display_name ?? null}
                onSignOut={logout}
              />
            )}

            {activeTab === "preferences" && <PreferencesTab />}

            {activeTab === "policy" && (
              <PolicyTab
                policyMode={policyMode}
                policyModes={POLICY_MODES}
                policyLoading={policyLoading}
                onPolicyChange={handlePolicyChange}
                defaultPermissionMode={defaultPermissionMode}
                permissionModes={PERMISSION_MODES}
                permissionLoading={permissionLoading}
                onDefaultPermissionModeChange={handleDefaultPermissionModeChange}
              />
            )}

            {activeTab === "budget" && (
              <SpendingTab
                budgetLimit={budgetLimit}
                editing={editingBudget}
                input={budgetInput}
                saving={budgetSaving}
                onEditStart={() => {
                  setBudgetInput(String(budgetLimit ?? 5));
                  setEditingBudget(true);
                }}
                onInputChange={setBudgetInput}
                onSave={handleBudgetSave}
                onCancel={() => setEditingBudget(false)}
              />
            )}

            {activeTab === "trust" && (
              <TrustTab
                trustByFamily={trustByFamily}
                loading={trustLoading}
                onLoad={handleTrustLoad}
                onCeilingChange={handleCeilingChange}
                onReset={handleResetTrust}
                ceilingLoading={ceilingLoading}
                resetLoading={resetLoading}
              />
            )}

            {activeTab === "filters" && <FiltersTab />}

            {activeTab === "model" && (
              <ModelTab
                open={open}
                loading={modelLoading}
                catalog={modelCatalog}
                config={modelConfig}
                onLoad={handleModelLoad}
                onSaveConfig={handleSaveModelConfig}
                onSaveProviderKey={handleSaveProviderKey}
                onTestProvider={handleTestProvider}
                onDeleteProvider={handleDeleteProviderKey}
                savingConfig={savingModelConfig}
                providerBusy={providerBusy}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
