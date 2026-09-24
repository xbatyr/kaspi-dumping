"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ExternalLink,
  History,
  Package,
  PackagePlus,
  Search,
  SlidersHorizontal,
} from "lucide-react";

import { ActiveToggle } from "@/components/ActiveToggle";
import { AddProductDialog } from "@/components/AddProductDialog";
import { PositionBadge } from "@/components/PositionBadge";
import { HistoryDialog } from "@/components/HistoryDialog";
import { StatusPanel } from "@/components/StatusPanel";
import { StrategyDialog } from "@/components/StrategyDialog";
import { relativeTime, tenge } from "@/lib/format";
import { STRATEGY_LABELS } from "@/lib/strategies";
import type { City, ProductRules, Rule, RuleList, Status } from "@/lib/types";

const DEFAULT_CITY = "750000000";

interface Props {
  data: RuleList;
  cities: City[];
  search: string;
  status: Status;
  feedUrl: string;
}

export function ProductsTable({ data, cities, search, status, feedUrl }: Props) {
  const router = useRouter();
  const [city, setCity] = useState(
    cities.some((item) => item.id === DEFAULT_CITY) ? DEFAULT_CITY : (cities[0]?.id ?? ""),
  );
  const [editing, setEditing] = useState<ProductRules | null>(null);
  const [adding, setAdding] = useState(false);
  const [historyOf, setHistoryOf] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function ruleFor(product: ProductRules): Rule | undefined {
    return product.rules.find((rule) => rule.city_id === city);
  }

  // The page's own search params come in as props, so this component never
  // reads them with useSearchParams and never needs a Suspense boundary.
  function goToPage(offset: number) {
    const next = new URLSearchParams();
    if (search) next.set("q", search);
    next.set("offset", String(Math.max(0, offset)));
    router.push(`/?${next}`);
  }

  return (
    <div className="space-y-4">
      <StatusPanel status={status} feedUrl={feedUrl} />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <form className="relative flex-1 sm:max-w-xs" action="/">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            name="q"
            defaultValue={search}
            placeholder="Поиск по названию или SKU"
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm outline-none focus:border-slate-400"
          />
        </form>

        <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          <PackagePlus className="size-4" />
          Добавить товары
        </button>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          Город
          <select
            value={city}
            onChange={(event) => setCity(event.target.value)}
            className="cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400"
          >
            {cities.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        </div>
      </div>

      {error && (
        <p className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}

      {data.items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white py-16 text-center">
          <Package className="mx-auto size-8 text-slate-300" />
          <p className="mt-3 text-sm font-medium text-slate-900">Товаров нет</p>
          <p className="mt-1 text-sm text-slate-500">
            {search
              ? "Поиск ничего не нашёл — попробуйте другой запрос."
              : "Нажмите «Добавить товары» — по одному или списком из таблицы."}
          </p>
        </div>
      ) : (
        <>
          {/* Desktop */}
          <div className="hidden overflow-hidden rounded-xl border border-slate-200 bg-white md:block">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">Товар</th>
                  <th className="px-4 py-3 font-medium">Бот</th>
                  <th className="px-4 py-3 text-right font-medium">Цена на Kaspi</th>
                  <th className="px-4 py-3 text-right font-medium">Расчётная</th>
                  <th className="px-4 py-3 text-right font-medium">Min / Max</th>
                  <th className="px-4 py-3 font-medium">Позиция</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((product) => {
                  const rule = ruleFor(product);
                  return (
                    <tr key={product.sku} className="hover:bg-slate-50/60">
                      <td className="px-4 py-3">
                        <div className="max-w-xs truncate font-medium text-slate-900">
                          {product.title}
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                          <span className="font-mono">{product.sku}</span>
                          <a
                            href={`https://kaspi.kz/shop/p/-${product.kaspi_product_id}/`}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-0.5 hover:text-slate-700"
                          >
                            карточка
                            <ExternalLink className="size-3" />
                          </a>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <ActiveToggle
                          ruleIds={rule ? [rule.id] : []}
                          isActive={rule?.is_active ?? false}
                          label={`Репрайсер для ${product.sku}`}
                          onError={setError}
                        />
                      </td>
                      <td className="tabular px-4 py-3 text-right font-medium text-slate-900">
                        {tenge(rule?.current_price)}
                        <div className="text-xs font-normal text-slate-400">
                          {relativeTime(rule?.last_evaluated_at ?? null)}
                        </div>
                      </td>
                      <td className="tabular px-4 py-3 text-right">
                        {tenge(rule?.last_change?.computed_price)}
                        {rule?.last_change?.competitor_top1_price && (
                          <div className="text-xs text-slate-400">
                            топ-1: {tenge(rule.last_change.competitor_top1_price)}
                          </div>
                        )}
                      </td>
                      <td className="tabular px-4 py-3 text-right text-slate-600">
                        {rule ? (
                          <>
                            {tenge(rule.min_price)}
                            <div className="text-xs text-slate-400">{tenge(rule.max_price)}</div>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <PositionBadge position={rule?.last_change?.expected_position ?? null} />
                        {rule && (
                          <div className="mt-1 text-xs text-slate-400">
                            {STRATEGY_LABELS[rule.strategy]}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          type="button"
                          onClick={() => setHistoryOf(product.sku)}
                          title="История цен"
                          className="mr-1 inline-flex cursor-pointer items-center rounded-lg border border-slate-200 p-1.5 text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                        >
                          <History className="size-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditing(product)}
                          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:border-slate-300 hover:bg-slate-50"
                        >
                          <SlidersHorizontal className="size-3.5" />
                          {rule ? "Стратегия" : "Настроить"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile */}
          <div className="space-y-3 md:hidden">
            {data.items.map((product) => {
              const rule = ruleFor(product);
              return (
                <div
                  key={product.sku}
                  className="rounded-xl border border-slate-200 bg-white p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-slate-900">{product.title}</p>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">{product.sku}</p>
                    </div>
                    <ActiveToggle
                      ruleIds={rule ? [rule.id] : []}
                      isActive={rule?.is_active ?? false}
                      label={`Репрайсер для ${product.sku}`}
                      onError={setError}
                    />
                  </div>

                  <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                    <div>
                      <dt className="text-xs text-slate-500">Цена на Kaspi</dt>
                      <dd className="tabular font-medium text-slate-900">
                        {tenge(rule?.current_price)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs text-slate-500">Расчётная</dt>
                      <dd className="tabular text-slate-700">
                        {tenge(rule?.last_change?.computed_price)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs text-slate-500">Min / Max</dt>
                      <dd className="tabular text-slate-700">
                        {rule ? `${tenge(rule.min_price)} — ${tenge(rule.max_price)}` : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs text-slate-500">Позиция</dt>
                      <dd>
                        <PositionBadge position={rule?.last_change?.expected_position ?? null} />
                      </dd>
                    </div>
                  </dl>

                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => setEditing(product)}
                      className="inline-flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700"
                    >
                      <SlidersHorizontal className="size-4" />
                      {rule ? "Стратегия" : "Настроить"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setHistoryOf(product.sku)}
                      aria-label="История цен"
                      className="inline-flex cursor-pointer items-center justify-center rounded-lg border border-slate-200 px-3 py-2 text-slate-600"
                    >
                      <History className="size-4" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {data.total > data.limit && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <span className="tabular">
            {data.offset + 1}–{Math.min(data.offset + data.limit, data.total)} из {data.total}
          </span>
          <span className="flex gap-2">
            <button
              type="button"
              disabled={data.offset === 0}
              onClick={() => goToPage(data.offset - data.limit)}
              className="cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Назад
            </button>
            <button
              type="button"
              disabled={data.offset + data.limit >= data.total}
              onClick={() => goToPage(data.offset + data.limit)}
              className="cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Вперёд
            </button>
          </span>
        </div>
      )}

      {adding && <AddProductDialog cities={cities} onClose={() => setAdding(false)} />}

      {historyOf && (
        <HistoryDialog sku={historyOf} cities={cities} onClose={() => setHistoryOf(null)} />
      )}

      {editing && (
        <StrategyDialog
          product={editing}
          cities={cities}
          focusCity={city}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
