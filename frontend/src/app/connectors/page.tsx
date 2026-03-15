"use client";

import { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchConnectors, createConnector, deleteConnector, testConnector } from "@/lib/api";

const PROVIDERS = [
  { id: "gmail", name: "Gmail", description: "Email monitoring" },
  { id: "calendar", name: "Google Calendar", description: "Calendar events" },
  { id: "github", name: "GitHub", description: "Repos, PRs, issues" },
  { id: "slack", name: "Slack", description: "Messages and mentions" },
];

export default function ConnectorsPage() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [flash, setFlash] = useState<string | null>(null);

  // Refetch connectors when returning from OAuth callback
  useEffect(() => {
    const status = searchParams.get("status");
    const provider = searchParams.get("provider");
    const error = searchParams.get("error");
    if (status === "connected" && provider) {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
      setFlash(`${provider} connected successfully`);
      // Clear URL params
      window.history.replaceState({}, "", "/connectors");
    } else if (error) {
      setFlash(`Error: ${error}`);
      window.history.replaceState({}, "", "/connectors");
    }
  }, [searchParams, queryClient]);

  const { data, isLoading } = useQuery({
    queryKey: ["connectors"],
    queryFn: fetchConnectors,
  });

  const connectMutation = useMutation({
    mutationFn: (provider: string) => createConnector(provider),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => deleteConnector(id),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["connectors"] });
      const prev = queryClient.getQueryData(["connectors"]);
      queryClient.setQueryData(["connectors"], (old: typeof data) => {
        if (!old?.connectors) return old;
        return { connectors: old.connectors.filter((c: Record<string, unknown>) => c.connector_id !== id) };
      });
      return { prev };
    },
    onError: (_err, _id, context) => {
      if (context?.prev) queryClient.setQueryData(["connectors"], context.prev);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });

  const connectors = (data?.connectors || []).filter(
    (c: Record<string, unknown>) => c.status === "active"
  ) as Array<{
    connector_id: string;
    provider: string;
    status: string;
  }>;

  async function handleTest(connectorId: string) {
    setTestingId(connectorId);
    try {
      const result = await testConnector(connectorId);
      setTestResult((prev) => ({
        ...prev,
        [connectorId]: (result as { status: string }).status,
      }));
    } catch {
      setTestResult((prev) => ({ ...prev, [connectorId]: "error" }));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <div className="p-6 space-y-6">
      <PageHeader title="Connectors" subtitle="Manage data source connections" />

      {flash && (
        <div
          className={`rounded-lg p-3 text-sm ${
            flash.startsWith("Error")
              ? "bg-red-900/20 border border-red-800 text-red-400"
              : "bg-green-900/20 border border-green-800 text-green-400"
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

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {PROVIDERS.map((provider) => {
          const connector = connectors.find((c) => c.provider === provider.id);
          const isConnected = !!connector;

          return (
            <Card key={provider.id}>
              <div className="p-4">
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <h3 className="text-sm font-medium text-white">{provider.name}</h3>
                    <p className="text-xs text-neutral-400">{provider.description}</p>
                  </div>
                  <Badge variant={isConnected ? "green" : "default"}>
                    {isConnected ? "Connected" : "Not connected"}
                  </Badge>
                </div>

                <div className="flex gap-2 mt-3">
                  {isConnected ? (
                    <>
                      <button
                        onClick={() => handleTest(connector.connector_id)}
                        disabled={testingId === connector.connector_id}
                        className="text-xs px-3 py-1.5 rounded-md border border-neutral-700 text-neutral-300 hover:bg-neutral-800 disabled:opacity-50"
                      >
                        {testingId === connector.connector_id ? "Testing..." : "Test"}
                      </button>
                      <button
                        onClick={() => disconnectMutation.mutate(connector.connector_id)}
                        className="text-xs px-3 py-1.5 rounded-md border border-red-800 text-red-400 hover:bg-red-900/20"
                      >
                        Disconnect
                      </button>
                      {testResult[connector.connector_id] && (
                        <span
                          className={`text-xs py-1.5 ${
                            testResult[connector.connector_id] === "healthy"
                              ? "text-green-400"
                              : "text-red-400"
                          }`}
                        >
                          {testResult[connector.connector_id]}
                        </span>
                      )}
                    </>
                  ) : (
                    <button
                      onClick={() => connectMutation.mutate(provider.id)}
                      disabled={connectMutation.isPending}
                      className="text-xs px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      Connect
                    </button>
                  )}
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
