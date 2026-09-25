import path from "node:path";
import type { NextConfig } from "next";

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
};

export default nextConfig;
