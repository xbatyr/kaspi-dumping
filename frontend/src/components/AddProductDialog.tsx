"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, FileUp, Loader2, PackagePlus, Search, Star, X } from "lucide-react";

import { importProducts, searchKaspi } from "@/lib/client";
import { tenge } from "@/lib/format";
import { parseCatalogCsv, CSV_TEMPLATE } from "@/lib/csv";
import type { City, KaspiCard, ProductDraft } from "@/lib/types";

export function AddProductDialog({ cities, onClose }: { cities: City[]; onClose: () => void }) {
  const router = useRouter();
  const [mode, setMode] = useState<"one" | "csv">("one");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const [sku, setSku] = useState("");
  const [title, setTitle] = useState("");
  const [kaspiId, setKaspiId] = useState("");
  const [brand, setBrand] = useState("");
  const [basePrice, setBasePrice] = useState("");
  const [storeId, setStoreId] = useState("PP1");
  const [stock, setStock] = useState("1");
  const [csv, setCsv] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<KaspiCard[] | null>(null);
  const [searching, setSearching] = useState(false);

  async function search() {
    if (query.trim().length < 2) return;
    setSearching(true);
    setError(null);
    try {
      setFound(await searchKaspi(query.trim(), cities[0]?.id ?? "750000000"));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Поиск не удался");
    } finally {
      setSearching(false);
    }
  }

  function take(card: KaspiCard) {
    setKaspiId(card.kaspi_product_id);
    setTitle(card.title);
    if (card.brand) setBrand(card.brand);
    if (!sku.trim()) setSku(card.kaspi_product_id);
    if (!basePrice.trim() && card.price) setBasePrice(card.price);
    setFound(null);
    setQuery("");
  }

  async function save() {
    setError(null);
    setDone(null);
    let items: ProductDraft[];
    if (mode === "one") {
      if (!sku.trim() || !title.trim() || !/^\d+$/.test(kaspiId.trim())) {
        setError("Заполните SKU, название и числовой ID карточки Kaspi");
        return;
      }
      items = [
        {
          sku: sku.trim(),
          title: title.trim(),
          kaspi_product_id: kaspiId.trim(),
          brand: brand.trim() || null,
          base_price: basePrice.trim() || null,
          is_active: true,
          availabilities: storeId.trim()
            ? [{ store_id: storeId.trim(), available: true, stock_count: Number(stock) || 0 }]
            : [],
          rules: [],
        },
      ];
    } else {
      const parsed = parseCatalogCsv(csv, cities);
      if (parsed.errors.length > 0) {
        setError(parsed.errors.slice(0, 3).join("; "));
        return;
      }
      if (parsed.items.length === 0) {
        setError("В таблице нет ни одной строки с товаром");
        return;
      }
      items = parsed.items;
    }

    setSaving(true);
    try {
      const result = await importProducts(items);
      const failed = result.errors.map((item) => `${item.sku}: ${item.reason}`).join("; ");
      setDone(
        `Добавлено: ${result.created}, обновлено: ${result.updated}` +
          (failed ? `. Не принято — ${failed}` : ""),
      );
      router.refresh();
      if (result.errors.length === 0 && mode === "one") {
        onClose();
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 p-0 sm:items-center sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-product-title"
        className="flex max-h-[92vh] w-full max-w-2xl flex-col rounded-t-2xl bg-white shadow-xl sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <h2 id="add-product-title" className="text-base font-semibold text-slate-900">
            Добавить товары
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="cursor-pointer rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className="flex gap-2 border-b border-slate-200 px-5 py-3">
          {(
            [
              ["one", "Один товар", PackagePlus],
              ["csv", "Загрузить списком", FileUp],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium ${
                mode === value
                  ? "bg-slate-900 text-white"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {mode === "one" ? (
            <>
              <div className="rounded-xl bg-slate-50 p-3">
                <span className="mb-1 block text-xs font-medium text-slate-600">
                  Найти товар на Kaspi
                </span>
                <div className="flex gap-2">
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void search();
                      }
                    }}
                    placeholder="Например: iPhone 13 128Gb"
                    className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900"
                  />
                  <button
                    type="button"
                    onClick={() => void search()}
                    disabled={searching || query.trim().length < 2}
                    className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                  >
                    {searching ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                    Найти
                  </button>
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  Выберите карточку из списка — ID, название и бренд подставятся сами.
                </p>
                {found && found.length === 0 && (
                  <p className="mt-2 text-xs text-slate-500">Ничего не нашлось, уточните запрос.</p>
                )}
                {found && found.length > 0 && (
                  <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto">
                    {found.map((card) => (
                      <li key={card.kaspi_product_id}>
                        <button
                          type="button"
                          onClick={() => take(card)}
                          className="w-full cursor-pointer rounded-lg border border-slate-200 bg-white p-2 text-left hover:border-slate-400"
                        >
                          <span className="block truncate text-sm text-slate-900">{card.title}</span>
                          <span className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                            <span className="font-mono">{card.kaspi_product_id}</span>
                            {card.price && <span>{tenge(card.price)}</span>}
                            {card.rating !== null && (
                              <span className="inline-flex items-center gap-0.5">
                                <Star className="size-3" />
                                {card.rating.toFixed(1)}
                              </span>
                            )}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="SKU (ваш артикул)" value={sku} onChange={setSku} placeholder="IPH13-128" />
                <Field
                  label="ID карточки Kaspi"
                  value={kaspiId}
                  onChange={setKaspiId}
                  placeholder="102298404"
                  hint="Цифры из ссылки kaspi.kz/shop/p/…-102298404/"
                />
              </div>
              <Field
                label="Название"
                value={title}
                onChange={setTitle}
                placeholder="Apple iPhone 13 128Gb"
              />
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Бренд" value={brand} onChange={setBrand} placeholder="Apple" />
                <Field
                  label="Базовая цена"
                  value={basePrice}
                  onChange={setBasePrice}
                  placeholder="399000"
                />
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Склад" value={storeId} onChange={setStoreId} placeholder="PP1" />
                  <Field label="Остаток" value={stock} onChange={setStock} placeholder="1" />
                </div>
              </div>
              <p className="text-xs text-slate-500">
                Бренд и склад обязательны для прайс-листа Kaspi: без них товар в фид не попадёт.
                Стратегию и цены Min/Max можно задать здесь же — кнопкой «Настроить» в таблице,
                или сразу списком на вкладке загрузки.
              </p>
            </>
          ) : (
            <>
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-slate-900">
                  Вставьте таблицу (CSV), первая строка — заголовки
                </span>
                <textarea
                  value={csv}
                  onChange={(event) => setCsv(event.target.value)}
                  rows={10}
                  spellCheck={false}
                  placeholder={CSV_TEMPLATE}
                  className="w-full rounded-lg border border-slate-300 p-3 font-mono text-xs outline-none focus:border-slate-900"
                />
              </label>
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>
                  Одна строка — товар в одном городе. Повторите SKU, чтобы добавить ещё город
                  или склад. Колонки можно называть по-русски.
                </span>
                <button
                  type="button"
                  onClick={() => setCsv(CSV_TEMPLATE)}
                  className="cursor-pointer underline-offset-2 hover:underline"
                >
                  Вставить образец
                </button>
              </div>
            </>
          )}

          {error && (
            <p className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {error}
            </p>
          )}
          {done && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{done}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-lg px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Закрыть
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900"
      />
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}
