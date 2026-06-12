"use client";

import { useState, useEffect, useCallback } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
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
import type { TrustDashboardEntry } from "@/lib/types";
import { TrustCapabilityCard } from "@/components/settings/trust-capability-card";
import { TRUST_LEVEL_LABELS } from "@/components/settings/trust-constants";
import { AccountTab } from "@/components/settings/account-tab";
import { BudgetTab } from "@/components/settings/budget-tab";

type SettingsTab = "account" | "policy" | "trust" | "budget";

const TABS = [
  { key: "account", label: "Account" },
  { key: "policy", label: "Policy" },
  { key: "trust", label: "Trust" },
  { key: "budget", label: "Budget" },
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

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("account");
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
  const { user, logout } = useAuth();
  const { addToast } = useToast();

  useEffect(() => {
    fetchPolicyMode()
      .then((r) => setPolicyModeState(r.mode))
      .catch(() => {});
    fetchBudget()
      .then((r) => setBudgetLimit(r.daily_limit_usd))
      .catch(() => {});
  }, []);

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

  useEffect(() => {
    if (activeTab === "trust") {
      loadTrust();
    }
  }, [activeTab, loadTrust]);

  async function handlePolicyChange(mode: string) {
    setPolicyLoading(true);
    try {
      await setPolicyMode(mode);
      setPolicyModeState(mode);
      addToast("Policy mode updated", "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    } finally {
      setPolicyLoading(false);
    }
  }

  async function handleBudgetSave() {
    const value = parseFloat(budgetInput);
    if (isNaN(value) || value <= 0) return;
    setBudgetSaving(true);
    try {
      const res = await updateBudgetLimit(value);
      setBudgetLimit(res.daily_limit_usd);
      setEditingBudget(false);
      addToast("Budget updated", "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    } finally {
      setBudgetSaving(false);
    }
  }

  async function handleCeilingChange(capability: string, maxLevel: string) {
    setCeilingLoading(capability);
    try {
      await setTrustCeiling(capability, maxLevel);
      setTrustEntries((prev) =>
        prev.map((e) =>
          e.capability === capability ? { ...e, ceiling: maxLevel } : e
        )
      );
      addToast(
        `Ceiling set to ${TRUST_LEVEL_LABELS[maxLevel] ?? maxLevel}`,
        "success"
      );
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    } finally {
      setCeilingLoading(null);
    }
  }

  async function handleResetTrust(capability: string) {
    setResetLoading(capability);
    try {
      await resetTrust(capability);
      await loadTrust();
      addToast(`Trust reset for ${capability}`, "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    } finally {
      setResetLoading(null);
    }
  }

  // Group trust entries by family
  const trustByFamily: Record<string, TrustDashboardEntry[]> = {};
  for (const entry of trustEntries) {
    const family = entry.family || "unknown";
    if (!trustByFamily[family]) trustByFamily[family] = [];
    trustByFamily[family].push(entry);
  }

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-3xl animate-fade-in">
      <PageHeader
        title="Settings"
        subtitle="Manage your account, policies, trust levels, and budget"
      />
      <Tabs
        tabs={TABS}
        active={activeTab}
        onChange={(k) => setActiveTab(k as SettingsTab)}
      />

      {activeTab === "account" && (
        <AccountTab
          email={user?.email ?? null}
          displayName={user?.display_name ?? null}
          onSignOut={logout}
        />
      )}

      {activeTab === "policy" && (
        <div className="space-y-2">
          {POLICY_MODES.map((pm) => {
            const isActive = policyMode === pm.value;
            return (
              <button
                key={pm.value}
                type="button"
                onClick={() => handlePolicyChange(pm.value)}
                disabled={policyLoading}
                className={`w-full text-left rounded-[var(--radius-lg)] border p-4 transition-all duration-150 cursor-pointer ${
                  isActive
                    ? "border-j-primary/40 bg-j-primary-soft"
                    : "border-b-secondary bg-surface-1 hover:bg-surface-2"
                } disabled:opacity-50`}
              >
                <div className="flex items-center gap-3">
                  <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${
                    isActive ? "border-j-primary" : "border-b-strong"
                  }`}>
                    {isActive && <div className="w-2 h-2 rounded-full bg-j-primary" />}
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-t-primary">
                      {pm.label}
                    </p>
                    <p className="text-xs text-t-tertiary mt-0.5">
                      {pm.description}
                    </p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {activeTab === "trust" && (
        <div className="space-y-6">
          {trustLoading && (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-16 rounded-[var(--radius-lg)] skeleton" />
              ))}
            </div>
          )}

          {!trustLoading && trustEntries.length === 0 && (
            <Card>
              <CardBody>
                <div className="text-center py-4">
                  <p className="text-sm text-t-secondary font-medium mb-1">No trust data yet</p>
                  <p className="text-xs text-t-muted">
                    Trust levels build as Jarvis performs actions and you approve or reject them.
                  </p>
                </div>
              </CardBody>
            </Card>
          )}

          {Object.entries(trustByFamily).map(([family, entries]) => (
            <div key={family}>
              <h3 className="text-[11px] uppercase text-t-muted font-medium mb-2.5 tracking-wider">
                {family}
              </h3>
              <div className="space-y-2">
                {entries.map((entry) => (
                  <TrustCapabilityCard
                    key={entry.capability}
                    entry={entry}
                    onCeilingChange={handleCeilingChange}
                    onReset={handleResetTrust}
                    ceilingDisabled={ceilingLoading === entry.capability}
                    resetDisabled={resetLoading === entry.capability}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {activeTab === "budget" && (
        <BudgetTab
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
    </div>
  );
}
