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
import { PriceQuickEdit } from "@/components/PriceQuickEdit";
import { HistoryDialog } from "@/components/HistoryDialog";
import { StatusPanel } from "@/components/StatusPanel";
import { linkKaspiCard } from "@/lib/client";
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
  const [city, setCity] = useState(() => {
    const counts = new Map<string, number>();
    for (const product of data.items) {
      for (const rule of product.rules) counts.set(rule.city_id, (counts.get(rule.city_id) ?? 0) + 1);
    }
    const mostUsed = [...counts].sort((a, b) => b[1] - a[1])[0]?.[0];
    return mostUsed ?? (cities.some((item) => item.id === DEFAULT_CITY) ? DEFAULT_CITY : (cities[0]?.id ?? ""));
  });
  const [adding, setAdding] = useState(false);
  const [historyOf, setHistoryOf] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function ruleFor(product: ProductRules): Rule | undefined {
    return product.rules.find((rule) => rule.city_id === city);
  }

  const visibleRuleIds = data.items.flatMap((product) => {
    const rule = ruleFor(product);
    return rule ? [rule.id] : [];
  });
  const selectedRuleIds = visibleRuleIds.filter((id) => selected.has(id));
  const allVisibleSelected = visibleRuleIds.length > 0 && selectedRuleIds.length === visibleRuleIds.length;

  function toggleSelected(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllVisible() {
    setSelected((current) => {
      const next = new Set(current);
      for (const id of visibleRuleIds) {
        if (allVisibleSelected) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }

  // The page's own search params come in as props, so this component never
  // reads them with useSearchParams and never needs a Suspense boundary.
  function goToPage(offset: number) {
    const next = new URLSearchParams();
    if (search) next.set("q", search);
    next.set("offset", String(Math.max(0, offset)));
    router.push(`/?${next}`);
  }

  async function linkCard(product: ProductRules) {
    const value = window.prompt(`ID карточки Kaspi для ${product.sku} (цифры в конце ссылки):`);
    if (value === null) return;
    const cardId = value.trim();
    if (!/^\d{1,64}$/.test(cardId)) {
      setError("ID карточки должен содержать только цифры");
      return;
    }
    try {
      await linkKaspiCard(product.sku, cardId);
      setNotice(`Карточка для ${product.sku} привязана. Теперь можно настроить стратегию.`);
      setError(null);
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось привязать карточку");
    }
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
            className="min-h-11 w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-base outline-none focus:border-slate-400 sm:text-sm"
          />
        </form>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        {selectedRuleIds.length > 0 && (
          <button
            type="button"
            onClick={() => router.push(`/strategies?bulk=${selectedRuleIds.join(",")}`)}
            className="min-h-11 cursor-pointer rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
          >
            Настроить выбранные ({selectedRuleIds.length})
          </button>
        )}
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="inline-flex min-h-11 cursor-pointer items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          <PackagePlus className="size-4" />
          Добавить товары
        </button>
        <label className="flex min-w-0 items-center gap-2 text-sm text-slate-600">
          Город
          <select
            value={city}
            onChange={(event) => { setCity(event.target.value); setSelected(new Set()); }}
            className="min-h-11 min-w-0 flex-1 cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-2 text-base outline-none focus:border-slate-400 sm:flex-none sm:text-sm"
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
      {notice && <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}

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
          <div className="hidden rounded-xl border border-slate-200 bg-white md:block">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-3">
                    <input type="checkbox" aria-label="Выбрать все правила на странице" checked={allVisibleSelected} onChange={toggleAllVisible} disabled={visibleRuleIds.length === 0} className="cursor-pointer" />
                  </th>
                  <th className="px-4 py-3 font-medium">Товар</th>
                  <th className="px-4 py-3 font-medium">Бот</th>
                  <th className="px-4 py-3 text-right font-medium">Цена в прайсе</th>
                  <th className="px-4 py-3 text-right font-medium">Расчётная</th>
                  <th className="px-4 py-3 text-right font-medium">Min / Max / Шаг</th>
                  <th className="px-4 py-3 font-medium">Позиция</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((product) => {
                  const rule = ruleFor(product);
                  return (
                    <tr key={product.sku} className="hover:bg-slate-50/60">
                      <td className="px-3 py-3">
                        <input type="checkbox" aria-label={`Выбрать ${product.sku}`} checked={rule ? selected.has(rule.id) : false} onChange={() => { if (rule) toggleSelected(rule.id); }} disabled={!rule} className="cursor-pointer" />
                      </td>
                      <td className="px-4 py-3">
                        <div className="max-w-xs truncate font-medium text-slate-900">
                          {product.title}
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                          <span className="font-mono">{product.sku}</span>
                          {product.kaspi_product_id ? <a
                            href={`https://kaspi.kz/shop/p/-${product.kaspi_product_id}/`}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-0.5 hover:text-slate-700"
                          >
                            карточка
                            <ExternalLink className="size-3" />
                          </a> : <button type="button" onClick={() => void linkCard(product)} className="text-amber-700 underline">Привязать карточку</button>}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {rule?.strategy === "manual" ? <span className="text-xs text-slate-500">Вручную</span> : <ActiveToggle
                          ruleIds={rule ? [rule.id] : []}
                          isActive={rule?.is_active ?? false}
                          label={`Репрайсер для ${product.sku}`}
                          onError={setError}
                        />}
                      </td>
                      <td className="tabular px-4 py-3 text-right font-medium text-slate-900">
                        <PriceQuickEdit key={`${product.sku}:${rule?.min_price}:${rule?.max_price}:${rule?.step}`} product={product} rule={rule} />
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
                            <div className="text-xs text-slate-400">шаг {tenge(String(rule.step))}</div>
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
                          onClick={() => product.kaspi_product_id ? router.push(`/strategies?sku=${encodeURIComponent(product.sku)}`) : void linkCard(product)}
                          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:border-slate-300 hover:bg-slate-50"
                        >
                          <SlidersHorizontal className="size-3.5" />
                          {!product.kaspi_product_id ? "Привязать" : "Настроить"}
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
                  className="min-w-0 rounded-xl border border-slate-200 bg-white p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1">
                        <label className="flex size-11 shrink-0 items-center justify-center"><input type="checkbox" aria-label={`Выбрать ${product.sku}`} checked={rule ? selected.has(rule.id) : false} onChange={() => { if (rule) toggleSelected(rule.id); }} disabled={!rule} className="size-5 cursor-pointer" /></label>
                        <p className="min-w-0 truncate font-medium text-slate-900">{product.title}</p>
                      </div>
                      <p className="ml-12 break-all font-mono text-xs text-slate-500">{product.sku}</p>
                    </div>
                    {rule?.strategy === "manual" ? <span className="text-xs text-slate-500">Вручную</span> : <ActiveToggle
                      ruleIds={rule ? [rule.id] : []}
                      isActive={rule?.is_active ?? false}
                      label={`Репрайсер для ${product.sku}`}
                      onError={setError}
                    />}
                  </div>

                  <dl className="mt-3 grid min-w-0 grid-cols-2 gap-x-3 gap-y-3 text-sm">
                    <div className="min-w-0">
                      <dt className="text-xs text-slate-500">Цена в прайсе</dt>
                      <dd className="tabular font-medium text-slate-900">
                        <PriceQuickEdit key={`${product.sku}:${rule?.min_price}:${rule?.max_price}:${rule?.step}`} product={product} rule={rule} />
                      </dd>
                    </div>
                    <div className="min-w-0">
                      <dt className="text-xs text-slate-500">Расчётная</dt>
                      <dd className="tabular text-slate-700">
                        {tenge(rule?.last_change?.computed_price)}
                      </dd>
                    </div>
                    <div className="col-span-2 min-w-0">
                      <dt className="text-xs text-slate-500">Min / Max / Шаг</dt>
                      <dd className="tabular text-slate-700">
                        {rule ? `${tenge(rule.min_price)} — ${tenge(rule.max_price)} · шаг ${tenge(String(rule.step))}` : "—"}
                      </dd>
                    </div>
                    <div className="col-span-2 min-w-0">
                      <dt className="text-xs text-slate-500">Позиция</dt>
                      <dd>
                        <PositionBadge position={rule?.last_change?.expected_position ?? null} />
                      </dd>
                    </div>
                  </dl>

                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => product.kaspi_product_id ? router.push(`/strategies?sku=${encodeURIComponent(product.sku)}`) : void linkCard(product)}
                      className="inline-flex min-h-11 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700"
                    >
                      <SlidersHorizontal className="size-4" />
                      {!product.kaspi_product_id ? "Привязать" : "Настроить"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setHistoryOf(product.sku)}
                      aria-label="История цен"
                      className="inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center rounded-lg border border-slate-200 px-3 py-2 text-slate-600"
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
              className="min-h-11 cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Назад
            </button>
            <button
              type="button"
              disabled={data.offset + data.limit >= data.total}
              onClick={() => goToPage(data.offset + data.limit)}
              className="min-h-11 cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
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

    </div>
  );
}
