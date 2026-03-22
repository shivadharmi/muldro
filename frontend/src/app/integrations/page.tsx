"use client";

import { useEffect, useState } from "react";
import {
  fetchInstallations,
  fetchMCPCatalog,
  fetchAllowlist,
  fetchMCPAudit,
  fetchTrustRecords,
  fetchAllHealth,
  type Installation,
  type CatalogEntry,
  type AllowlistEntry,
  type AuditEvent,
  type TrustRecord,
  type HealthCheck,
} from "@/lib/api";

type Tab = "installations" | "catalog" | "trust" | "allowlist" | "audit";

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-500/20 text-green-400",
  healthy: "bg-green-500/20 text-green-400",
  paused: "bg-yellow-500/20 text-yellow-400",
  degraded: "bg-yellow-500/20 text-yellow-400",
  error: "bg-red-500/20 text-red-400",
  disabled: "bg-neutral-500/20 text-neutral-400",
  unavailable: "bg-red-500/20 text-red-400",
  unknown: "bg-neutral-500/20 text-neutral-400",
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium uppercase ${color}`}>
      {status}
    </span>
  );
}

export default function IntegrationsPage() {
  const [tab, setTab] = useState<Tab>("installations");
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [allowlist, setAllowlist] = useState<AllowlistEntry[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [trustRecords, setTrustRecords] = useState<TrustRecord[]>([]);
  const [healthChecks, setHealthChecks] = useState<HealthCheck[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const load = async () => {
      try {
        const [inst, cat, allow, aud, trust, health] = await Promise.all([
          fetchInstallations().catch(() => []),
          fetchMCPCatalog().catch(() => []),
          fetchAllowlist().catch(() => []),
          fetchMCPAudit(50).catch(() => []),
          fetchTrustRecords().catch(() => []),
          fetchAllHealth().catch(() => []),
        ]);
        if (!cancelled) {
          setInstallations(inst);
          setCatalog(cat);
          setAllowlist(allow);
          setAudit(aud);
          setTrustRecords(trust);
          setHealthChecks(health);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, []);

  const tabs: { key: Tab; label: string; count: number }[] = [
    { key: "installations", label: "Installations", count: installations.length },
    { key: "catalog", label: "Catalog", count: catalog.length },
    { key: "trust", label: "Trust & Health", count: trustRecords.length },
    { key: "allowlist", label: "Allowlist", count: allowlist.length },
    { key: "audit", label: "Audit Trail", count: audit.length },
  ];

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-t-primary">Integrations</h1>
        <p className="text-sm text-t-tertiary mt-1">
          Manage connector installations, MCP server catalog, trust policies, and audit trail.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-b-primary">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${
              tab === t.key
                ? "text-accent-primary border-b-2 border-accent-primary"
                : "text-t-tertiary hover:text-t-secondary"
            }`}
          >
            {t.label}
            <span className="ml-1.5 text-xs text-t-tertiary">({t.count})</span>
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-t-tertiary text-sm animate-pulse py-8 text-center">Loading...</div>
      ) : (
        <>
          {tab === "installations" && <InstallationsTab installations={installations} />}
          {tab === "catalog" && <CatalogTab catalog={catalog} />}
          {tab === "trust" && <TrustHealthTab trustRecords={trustRecords} healthChecks={healthChecks} />}
          {tab === "allowlist" && <AllowlistTab allowlist={allowlist} />}
          {tab === "audit" && <AuditTab audit={audit} />}
        </>
      )}
    </div>
  );
}

function InstallationsTab({ installations }: { installations: Installation[] }) {
  if (installations.length === 0) {
    return <p className="text-t-tertiary text-sm py-4">No installations yet.</p>;
  }

  return (
    <div className="grid gap-3">
      {installations.map((inst) => (
        <div
          key={inst.install_id}
          className="flex items-center justify-between p-4 rounded-lg bg-surface-1 border border-b-primary"
        >
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm text-t-primary">{inst.display_name}</span>
              <span className="text-xs text-t-tertiary font-mono">{inst.server_name}</span>
            </div>
            <div className="flex items-center gap-2 text-xs text-t-tertiary">
              <span>Transport: {inst.transport}</span>
              {inst.auth_provider && <span>Auth: {inst.auth_provider}</span>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={inst.health_status} />
            <StatusBadge status={inst.status} />
          </div>
        </div>
      ))}
    </div>
  );
}

function CatalogTab({ catalog }: { catalog: CatalogEntry[] }) {
  if (catalog.length === 0) {
    return <p className="text-t-tertiary text-sm py-4">No servers in catalog.</p>;
  }

  return (
    <div className="grid gap-3">
      {catalog.map((entry) => (
        <div
          key={entry.catalog_id}
          className="p-4 rounded-lg bg-surface-1 border border-b-primary"
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm text-t-primary">{entry.display_name}</span>
              {entry.verified && (
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-400">
                  Verified
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-t-tertiary">{entry.tool_count} tools</span>
              <span className={`text-xs px-2 py-0.5 rounded ${
                entry.risk_score <= 30 ? "bg-green-500/20 text-green-400"
                  : entry.risk_score <= 60 ? "bg-yellow-500/20 text-yellow-400"
                    : "bg-red-500/20 text-red-400"
              }`}>
                Risk: {entry.risk_score}
              </span>
              <StatusBadge status={entry.default_trust_tier} />
            </div>
          </div>
          {entry.description && (
            <p className="text-xs text-t-secondary line-clamp-2">{entry.description}</p>
          )}
          {entry.tags.length > 0 && (
            <div className="flex gap-1 mt-2">
              {entry.tags.map((tag) => (
                <span key={tag} className="px-1.5 py-0.5 rounded bg-surface-2 text-[10px] text-t-tertiary">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function AllowlistTab({ allowlist }: { allowlist: AllowlistEntry[] }) {
  if (allowlist.length === 0) {
    return <p className="text-t-tertiary text-sm py-4">No allowlist entries. All servers permitted by default.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-b-primary text-left text-t-tertiary text-xs">
          <th className="pb-2 font-medium">Server</th>
          <th className="pb-2 font-medium">Max Trust</th>
          <th className="pb-2 font-medium">Approval</th>
          <th className="pb-2 font-medium">Status</th>
          <th className="pb-2 font-medium">Reason</th>
        </tr>
      </thead>
      <tbody>
        {allowlist.map((entry) => (
          <tr key={entry.allowlist_id} className="border-b border-b-primary/50">
            <td className="py-2 text-t-primary font-mono">{entry.server_name}</td>
            <td className="py-2"><StatusBadge status={entry.max_trust_tier} /></td>
            <td className="py-2 text-t-secondary">{entry.requires_approval ? "Required" : "Auto"}</td>
            <td className="py-2"><StatusBadge status={entry.enabled ? "active" : "disabled"} /></td>
            <td className="py-2 text-t-tertiary text-xs">{entry.reason || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const TIER_INFO: Record<string, { color: string; label: string; desc: string }> = {
  T0: { color: "bg-green-500", label: "T0 — Native", desc: "Built-in, fully trusted" },
  T1: { color: "bg-blue-500", label: "T1 — Official", desc: "Verified official server" },
  T2: { color: "bg-yellow-500", label: "T2 — Community", desc: "Community-reviewed" },
  T3: { color: "bg-red-500", label: "T3 — User", desc: "User-added, unverified" },
};

function TrustHealthTab({ trustRecords, healthChecks }: { trustRecords: TrustRecord[]; healthChecks: HealthCheck[] }) {
  // Group trust records by tier
  const byTier: Record<string, TrustRecord[]> = {};
  for (const r of trustRecords) {
    (byTier[r.trust_tier] ??= []).push(r);
  }

  // Build health lookup
  const healthByServer: Record<string, string> = {};
  for (const h of healthChecks) {
    if (h.server_name) healthByServer[h.server_name] = h.health_status;
  }

  const tiers = ["T0", "T1", "T2", "T3"];

  return (
    <div className="space-y-6">
      {/* Trust tier overview */}
      <div>
        <h3 className="text-sm font-medium text-t-primary mb-3">Trust Tiers</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {tiers.map((tier) => {
            const info = TIER_INFO[tier] || { color: "bg-neutral-500", label: tier, desc: "" };
            const records = byTier[tier] || [];
            return (
              <div key={tier} className="rounded-lg border border-b-primary bg-surface-0 p-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className={`w-2.5 h-2.5 rounded-full ${info.color}`} />
                  <span className="text-xs font-medium text-t-primary">{info.label}</span>
                </div>
                <p className="text-[10px] text-t-tertiary mb-2">{info.desc}</p>
                <p className="text-lg font-bold text-t-primary">{records.length}</p>
                <p className="text-[10px] text-t-tertiary">server{records.length !== 1 ? "s" : ""}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Server health matrix */}
      <div>
        <h3 className="text-sm font-medium text-t-primary mb-3">Server Health</h3>
        {trustRecords.length === 0 ? (
          <p className="text-sm text-t-tertiary py-4">No trust records configured.</p>
        ) : (
          <div className="space-y-1.5">
            {trustRecords.map((r) => {
              const health = healthByServer[r.server_name] || "unknown";
              return (
                <div key={r.trust_id} className="flex items-center justify-between p-3 rounded-lg bg-surface-0 border border-b-primary">
                  <div className="flex items-center gap-3">
                    <span className={`w-2 h-2 rounded-full ${
                      TIER_INFO[r.trust_tier]?.color || "bg-neutral-500"
                    }`} />
                    <div>
                      <span className="text-sm text-t-primary">{r.server_name}</span>
                      {r.verified_by && (
                        <span className="text-[10px] text-t-tertiary ml-2">by {r.verified_by}</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={r.trust_tier} />
                    <StatusBadge status={health} />
                    <StatusBadge status={r.status} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function AuditTab({ audit }: { audit: AuditEvent[] }) {
  if (audit.length === 0) {
    return <p className="text-t-tertiary text-sm py-4">No audit events yet.</p>;
  }

  return (
    <div className="space-y-1">
      {audit.map((evt) => (
        <div
          key={evt.audit_id}
          className="flex items-center justify-between py-2 px-3 rounded hover:bg-surface-1 text-xs"
        >
          <div className="flex items-center gap-3">
            <StatusBadge status={evt.status} />
            <span className="text-t-primary font-mono">{evt.tool_name}</span>
            <span className="text-t-tertiary">{evt.action}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-t-tertiary">{evt.server_name}</span>
            <StatusBadge status={evt.trust_tier} />
            {evt.latency_ms != null && (
              <span className="text-t-tertiary">{evt.latency_ms}ms</span>
            )}
            {evt.occurred_at && (
              <span className="text-t-tertiary">
                {new Date(evt.occurred_at).toLocaleString()}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
