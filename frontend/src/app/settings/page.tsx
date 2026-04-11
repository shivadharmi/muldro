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
  first_use: "bg-gray-500",
  learning: "bg-blue-500",
  trusted: "bg-green-500",
  autonomous: "bg-purple-500",
  blocked: "bg-red-500",
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
    try {
      await setPolicyMode(mode);
      setPolicyModeState(mode);
      addToast("Policy mode updated", "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
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
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
    }
  }

  async function handleCeilingChange(capability: string, maxLevel: string) {
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
    }
  }

  async function handleResetTrust(capability: string) {
    try {
      await resetTrust(capability);
      await loadTrust();
      addToast(`Trust reset for ${capability}`, "success");
    } catch (err) {
      addToast(
        `Failed: ${err instanceof Error ? err.message : "Unknown"}`,
        "error"
      );
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
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader
        title="Settings"
        subtitle="Account, policy, trust, and budget"
      />
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
                <p className="text-sm text-t-primary">
                  {user?.email ?? "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-t-muted uppercase mb-1">
                  Display Name
                </p>
                <p className="text-sm text-t-primary">
                  {user?.display_name ?? "—"}
                </p>
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
              className={
                policyMode === pm.value ? "ring-1 ring-accent-primary" : ""
              }
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
                    <p className="text-sm font-medium text-t-primary">
                      {pm.label}
                    </p>
                    <p className="text-xs text-t-tertiary">
                      {pm.description}
                    </p>
                  </div>
                </label>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {activeTab === "trust" && (
        <div className="space-y-6">
          {trustLoading && (
            <p className="text-sm text-t-tertiary">Loading trust data...</p>
          )}

          {!trustLoading && trustEntries.length === 0 && (
            <Card>
              <CardBody>
                <p className="text-sm text-t-tertiary">
                  No trust data yet. Trust levels build as Jarvis performs
                  actions and you approve or reject them.
                </p>
              </CardBody>
            </Card>
          )}

          {Object.entries(trustByFamily).map(([family, entries]) => (
            <div key={family}>
              <h3 className="text-xs uppercase text-t-muted mb-2 tracking-wider">
                {family}
              </h3>
              <div className="space-y-2">
                {entries.map((entry) => (
                  <TrustCapabilityCard
                    key={entry.capability}
                    entry={entry}
                    onCeilingChange={handleCeilingChange}
                    onReset={handleResetTrust}
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
                <p className="text-xs text-t-muted uppercase mb-1">
                  Daily Token Budget
                </p>
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
                      <span className="text-xs text-t-tertiary font-normal ml-1">
                        / day
                      </span>
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

// ── Trust Capability Card ───────────────────────────────────────

interface TrustCapabilityCardProps {
  entry: TrustDashboardEntry;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
}

function TrustCapabilityCard({
  entry,
  onCeilingChange,
  onReset,
}: TrustCapabilityCardProps) {
  const [expanded, setExpanded] = useState(false);

  const bestProgress = entry.risk_levels.reduce((best, rl) => {
    const pct = rl.graduation_progress?.percentage ?? 0;
    return pct > best ? pct : best;
  }, 0);

  return (
    <Card>
      <CardBody>
        <div className="space-y-2">
          {/* Header row */}
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center justify-between text-left"
          >
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-gray-400"}`}
              />
              <span className="text-sm font-medium text-t-primary">
                {entry.capability}
              </span>
              <span className="text-xs text-t-tertiary">
                {TRUST_LEVEL_LABELS[entry.trust_level] ?? entry.trust_level}
              </span>
            </div>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              className={`text-t-tertiary transition-transform ${expanded ? "rotate-90" : ""}`}
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
            <div className="w-full h-1.5 bg-surface-2 rounded-full">
              <div
                className={`h-full rounded-full transition-all ${TRUST_LEVEL_COLORS[entry.trust_level] ?? "bg-gray-400"}`}
                style={{
                  width: `${Math.min(bestProgress * 100, 100)}%`,
                }}
              />
            </div>
          )}

          {/* Expanded: per-risk breakdown + controls */}
          {expanded && (
            <div className="pt-2 space-y-3 border-t border-b-primary">
              {entry.risk_levels.map((rl) => (
                <div
                  key={rl.risk_level}
                  className="flex items-center justify-between text-xs"
                >
                  <span className="text-t-secondary w-16">
                    {rl.risk_level}
                  </span>
                  <span
                    className={`px-1.5 py-0.5 rounded ${TRUST_LEVEL_COLORS[rl.trust_level] ?? "bg-gray-400"} text-white`}
                  >
                    {TRUST_LEVEL_LABELS[rl.trust_level] ?? rl.trust_level}
                  </span>
                  <span className="text-t-tertiary">
                    {rl.approved_count}
                    <span className="text-t-muted"> approved</span>
                    {rl.rejected_count > 0 && (
                      <span className="text-red-400 ml-1">
                        {rl.rejected_count} rejected
                      </span>
                    )}
                  </span>
                  {rl.graduation_progress?.next_level && (
                    <span className="text-t-tertiary">
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
              <div className="flex items-center gap-2 pt-2">
                <label className="text-xs text-t-muted">Ceiling:</label>
                <select
                  value={entry.ceiling}
                  onChange={(e) =>
                    onCeilingChange(entry.capability, e.target.value)
                  }
                  className="text-xs rounded bg-surface-2 border border-b-primary px-2 py-1 text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring"
                >
                  {CEILING_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>

                <button
                  onClick={() => onReset(entry.capability)}
                  className="ml-auto text-xs text-j-error hover:underline"
                >
                  Reset Trust
                </button>
              </div>
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
