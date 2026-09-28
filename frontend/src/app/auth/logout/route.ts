import { NextResponse, type NextRequest } from "next/server";

import { sameOrigin, SESSION_COOKIE } from "@/lib/session";

export function POST(request: NextRequest) {
  if (!sameOrigin(request)) return new Response("Недопустимый источник", { status: 403 });
  const response = new NextResponse(null, { status: 303, headers: { Location: "/login" } });
  response.cookies.set(SESSION_COOKIE, "", { httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax", path: "/", maxAge: 0 });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
