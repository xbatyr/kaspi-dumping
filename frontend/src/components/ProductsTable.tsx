"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Package,
  PackagePlus,
  Search,
  SlidersHorizontal,
} from "lucide-react";

import { ProductCard } from "@/components/ProductCard";
import { AddProductDialog } from "@/components/AddProductDialog";
import { BulkToolsDialog } from "@/components/BulkToolsDialog";
import { HistoryDialog } from "@/components/HistoryDialog";
import { StatusPanel } from "@/components/StatusPanel";
import { linkKaspiCard } from "@/lib/client";
import type { CatalogFilters, Category, City, ProductRules, Rule, RuleList, Status } from "@/lib/types";

const DEFAULT_CITY = "750000000";

interface Props {
  data: RuleList;
  cities: City[];
  categories: Category[];
  filters: CatalogFilters;
  status: Status;
  feedUrl: string;
}

export function ProductsTable({ data, cities, categories, filters, status, feedUrl }: Props) {
  const router = useRouter();
  const { q: search, bot, sale, category, sort } = filters;

  /** Every filter lives in the URL, so a filtered catalogue can be reloaded,
   *  shared or paged without the page holding a second copy of the state. */
  function go(changes: Partial<CatalogFilters> & { offset?: number }) {
    const next = new URLSearchParams();
    const merged = { ...filters, ...changes };
    if (merged.q) next.set("q", merged.q);
    if (merged.bot !== "all") next.set("bot", merged.bot);
    if (merged.sale !== "all") next.set("sale", merged.sale);
    if (merged.category) next.set("category", merged.category);
    if (merged.sort !== "sku") next.set("sort", merged.sort);
    if (changes.offset) next.set("offset", String(Math.max(0, changes.offset)));
    router.push(`/?${next}`);
  }
  const [city, setCity] = useState(() => {
    const counts = new Map<string, number>();
    for (const product of data.items) {
      for (const rule of product.rules) counts.set(rule.city_id, (counts.get(rule.city_id) ?? 0) + 1);
    }
    const mostUsed = [...counts].sort((a, b) => b[1] - a[1])[0]?.[0];
    return mostUsed ?? (cities.some((item) => item.id === DEFAULT_CITY) ? DEFAULT_CITY : (cities[0]?.id ?? ""));
  });
  const [adding, setAdding] = useState(false);
  const [tools, setTools] = useState(false);
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
      <details className="rounded-xl border border-slate-200 bg-white px-4 py-3">
        <summary className="cursor-pointer text-sm text-slate-600">Товаров: <b>{status.products_total}</b> · Правил в работе: <b>{status.rules_active}</b> · {status.feed_ready ? "XML готов" : "XML требует внимания"}<span className="ml-2 text-xs text-[#345c7f]">Состояние магазина и ссылка XML</span></summary>
        <div className="mt-3"><StatusPanel status={status} feedUrl={feedUrl} /></div>
      </details>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <form className="relative flex-1 sm:max-w-xs" action="/">
          <input type="hidden" name="bot" value={bot} /><input type="hidden" name="sale" value={sale} />
          <input type="hidden" name="category" value={category} /><input type="hidden" name="sort" value={sort} />
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
            Массовые настройки ({selectedRuleIds.length})
          </button>
        )}
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="inline-flex min-h-11 cursor-pointer items-center justify-center gap-1.5 rounded-lg bg-emerald-700 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-800"
        >
          <PackagePlus className="size-4" />
          Добавить товары
        </button>
        <button type="button" onClick={() => setTools(true)} className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 hover:border-slate-300 hover:bg-slate-50"><SlidersHorizontal className="size-4" />Инструменты</button>
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

      <details key={`${sale}:${category}:${bot}:${sort}`} className="rounded-xl border border-slate-200 bg-white px-4 py-3" open={sale !== "all" || category !== "" || bot !== "all" || sort !== "sku"}>
        <summary className="cursor-pointer text-sm font-medium text-slate-700">Фильтры и сортировка{(sale !== "all" || category !== "" || bot !== "all" || sort !== "sku") && <span className="ml-2 rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800">Применены</span>}</summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="min-w-0 text-xs text-slate-600">Продажа
          <select className="catalog-input mt-1" value={sale} onChange={event => go({ sale: event.target.value })}>
            <option value="all">Все товары</option>
            <option value="on">На продаже</option>
            <option value="off">Сняты с продажи</option>
          </select>
        </label>
        <label className="min-w-0 text-xs text-slate-600">Категория
          <select className="catalog-input mt-1" value={category} onChange={event => go({ category: event.target.value })}>
            <option value="">Все категории</option>
            {categories.map(item => <option key={item.name ?? "__none__"} value={item.name ?? "__none__"}>{(item.name ?? "Без категории")} ({item.products})</option>)}
          </select>
        </label>
        <label className="min-w-0 text-xs text-slate-600">Работа бота
          <select className="catalog-input mt-1" value={bot} onChange={event => go({ bot: event.target.value })}>
            <option value="all">Все товары</option>
            <option value="enabled">Бот включён</option>
            <option value="disabled">Бот выключен / вручную</option>
            <option value="unlinked">Без карточки Kaspi</option>
          </select>
        </label>
        <label className="min-w-0 text-xs text-slate-600">Сортировка
          <select className="catalog-input mt-1" value={sort} onChange={event => go({ sort: event.target.value })}>
            <option value="sku">По артикулу</option>
            <option value="title">По названию</option>
            <option value="price_asc">Цена: сначала дешёвые</option>
            <option value="price_desc">Цена: сначала дорогие</option>
            <option value="margin_asc">Маржа: сначала худшая</option>
            <option value="margin_desc">Маржа: сначала лучшая</option>
            <option value="updated">Недавно изменённые</option>
          </select>
        </label>
      </div>
      <a href={feedUrl} download="kaspi.xml" className="mt-3 inline-flex min-h-11 items-center rounded-lg border border-slate-200 px-4 text-sm text-[#345c7f]">Скачать XML</a>
      </details>
      <datalist id="product-categories">{categories.map(item => item.name && <option key={item.name} value={item.name} />)}</datalist>

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
          <div className="flex items-center justify-between gap-3 text-sm text-slate-600">
            <label className="flex min-h-11 items-center gap-3"><input type="checkbox" aria-label="Выбрать все правила на странице" checked={allVisibleSelected} onChange={toggleAllVisible} disabled={!visibleRuleIds.length} className="size-5 accent-emerald-600" />Выбрать все на странице</label>
            <span>{data.total} товаров</span>
          </div>
          <div className="catalog-grid catalog-head sticky top-0 z-20 hidden rounded-lg border border-slate-200 bg-white lg:grid" aria-hidden="true">
            {['Город', 'Место', 'Цена 1 места / Магазин', 'Текущая цена (XML) / Маржа', 'Мин. цена / Маржа', 'Макс. цена / Маржа', 'Шаг', 'Точки продаж / Остатки', 'Действия'].map(label => <div key={label}>{label}</div>)}
          </div>
          <div className="space-y-3">
            {data.items.map(product => {
              const rule = ruleFor(product);
              return <ProductCard key={product.sku} product={product} rule={rule}
                cityName={cities.find(item => item.id === city)?.name ?? city}
                selected={rule ? selected.has(rule.id) : false}
                onSelect={() => { if (rule) toggleSelected(rule.id); }}
                onHistory={() => setHistoryOf(product.sku)} onLink={() => void linkCard(product)} />;
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
              onClick={() => go({ offset: data.offset - data.limit })}
              className="min-h-11 cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Назад
            </button>
            <button
              type="button"
              disabled={data.offset + data.limit >= data.total}
              onClick={() => go({ offset: data.offset + data.limit })}
              className="min-h-11 cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Вперёд
            </button>
          </span>
        </div>
      )}

      {adding && <AddProductDialog cities={cities} onClose={() => setAdding(false)} />}

      {tools && <BulkToolsDialog
        skus={data.items.filter(product => product.rules.some(rule => selected.has(rule.id))).map(product => product.sku)}
        onClose={() => setTools(false)} />}

      {historyOf && (
        <HistoryDialog sku={historyOf} cities={cities} onClose={() => setHistoryOf(null)} />
      )}

    </div>
  );
}
