"use client";

import { useState, useEffect } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import {
  fetchPolicyMode,
  setPolicyMode,
  fetchBudget,
  updateBudgetLimit,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";
type SettingsTab = "account" | "policy" | "budget";

const TABS = [
  { key: "account", label: "Account" },
  { key: "policy", label: "Policy" },
  { key: "budget", label: "Budget" },
];

const POLICY_MODES = [
  { value: "lockdown", label: "Lockdown", description: "All actions blocked" },
  { value: "approval_required", label: "Approval Required", description: "All actions need approval" },
  { value: "suggest_only", label: "Suggest Only", description: "Jarvis suggests, never acts" },
  { value: "full_auto", label: "Full Auto", description: "Jarvis acts autonomously" },
];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("account");
  const [policyMode, setPolicyModeState] = useState("approval_required");
  const [budgetLimit, setBudgetLimit] = useState<number | null>(null);
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetInput, setBudgetInput] = useState("");
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

  async function handlePolicyChange(mode: string) {
    try {
      await setPolicyMode(mode);
      setPolicyModeState(mode);
      addToast("Policy mode updated", "success");
    } catch (err) {
      addToast(`Failed: ${err instanceof Error ? err.message : "Unknown"}`, "error");
    }
  }

  async function handleBudgetSave() {
    const value = parseFloat(budgetInput);
    if (isNaN(value) || value <= 0) return;
    try {
      const res = await updateBudgetLimit(value);
      setBudgetLimit(res.daily_limit_usd);
      setEditingBudget(false);
      addToast("Budget updated", "success");
    } catch (err) {
      addToast(`Failed: ${err instanceof Error ? err.message : "Unknown"}`, "error");
    }
  }

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader title="Settings" subtitle="Account, policy, and budget" />
      <Tabs
        tabs={TABS}
        active={activeTab}
        onChange={(k) => setActiveTab(k as SettingsTab)}
      />

      {activeTab === "account" && (
        <Card>
          <CardBody>
            <div className="space-y-4">
              <div>
                <p className="text-xs text-t-muted uppercase mb-1">Email</p>
                <p className="text-sm text-t-primary">{user?.email ?? "—"}</p>
              </div>
              <div>
                <p className="text-xs text-t-muted uppercase mb-1">Display Name</p>
                <p className="text-sm text-t-primary">{user?.display_name ?? "—"}</p>
              </div>
              <button
                onClick={logout}
                className="px-4 py-2 rounded-lg border border-j-error text-j-error text-sm hover:bg-j-error/10 transition-colors"
              >
                Sign Out
              </button>
            </div>
          </CardBody>
        </Card>
      )}

      {activeTab === "policy" && (
        <div className="space-y-3">
          {POLICY_MODES.map((pm) => (
            <Card
              key={pm.value}
              className={policyMode === pm.value ? "ring-1 ring-accent-primary" : ""}
            >
              <CardBody>
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="radio"
                    name="policy"
                    checked={policyMode === pm.value}
                    onChange={() => handlePolicyChange(pm.value)}
                    className="mt-0.5"
                  />
                  <div>
                    <p className="text-sm font-medium text-t-primary">{pm.label}</p>
                    <p className="text-xs text-t-tertiary">{pm.description}</p>
                  </div>
                </label>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {activeTab === "budget" && (
        <Card>
          <CardBody>
            <div className="space-y-4">
              <div>
                <p className="text-xs text-t-muted uppercase mb-1">Daily Token Budget</p>
                {editingBudget ? (
                  <div className="flex items-center gap-2">
                    <span className="text-t-secondary">$</span>
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={budgetInput}
                      onChange={(e) => setBudgetInput(e.target.value)}
                      className="w-32 rounded bg-surface-2 border border-b-primary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring"
                      autoFocus
                    />
                    <button
                      onClick={handleBudgetSave}
                      className="px-3 py-2 rounded-lg bg-j-primary text-j-primary-fg text-sm hover:bg-j-primary-hover"
                    >
                      Save
                    </button>
                    <button
                      onClick={() => setEditingBudget(false)}
                      className="px-3 py-2 rounded-lg text-t-secondary text-sm hover:bg-surface-2"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <p className="text-lg font-semibold text-t-primary">
                      ${budgetLimit?.toFixed(2) ?? "—"}
                      <span className="text-xs text-t-tertiary font-normal ml-1">/ day</span>
                    </p>
                    <button
                      onClick={() => {
                        setBudgetInput(String(budgetLimit ?? 5));
                        setEditingBudget(true);
                      }}
                      className="text-xs text-accent-primary hover:underline"
                    >
                      Edit
                    </button>
                  </div>
                )}
              </div>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
