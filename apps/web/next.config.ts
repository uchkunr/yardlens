import path from "node:path";

import type { NextConfig } from "next";

// Where the Next.js server reaches the API (rewrites). In Docker that is the `api` service; the
// browser's own address for the API (NEXT_PUBLIC_API_URL) may be the dashboard's origin instead.
const apiUrl = (process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100").replace(
  /\/$/,
  "",
);

const nextConfig: NextConfig = {
  // The Docker image runs Next.js's standalone server; the files it needs are traced from the
  // monorepo root, so the dependencies hoisted there are found.
  ...(process.env.NEXT_STANDALONE
    ? { output: "standalone", outputFileTracingRoot: path.resolve(process.cwd(), "../..") }
    : {}),
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
