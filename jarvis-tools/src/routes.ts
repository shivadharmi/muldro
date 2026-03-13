/**
 * HTTP routes registered on the OpenClaw Gateway.
 *
 * These receive webhook payloads from external services (Gmail, Calendar, etc.)
 * and forward them to the Jarvis backend for processing.
 * No payload parsing or business logic here — just forwarding.
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

function forwardRoute(
  api: PluginApi,
  gatewayPath: string,
  backendPath: string,
  config: BackendConfig
) {
  api.registerHttpRoute({
    path: gatewayPath,
    auth: "plugin",
    match: "exact",
    handler: async (req: HttpRequest, res: HttpResponse) => {
      const result = await callBackend(config, backendPath, "POST", req.body ? JSON.parse(req.body) : undefined);
      res.statusCode = result.success ? 200 : 502;
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(result));
      return true;
    },
  });
}

export function registerRoutes(api: PluginApi, config: BackendConfig) {
  // Gmail push notifications (from Google Pub/Sub or polling results)
  forwardRoute(api, "/jarvis/webhook/gmail", "/v1/webhooks/gmail", config);

  // Calendar push notifications
  forwardRoute(api, "/jarvis/webhook/calendar", "/v1/webhooks/calendar", config);

  // Slack events
  forwardRoute(api, "/jarvis/webhook/slack", "/v1/webhooks/slack", config);

  // Generic connector webhook (for future connectors)
  forwardRoute(api, "/jarvis/webhook/generic", "/v1/webhooks/generic", config);

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
