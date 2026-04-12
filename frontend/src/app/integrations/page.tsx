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
import { SkeletonGrid } from "@/components/ui/skeleton";

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

function IntegrationsContent() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [connecting, setConnecting] = useState<string | null>(null);
  const { addToast } = useToast();

  // Refetch when returning from OAuth callback
  useEffect(() => {
    const status = searchParams.get("status");
    const provider = searchParams.get("provider");
    const error = searchParams.get("error");
    if (status === "connected" && provider) {
      queryClient.invalidateQueries({ queryKey: ["installations"] });
      queryClient.invalidateQueries({ queryKey: ["auth-providers"] });
      addToast(`${provider} connected successfully`, "success");
      window.history.replaceState({}, "", "/integrations");
    } else if (error) {
      addToast(`Error: ${error}`, "error");
      window.history.replaceState({}, "", "/integrations");
    }
  }, [searchParams, queryClient, addToast]);

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

  const activeInstallations = installations.filter((i: Installation) => i.enabled).map((i: Installation) => ({
    install_id: i.install_id,
    server_name: i.server_name,
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

  async function handleTest(installId: string) {
    setTestingId(installId);
    try {
      const result = await checkInstallationHealth(installId);
      setTestResult((prev) => ({
        ...prev,
        [installId]: result.health_status,
      }));
      setTimeout(() => {
        setTestResult((prev) => {
          const next = { ...prev };
          delete next[installId];
          return next;
        });
      }, 5000);
    } catch {
      setTestResult((prev) => ({ ...prev, [installId]: "error" }));
    } finally {
      setTestingId(null);
    }
  }

  function renderProviderCard(provider: AuthProvider) {
    const installation = activeInstallations.find((i) => i.server_name === provider.name);
    const isConnected = provider.connected || !!installation;
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
              {provider.scopes.slice(0, 2).map((scope) => (
                <span
                  key={scope}
                  className="text-[11px] px-1.5 py-0.5 rounded bg-surface-2 text-t-secondary"
                >
                  {scope.split("/").pop()?.split(":").pop() || scope}
                </span>
              ))}
              {provider.scopes.length > 2 && (
                <span
                  className="text-[11px] px-1.5 py-0.5 text-t-secondary"
                  title={provider.scopes.join(", ")}
                >
                  +{provider.scopes.length - 2} more
                </span>
              )}
            </div>
          )}

          <div className="flex gap-2">
            {isConnected && installation ? (
              <>
                <button
                  onClick={() => handleTest(installation.install_id)}
                  disabled={testingId === installation.install_id}
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2 disabled:opacity-50"
                >
                  {testingId === installation.install_id
                    ? "Testing..."
                    : "Test"}
                </button>
                <button
                  onClick={() => handleConnect(provider.name)}
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2"
                >
                  Reauthorize
                </button>
                <button
                  onClick={() =>
                    disconnectMutation.mutate(installation.install_id)
                  }
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-j-error/30 text-j-error hover:bg-j-error-soft"
                >
                  Disconnect
                </button>
                {testResult[installation.install_id] && (
                  <Badge
                    variant={testResult[installation.install_id] === "healthy" ? "success" : "error"}
                  >
                    {testResult[installation.install_id]}
                  </Badge>
                )}
              </>
            ) : isConnected ? (
              <button
                onClick={() => handleConnect(provider.name)}
                className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2"
              >
                Reauthorize
              </button>
            ) : (
              <button
                onClick={() => handleConnect(provider.name)}
                disabled={connecting === provider.name || !provider.configured}
                className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
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
        title="Integrations"
        subtitle="Manage OAuth provider connections and data sources"
        variant="config"
      />

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

      {providers.length === 0 && <SkeletonGrid count={6} />}

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
        aria-expanded={expanded}
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
                        ? "bg-j-success"
                        : inst.health_status === "degraded"
                          ? "bg-j-warning"
                          : "bg-j-error"
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
                      ? "text-j-success"
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

export default function IntegrationsPage() {
  return (
    <Suspense>
      <IntegrationsContent />
    </Suspense>
  );
}
