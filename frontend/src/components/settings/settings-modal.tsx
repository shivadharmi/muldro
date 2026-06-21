"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchPolicyMode,
  setPolicyMode,
  fetchBudget,
  updateBudgetLimit,
  fetchTrustDashboard,
  setTrustCeiling,
  resetTrust,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";
import { errorToMessage } from "@/lib/api-error";
import type { TrustDashboardEntry } from "@/lib/types";
import { TRUST_LEVEL_LABELS } from "@/components/settings/trust-constants";
import {
  useSettingsModalStore,
  type SettingsTab,
} from "@/stores/settings-modal-store";
import { AccountTab } from "./account-tab";
import { PreferencesTab } from "./preferences-tab";
import { PolicyTab } from "./policy-tab";
import { TrustTab } from "./trust-tab";
import { SpendingTab } from "./spending-tab";

const TABS: Array<{ key: SettingsTab; label: string }> = [
  { key: "account", label: "Account" },
  { key: "preferences", label: "Preferences" },
  { key: "policy", label: "Policy" },
  { key: "budget", label: "Budget" },
  { key: "trust", label: "Trust" },
];

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
    description: "Jarvis suggests, never acts",
  },
  {
    value: "full_auto",
    label: "Full Auto",
    description: "Jarvis acts autonomously",
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
  const [budgetSaving, setBudgetSaving] = useState(false);
  const [ceilingLoading, setCeilingLoading] = useState<string | null>(null);
  const [resetLoading, setResetLoading] = useState<string | null>(null);
  const trustLoadedOnce = useRef(false);

  // Load policy + budget when the modal first opens.
  useEffect(() => {
    if (!open) return;
    fetchPolicyMode()
      .then((r) => setPolicyModeState(r.mode))
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
                className={`text-left rounded-[var(--radius-md)] px-3 py-2 text-[13px] whitespace-nowrap transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring ${
                  isActive
                    ? "bg-j-primary-soft text-j-primary font-medium"
                    : "text-t-tertiary hover:text-t-primary hover:bg-surface-2"
                }`}
              >
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
          </div>
        </div>
      </div>
    </div>
  );
}
