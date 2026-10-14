import type { NextConfig } from "next";

const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8100").replace(/\/$/, "");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // Short-lived JSON requests and snapshot images are proxied same-origin. Long-lived
  // connections (MJPEG, SSE) go straight to the API so they never block regular requests.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiUrl}/api/:path*` },
      { source: "/media/:path*", destination: `${apiUrl}/media/:path*` },
    ];
  },
};

export default nextConfig;
