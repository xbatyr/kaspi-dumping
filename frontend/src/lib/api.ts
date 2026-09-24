/** Server-side calls to the FastAPI backend.
 *
 *  The browser never talks to FastAPI directly: next.config.ts rewrites
 *  /api/* to it, so there is one origin and no CORS to configure.
 */

import type { City, GlobalStrategy, ProductRules, RuleList, Settings, Status } from "@/lib/types";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.REPRICER_API_KEY ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    options?: ErrorOptions,
  ) {
    super(message, options);
  }
}

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { ...init?.headers, "X-API-Key": API_KEY },
    });
  } catch (cause) {
    // Keep the original failure attached: the page shows the friendly line,
    // the server log keeps the socket error.
    throw new ApiError(
      `Бэкенд недоступен по адресу ${API_URL}. Запущен ли uvicorn?`,
      undefined,
      { cause },
    );
  }
  if (response.status === 409) {
    throw new ApiError("Магазин ещё не настроен", 409);
  }
  if (response.status === 401 || response.status === 503) {
    throw new ApiError(
      "Бэкенд не принял ключ: проверьте REPRICER_API_KEY у панели и у API.",
      response.status,
    );
  }
  if (!response.ok) {
    throw new ApiError(`${path} ответил ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export function fetchRules(params: {
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<RuleList> {
  const query = new URLSearchParams();
  if (params.search) query.set("search", params.search);
  query.set("limit", String(params.limit ?? 50));
  query.set("offset", String(params.offset ?? 0));
  // Always fresh: this is a control panel, a cached price is a wrong price.
  return get<RuleList>(`/api/rules?${query}`, { cache: "no-store" });
}

export function fetchCities(): Promise<City[]> {
  // The list changes about as often as Kazakhstan gains a city.
  return get<City[]>("/api/cities", { next: { revalidate: 3600 } });
}

export function fetchProductRules(sku: string): Promise<ProductRules> {
  return get<ProductRules>(`/api/products/${encodeURIComponent(sku)}`, { cache: "no-store" });
}

export function fetchGlobalStrategy(): Promise<GlobalStrategy> {
  return get<GlobalStrategy>("/api/strategy", { cache: "no-store" });
}

export function fetchSettings(): Promise<Settings> {
  return get<Settings>("/api/settings", { cache: "no-store" });
}

export function fetchStatus(): Promise<Status> {
  return get<Status>("/api/status", { cache: "no-store" });
}

/** The link the merchant pastes into the Kaspi cabinet. */
export function feedUrl(): string {
  return process.env.FEED_PUBLIC_URL ?? `${API_URL}/feed/kaspi.xml`;
}
