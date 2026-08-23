"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { UnifiedIntegration } from "@/lib/api";
import {
  GoogleLogo,
  GitHubLogo,
  SlackLogo,
  NotionLogo,
  AtlassianLogo,
  FolderIcon,
} from "@/components/integrations/logos";

type LogoComponent = React.FC<{ className?: string }>;

const LOGOS: Record<string, LogoComponent> = {
  "google-workspace": GoogleLogo,
  github: GitHubLogo,
  slack: SlackLogo,
  notion: NotionLogo,
  atlassian: AtlassianLogo,
  filesystem: FolderIcon,
};

/**
 * Display name for an OpenConnector provider slug. The registry owns these
 * (served as `oc_provider_labels`); the client must not restate them, or a
 * newly registered provider silently renders as a raw slug.
 */
export function providerLabel(
  integration: UnifiedIntegration,
  provider: string,
): string {
  return integration.oc_provider_labels?.[provider] ?? provider;
}

function HealthDot({ status }: { status: string }) {
  const color =
    status === "healthy"
      ? "bg-j-success"
      : status === "degraded"
        ? "bg-j-warning"
        : "bg-j-error";
  return (
    <span
      className={`w-2 h-2 rounded-full ${color}`}
      title={status}
    />
  );
}

function CredentialChip({
  connected,
  label,
}: {
  connected: boolean;
  label: string;
}) {
  return (
    <span
      className={`text-[11px] px-1.5 py-0.5 rounded ${
        connected
          ? "bg-j-success-soft text-j-success"
          : "bg-surface-2 text-t-secondary"
      }`}
    >
      {connected ? "✓" : "○"} {label}
    </span>
  );
}

export interface IntegrationCardProps {
  integration: UnifiedIntegration;
  /** A gateway popup walk is in flight for this card. */
  isPending: boolean;
  /** A native full-page OAuth redirect is in flight for this card. */
  isNativePending: boolean;
  /** Providers a blocked popup left unconnected, or undefined if none. */
  blockedProviders?: string[];
  onConnect: () => void;
  onConnectNative: () => void;
  onRetryBlocked: (providers: string[]) => void;
  onDisconnect: () => void;
}

export function IntegrationCard({
  integration,
  isPending,
  isNativePending,
  blockedProviders,
  onConnect,
  onConnectNative,
  onRetryBlocked,
  onDisconnect,
}: IntegrationCardProps) {
  const Logo = LOGOS[integration.server_name];
  const isGateway = !!integration.oc_providers?.length;
  const providerStates = Object.entries(integration.provider_connections ?? {});
  // A dual-credential installation: gateway-backed for its actions, and holding
  // its own OAuth token for a poll the gateway cannot serve. The backend derives
  // this from the registries; the card only renders what it is told.
  const isDual = !!integration.native_provider;
  const gatewayMissing =
    isGateway && providerStates.some(([, connected]) => !connected);
  const nativeMissing = isDual && !integration.native_connected;
  const busy = isPending || isNativePending;
  // The popup-poll flow never navigates this tab, so it must not say so.
  const pendingLabel = isPending ? "Waiting for approval…" : "Redirecting...";
  const nativeLabel = `Connect ${integration.native_purpose || "notifications"}`;

  return (
    <Card>
      <div className="p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2.5">
            {Logo ? (
              <Logo className="w-5 h-5 shrink-0" />
            ) : (
              <span className="w-5 h-5 rounded bg-surface-2" />
            )}
            <div>
              <h3 className="text-sm font-medium text-t-primary">
                {integration.display_name}
              </h3>
              <p className="text-xs text-t-secondary">
                {integration.category === "local"
                  ? "Local tool"
                  : integration.category === "token"
                    ? "Token auth"
                    : "OAuth connection"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <HealthDot status={integration.health_status} />
            <Badge variant={integration.connected ? "green" : "default"}>
              {integration.connected ? "Connected" : "Not connected"}
            </Badge>
          </div>
        </div>

        {(providerStates.length > 1 || isDual) && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {providerStates.map(([provider, isConnected]) => (
              <CredentialChip
                key={provider}
                connected={isConnected}
                label={providerLabel(integration, provider)}
              />
            ))}
            {isDual && (
              <CredentialChip
                connected={!!integration.native_connected}
                label={integration.native_purpose || integration.native_provider!}
              />
            )}
          </div>
        )}

        {blockedProviders && !isPending && (
          <button
            onClick={() => onRetryBlocked(blockedProviders)}
            className="w-full text-left text-xs px-2.5 py-1.5 mb-3 rounded-[var(--radius-md)] border border-j-warning/40 bg-j-warning-soft text-j-warning hover:bg-j-warning-soft/70"
          >
            Popup blocked — click to connect{" "}
            {blockedProviders
              .map((p) => providerLabel(integration, p))
              .join(", ")}
          </button>
        )}

        {integration.scopes.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-3">
            {integration.scopes.slice(0, 2).map((scope) => (
              <span
                key={scope}
                className="text-[11px] px-1.5 py-0.5 rounded bg-surface-2 text-t-secondary"
              >
                {scope.split(".").pop() || scope}
              </span>
            ))}
            {integration.scopes.length > 2 && (
              <span
                className="text-[11px] px-1.5 py-0.5 text-t-secondary"
                title={integration.scopes.join(", ")}
              >
                +{integration.scopes.length - 2} more
              </span>
            )}
          </div>
        )}

        {integration.category !== "local" && (
          <div className="flex gap-2">
            {integration.connected ? (
              <>
                <button
                  onClick={onConnect}
                  disabled={busy}
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2 disabled:opacity-50"
                >
                  {busy ? pendingLabel : "Reauthorize"}
                </button>
                {integration.install_id && (
                  <button
                    onClick={onDisconnect}
                    className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-j-error/30 text-j-error hover:bg-j-error-soft"
                  >
                    Disconnect
                  </button>
                )}
              </>
            ) : isDual ? (
              // Two grants, two buttons, never chained. The gateway flow opens a
              // POPUP and the native flow is a full-page redirect: firing them in
              // sequence navigates away and abandons the popup, and the popup
              // needs the user-activation budget of the click that opened it —
              // spending it on a redirect first loses the popup silently. The
              // labels name the job rather than the mechanism so the founder
              // learns these are two grants, not a retry of one.
              <>
                {gatewayMissing && (
                  <button
                    onClick={onConnect}
                    disabled={busy}
                    className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
                  >
                    {busy ? pendingLabel : "Connect actions"}
                  </button>
                )}
                {nativeMissing && (
                  <button
                    onClick={onConnectNative}
                    disabled={busy}
                    className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
                  >
                    {busy ? pendingLabel : nativeLabel}
                  </button>
                )}
              </>
            ) : (
              <button
                onClick={onConnect}
                disabled={busy || !integration.configured}
                className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
              >
                {busy
                  ? pendingLabel
                  : !integration.configured
                    ? "Not configured"
                    : "Connect"}
              </button>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
