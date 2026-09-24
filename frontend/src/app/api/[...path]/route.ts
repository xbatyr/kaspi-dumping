import type { NextRequest } from "next/server";

/**
 * Forwards /api/* to FastAPI.
 *
 * A next.config rewrite would do the same, but its destination is baked into
 * the build manifest, so the backend address would be fixed at build time. A
 * route handler reads API_URL per request, which is what a container needs.
 *
 * The browser therefore only ever talks to this origin, and FastAPI needs no
 * CORS configuration.
 */

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
// Stays on the server: the browser never sees the key, it only sees this origin,
// which the dashboard password already guards.
const API_KEY = process.env.REPRICER_API_KEY ?? "";

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const target = `${API_URL}/api/${path.join("/")}${request.nextUrl.search}`;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: {
        "Content-Type": request.headers.get("content-type") ?? "application/json",
        Accept: "application/json",
        "X-API-Key": API_KEY,
      },
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
    });
  } catch {
    return Response.json(
      { detail: `Бэкенд недоступен по адресу ${API_URL}` },
      { status: 502 },
    );
  }

  // 204 and 304 must not carry a body.
  const body = upstream.status === 204 || upstream.status === 304 ? null : upstream.body;
  return new Response(body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function PUT(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
