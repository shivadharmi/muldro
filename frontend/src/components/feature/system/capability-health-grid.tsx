"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchHomeFeed } from "@/lib/api";

const statusColors: Record<string, string> = {
  healthy: "border-status-success bg-status-success/5",
  degraded: "border-status-warning bg-status-warning/5",
  unavailable: "border-status-error bg-status-error/5",
  unconfigured: "border-b-primary bg-surface-1",
};

const statusDotColors: Record<string, string> = {
  healthy: "bg-status-success",
  degraded: "bg-status-warning",
  unavailable: "bg-status-error",
  unconfigured: "bg-surface-2",
};

interface CapabilityItem {
  family: string;
  status: string;
  provider?: string | null;
  capabilities_available?: number;
  capabilities_total?: number;
  message?: string | null;
}

interface Props {
  capabilities?: CapabilityItem[];
}

export function CapabilityHealthGrid({ capabilities: propCapabilities }: Props) {
  const { data: homeFeed } = useQuery({
    queryKey: ["home-feed"],
    queryFn: fetchHomeFeed,
    enabled: !propCapabilities,
  });

  const capabilities = propCapabilities ?? homeFeed?.capability_health ?? [];

  if (capabilities.length === 0) {
    return (
      <div className="text-sm text-t-tertiary">
        No capability data available.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
      {capabilities.map((cap: CapabilityItem) => (
        <div
          key={cap.family}
          className={`rounded-[var(--radius-md)] border p-3 ${statusColors[cap.status] ?? statusColors.unconfigured}`}
        >
          <div className="flex items-center gap-2 mb-1">
            <span className={`w-2 h-2 rounded-full ${statusDotColors[cap.status] ?? "bg-surface-2"}`} />
            <span className="text-sm font-medium text-t-primary capitalize">
              {cap.family}
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs text-t-tertiary">
            <span className="capitalize">{cap.status}</span>
            {cap.provider && <span>via {cap.provider}</span>}
          </div>

          {cap.capabilities_total != null && cap.capabilities_total > 0 && (
            <div className="mt-2">
              <div className="h-1 bg-surface-2 rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent-primary rounded-full"
                  style={{
                    width: `${((cap.capabilities_available ?? 0) / cap.capabilities_total) * 100}%`,
                  }}
                />
              </div>
              <p className="text-[10px] text-t-tertiary mt-0.5">
                {cap.capabilities_available ?? 0}/{cap.capabilities_total} capabilities
              </p>
            </div>
          )}

          {cap.message && (
            <p className="text-xs text-t-tertiary mt-1">{cap.message}</p>
          )}
        </div>
      ))}
    </div>
  );
}
