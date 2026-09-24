import type { NextConfig } from "next";

/** Calls to FastAPI go through src/app/api/[...path]/route.ts rather than a
 *  rewrite here: a rewrite's destination is frozen into the build, while the
 *  route handler reads API_URL at request time. */
const nextConfig: NextConfig = { output: "standalone" };

export default nextConfig;
