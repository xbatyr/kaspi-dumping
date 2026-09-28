import type { NextConfig } from "next";

/** Calls to FastAPI go through src/app/api/[...path]/route.ts rather than a
 *  rewrite here: a rewrite's destination is frozen into the build, while the
 *  route handler reads API_URL at request time. */
const nextConfig: NextConfig = {
  output: "standalone",
  // The Oracle VM has 1 GB RAM. Docker builds with Webpack and a small heap;
  // limit the page workers as well so deploys leave memory for the live site.
  experimental: { cpus: 1, webpackMemoryOptimizations: true },
  images: { remotePatterns: [{ protocol: "https", hostname: "resources.cdn-kaspi.kz", pathname: "/img/**" }] },
};

export default nextConfig;
