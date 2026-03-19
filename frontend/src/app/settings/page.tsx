"use client";

import { Fragment, useState, useEffect } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardHeader } from "@/components/ui/card";
import { Table, TableHeader, TableBody, Th, Td } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { OAuthPanel } from "@/components/settings/oauth-panel";
import { AGENT_CONFIGS } from "@/lib/agent-config";
import { fetchPolicyMode, setPolicyMode, fetchBudget, updateBudgetLimit } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";

const POLICY_MODES = [
  { value: "lockdown", label: "Lockdown", description: "All actions blocked" },
  { value: "approval_required", label: "Approval Required", description: "All actions need approval" },
  { value: "suggest_only", label: "Suggest Only", description: "Jarvis suggests, never acts" },
  { value: "full_auto", label: "Full Auto", description: "Jarvis acts autonomously" },
];

export default function SettingsPage() {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"agents" | "policy" | "budget" | "connectors" | "account">("agents");
  const [policyMode, setPolicyModeState] = useState("approval_required");
  const [budgetLimit, setBudgetLimit] = useState<number | null>(null);
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetInput, setBudgetInput] = useState("");
  const { user, logout } = useAuth();
  const { addToast } = useToast();

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

  const tabs = [
    { key: "agents" as const, label: "Agents" },
    { key: "policy" as const, label: "Policy" },
    { key: "budget" as const, label: "Budget" },
    { key: "connectors" as const, label: "Connectors" },
    { key: "account" as const, label: "Account" },
  ];

  return (
    <div className="p-6 space-y-6">
      <PageHeader title="Settings" subtitle="System configuration and preferences" />

      <div className="flex gap-1 border-b border-neutral-800 pb-px">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              activeTab === tab.key
                ? "bg-neutral-800 text-white border-b-2 border-blue-500"
                : "text-neutral-400 hover:text-white"
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
                    className="cursor-pointer hover:bg-neutral-800/30"
                    onClick={() =>
                      setExpandedAgent(expandedAgent === agent.name ? null : agent.name)
                    }
                  >
                    <Td className="font-medium text-white capitalize">{agent.name}</Td>
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
                      <span className="text-neutral-400">{agent.tools.length} tools</span>
                      <span className="text-neutral-600 ml-1 text-xs">
                        {expandedAgent === agent.name ? "▲" : "▼"}
                      </span>
                    </Td>
                  </tr>
                  {expandedAgent === agent.name && (
                    <tr key={`${agent.name}-tools`}>
                      <td colSpan={5} className="px-4 py-3 bg-neutral-800/30">
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
                    ? "border-blue-500 bg-blue-500/10"
                    : "border-neutral-700 hover:bg-neutral-800"
                }`}
              >
                <input
                  type="radio"
                  name="policyMode"
                  value={mode.value}
                  checked={policyMode === mode.value}
                  onChange={() => handlePolicyChange(mode.value)}
                  className="text-blue-500"
                />
                <div>
                  <div className="text-sm font-medium text-white">{mode.label}</div>
                  <div className="text-xs text-neutral-400">{mode.description}</div>
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
              <span className="text-sm text-neutral-400">Daily Limit:</span>
              {editingBudget ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-neutral-400">$</span>
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={budgetInput}
                    onChange={(e) => setBudgetInput(e.target.value)}
                    className="w-32 rounded bg-neutral-800 border border-neutral-700 px-3 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  <button
                    onClick={handleBudgetSave}
                    className="px-3 py-1.5 rounded bg-blue-600 text-sm text-white hover:bg-blue-700"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setEditingBudget(false)}
                    className="px-3 py-1.5 text-sm text-neutral-400 hover:text-white"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-white font-medium">
                    {budgetLimit !== null ? `$${budgetLimit.toFixed(2)}` : "Loading..."}
                  </span>
                  <button
                    onClick={() => {
                      setBudgetInput(String(budgetLimit ?? 5));
                      setEditingBudget(true);
                    }}
                    className="text-xs text-neutral-400 hover:text-white"
                  >
                    Edit
                  </button>
                </div>
              )}
            </div>
            <p className="text-xs text-neutral-500">
              When daily spend exceeds this limit, the system degrades to cheaper models and eventually pauses non-critical operations.
            </p>
          </div>
        </Card>
      )}

      {activeTab === "connectors" && <OAuthPanel />}

      {activeTab === "account" && (
        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Account</span>
          </CardHeader>
          <div className="p-4 space-y-4">
            {user && (
              <div className="space-y-2">
                <div className="text-sm text-neutral-400">Email</div>
                <div className="text-white">{user.email}</div>
                <div className="text-sm text-neutral-400 mt-2">Display Name</div>
                <div className="text-white">{user.display_name || "Not set"}</div>
                <div className="text-sm text-neutral-400 mt-2">User ID</div>
                <div className="text-white font-mono text-xs">{user.user_id}</div>
              </div>
            )}
            <button
              onClick={logout}
              className="rounded-lg bg-red-600/20 border border-red-800 px-4 py-2 text-sm text-red-400 hover:bg-red-600/30"
            >
              Sign Out
            </button>
          </div>
        </Card>
      )}
    </div>
  );
}
