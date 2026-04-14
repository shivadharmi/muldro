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

const TRUST_LEVEL_COLORS: Record<string, string> = {
  first_use: "bg-t-muted",
  learning: "bg-j-info",
  trusted: "bg-j-success",
  autonomous: "bg-j-secondary",
  blocked: "bg-j-error",
};

const TRUST_LEVEL_LABELS: Record<string, string> = {
  first_use: "First Use",
  learning: "Learning",
  trusted: "Trusted",
  autonomous: "Autonomous",
  blocked: "Blocked",
};

const CEILING_OPTIONS = [
  { value: "blocked", label: "Blocked" },
  { value: "first_use", label: "First Use" },
  { value: "learning", label: "Learning" },
  { value: "trusted", label: "Trusted" },
  { value: "autonomous", label: "Autonomous (no limit)" },
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
        <Card>
          <CardBody>
            <div className="space-y-5">
              <div className="grid grid-cols-[120px_1fr] gap-y-4 gap-x-4 items-baseline">
                <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider">Email</p>
                <p className="text-sm text-t-primary">
                  {user?.email ?? "—"}
                </p>
                <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider">
                  Display Name
                </p>
                <p className="text-sm text-t-primary">
                  {user?.display_name ?? "—"}
                </p>
              </div>
              <div className="pt-4 border-t border-b-secondary">
                <button
                  onClick={logout}
                  className="px-4 py-2 rounded-[var(--radius-md)] border border-j-error/30 text-j-error text-[13px] font-medium hover:bg-j-error-soft transition-colors cursor-pointer"
                >
                  Sign Out
                </button>
              </div>
            </div>
          </CardBody>
        </Card>
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
        <Card>
          <CardBody>
            <div className="space-y-4">
              <div>
                <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
                  Daily Token Budget
                </p>
                {editingBudget ? (
                  <div className="flex items-center gap-2">
                    <span className="text-t-secondary text-sm">$</span>
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={budgetInput}
                      onChange={(e) => setBudgetInput(e.target.value)}
                      className="w-32 rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring transition-colors"
                      autoFocus
                    />
                    <button
                      onClick={handleBudgetSave}
                      disabled={budgetSaving}
                      className="px-3.5 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover disabled:opacity-50 transition-colors cursor-pointer"
                    >
                      {budgetSaving ? "Saving..." : "Save"}
                    </button>
                    <button
                      onClick={() => setEditingBudget(false)}
                      className="px-3.5 py-2 rounded-[var(--radius-md)] text-t-secondary text-[13px] hover:bg-surface-2 transition-colors cursor-pointer"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-3">
                    <p className="text-2xl font-semibold text-t-primary tracking-tight">
                      ${budgetLimit?.toFixed(2) ?? "—"}
                      <span className="text-sm text-t-muted font-normal ml-1">
                        / day
                      </span>
                    </p>
                    <button
                      onClick={() => {
                        setBudgetInput(String(budgetLimit ?? 5));
                        setEditingBudget(true);
                      }}
                      className="text-xs text-j-primary hover:text-j-primary-hover font-medium cursor-pointer"
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

// ── Trust Capability Card ───────────────────────────────────────

interface TrustCapabilityCardProps {
  entry: TrustDashboardEntry;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingDisabled?: boolean;
  resetDisabled?: boolean;
}

function TrustCapabilityCard({
  entry,
  onCeilingChange,
  onReset,
  ceilingDisabled,
  resetDisabled,
}: TrustCapabilityCardProps) {
  const [expanded, setExpanded] = useState(false);

  const bestProgress = entry.risk_levels.reduce((best, rl) => {
    const pct = rl.graduation_progress?.percentage ?? 0;
    return pct > best ? pct : best;
  }, 0);

  return (
    <div className="rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 overflow-hidden">
      <div className="px-4 py-3 space-y-2">
        {/* Header row */}
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center justify-between text-left cursor-pointer group"
        >
          <div className="flex items-center gap-2.5">
            <span
              className={`w-2 h-2 rounded-full shrink-0 ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-t-muted"}`}
            />
            <span className="text-[13px] font-medium text-t-primary">
              {entry.capability}
            </span>
            <span className="text-[11px] text-t-muted px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-2">
              {TRUST_LEVEL_LABELS[entry.trust_level] ?? entry.trust_level}
            </span>
          </div>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            className={`text-t-muted group-hover:text-t-secondary transition-all duration-150 ${expanded ? "rotate-90" : ""}`}
          >
            <path
              d="M9 18l6-6-6-6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        {/* Graduation progress bar */}
        {entry.trust_level !== "autonomous" && (
          <div className="w-full h-1 bg-surface-3 rounded-full">
            <div
              className={`h-full rounded-full transition-all duration-300 ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-t-muted"}`}
              style={{
                width: `${Math.min(bestProgress * 100, 100)}%`,
              }}
            />
          </div>
        )}
      </div>

      {/* Expanded: per-risk breakdown + controls */}
      {expanded && (
        <div className="px-4 pb-4 pt-2 space-y-3 border-t border-b-secondary">
          {entry.risk_levels.map((rl) => (
            <div
              key={rl.risk_level}
              className="flex items-center justify-between text-xs"
            >
              <span className="text-t-secondary w-16 capitalize">
                {rl.risk_level}
              </span>
              <span
                className={`px-1.5 py-0.5 rounded-[var(--radius-sm)] ${TRUST_LEVEL_COLORS[rl.trust_level] ?? "bg-t-muted"} text-white text-[10px] font-medium`}
              >
                {TRUST_LEVEL_LABELS[rl.trust_level] ?? rl.trust_level}
              </span>
              <span className="text-t-tertiary">
                {rl.approved_count}
                <span className="text-t-muted"> approved</span>
                {rl.rejected_count > 0 && (
                  <span className="text-j-error ml-1">
                    {rl.rejected_count} rejected
                  </span>
                )}
              </span>
              {rl.graduation_progress?.next_level && (
                <span className="text-t-muted text-[10px]">
                  {rl.graduation_progress.current}/
                  {rl.graduation_progress.target} to{" "}
                  {TRUST_LEVEL_LABELS[
                    rl.graduation_progress.next_level
                  ] ?? rl.graduation_progress.next_level}
                </span>
              )}
            </div>
          ))}

          {/* Ceiling control */}
          <div className="flex items-center gap-2 pt-2 border-t border-b-secondary">
            <label className="text-[11px] text-t-muted font-medium">Ceiling</label>
            <select
              value={entry.ceiling}
              onChange={(e) =>
                onCeilingChange(entry.capability, e.target.value)
              }
              disabled={ceilingDisabled}
              className="text-xs rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-2.5 py-1.5 text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring disabled:opacity-50 cursor-pointer"
            >
              {CEILING_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>

            <button
              onClick={() => onReset(entry.capability)}
              disabled={resetDisabled}
              className="ml-auto text-xs text-j-error hover:text-j-error/80 font-medium disabled:opacity-50 cursor-pointer"
            >
              {resetDisabled ? "Resetting..." : "Reset"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
