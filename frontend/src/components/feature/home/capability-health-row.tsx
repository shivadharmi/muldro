"use client";

interface CapabilityHealth {
  family: string;
  status: string;
  provider?: string | null;
  capabilities_available?: number;
  capabilities_total?: number;
  message?: string | null;
  // Legacy field names
  connected_count?: number;
  total_count?: number;
}

interface Props {
  capabilities: CapabilityHealth[];
}

const statusColors: Record<string, string> = {
  healthy: "bg-status-success",
  degraded: "bg-status-warning",
  unavailable: "bg-status-error",
  unconfigured: "bg-surface-2",
  unknown: "bg-surface-2",
};

export function CapabilityHealthRow({ capabilities }: Props) {
  if (capabilities.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-t-secondary uppercase tracking-wider">
        Capabilities
      </h3>
      <div className="flex flex-wrap gap-2">
        {capabilities.map((cap) => (
          <div
            key={cap.family}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-1 text-xs"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${statusColors[cap.status] ?? "bg-surface-2"}`}
            />
            <span className="text-t-secondary capitalize">{cap.family}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
