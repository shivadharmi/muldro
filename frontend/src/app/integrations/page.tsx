"use client";

import { Suspense, useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  fetchUnifiedIntegrations,
  getAuthUrl,
  disconnectInstallation,
  type UnifiedIntegration,
} from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";
import { SkeletonGrid } from "@/components/ui/skeleton";
import {
  GoogleLogo,
  GitHubLogo,
  SlackLogo,
  NotionLogo,
  AtlassianLogo,
  PlaywrightLogo,
  FolderIcon,
} from "@/components/integrations/logos";

type LogoComponent = React.FC<{ className?: string }>;

const LOGOS: Record<string, LogoComponent> = {
  "google-workspace": GoogleLogo,
  github: GitHubLogo,
  slack: SlackLogo,
  notion: NotionLogo,
  atlassian: AtlassianLogo,
  playwright: PlaywrightLogo,
  filesystem: FolderIcon,
};

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

function IntegrationsContent() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const [connecting, setConnecting] = useState<string | null>(null);
  const { addToast } = useToast();

  useEffect(() => {
    const status = searchParams.get("status");
    const provider = searchParams.get("provider");
    const error = searchParams.get("error");
    if (status === "connected" && provider) {
      queryClient.invalidateQueries({ queryKey: ["unified-integrations"] });
      addToast(`${provider} connected successfully`, "success");
      window.history.replaceState({}, "", "/integrations");
    } else if (error) {
      addToast(`Error: ${error}`, "error");
      window.history.replaceState({}, "", "/integrations");
    }
  }, [searchParams, queryClient, addToast]);

  const { data: integrations, isLoading } = useQuery({
    queryKey: ["unified-integrations"],
    queryFn: fetchUnifiedIntegrations,
  });

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => disconnectInstallation(id),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["unified-integrations"] });
      const prev = queryClient.getQueryData(["unified-integrations"]);
      // Flip the row to "Not connected" rather than removing it — the
      // catalog entry stays so the user can reconnect via the same card.
      queryClient.setQueryData(
        ["unified-integrations"],
        (old: UnifiedIntegration[] | undefined) =>
          old
            ? old.map((i) =>
                i.install_id === id ? { ...i, connected: false } : i,
              )
            : old,
      );
      return { prev };
    },
    onSuccess: (_data, _id) => {
      addToast("Disconnected successfully", "success");
    },
    onError: (err, _id, context) => {
      if (context?.prev)
        queryClient.setQueryData(["unified-integrations"], context.prev);
      addToast(`Failed to disconnect: ${errorToMessage(err)}`, "error");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["unified-integrations"] });
    },
  });

  async function handleConnect(integration: UnifiedIntegration) {
    const provider = integration.provider;
    if (!provider) return;
    setConnecting(integration.server_name);
    try {
      const { url } = await getAuthUrl(provider);
      window.location.assign(url);
    } catch (err) {
      addToast(
        `Failed to start OAuth: ${errorToMessage(err)}`,
        "error",
      );
      setConnecting(null);
    }
  }

  const services = (integrations ?? []).filter(
    (i) => i.category === "oauth" || i.category === "token",
  );
  const localTools = (integrations ?? []).filter(
    (i) => i.category === "local",
  );

  function renderCard(integration: UnifiedIntegration) {
    const Logo = LOGOS[integration.server_name];

    return (
      <Card key={integration.server_name}>
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
              <Badge
                variant={integration.connected ? "green" : "default"}
              >
                {integration.connected ? "Connected" : "Not connected"}
              </Badge>
            </div>
          </div>

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
              {integration.connected && integration.install_id ? (
                <>
                  <button
                    onClick={() => handleConnect(integration)}
                    className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2"
                  >
                    Reauthorize
                  </button>
                  <button
                    onClick={() =>
                      disconnectMutation.mutate(integration.install_id!)
                    }
                    className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-j-error/30 text-j-error hover:bg-j-error-soft"
                  >
                    Disconnect
                  </button>
                </>
              ) : integration.connected ? (
                <button
                  onClick={() => handleConnect(integration)}
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] border border-b-primary text-t-primary hover:bg-surface-2"
                >
                  Reauthorize
                </button>
              ) : (
                <button
                  onClick={() => handleConnect(integration)}
                  disabled={
                    connecting === integration.server_name ||
                    !integration.configured
                  }
                  className="text-xs px-2.5 py-1 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover disabled:opacity-50"
                >
                  {connecting === integration.server_name
                    ? "Redirecting..."
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

  if (isLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-6">
        <PageHeader
          title="Integrations"
          subtitle="Manage connections and data sources"
          variant="config"
        />
        <SkeletonGrid count={6} />
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <PageHeader
        title="Integrations"
        subtitle="Manage connections and data sources"
        variant="config"
      />

      {services.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-t-secondary mb-3">
            Connected Services
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {services.map(renderCard)}
          </div>
        </div>
      )}

      {localTools.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-t-secondary mb-3">
            Local Tools
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {localTools.map(renderCard)}
          </div>
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
