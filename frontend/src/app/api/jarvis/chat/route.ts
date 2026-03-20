/**
 * Next.js Route Handler for SSE streaming.
 *
 * Pipes the backend SSE stream directly to the browser without buffering.
 * Route Handlers take priority over rewrites() — no config change needed.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

import { NextRequest } from "next/server";

export async function POST(req: NextRequest) {
  const backend = process.env.BACKEND_URL || "http://localhost:8000";
  const body = await req.text();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const auth = req.headers.get("authorization");
  if (auth) headers["Authorization"] = auth;

  const cookie = req.headers.get("cookie");
  if (cookie) headers["Cookie"] = cookie;

  const backendRes = await fetch(`${backend}/v1/jarvis/chat`, {
    method: "POST",
    headers,
    body,
  });

  if (!backendRes.ok || !backendRes.body) {
    const text = await backendRes.text().catch(() => "Backend error");
    return new Response(text, { status: backendRes.status });
  }

  return new Response(backendRes.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
      Connection: "keep-alive",
    },
  });
}
