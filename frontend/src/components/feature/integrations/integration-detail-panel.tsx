"use client";

import { Badge } from "@/components/ui/badge";

interface IntegrationDetail {
  install_id: string;
  provider: string;
  status: string;
  last_sync_at?: string | null;
  event_count_24h?: number;
  error_count_24h?: number;
  capabilities?: string[];
}

interface Props {
  integration: IntegrationDetail | null;
}

export function IntegrationDetailPanel({ integration }: Props) {
  if (!integration) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-t-tertiary">
        Select an integration to view details
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-medium text-t-primary capitalize">
          {integration.provider}
        </h3>
        <Badge variant={integration.status === "active" ? "green" : "default"}>
          {integration.status}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-[var(--radius-md)] bg-surface-1 p-3">
          <p className="text-xs text-t-tertiary">Events (24h)</p>
          <p className="text-lg font-medium text-t-primary">
            {integration.event_count_24h ?? 0}
          </p>
        </div>
        <div className="rounded-[var(--radius-md)] bg-surface-1 p-3">
          <p className="text-xs text-t-tertiary">Errors (24h)</p>
          <p className={`text-lg font-medium ${
            (integration.error_count_24h ?? 0) > 0 ? "text-status-error" : "text-t-primary"
          }`}>
            {integration.error_count_24h ?? 0}
          </p>
        </div>
      </div>

      {integration.last_sync_at && (
        <div className="text-xs text-t-tertiary">
          Last sync: {new Date(integration.last_sync_at).toLocaleString()}
        </div>
      )}

      {integration.capabilities && integration.capabilities.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-t-secondary uppercase tracking-wider mb-1">
            Capabilities
          </h4>
          <div className="flex flex-wrap gap-1">
            {integration.capabilities.map((cap) => (
              <span
                key={cap}
                className="px-2 py-0.5 rounded-full bg-surface-1 text-xs text-t-secondary"
              >
                {cap}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
