/**
 * HTTP routes registered on the OpenClaw Gateway.
 *
 * Source-specific webhook forwarding has been removed.
 * Event ingestion now happens via the jarvis_ingest_event tool.
 * Only the health check route remains.
 */

import { callBackend, type BackendConfig } from "./backend-client.js";

type HttpRequest = {
  method: string;
  headers: Record<string, string | string[] | undefined>;
  url: string;
  body?: string;
};

type HttpResponse = {
  statusCode: number;
  end: (body?: string) => void;
  setHeader: (name: string, value: string) => void;
};

type PluginApi = {
  registerHttpRoute: (def: {
    path: string;
    auth: "plugin" | "gateway" | "none";
    match: "exact" | "prefix";
    handler: (req: HttpRequest, res: HttpResponse) => Promise<boolean>;
  }) => void;
};

export function registerRoutes(api: PluginApi, config: BackendConfig) {
  // Health check — verifies backend is reachable
  api.registerHttpRoute({
    path: "/jarvis/health",
    auth: "none",
    match: "exact",
    handler: async (_req: HttpRequest, res: HttpResponse) => {
      const result = await callBackend(config, "/v1/health", "GET");
      res.statusCode = result.success ? 200 : 503;
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ plugin: "ok", backend: result.success ? "ok" : "unreachable" }));
      return true;
    },
  });
}
