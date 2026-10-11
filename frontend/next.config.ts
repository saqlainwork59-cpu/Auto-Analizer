import type { NextConfig } from "next";

const API = process.env.API_INTERNAL_URL || "http://localhost:8000";

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // Free hosting tiers put the API to sleep when idle; give it time to wake up (~1 min) before
  // the /api proxy gives up. WebSockets are kept alive by the client's 25 s ping.
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    // Same-origin /api proxy (REST + WebSocket) so auth cookies stay first-party.
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
