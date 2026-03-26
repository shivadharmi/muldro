import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Disable built-in compression so SSE streams aren't buffered by gzip.
  // In production, Nginx or CloudFront handles compression at the edge.
  compress: false,
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
