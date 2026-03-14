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
  method: "GET" | "POST" | "PATCH" | "DELETE" = "POST",
  body?: unknown
): Promise<BackendResponse> {
  const url = `${config.backendUrl}${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (config.backendToken) {
    headers["Authorization"] = `Bearer ${config.backendToken}`;
  }

  const hasBody = method !== "GET" && method !== "DELETE" && body;

  try {
    const res = await fetch(url, {
      method,
      headers,
      body: hasBody ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: `Backend returned ${res.status}: ${text}` };
    }

    if (res.status === 204) {
      return { success: true };
    }

    const data = await res.json();
    return { success: true, data };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, error: `Backend request failed: ${msg}` };
  }
}
