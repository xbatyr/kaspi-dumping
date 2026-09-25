import Link from "next/link";
import { AlertTriangle, Store } from "lucide-react";

import { ProductsTable } from "@/components/ProductsTable";
import { ApiError, feedUrl, fetchCities, fetchRules, fetchStatus } from "@/lib/api";
import type { City, RuleList, Status } from "@/lib/types";

const PAGE_SIZE = 50;

type Loaded =
  | { ok: true; data: RuleList; cities: City[]; status: Status }
  | { ok: false; message: string; needsSetup: boolean };

/** Keeps the fetching (and its try/catch) away from the JSX: a try block around
 *  rendering would not catch render errors anyway. */
async function load(search: string, offset: number, bot: string, sort: string): Promise<Loaded> {
  try {
    const [data, cities, status] = await Promise.all([
      fetchRules({ search, limit: PAGE_SIZE, offset, bot, sort }),
      fetchCities(),
      fetchStatus(),
    ]);
    return { ok: true, data, cities, status };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof ApiError ? error.message : "Не удалось загрузить данные",
      needsSetup: error instanceof ApiError && error.status === 409,
    };
  }
}

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; offset?: string; bot?: string; sort?: string }>;
}) {
  const { q = "", offset = "0", bot = "all", sort = "sku" } = await searchParams;
  const result = await load(q, Number(offset) || 0, bot, sort);

  if (!result.ok && result.needsSetup) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-8 text-center">
        <Store className="mx-auto size-8 text-slate-300" />
        <h2 className="mt-3 text-base font-semibold text-slate-900">Давайте настроим магазин</h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
          Укажите ID вашего магазина на Kaspi и название компании — после этого можно
          добавлять товары и включать бота.
        </p>
        <Link
          href="/settings"
          className="mt-4 inline-flex rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          Перейти к настройкам
        </Link>
      </div>
    );
  }

  if (!result.ok) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6">
        <p className="flex items-center gap-2 font-medium text-rose-800">
          <AlertTriangle className="size-5" />
          {result.message}
        </p>
        <p className="mt-2 text-sm text-rose-700">
          Проверьте, что FastAPI запущен и что переменная <code>API_URL</code> указывает на него.
        </p>
      </div>
    );
  }

  return (
    <ProductsTable
      data={result.data}
      cities={result.cities}
      status={result.status}
      feedUrl={feedUrl()}
      search={q}
      bot={bot}
      sort={sort}
    />
  );
}
