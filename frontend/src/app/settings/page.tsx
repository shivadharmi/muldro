"use client";

import { useState, useEffect, useCallback } from "react";
import { PageHeader } from "@/components/layout/page-header";
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
import { errorToMessage } from "@/lib/api-error";
import { TRUST_LEVEL_LABELS } from "@/components/settings/trust-constants";
import { AccountTab } from "@/components/settings/account-tab";
import { HowJarvisActsTab } from "@/components/settings/how-jarvis-acts-tab";
import { SpendingTab } from "@/components/settings/spending-tab";

type SettingsTab = "account" | "how_jarvis_acts" | "spending";

const TABS = [
  { key: "account", label: "Account" },
  { key: "how_jarvis_acts", label: "How Jarvis acts" },
  { key: "spending", label: "Spending" },
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
  const [trustLoadedOnce, setTrustLoadedOnce] = useState(false);
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

  const handleTrustExpand = useCallback(() => {
    if (trustLoadedOnce) return;
    setTrustLoadedOnce(true);
    loadTrust();
  }, [trustLoadedOnce, loadTrust]);

  async function handlePolicyChange(mode: string) {
    setPolicyLoading(true);
    try {
      await setPolicyMode(mode);
      setPolicyModeState(mode);
      addToast("Policy mode updated", "success");
    } catch (err) {
      addToast(
        errorToMessage(err),
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
        errorToMessage(err),
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
        errorToMessage(err),
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
        errorToMessage(err),
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
        subtitle="Manage your account, autonomy, and spending"
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

      {activeTab === "how_jarvis_acts" && (
        <HowJarvisActsTab
          policyMode={policyMode}
          policyModes={POLICY_MODES}
          policyLoading={policyLoading}
          onPolicyChange={handlePolicyChange}
          trustByFamily={trustByFamily}
          trustLoading={trustLoading}
          onTrustExpand={handleTrustExpand}
          onCeilingChange={handleCeilingChange}
          onReset={handleResetTrust}
          ceilingLoading={ceilingLoading}
          resetLoading={resetLoading}
        />
      )}

      {activeTab === "spending" && (
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
    </div>
  );
}
