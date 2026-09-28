import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, validSession } from "@/lib/session";

export async function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  const isApi = pathname.startsWith("/api/");
  const isLogin = pathname === "/login" || pathname.startsWith("/login/");
  const isAuth = pathname === "/auth/login" || pathname === "/auth/logout";

  // An unset password must never open the dashboard or its API.
  if (!process.env.DASHBOARD_PASSWORD) {
    return new NextResponse("Панель не настроена", { status: 503 });
  }

  const signedIn = await validSession(request.cookies.get(SESSION_COOKIE)?.value);
  if (isLogin || isAuth) {
    if (isLogin && signedIn) return NextResponse.redirect(new URL("/", request.url));
    return NextResponse.next();
  }
  if (signedIn) return NextResponse.next();
  if (isApi) return NextResponse.json({ detail: "Требуется вход" }, { status: 401 });

  const login = new URL("/login", request.url);
  const destination = `${pathname}${request.nextUrl.search}`;
  if (destination !== "/") login.searchParams.set("next", destination);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
