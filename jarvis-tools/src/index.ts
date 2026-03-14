/**
 * Jarvis Tools — OpenClaw Plugin Entry Point
 *
 * This plugin is the integration layer between OpenClaw and the Jarvis backend.
 * It registers:
 *   - Agent tools (jarvis_command, jarvis_brief, jarvis_approve, etc.)
 *   - HTTP routes (webhook intake for Gmail, Calendar, Slack)
 *
 * All business logic lives in the Jarvis backend (Python/FastAPI).
 * This plugin is intentionally thin — HTTP calls only, no state.
 */

import { registerTools } from "./tools.js";
import { registerRoutes } from "./routes.js";
import type { BackendConfig } from "./backend-client.js";

interface PluginApi {
  registerTool: (def: unknown, opts?: { optional: boolean }) => void;
  registerHttpRoute: (def: unknown) => void;
  config: {
    plugins: {
      entries: Record<string, { config?: Record<string, unknown> }>;
    };
  };
}

export default {
  id: "jarvis-tools",

  register(api: PluginApi) {
    const pluginConfig = api.config?.plugins?.entries?.["jarvis-tools"]?.config as
      | { backendUrl?: string; backendToken?: string }
      | undefined;
    const config: BackendConfig = {
      backendUrl: pluginConfig?.backendUrl || process.env.JARVIS_BACKEND_URL || "http://localhost:8000",
      backendToken: pluginConfig?.backendToken || process.env.JARVIS_BACKEND_TOKEN,
    };

    registerTools(api as Parameters<typeof registerTools>[0], config);
    registerRoutes(api as Parameters<typeof registerRoutes>[0], config);
  },
};
