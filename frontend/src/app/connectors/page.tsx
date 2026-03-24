"use client";

import { Suspense, useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  fetchAuthProviders,
  getAuthUrl,
  deleteInstallation,
  checkInstallationHealth,
  fetchInstallations,
  type AuthProvider,
  type Installation,
} from "@/lib/api";
import { useToast } from "@/components/ui/toast";

/** Icons for each provider (simple emoji fallback). */
const PROVIDER_ICONS: Record<string, string> = {
  google: "🔵",
  github: "🐙",
  discord: "💬",
  slack: "💜",
  linear: "📐",
  notion: "📝",
  jira: "🔷",
  linkedin: "💼",
  twitter: "🐦",
};

function ConnectorsContent() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [flash, setFlash] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);

  // Refetch when returning from OAuth callback
  useEffect(() => {
    const status = searchParams.get("status");
    const provider = searchParams.get("provider");
    const error = searchParams.get("error");
    if (status === "connected" && provider) {
      queryClient.invalidateQueries({ queryKey: ["installations"] });
      queryClient.invalidateQueries({ queryKey: ["auth-providers"] });
      setFlash(`${provider} connected successfully`);
      window.history.replaceState({}, "", "/connectors");
    } else if (error) {
      setFlash(`Error: ${error}`);
      window.history.replaceState({}, "", "/connectors");
    }
  }, [searchParams, queryClient]);

  // Fetch available providers from backend
  const { data: providersData } = useQuery({
    queryKey: ["auth-providers"],
    queryFn: fetchAuthProviders,
  });

  // Fetch installed integrations
  const { data: installations = [] } = useQuery({
    queryKey: ["installations"],
    queryFn: fetchInstallations,
  });

  const { addToast } = useToast();

  const connectors = installations.filter((i: Installation) => i.enabled).map((i: Installation) => ({
    connector_id: i.install_id,
    provider: i.server_name,
    status: i.status,
  }));

  const providers: AuthProvider[] = providersData?.providers || [];

  // Group providers: configured first, then unconfigured
  const configuredProviders = providers.filter((p) => p.configured);
  const unconfiguredProviders = providers.filter((p) => !p.configured);

  async function handleConnect(providerName: string) {
    setConnecting(providerName);
    try {
      // Map sub-providers (gmail, calendar) to their OAuth parent
      const oauthProvider = ["gmail", "calendar", "drive"].includes(providerName)
        ? "google"
        : providerName;
      const { url } = await getAuthUrl(oauthProvider);
      window.location.href = url;
    } catch (err) {
      addToast(
        `Failed to start OAuth: ${err instanceof Error ? err.message : "Unknown error"}`,
        "error"
      );
      setConnecting(null);
    }
  }

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => deleteInstallation(id),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["installations"] });
      const prev = queryClient.getQueryData(["installations"]);
      queryClient.setQueryData(
        ["installations"],
        (old: Installation[] | undefined) => {
          if (!old) return old;
          return old.filter((inst) => inst.install_id !== id);
        }
      );
      return { prev };
    },
    onError: (err, _id, context) => {
      if (context?.prev)
        queryClient.setQueryData(["installations"], context.prev);
      addToast(`Failed to disconnect: ${err.message}`, "error");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["installations"] });
      queryClient.invalidateQueries({ queryKey: ["auth-providers"] });
    },
  });

  async function handleTest(connectorId: string) {
    setTestingId(connectorId);
    try {
      const result = await checkInstallationHealth(connectorId);
      setTestResult((prev) => ({
        ...prev,
        [connectorId]: result.health_status,
      }));
    } catch {
      setTestResult((prev) => ({ ...prev, [connectorId]: "error" }));
    } finally {
      setTestingId(null);
    }
  }

  function renderProviderCard(provider: AuthProvider) {
    const connector = connectors.find((c) => c.provider === provider.name);
    const isConnected = provider.connected || !!connector;
    const icon = PROVIDER_ICONS[provider.name] || "🔌";

    return (
      <Card key={provider.name}>
        <div className="p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="text-lg">{icon}</span>
              <div>
                <h3 className="text-sm font-medium text-t-primary">
                  {provider.display_name}
                </h3>
                <p className="text-xs text-t-secondary">
                  {provider.type === "builtin"
                    ? "Native integration"
                    : "OAuth connection"}
                </p>
              </div>
            </div>
            <Badge variant={isConnected ? "green" : "default"}>
              {isConnected ? "Connected" : "Not connected"}
            </Badge>
          </div>

          {provider.scopes.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3">
              {provider.scopes.slice(0, 3).map((scope) => (
                <span
                  key={scope}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-t-secondary"
                >
                  {scope.split("/").pop()?.split(":").pop() || scope}
                </span>
              ))}
              {provider.scopes.length > 3 && (
                <span className="text-[10px] px-1.5 py-0.5 text-t-secondary">
                  +{provider.scopes.length - 3} more
                </span>
              )}
            </div>
          )}

          <div className="flex gap-2">
            {isConnected && connector ? (
              <>
                <button
                  onClick={() => handleTest(connector.connector_id)}
                  disabled={testingId === connector.connector_id}
                  className="text-xs px-3 py-1.5 rounded-md border border-b-primary text-t-primary hover:bg-surface-2 disabled:opacity-50"
                >
                  {testingId === connector.connector_id
                    ? "Testing..."
                    : "Test"}
                </button>
                <button
                  onClick={() => handleConnect(provider.name)}
                  className="text-xs px-3 py-1.5 rounded-md border border-b-primary text-t-primary hover:bg-surface-2"
                >
                  Reauthorize
                </button>
                <button
                  onClick={() =>
                    disconnectMutation.mutate(connector.connector_id)
                  }
                  className="text-xs px-3 py-1.5 rounded-md border border-j-error/30 text-j-error hover:bg-j-error-soft"
                >
                  Disconnect
                </button>
                {testResult[connector.connector_id] && (
                  <span
                    className={`text-xs py-1.5 ${
                      testResult[connector.connector_id] === "healthy"
                        ? "text-j-success"
                        : "text-j-error"
                    }`}
                  >
                    {testResult[connector.connector_id]}
                  </span>
                )}
              </>
            ) : isConnected ? (
              <button
                onClick={() => handleConnect(provider.name)}
                className="text-xs px-3 py-1.5 rounded-md border border-b-primary text-t-primary hover:bg-surface-2"
              >
                Reauthorize
              </button>
            ) : (
              <button
                onClick={() => handleConnect(provider.name)}
                disabled={connecting === provider.name || !provider.configured}
                className="text-xs px-3 py-1.5 rounded-md bg-j-primary text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
              >
                {connecting === provider.name
                  ? "Redirecting..."
                  : !provider.configured
                    ? "Not configured"
                    : "Connect"}
              </button>
            )}
          </div>
        </div>
      </Card>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader
        title="Connectors"
        subtitle="Manage OAuth provider connections and data sources"
        variant="config"
      />

      {flash && (
        <div
          className={`rounded-lg p-3 text-sm ${
            flash.startsWith("Error")
              ? "bg-j-error-soft border border-j-error/30 text-j-error"
              : "bg-j-success-soft border border-j-success/30 text-j-success"
          }`}
        >
          {flash}
          <button
            onClick={() => setFlash(null)}
            className="ml-3 text-xs opacity-70 hover:opacity-100"
          >
            dismiss
          </button>
        </div>
      )}

      {configuredProviders.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-t-secondary mb-3">
            Available Providers
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {configuredProviders.map(renderProviderCard)}
          </div>
        </div>
      )}

      {unconfiguredProviders.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-t-secondary mb-3">
            Unconfigured
            <span className="font-normal ml-1 text-t-tertiary">
              (set OAuth credentials in .env)
            </span>
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 opacity-60">
            {unconfiguredProviders.map(renderProviderCard)}
          </div>
        </div>
      )}

      {providers.length === 0 && (
        <div className="text-center py-12 text-t-secondary">
          <p>Loading providers...</p>
        </div>
      )}

      {/* Advanced: MCP server installations */}
      <AdvancedMCPSection />
    </div>
  );
}

function AdvancedMCPSection() {
  const [expanded, setExpanded] = useState(false);

  const { data: installations } = useQuery({
    queryKey: ["mcp-installations"],
    queryFn: fetchInstallations,
    enabled: expanded,
  });

  return (
    <div className="border-t border-b-primary pt-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-xs text-t-tertiary hover:text-t-secondary transition-colors cursor-pointer"
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          className={`transition-transform ${expanded ? "" : "-rotate-90"}`}
        >
          <path
            d="M3 4.5l3 3 3-3"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Advanced: MCP Servers
        {installations && installations.length > 0 && (
          <span className="text-t-muted">({installations.length})</span>
        )}
      </button>

      {expanded && (
        <div className="mt-3 space-y-2">
          {!installations || installations.length === 0 ? (
            <p className="text-xs text-t-tertiary py-2">No MCP servers installed.</p>
          ) : (
            installations.map((inst: Installation) => (
              <div
                key={inst.install_id}
                className="flex items-center justify-between px-3 py-2 rounded-lg bg-surface-1 border border-b-primary"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      inst.health_status === "healthy"
                        ? "bg-green-400"
                        : inst.health_status === "degraded"
                          ? "bg-yellow-400"
                          : "bg-red-400"
                    }`}
                  />
                  <span className="text-xs text-t-primary font-medium">
                    {inst.display_name || inst.server_name}
                  </span>
                  <span className="text-[10px] text-t-tertiary">
                    {inst.transport}
                  </span>
                </div>
                <span
                  className={`text-[10px] ${
                    inst.status === "active"
                      ? "text-green-400"
                      : "text-t-tertiary"
                  }`}
                >
                  {inst.status}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export default function ConnectorsPage() {
  return (
    <Suspense>
      <ConnectorsContent />
    </Suspense>
  );
}
