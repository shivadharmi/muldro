"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { getGoogleAuthUrl, fetchConnectors, deleteConnector } from "@/lib/api";

const PROVIDERS = [
  {
    id: "google",
    name: "Google Workspace",
    description: "Gmail, Calendar",
    connectorIds: ["gmail", "calendar"],
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path
          d="M14.5 8.2c0-.6-.1-1.2-.2-1.7H8v3.3h3.7c-.2.9-.7 1.7-1.4 2.2v1.8h2.3c1.3-1.2 2-3 2-5.6z"
          fill="#4285F4"
        />
        <path
          d="M8 15c1.9 0 3.5-.6 4.6-1.7l-2.3-1.8c-.6.4-1.4.7-2.4.7-1.8 0-3.4-1.2-3.9-2.9H1.8v1.8C2.9 13.3 5.3 15 8 15z"
          fill="#34A853"
        />
        <path
          d="M4.1 9.3c-.1-.4-.2-.8-.2-1.3s.1-.9.2-1.3V4.9H1.8C1.3 5.9 1 7 1 8s.3 2.1.8 3.1l2.3-1.8z"
          fill="#FBBC05"
        />
        <path
          d="M8 3.8c1 0 1.9.4 2.7 1.1l2-2C11.5 1.7 9.9 1 8 1 5.3 1 2.9 2.7 1.8 5.1l2.3 1.8C4.6 5 6.2 3.8 8 3.8z"
          fill="#EA4335"
        />
      </svg>
    ),
  },
];

type Connector = { connector_id: string; provider: string; status: string };

export function OAuthPanel() {
  const queryClient = useQueryClient();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["connectors"],
    queryFn: fetchConnectors,
  });

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => deleteConnector(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });

  const connectors = ((data?.connectors || []) as Connector[]).filter(
    (c) => c.status === "active"
  );

  const handleConnect = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getGoogleAuthUrl();
      if (result.url) {
        window.location.href = result.url;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to get auth URL");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <span className="text-sm font-medium">OAuth Connections</span>
      </CardHeader>
      <CardBody>
        {PROVIDERS.map((provider) => {
          const linked = connectors.filter((c) =>
            provider.connectorIds.includes(c.provider)
          );
          const isConnected = linked.length > 0;

          return (
            <div key={provider.id} className="flex items-center justify-between py-3">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded bg-neutral-800 flex items-center justify-center">
                  {provider.icon}
                </div>
                <div>
                  <p className="text-sm font-medium">{provider.name}</p>
                  <p className="text-xs text-neutral-500">{provider.description}</p>
                  {isConnected && (
                    <div className="flex gap-1 mt-1">
                      {linked.map((c) => (
                        <Badge key={c.connector_id} variant="green">
                          {c.provider}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {isConnected && (
                  <Badge variant="green">Connected</Badge>
                )}
                {isConnected ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => linked.forEach((c) => disconnectMutation.mutate(c.connector_id))}
                    disabled={disconnectMutation.isPending}
                  >
                    Disconnect
                  </Button>
                ) : (
                  <Button size="sm" variant="secondary" onClick={handleConnect} disabled={loading}>
                    {loading ? "Loading..." : "Connect"}
                  </Button>
                )}
              </div>
            </div>
          );
        })}
        {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
      </CardBody>
    </Card>
  );
}
