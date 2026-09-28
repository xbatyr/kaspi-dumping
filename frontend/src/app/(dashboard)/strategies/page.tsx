import Link from "next/link";
import { AlertTriangle, Search } from "lucide-react";

import { GlobalStrategyEditor } from "@/components/GlobalStrategyEditor";
import { ProductPriceEditor } from "@/components/ProductPriceEditor";
import { BulkRuleLauncher } from "@/components/BulkRuleLauncher";
import { ApiError, fetchCities, fetchGlobalStrategy, fetchProductRules, fetchRules } from "@/lib/api";
import type { City, GlobalStrategy, ProductRules, RuleList } from "@/lib/types";

const PAGE_SIZE = 30;

function path(q: string, offset: number, sku?: string): string {
  const query = new URLSearchParams();
  if (q) query.set("q", q);
  if (offset) query.set("offset", String(offset));
  if (sku) query.set("sku", sku);
  return `/strategies?${query}`;
}

async function load(q: string, offset: number, sku: string): Promise<{
  data: RuleList; cities: City[]; global: GlobalStrategy; selected: ProductRules | null;
}> {
  const [data, cities, global] = await Promise.all([
    fetchRules({ search: q, offset, limit: PAGE_SIZE }),
    fetchCities(),
    fetchGlobalStrategy(),
  ]);
  const selected = sku
    ? data.items.find((item) => item.sku === sku) ?? await fetchProductRules(sku)
    : data.items[0] ?? null;
  return { data, cities, global, selected };
}

export default async function StrategiesPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; offset?: string; sku?: string; bulk?: string; updated?: string }>;
}) {
  const { q = "", offset: rawOffset = "0", sku = "", bulk = "", updated = "" } = await searchParams;
  const offset = Math.max(0, Number(rawOffset) || 0);
  let result;
  try {
    result = await load(q, offset, sku);
  } catch (error) {
    return <p className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-800">
      <AlertTriangle className="mr-2 inline size-4" />
      {error instanceof ApiError ? error.message : "Не удалось загрузить настройки стратегий"}
    </p>;
  }

  const { data, cities, global, selected } = result;
  const counts = new Map<string, number>();
  for (const product of data.items) for (const rule of product.rules) counts.set(rule.city_id, (counts.get(rule.city_id) ?? 0) + 1);
  const defaultCity = [...counts].sort((a, b) => b[1] - a[1])[0]?.[0] ?? cities[0]?.id ?? "";

  return <div className="space-y-5">
    {bulk && <BulkRuleLauncher ruleIds={[...new Set(bulk.split(",").map(Number).filter((id) => Number.isSafeInteger(id) && id > 0))]} />}
    {/^\d+$/.test(updated) && <p role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">Обновлено правил: {updated}</p>}
    <div>
      <h2 className="text-xl font-semibold text-slate-900">Стратегии</h2>
      <p className="mt-1 text-sm text-slate-500">Стратегия, города и белый список общие. Min/Max и шаг задаются отдельно для каждого товара.</p>
    </div>
    <GlobalStrategyEditor current={global} cities={cities} defaultCity={defaultCity} />
    <div className="grid gap-5 lg:grid-cols-[minmax(240px,300px)_minmax(0,1fr)]">
      <aside className="order-2 min-w-0 rounded-xl border border-slate-200 bg-white p-4 shadow-sm lg:order-1 lg:self-start">
        <form action="/strategies" className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
          <input name="q" type="search" defaultValue={q} placeholder="Название или SKU" aria-label="Найти товар"
            className="w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-slate-400" />
        </form>
        <p className="mt-3 text-xs text-slate-500">Товаров: {data.total}</p>
        <div className="mt-2 max-h-60 space-y-1 overflow-y-auto lg:max-h-[64vh]">
          {data.items.map((product) => <Link key={product.sku} href={path(q, offset, product.sku)}
            className={`block rounded-lg px-3 py-2 text-sm ${selected?.sku === product.sku ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"}`}>
            <span className="block truncate font-medium">{product.title}</span>
            <span className={`block truncate font-mono text-xs ${selected?.sku === product.sku ? "text-slate-300" : "text-slate-500"}`}>{product.sku}</span>
          </Link>)}
          {data.items.length === 0 && <p className="px-3 py-4 text-sm text-slate-500">Товары не найдены.</p>}
        </div>
        {data.total > PAGE_SIZE && <div className="mt-3 flex justify-between border-t border-slate-100 pt-3 text-sm">
          {offset > 0 ? <Link href={path(q, Math.max(0, offset - PAGE_SIZE))} className="text-slate-700 underline">Назад</Link> : <span />}
          {offset + PAGE_SIZE < data.total ? <Link href={path(q, offset + PAGE_SIZE)} className="text-slate-700 underline">Вперёд</Link> : <span />}
        </div>}
      </aside>
      {selected ? selected.kaspi_product_id
        ? <div className="order-1 min-w-0 lg:order-2"><ProductPriceEditor key={selected.sku} product={selected} /></div>
        : <div className="order-1 min-w-0 rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900 lg:order-2">
            Для этого товара сначала привяжите карточку Kaspi во вкладке <Link href="/" className="underline">«Товары»</Link>.
          </div>
        : <div className="order-1 min-w-0 rounded-xl border border-dashed border-slate-300 bg-white p-8 text-sm text-slate-500 lg:order-2">Выберите товар.</div>}
    </div>
  </div>;
}
