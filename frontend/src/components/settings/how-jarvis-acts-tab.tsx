import { TrustSection } from "./trust-section";
import type { TrustDashboardEntry } from "@/lib/types";

interface PolicyMode {
  value: string;
  label: string;
  description: string;
}

interface HowJarvisActsTabProps {
  policyMode: string;
  policyModes: PolicyMode[];
  policyLoading: boolean;
  onPolicyChange: (value: string) => void;
  trustByFamily: Record<string, TrustDashboardEntry[]>;
  trustLoading: boolean;
  onTrustExpand: () => void;
  onCeilingChange: (capability: string, maxLevel: string) => void;
  onReset: (capability: string) => void;
  ceilingLoading: string | null;
  resetLoading: string | null;
}

export function HowJarvisActsTab({
  policyMode,
  policyModes,
  policyLoading,
  onPolicyChange,
  trustByFamily,
  trustLoading,
  onTrustExpand,
  onCeilingChange,
  onReset,
  ceilingLoading,
  resetLoading,
}: HowJarvisActsTabProps) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-t-tertiary leading-relaxed">
        Your overall posture applies to everything; per-capability trust fine-tunes
        how much Jarvis can do on its own for each kind of action.
      </p>

      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Overall posture
        </p>
        <div className="space-y-2">
          {policyModes.map((pm) => {
            const isActive = policyMode === pm.value;
            return (
              <button
                key={pm.value}
                type="button"
                onClick={() => onPolicyChange(pm.value)}
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
                    <p className="text-[13px] font-medium text-t-primary">{pm.label}</p>
                    <p className="text-xs text-t-tertiary mt-0.5">{pm.description}</p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-b-secondary pt-2">
        <TrustSection
          trustByFamily={trustByFamily}
          loading={trustLoading}
          onExpand={onTrustExpand}
          onCeilingChange={onCeilingChange}
          onReset={onReset}
          ceilingLoading={ceilingLoading}
          resetLoading={resetLoading}
        />
      </div>
    </div>
  );
}
