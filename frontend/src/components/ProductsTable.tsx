"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Layers,
  Loader2,
  Package,
  PackagePlus,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";

import { ProductCard } from "@/components/ProductCard";
import { AddProductDialog } from "@/components/AddProductDialog";
import { BulkToolsDialog } from "@/components/BulkToolsDialog";
import { HistoryDialog } from "@/components/HistoryDialog";
import { StatusPanel } from "@/components/StatusPanel";
import { linkKaspiCard, setOnSale } from "@/lib/client";
import { DEFAULT_SALE_FILTER } from "@/lib/filters";
import type { CatalogFilters, Category, City, ProductRules, Rule, RuleList, Status } from "@/lib/types";

const DEFAULT_CITY = "750000000";

interface Props {
  data: RuleList;
  cities: City[];
  categories: Category[];
  filters: CatalogFilters;
  status: Status;
  feedUrl: string;
  /** Open «Массовые настройки» right away: the setup tour links here. */
  openTools?: boolean;
}

/** "3 товара", "5 товаров": the selection bar speaks Russian. */
function products(count: number): string {
  const tens = count % 100, ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} товаров`;
  if (ones === 1) return `${count} товар`;
  if (ones >= 2 && ones <= 4) return `${count} товара`;
  return `${count} товаров`;
}

/** 1 2 3 … 12: the first and last page always, and a window around the current one. */
function pageNumbers(current: number, last: number): (number | "gap")[] {
  if (last <= 10) return Array.from({ length: last }, (_, index) => index + 1);
  const start = Math.max(1, Math.min(current - 4, last - 8));
  const end = Math.min(last, start + 8);
  const pages: (number | "gap")[] = [];
  if (start > 1) pages.push(1, ...(start > 2 ? ["gap" as const] : []));
  for (let page = start; page <= end; page += 1) pages.push(page);
  if (end < last) pages.push(...(end < last - 1 ? ["gap" as const] : []), last);
  return pages;
}

function Pagination({ data, onPage }: { data: RuleList; onPage: (offset: number) => void }) {
  const last = Math.ceil(data.total / data.limit);
  if (last <= 1) return null;
  const current = Math.floor(data.offset / data.limit) + 1;
  const box = "flex min-h-10 min-w-10 items-center justify-center rounded-md border px-2 text-sm tabular";
  return <nav aria-label="Страницы" className="flex flex-wrap items-center justify-end gap-1.5">
    <button type="button" aria-label="Предыдущая страница" disabled={current === 1} onClick={() => onPage(data.offset - data.limit)} className={`${box} border-slate-200 bg-white text-slate-600 disabled:opacity-40`}><ChevronLeft className="size-4" /></button>
    {pageNumbers(current, last).map((page, index) => page === "gap"
      ? <span key={`gap-${index}`} className="px-1 text-slate-400">…</span>
      : <button key={page} type="button" aria-current={page === current ? "page" : undefined} onClick={() => onPage((page - 1) * data.limit)}
          className={`${box} ${page === current ? "border-[#345c7f] bg-[#eef3f8] font-semibold text-[#345c7f]" : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"}`}>{page}</button>)}
    <button type="button" aria-label="Следующая страница" disabled={current === last} onClick={() => onPage(data.offset + data.limit)} className={`${box} border-slate-200 bg-white text-slate-600 disabled:opacity-40`}><ChevronRight className="size-4" /></button>
  </nav>;
}

export function ProductsTable({ data, cities, categories, filters, status, feedUrl, openTools = false }: Props) {
  const router = useRouter();
  const { q: search, bot, sale, category, sort } = filters;

  /** Every filter lives in the URL, so a filtered catalogue can be reloaded,
   *  shared or paged without the page holding a second copy of the state. */
  function go(changes: Partial<CatalogFilters> & { offset?: number }) {
    const next = new URLSearchParams();
    const merged = { ...filters, ...changes };
    if (merged.q) next.set("q", merged.q);
    if (merged.bot !== "all") next.set("bot", merged.bot);
    // The default is left out of the URL, so «Все товары» has to be spelled out.
    if (merged.sale !== DEFAULT_SALE_FILTER) next.set("sale", merged.sale);
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
  const [tools, setTools] = useState(openTools);
  const [menu, setMenu] = useState(false);
  const [saleBusy, setSaleBusy] = useState(false);
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


  const selectedProducts = data.items.filter(product => product.rules.some(rule => selected.has(rule.id)));
  const allSelectedOffSale = selectedProducts.length > 0 && selectedProducts.every(product => !product.is_active);

  async function toggleSale() {
    setSaleBusy(true); setError(null);
    try {
      const updated = await setOnSale(selectedProducts.map(product => product.sku), allSelectedOffSale);
      setNotice(allSelectedOffSale ? `В продажу возвращено: ${products(updated)}.` : `Снято с продажи: ${products(updated)}. Они уйдут из следующего XML.`);
      setSelected(new Set());
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось изменить продажу");
    } finally { setSaleBusy(false); }
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
      {!status.worker_enabled && (
        <div role="status" className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-amber-700" />
          <div className="min-w-0">
            <p><strong>Автоматический демпинг выключен.</strong> Настроенные правила сохранены, но цены сейчас не пересчитываются.</p>
            <Link href="/settings" className="mt-1 inline-block font-semibold text-amber-900 underline underline-offset-2 hover:text-amber-700">Открыть настройки</Link>
          </div>
        </div>
      )}
      <details className="rounded-xl border border-slate-200 bg-white px-4 py-3">
        <summary className="cursor-pointer text-sm text-slate-600">Товаров: <b>{status.products_total}</b> · {status.worker_enabled ? "Правил в работе" : "Настроено правил"}: <b>{status.rules_active}</b> · {status.feed_ready ? "XML готов" : "XML требует внимания"}<span className="ml-2 text-xs text-[#345c7f]">Состояние магазина и ссылка XML</span></summary>
        <div className="mt-3"><StatusPanel status={status} feedUrl={feedUrl} /></div>
      </details>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-2xl font-semibold text-slate-900">Товары <span className="tabular">({data.total})</span></h2>
        <label className="flex min-w-0 items-center gap-2 text-sm text-slate-600">
          Город
          <select
            value={city}
            onChange={(event) => { setCity(event.target.value); setSelected(new Set()); }}
            className="min-h-11 min-w-0 cursor-pointer rounded-lg border border-slate-200 bg-white px-3 py-2 text-base outline-none focus:border-slate-400 sm:text-sm"
          >
            {cities.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[auto_auto_minmax(0,1.6fr)_repeat(4,minmax(0,1fr))] lg:items-end">
        <button type="button" onClick={() => setTools(true)}
          className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700">
          <Layers className="size-4" />Массовые настройки
        </button>
        <div className="relative">
          <button type="button" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}
            className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-[#345c7f] px-4 text-sm font-medium text-white hover:bg-[#2b4d6b]">
            <SlidersHorizontal className="size-4" />Инструменты<ChevronDown className="size-4" />
          </button>
          {menu && <button type="button" aria-hidden="true" tabIndex={-1} onClick={() => setMenu(false)} className="fixed inset-0 z-20 cursor-default" />}
          {menu && <div role="menu" className="absolute left-0 z-30 mt-1 w-64 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
            <button role="menuitem" type="button" onClick={() => { setMenu(false); setTools(true); }} className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm hover:bg-slate-50"><SlidersHorizontal className="size-4 text-slate-400" />Массовые настройки</button>
            <button role="menuitem" type="button" onClick={() => { setMenu(false); setAdding(true); }} className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm hover:bg-slate-50"><PackagePlus className="size-4 text-slate-400" />Добавить товары</button>
            <a role="menuitem" href={feedUrl} download="kaspi.xml" onClick={() => setMenu(false)} className="flex min-h-11 w-full items-center gap-2 px-3 text-sm text-slate-800 hover:bg-slate-50"><Download className="size-4 text-slate-400" />Скачать XML</a>
          </div>}
        </div>
        <form className="flex min-w-0" action="/">
          <input type="hidden" name="bot" value={bot} /><input type="hidden" name="sale" value={sale} />
          <input type="hidden" name="category" value={category} /><input type="hidden" name="sort" value={sort} />
          <label className="relative min-w-0 flex-1">
            <span className="sr-only">Поиск по артикулу или названию</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              name="q"
              defaultValue={search}
              placeholder="Поиск по артикулу, названию"
              className="min-h-11 w-full rounded-l-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-base outline-none focus:border-slate-500 sm:text-sm"
            />
          </label>
          <button className="min-h-11 shrink-0 rounded-r-lg bg-[#345c7f] px-4 text-sm font-medium text-white hover:bg-[#2b4d6b]">Поиск</button>
        </form>
        <label className="min-w-0 text-xs text-slate-600">Фильтры
          <select className="catalog-input mt-1" value={sale} onChange={event => go({ sale: event.target.value })}>
            <option value="on">В наличии ({data.sale_counts.on})</option>
            <option value="all">Все товары ({data.sale_counts.all})</option>
            <option value="off">Нет в наличии / сняты ({data.sale_counts.off})</option>
          </select>
        </label>
        <label className="min-w-0 text-xs text-slate-600">Сортировать по
          <select className="catalog-input mt-1" value={sort} onChange={event => go({ sort: event.target.value })}>
            <option value="sku">По умолчанию</option>
            <option value="title">По названию</option>
            <option value="price_asc">Цена: сначала дешёвые</option>
            <option value="price_desc">Цена: сначала дорогие</option>
            <option value="margin_asc">Маржа: сначала худшая</option>
            <option value="margin_desc">Маржа: сначала лучшая</option>
            <option value="updated">Недавно изменённые</option>
          </select>
        </label>
        <label className="min-w-0 text-xs text-slate-600">Категории
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
      </div>
      <Pagination data={data} onPage={offset => go({ offset })} />
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
              : sale !== "all" && data.sale_counts.all > 0
                ? "С этим фильтром ничего нет — остальные товары в «Все товары»."
                : "Откройте «Инструменты» → «Добавить товары» — по одному или списком из таблицы."}
          </p>
          {!search && sale !== "all" && data.sale_counts.all > 0 && (
            <button type="button" onClick={() => go({ sale: "all" })} className="mt-3 min-h-10 rounded-lg border border-slate-300 px-4 text-sm font-medium text-[#345c7f] hover:bg-slate-50">
              Показать все товары ({data.sale_counts.all})
            </button>
          )}
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

      <Pagination data={data} onPage={offset => go({ offset })} />

      {selectedProducts.length > 0 && (
        <div role="region" aria-label="Выбранные товары" className="fixed inset-x-4 bottom-20 z-40 mx-auto flex max-w-xl flex-wrap items-center justify-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-[0_8px_30px_-8px_rgba(15,23,42,0.35)] md:bottom-6">
          <span className="text-sm text-slate-700">Выбрано {products(selectedProducts.length)}</span>
          <button type="button" disabled={saleBusy} onClick={() => void toggleSale()}
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-[#345c7f] px-4 text-sm font-medium text-white hover:bg-[#2b4d6b] disabled:opacity-60">
            {saleBusy && <Loader2 className="size-4 animate-spin" />}{allSelectedOffSale ? "Вернуть в продажу" : "Снять с продажи"}
          </button>
          <button type="button" onClick={() => setTools(true)} className="min-h-10 rounded-lg border border-slate-300 px-3 text-sm font-medium text-slate-800 hover:bg-slate-50">Массовые настройки</button>
          <button type="button" aria-label="Снять выделение" onClick={() => setSelected(new Set())} className="flex size-9 items-center justify-center rounded-full border border-slate-300 text-slate-500 hover:bg-slate-50"><X className="size-4" /></button>
        </div>
      )}

      {adding && <AddProductDialog cities={cities} onClose={() => setAdding(false)} />}

      {tools && <BulkToolsDialog
        skus={selectedProducts.map(product => product.sku)}
        categories={categories}
        onClose={() => setTools(false)} />}

      {historyOf && (
        <HistoryDialog sku={historyOf} cities={cities} onClose={() => setHistoryOf(null)} />
      )}

    </div>
  );
}
