import { NextResponse, type NextRequest } from "next/server";

/**
 * A password on the door.
 *
 * This is Next 16's `proxy.ts`: the old `middleware.ts` name is deprecated and,
 * as we found out the hard way, silently ignored -- the dashboard was wide open
 * until this file was renamed.
 *
 * The dashboard can pause the shop and move every price, so it is not left
 * open. Basic auth keeps it to one environment variable and no login page; the
 * password travels in a header, so serve the dashboard over HTTPS.
 *
 * With DASHBOARD_PASSWORD unset the dashboard refuses to open rather than
 * opening to everyone.
 */

const USER = process.env.DASHBOARD_USER ?? "owner";
const PASSWORD = process.env.DASHBOARD_PASSWORD ?? "";

export function proxy(request: NextRequest) {
  if (!PASSWORD) {
    return new NextResponse(
      "DASHBOARD_PASSWORD не задан — панель закрыта. Задайте пароль в .env.local и перезапустите.",
      { status: 503 },
    );
  }

  const header = request.headers.get("authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  if (scheme === "Basic" && encoded) {
    const [user, ...rest] = atob(encoded).split(":");
    if (user === USER && rest.join(":") === PASSWORD) {
      return NextResponse.next();
    }
  }
  return new NextResponse("Требуется вход", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Kaspi Repricer", charset="UTF-8"' },
  });
}

export const config = {
  // Everything but Next's own assets. The Kaspi feed is served by FastAPI, not
  // from here, so nothing public lives behind this.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
