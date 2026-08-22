"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Card, CardBody } from "@/components/ui/card";
import { fetchTrustDashboard, resetTrust, setTrustCeiling } from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";
import type { TrustDashboardEntry } from "@/lib/types";
import { TrustCapabilityCard } from "./trust-capability-card";
import { TRUST_LEVEL_LABELS } from "./trust-constants";

/**
 * Dedicated per-capability trust tab. Owns the dashboard load, the ceiling
 * writes and the resets — the settings shell routes to this tab, it does not
 * fetch for it (defect L5). Families are grouped and displayed inline, since
 * the tab itself is the disclosure.
 */
export function TrustTab() {
  const { addToast } = useToast();
  const [entries, setEntries] = useState<TrustDashboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [ceilingLoading, setCeilingLoading] = useState<string | null>(null);
  const [resetLoading, setResetLoading] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchTrustDashboard();
      setEntries(data.capabilities);
    } catch {
      addToast("Failed to load trust data", "error");
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    load();
  }, [load]);

  const handleCeilingChange = useCallback(
    async (capability: string, maxLevel: string) => {
      setCeilingLoading(capability);
      try {
        await setTrustCeiling(capability, maxLevel);
        setEntries((prev) =>
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

  const handleReset = useCallback(
    async (capability: string) => {
      setResetLoading(capability);
      try {
        await resetTrust(capability);
        await load();
        addToast(`Trust reset for ${capability}`, "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setResetLoading(null);
      }
    },
    [load, addToast],
  );

  const families = useMemo(() => {
    const byFamily: Record<string, TrustDashboardEntry[]> = {};
    for (const entry of entries) {
      const family = entry.family || "unknown";
      byFamily[family] = [...(byFamily[family] ?? []), entry];
    }
    return Object.entries(byFamily);
  }, [entries]);

  return (
    <div className="space-y-6">
      <p className="text-sm text-t-tertiary leading-relaxed">
        Per-capability trust fine-tunes how much Muldro can do on its own for each
        kind of action. Levels build as Muldro acts and you approve or reject.
      </p>

      {loading && (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 rounded-[var(--radius-lg)] skeleton" />
          ))}
        </div>
      )}

      {!loading && families.length === 0 && (
        <Card>
          <CardBody>
            <div className="text-center py-4">
              <p className="text-sm text-t-secondary font-medium mb-1">
                No trust data yet
              </p>
              <p className="text-xs text-t-muted">
                Trust levels build as Muldro performs actions and you approve or
                reject them.
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      {!loading &&
        families.map(([family, familyEntries]) => (
          <div key={family}>
            <h3 className="text-[11px] uppercase text-t-muted font-medium mb-2.5 tracking-wider">
              {family}
            </h3>
            <div className="space-y-2">
              {familyEntries.map((entry) => (
                <TrustCapabilityCard
                  key={entry.capability}
                  entry={entry}
                  onCeilingChange={handleCeilingChange}
                  onReset={handleReset}
                  ceilingDisabled={ceilingLoading === entry.capability}
                  resetDisabled={resetLoading === entry.capability}
                />
              ))}
            </div>
          </div>
        ))}
    </div>
  );
}
