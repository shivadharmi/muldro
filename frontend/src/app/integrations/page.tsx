"use client";

import { Suspense, useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import {
  fetchUnifiedIntegrations,
  getAuthUrl,
  disconnectInstallation,
  type UnifiedIntegration,
} from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";
import {
  useConnectAccount,
  type ConnectRun,
  type ProviderOutcome,
} from "@/hooks/useConnectAccount";
import { SkeletonGrid } from "@/components/ui/skeleton";
import {
  IntegrationCard,
  providerLabel,
} from "@/components/integrations/integration-card";

/** How each non-active outcome reads to the user. */
const OUTCOME_WORDING: Record<Exclude<ProviderOutcome, "active">, string> = {
  blocked: "popup blocked",
  cancelled: "cancelled",
  timeout: "still pending",
  error: "failed",
};

/** Providers a blocked popup left unconnected, per installation server_name. */
type BlockedProviders = Record<string, string[]>;

function IntegrationsContent() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const [connecting, setConnecting] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<BlockedProviders>({});
  // The native redirect is tracked apart from the gateway walk: a dual-credential
  // card can offer both, and only one of them navigates this tab.
  const [redirecting, setRedirecting] = useState<string | null>(null);
  const { addToast } = useToast();
  const gatewayConnect = useConnectAccount();

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

  /** Report one finished gateway walk: refetch, then say what actually happened. */
  function reportRun(integration: UnifiedIntegration, run: ConnectRun) {
    // Refetch on EVERY terminal state, not just success. An all-cancelled walk
    // can still have connected something (the popup usually closes right after
    // approval), and with refetchOnWindowFocus off nothing else would ever
    // correct a card that silently reads "Not connected".
    queryClient.invalidateQueries({ queryKey: ["unified-integrations"] });

    const entries = Object.entries(run.outcomes);
    const unfinished = entries.filter(([, o]) => o !== "active").map(([p]) => p);
    // A blocked popup can only be recovered by a fresh user gesture, so the
    // card has to render something clickable — a toast offers nothing to click.
    // Keyed per card, so reporting one installation can't wipe another's.
    setBlocked((prev) => {
      const next = { ...prev };
      if (entries.some(([, o]) => o === "blocked")) {
        next[integration.server_name] = unfinished;
      } else {
        delete next[integration.server_name];
      }
      return next;
    });

    const label = (provider: string) => providerLabel(integration, provider);
    const describe = ([provider, outcome]: [string, ProviderOutcome]) => {
      const word = OUTCOME_WORDING[outcome as Exclude<ProviderOutcome, "active">];
      const cause = run.errors[provider];
      return cause
        ? `${label(provider)} ${word} (${cause})`
        : `${label(provider)} ${word}`;
    };
    const detail = entries.filter(([, o]) => o !== "active").map(describe);

    switch (run.state) {
      case "active":
        addToast("Connected successfully", "success");
        break;
      case "partial":
        addToast(`Partly connected — ${detail.join(", ")}`, "warning");
        break;
      case "blocked":
        addToast(
          `Popup blocked — click to connect ${unfinished.map(label).join(", ")}`,
          "warning",
        );
        break;
      case "cancelled":
        addToast("Connection cancelled", "info");
        break;
      case "timeout":
        addToast("Connection timed out — please try again", "error");
        break;
      case "error":
        // Surface the cause: HTTP 503 "connection service not configured" is
        // the local-dev-without-OpenConnector case and must fail loudly.
        addToast(`Failed to start connection — ${detail.join(", ")}`, "error");
        break;
    }
  }

  /** Walk `providers` for one installation via the popup-poll flow. */
  async function runGateway(
    integration: UnifiedIntegration,
    providers: string[],
  ) {
    if (providers.length === 0) return;
    const previouslyConnecting = connecting;
    setConnecting(integration.server_name);
    const run = await gatewayConnect.start(providers);
    if (run === null) {
      // Another walk already owns the flow, so nothing ran for this card.
      // Restore whichever card was pending instead of clearing its highlight
      // and reporting a run that never happened.
      setConnecting(previouslyConnecting);
      return;
    }
    // Clear the pending card. Without this the stale server_name lingers and
    // the NEXT gateway integration inherits this card's pending highlight.
    setConnecting(null);
    reportRun(integration, run);
  }

  async function handleConnect(integration: UnifiedIntegration) {
    // Gateway-backed providers (OpenConnector) use the popup-poll flow; OC owns
    // the OAuth callback and never redirects back to us (spike-findings-connect §4).
    if (integration.oc_providers?.length) {
      // One installation can fan out to several OC providers (e.g. Google
      // Workspace -> gmail + googlecalendar); the hook walks them in order.
      // `connected` is all-of, so a half-connected install still renders
      // Connect: walk only what is missing, or the user must dismiss a
      // redundant Gmail consent before Calendar's even opens — and that
      // redundant popup spends the single click's user-activation budget.
      // Reauthorize (connected) legitimately re-consents everything.
      const missing = integration.oc_providers.filter(
        (p) => !integration.provider_connections?.[p],
      );
      await runGateway(
        integration,
        integration.connected || missing.length === 0
          ? integration.oc_providers
          : missing,
      );
      return;
    }
    await connectNative(integration, integration.provider);
  }

  /** Start the full-page OAuth redirect for one natively authenticated grant. */
  async function connectNative(
    integration: UnifiedIntegration,
    provider: string | null | undefined,
  ) {
    if (!provider) return;
    setRedirecting(integration.server_name);
    try {
      const { url } = await getAuthUrl(provider);
      window.location.assign(url);
    } catch (err) {
      addToast(`Failed to start OAuth: ${errorToMessage(err)}`, "error");
      setRedirecting(null);
    }
  }

  const services = (integrations ?? []).filter(
    (i) => i.category === "oauth" || i.category === "token",
  );
  const localTools = (integrations ?? []).filter(
    (i) => i.category === "local",
  );

  function renderCard(integration: UnifiedIntegration) {
    const isGateway = !!integration.oc_providers?.length;
    return (
      <IntegrationCard
        key={integration.server_name}
        integration={integration}
        isPending={
          connecting === integration.server_name &&
          (!isGateway || gatewayConnect.state === "connecting")
        }
        isNativePending={redirecting === integration.server_name}
        blockedProviders={blocked[integration.server_name]}
        onConnect={() => handleConnect(integration)}
        onConnectNative={() =>
          connectNative(integration, integration.native_provider)
        }
        onRetryBlocked={(providers) => runGateway(integration, providers)}
        onDisconnect={() => disconnectMutation.mutate(integration.install_id!)}
      />
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
