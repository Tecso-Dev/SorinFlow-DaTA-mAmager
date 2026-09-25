import path from "node:path";
import type { NextConfig } from "next";

// In production Traefik sends /api, /images and /downloads to the backend and
// the browser never reaches Next.js with them. On a laptop the dev server is
// the only origin, so it forwards them (same origin keeps the session cookie);
// so does a production build made with BACKEND_INTERNAL_URL set (the e2e
// suite's, scripts/e2e_next_up.sh). Rewrites are fixed at build time.
const backend = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8020";
const forward = process.env.NODE_ENV === "development" || !!process.env.BACKEND_INTERNAL_URL;

const nextConfig: NextConfig = {
  // A self-contained server for the Docker image (node server.js), no
  // node_modules at runtime.
  output: "standalone",
  // The repo root has its own package.json (eslint for the old panel); without
  // these Next treats it as the workspace and nests the standalone output.
  outputFileTracingRoot: path.join(__dirname),
  turbopack: { root: path.join(__dirname) },
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    if (!forward) return [];
    return ["api", "images", "downloads", "dashboard"].map((p) => ({
      source: `/${p}/:path*`,
      destination: `${backend}/${p}/:path*`,
    }));
  },
};

export default nextConfig;
