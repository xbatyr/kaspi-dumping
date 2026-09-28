import type { NextConfig } from "next";

/** Calls to FastAPI go through src/app/api/[...path]/route.ts rather than a
 *  rewrite here: a rewrite's destination is frozen into the build, while the
 *  route handler reads API_URL at request time. */
const nextConfig: NextConfig = {
  output: "standalone",
  // The Oracle Always Free VM has 1 GB RAM. One build worker avoids swapping
  // away the live web/API processes when the update timer rebuilds the image.
  experimental: { cpus: 1 },
  images: { remotePatterns: [{ protocol: "https", hostname: "resources.cdn-kaspi.kz", pathname: "/img/**" }] },
};

export default nextConfig;
