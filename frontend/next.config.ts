import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Note: /api/jarvis/chat is handled by Route Handler (src/app/api/jarvis/chat/route.ts)
    // for unbuffered SSE streaming. Other /api/* paths proxy through rewrites.
    const backend = process.env.BACKEND_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
