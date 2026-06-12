"use client";

import { useState } from "react";
import { Card, CardBody } from "@/components/ui/card";
import type { TrustDashboardEntry } from "@/lib/types";
import { TrustCapabilityCard } from "./trust-capability-card";

interface TrustSectionProps {
  trustByFamily: Record<string, TrustDashboardEntry[]>;
  loading: boolean;
  onExpand: () => void;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingLoading: string | null;
  resetLoading: string | null;
}

/**
 * Progressive-disclosure wrapper for the per-capability trust list. Collapsed by
 * default so a new user sees only the global posture; the grouped-by-family
 * capability cards live behind the expander. Trust data is lazy-loaded the first
 * time the section is expanded (the parent fetches in onExpand).
 */
export function TrustSection({
  trustByFamily,
  loading,
  onExpand,
  onCeilingChange,
  onReset,
  ceilingLoading,
  resetLoading,
}: TrustSectionProps) {
  const [expanded, setExpanded] = useState(false);

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next) onExpand();
  }

  const families = Object.entries(trustByFamily);

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className="w-full flex items-center gap-2 text-left cursor-pointer group py-2"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className={`text-t-muted group-hover:text-t-secondary transition-all duration-150 ${expanded ? "rotate-90" : ""}`}
        >
          <path d="M9 18l6-6-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="text-[13px] font-medium text-t-primary">Per-capability trust</span>
      </button>

      {expanded && (
        <div className="space-y-6 pt-2">
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
                  <p className="text-sm text-t-secondary font-medium mb-1">No trust data yet</p>
                  <p className="text-xs text-t-muted">
                    Trust levels build as Jarvis performs actions and you approve or reject them.
                  </p>
                </div>
              </CardBody>
            </Card>
          )}

          {families.map(([family, entries]) => (
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
      )}
    </div>
  );
}
