"use client";

import { Fragment, useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardHeader, CardBody } from "@/components/ui/card";
import { Table, TableHeader, TableBody, Th, Td } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { OAuthPanel } from "@/components/settings/oauth-panel";
import { AGENT_CONFIGS } from "@/lib/agent-config";
import {
  fetchPolicyMode,
  setPolicyMode,
  fetchBudget,
  updateBudgetLimit,
  fetchSchedules,
  fetchTriggers,
  fetchRoutes,
  fetchWorkflows,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";

type SettingsTab =
  | "agents"
  | "policy"
  | "budget"
  | "connectors"
  | "schedules"
  | "triggers"
  | "routes"
  | "workflows"
  | "account";

const POLICY_MODES = [
  { value: "lockdown", label: "Lockdown", description: "All actions blocked" },
  { value: "approval_required", label: "Approval Required", description: "All actions need approval" },
  { value: "suggest_only", label: "Suggest Only", description: "Jarvis suggests, never acts" },
  { value: "full_auto", label: "Full Auto", description: "Jarvis acts autonomously" },
];

export default function SettingsPage() {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<SettingsTab>("agents");
  const [policyMode, setPolicyModeState] = useState("approval_required");
  const [budgetLimit, setBudgetLimit] = useState<number | null>(null);
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetInput, setBudgetInput] = useState("");
  const { user, logout } = useAuth();
  const { addToast } = useToast();

  const { data: schedules } = useQuery({
    queryKey: ["schedules"],
    queryFn: fetchSchedules,
    enabled: activeTab === "schedules",
  });

  const { data: triggers } = useQuery({
    queryKey: ["triggers"],
    queryFn: fetchTriggers,
    enabled: activeTab === "triggers",
  });

  const { data: routes } = useQuery({
    queryKey: ["routes"],
    queryFn: fetchRoutes,
    enabled: activeTab === "routes",
  });

  const { data: workflows } = useQuery({
    queryKey: ["workflows"],
    queryFn: fetchWorkflows,
    enabled: activeTab === "workflows",
  });

  useEffect(() => {
    fetchPolicyMode().then((r) => setPolicyModeState(r.mode)).catch((err) => {
      addToast(`Failed to load policy: ${err.message}`, "error");
    });
    fetchBudget().then((r) => setBudgetLimit(r.daily_limit_usd)).catch((err) => {
      addToast(`Failed to load budget: ${err.message}`, "error");
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handlePolicyChange(mode: string) {
    try {
      await setPolicyMode(mode);
      setPolicyModeState(mode);
      addToast("Policy mode updated", "success");
    } catch (err) {
      addToast(`Failed to update policy: ${err instanceof Error ? err.message : "Unknown error"}`, "error");
    }
  }

  async function handleBudgetSave() {
    const value = parseFloat(budgetInput);
    if (isNaN(value) || value <= 0) return;
    try {
      const res = await updateBudgetLimit(value);
      setBudgetLimit(res.daily_limit_usd);
      setEditingBudget(false);
      addToast("Budget limit updated", "success");
    } catch (err) {
      addToast(`Failed to update budget: ${err instanceof Error ? err.message : "Unknown error"}`, "error");
    }
  }

  const tabs: { key: SettingsTab; label: string }[] = [
    { key: "agents", label: "Agents" },
    { key: "policy", label: "Policy" },
    { key: "budget", label: "Budget" },
    { key: "connectors", label: "Connectors" },
    { key: "schedules", label: "Schedules" },
    { key: "triggers", label: "Triggers" },
    { key: "routes", label: "Routes" },
    { key: "workflows", label: "Workflows" },
    { key: "account", label: "Account" },
  ];

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader title="Settings" subtitle="System configuration and preferences" variant="config" />

      <div className="flex gap-1 border-b border-b-primary pb-px flex-wrap">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap cursor-pointer ${
              activeTab === tab.key
                ? "border-j-primary text-j-primary"
                : "border-transparent text-t-tertiary hover:text-t-secondary"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "agents" && (
        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Agent Configuration</span>
          </CardHeader>
          <Table>
            <TableHeader>
              <Th>Agent</Th>
              <Th>Model</Th>
              <Th>Max Tokens</Th>
              <Th>Temperature</Th>
              <Th>Tools</Th>
            </TableHeader>
            <TableBody>
              {AGENT_CONFIGS.map((agent) => (
                <Fragment key={agent.name}>
                  <tr
                    className="cursor-pointer hover:bg-surface-2/30"
                    onClick={() =>
                      setExpandedAgent(expandedAgent === agent.name ? null : agent.name)
                    }
                  >
                    <Td className="font-medium text-t-primary capitalize">{agent.name}</Td>
                    <Td>
                      <Badge
                        variant={
                          agent.model_tier === "opus"
                            ? "purple"
                            : agent.model_tier === "haiku"
                              ? "green"
                              : "blue"
                        }
                      >
                        {agent.model_tier}
                      </Badge>
                    </Td>
                    <Td>{agent.max_tokens.toLocaleString()}</Td>
                    <Td>{agent.temperature}</Td>
                    <Td>
                      <span className="text-t-secondary">{agent.tools.length} tools</span>
                      <span className="text-t-muted ml-1 text-xs">
                        {expandedAgent === agent.name ? "▲" : "▼"}
                      </span>
                    </Td>
                  </tr>
                  {expandedAgent === agent.name && (
                    <tr key={`${agent.name}-tools`}>
                      <td colSpan={5} className="px-4 py-3 bg-surface-2/30">
                        <div className="flex flex-wrap gap-1.5">
                          {agent.tools.map((tool) => (
                            <Badge key={tool} variant="default">
                              {tool}
                            </Badge>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {activeTab === "policy" && (
        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Policy Mode</span>
          </CardHeader>
          <div className="p-4 space-y-3">
            {POLICY_MODES.map((mode) => (
              <label
                key={mode.value}
                className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                  policyMode === mode.value
                    ? "border-j-primary bg-j-primary-soft"
                    : "border-b-primary hover:bg-surface-2"
                }`}
              >
                <input
                  type="radio"
                  name="policyMode"
                  value={mode.value}
                  checked={policyMode === mode.value}
                  onChange={() => handlePolicyChange(mode.value)}
                  className="accent-[var(--jarvis-primary)]"
                />
                <div>
                  <div className="text-sm font-medium text-t-primary">{mode.label}</div>
                  <div className="text-xs text-t-secondary">{mode.description}</div>
                </div>
              </label>
            ))}
          </div>
        </Card>
      )}

      {activeTab === "budget" && (
        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Daily Token Budget</span>
          </CardHeader>
          <div className="p-4 space-y-4">
            <div className="flex items-center gap-3">
              <span className="text-sm text-t-secondary">Daily Limit:</span>
              {editingBudget ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-t-secondary">$</span>
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={budgetInput}
                    onChange={(e) => setBudgetInput(e.target.value)}
                    className="w-32 rounded bg-surface-2 border border-b-primary px-3 py-1.5 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring"
                  />
                  <button
                    onClick={handleBudgetSave}
                    className="px-3 py-1.5 rounded bg-j-primary text-sm text-j-primary-fg hover:bg-j-primary-hover"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setEditingBudget(false)}
                    className="px-3 py-1.5 text-sm text-t-secondary hover:text-t-primary"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-t-primary font-medium">
                    {budgetLimit !== null ? `$${budgetLimit.toFixed(2)}` : "Loading..."}
                  </span>
                  <button
                    onClick={() => {
                      setBudgetInput(String(budgetLimit ?? 5));
                      setEditingBudget(true);
                    }}
                    className="text-xs text-t-secondary hover:text-t-primary"
                  >
                    Edit
                  </button>
                </div>
              )}
            </div>
            <p className="text-xs text-t-tertiary">
              When daily spend exceeds this limit, the system degrades to cheaper models and eventually pauses non-critical operations.
            </p>
          </div>
        </Card>
      )}

      {activeTab === "connectors" && <OAuthPanel />}

      {activeTab === "schedules" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Schedules</span>
              <Link href="/schedules" className="text-xs text-j-primary hover:underline">
                Manage schedules
              </Link>
            </div>
          </CardHeader>
          <CardBody>
            {!schedules || schedules.length === 0 ? (
              <p className="text-xs text-t-muted">No schedules configured.</p>
            ) : (
              <div className="space-y-2">
                {schedules.map((s) => (
                  <div key={s.schedule_id} className="flex items-center justify-between p-2.5 rounded-[var(--radius-sm)] bg-surface-2/50">
                    <div>
                      <p className="text-sm font-medium text-t-primary">{s.name}</p>
                      <p className="text-xs text-t-tertiary">{s.description}</p>
                      {s.cron_expr && <p className="text-[10px] text-t-muted font-mono mt-0.5">{s.cron_expr}</p>}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-t-muted">{s.run_count} runs</span>
                      <Badge variant={s.enabled ? "green" : "default"}>
                        {s.enabled ? "Active" : "Paused"}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {activeTab === "triggers" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Triggers</span>
              <Link href="/triggers" className="text-xs text-j-primary hover:underline">
                Manage triggers
              </Link>
            </div>
          </CardHeader>
          <CardBody>
            {!triggers?.triggers || triggers.triggers.length === 0 ? (
              <p className="text-xs text-t-muted">No triggers configured.</p>
            ) : (
              <div className="space-y-2">
                {triggers.triggers.map((t: Record<string, unknown>) => (
                  <div key={String(t.trigger_id)} className="flex items-center justify-between p-2.5 rounded-[var(--radius-sm)] bg-surface-2/50">
                    <div>
                      <p className="text-sm font-medium text-t-primary">{String(t.name)}</p>
                      <p className="text-xs text-t-tertiary">{String(t.description || "")}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-t-muted">{String(t.fire_count || 0)} fires</span>
                      <Badge variant={t.enabled ? "green" : "default"}>
                        {t.enabled ? "Active" : "Disabled"}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {activeTab === "routes" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Agent Routes</span>
              <Link href="/routes" className="text-xs text-j-primary hover:underline">
                Manage routes
              </Link>
            </div>
          </CardHeader>
          <CardBody>
            {!routes || routes.length === 0 ? (
              <p className="text-xs text-t-muted">No routes configured.</p>
            ) : (
              <div className="space-y-2">
                {routes.map((r) => (
                  <div key={r.route_id} className="flex items-center justify-between p-2.5 rounded-[var(--radius-sm)] bg-surface-2/50">
                    <div>
                      <p className="text-sm font-medium text-t-primary">{r.name}</p>
                      <p className="text-xs text-t-tertiary">{r.description}</p>
                      <div className="flex items-center gap-1 mt-1">
                        {r.agent_pipeline.map((step, i) => (
                          <span key={i} className="text-[10px] text-j-primary">
                            {i > 0 && <span className="text-t-muted mx-0.5">&rarr;</span>}
                            {String((step as Record<string, unknown>).agent || "unknown")}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-t-muted">priority: {r.priority}</span>
                      <Badge variant={r.enabled ? "green" : "default"}>
                        {r.enabled ? "Active" : "Disabled"}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {activeTab === "workflows" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Workflows</span>
              <Link href="/workflows" className="text-xs text-j-primary hover:underline">
                Manage workflows
              </Link>
            </div>
          </CardHeader>
          <CardBody>
            {!workflows || workflows.length === 0 ? (
              <p className="text-xs text-t-muted">No workflows defined.</p>
            ) : (
              <div className="space-y-2">
                {workflows.map((w) => (
                  <div key={w.name} className="flex items-center justify-between p-2.5 rounded-[var(--radius-sm)] bg-surface-2/50">
                    <div>
                      <p className="text-sm font-medium text-t-primary">{w.name}</p>
                      <p className="text-xs text-t-tertiary">{w.description}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-t-muted">{w.step_count} steps</span>
                      <div className="flex gap-1">
                        {w.tags.map((tag) => (
                          <Badge key={tag} variant="default">{tag}</Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {activeTab === "account" && (
        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Account</span>
          </CardHeader>
          <div className="p-4 space-y-4">
            {user && (
              <div className="space-y-2">
                <div className="text-sm text-t-secondary">Email</div>
                <div className="text-t-primary">{user.email}</div>
                <div className="text-sm text-t-secondary mt-2">Display Name</div>
                <div className="text-t-primary">{user.display_name || "Not set"}</div>
                <div className="text-sm text-t-secondary mt-2">User ID</div>
                <div className="text-t-primary font-mono text-xs">{user.user_id}</div>
              </div>
            )}
            <button
              onClick={logout}
              className="rounded-lg bg-j-error-soft border border-j-error/30 px-4 py-2 text-sm text-j-error hover:bg-j-error/20"
            >
              Sign Out
            </button>
          </div>
        </Card>
      )}
    </div>
  );
}
