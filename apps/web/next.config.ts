import type { NextConfig } from "next";

const API_URL = process.env.WARDEN_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // The browser talks only to this origin; the API stays private behind it,
  // which keeps session cookies first-party and avoids CORS entirely.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
