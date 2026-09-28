import { createHash, timingSafeEqual } from "node:crypto";
import { NextResponse, type NextRequest } from "next/server";

import { createSession, safeDestination, sameOrigin, SESSION_COOKIE, SESSION_SECONDS } from "@/lib/session";

const attempts = new Map<string, { count: number; until: number }>();

function equal(left: string, right: string): boolean {
  const a = createHash("sha256").update(left).digest();
  const b = createHash("sha256").update(right).digest();
  return timingSafeEqual(a, b);
}

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return new Response("Недопустимый источник", { status: 403 });
  const form = await request.formData();
  const username = String(form.get("username") ?? "").slice(0, 256);
  const password = String(form.get("password") ?? "").slice(0, 1024);
  const destination = safeDestination(String(form.get("next") ?? "/"));
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  const now = Date.now();
  if (attempts.size >= 1000) {
    for (const [key, value] of attempts) if (value.until <= now) attempts.delete(key);
    if (attempts.size >= 1000) attempts.delete(attempts.keys().next().value!);
  }
  const current = attempts.get(ip);
  if (current && current.until > now && current.count >= 8) {
    return new Response("Слишком много попыток. Повторите через 15 минут.", { status: 429 });
  }
  const user = process.env.DASHBOARD_USER ?? "owner";
  const pass = process.env.DASHBOARD_PASSWORD ?? "";
  if (!pass || !equal(username, user) || !equal(password, pass)) {
    attempts.set(ip, { count: current && current.until > now ? current.count + 1 : 1, until: now + 15 * 60_000 });
    const login = new URL("/login", "http://local.invalid");
    login.searchParams.set("error", "1");
    if (destination !== "/") login.searchParams.set("next", destination);
    return new NextResponse(null, { status: 303, headers: { Location: `${login.pathname}${login.search}`, "Cache-Control": "no-store" } });
  }
  attempts.delete(ip);
  const response = new NextResponse(null, { status: 303, headers: { Location: destination } });
  response.cookies.set(SESSION_COOKIE, await createSession(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_SECONDS,
  });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
