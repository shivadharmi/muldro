/** Capability and integration types. */

export type CapabilityFamily =
  | "email"
  | "calendar"
  | "repo"
  | "issue"
  | "doc"
  | "workflow"
  | "messaging"
  | "browser"
  | "search"
  | "internal";

export type CapabilityHealthStatus = "healthy" | "degraded" | "unavailable";

export interface CapabilityHealth {
  family: CapabilityFamily;
  status: CapabilityHealthStatus;
  active_backends: number;
  last_checked: string | null;
}

export type TrustTier = "T0" | "T1" | "T2" | "T3";

export interface IntegrationInstallation {
  install_id: string;
  server_name: string;
  display_name: string;
  transport: "stdio" | "sse" | "streamable-http";
  trust_tier: TrustTier;
  status: string;
  health_status: string;
  scopes_granted: string[];
}
