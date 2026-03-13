/**
 * Thin HTTP client for calling the Jarvis backend from OpenClaw tools.
 * All business logic lives in the backend — this just makes HTTP calls.
 */

export interface BackendConfig {
  backendUrl: string;
  backendToken?: string;
}

export interface BackendResponse {
  success: boolean;
  data?: unknown;
  error?: string;
}

export async function callBackend(
  config: BackendConfig,
  path: string,
  method: "GET" | "POST" = "POST",
  body?: unknown
): Promise<BackendResponse> {
  const url = `${config.backendUrl}${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (config.backendToken) {
    headers["Authorization"] = `Bearer ${config.backendToken}`;
  }

  try {
    const res = await fetch(url, {
      method,
      headers,
      body: method === "POST" && body ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: `Backend returned ${res.status}: ${text}` };
    }

    const data = await res.json();
    return { success: true, data };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, error: `Backend request failed: ${msg}` };
  }
}
