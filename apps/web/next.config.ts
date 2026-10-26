import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

const nextConfig = (phase: string): NextConfig => ({
  // Keep live development chunks separate from production build output.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  output: "standalone",
  // Bank imports can contain several source PDFs/DOCX files.  The API still
  // enforces its 50 MB per-file validation; this raises only Next's default
  // 10 MB multipart gateway cap so the relay route can pass a normal batch.
  experimental: {
    middlewareClientMaxBodySize: "200mb",
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.API_INTERNAL_URL ?? "http://localhost:8000"}/:path*` }];
  }
});
export default nextConfig;
