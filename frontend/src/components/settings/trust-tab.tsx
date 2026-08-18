"use client";

import { useEffect } from "react";
import { Card, CardBody } from "@/components/ui/card";
import type { TrustDashboardEntry } from "@/lib/types";
import { TrustCapabilityCard } from "./trust-capability-card";

interface TrustTabProps {
  trustByFamily: Record<string, TrustDashboardEntry[]>;
  loading: boolean;
  onLoad: () => void;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingLoading: string | null;
  resetLoading: string | null;
}

/**
 * Dedicated per-capability trust tab. Trust data is lazy-loaded the first time
 * the tab mounts (the parent fetches in onLoad); families are grouped and
 * displayed inline since the tab itself is the disclosure.
 */
export function TrustTab({
  trustByFamily,
  loading,
  onLoad,
  onCeilingChange,
  onReset,
  ceilingLoading,
  resetLoading,
}: TrustTabProps) {
  useEffect(() => {
    onLoad();
  }, [onLoad]);

  const families = Object.entries(trustByFamily);

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
        families.map(([family, entries]) => (
          <div key={family}>
            <h3 className="text-[11px] uppercase text-t-muted font-medium mb-2.5 tracking-wider">
              {family}
            </h3>
            <div className="space-y-2">
              {entries.map((entry) => (
                <TrustCapabilityCard
                  key={entry.capability}
                  entry={entry}
                  onCeilingChange={onCeilingChange}
                  onReset={onReset}
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
