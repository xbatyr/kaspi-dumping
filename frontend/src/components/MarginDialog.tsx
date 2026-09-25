"use client";

import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";

import { previewMargin } from "@/lib/client";
import { tenge } from "@/lib/format";
import type { Margin, ProductRules } from "@/lib/types";

function Row({ label, value, tone }: { label: string; value: string; tone?: "bad" | "good" }) {
  return <div className="flex items-baseline justify-between gap-4 border-b border-slate-100 py-2 last:border-0">
    <span className="text-sm text-slate-600">{label}</span>
    <span className={`tabular text-sm font-medium ${tone === "bad" ? "text-rose-700" : tone === "good" ? "text-emerald-700" : "text-slate-900"}`}>{value}</span>
  </div>;
}

/** Plays with the numbers before they are saved anywhere: the merchant asks
 *  "what do I actually earn at this price?" and gets the whole breakdown. */
export function MarginDialog({ product, onClose }: { product: ProductRules; onClose: () => void }) {
  const [price, setPrice] = useState(product.rules[0]?.current_price ?? product.base_price ?? "");
  const [cost, setCost] = useState(product.purchase_price ?? "");
  const [commission, setCommission] = useState(product.commission_percent ?? "");
  const [delivery, setDelivery] = useState(product.delivery_cost ?? "");
  const [result, setResult] = useState<Margin | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!Number.isFinite(Number(price)) || Number(price) <= 0) { setResult(null); return; }
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        setResult(await previewMargin({
          price: String(price),
          sku: product.sku,
          ...(cost === "" ? {} : { purchase_price: String(cost) }),
          ...(commission === "" ? {} : { commission_percent: String(commission) }),
          ...(delivery === "" ? {} : { delivery_cost: String(delivery) }),
        }));
        setError(null);
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : "Не удалось посчитать");
      } finally { setBusy(false); }
    }, 300);
    return () => clearTimeout(timer);
  }, [price, cost, commission, delivery, product.sku]);

  const loss = result !== null && Number(result.profit) < 0;

  return <div role="dialog" aria-modal="true" aria-label={`Маржинальность ${product.sku}`} className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 sm:items-center sm:p-4">
    <div className="max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl bg-white p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] shadow-xl sm:rounded-2xl">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-slate-900">Расчёт маржинальности</h2>
          <p className="mt-1 truncate text-sm text-slate-500">{product.title}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100"><X className="size-5" /></button>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-sm text-slate-700">Цена продажи, ₸<input autoFocus type="number" min={1} value={price} onChange={(event) => setPrice(event.target.value)} className="catalog-input mt-1" /></label>
        <label className="text-sm text-slate-700">Себестоимость, ₸<input type="number" min={0} value={cost} onChange={(event) => setCost(event.target.value)} placeholder="Не указана" className="catalog-input mt-1" /></label>
        <label className="text-sm text-slate-700">Комиссия Kaspi, %<input type="number" min={0} max={100} step="0.1" value={commission} onChange={(event) => setCommission(event.target.value)} placeholder="Из настроек магазина" className="catalog-input mt-1" /></label>
        <label className="text-sm text-slate-700">Доставка, ₸<input type="number" min={0} value={delivery} onChange={(event) => setDelivery(event.target.value)} placeholder="Из настроек магазина" className="catalog-input mt-1" /></label>
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-500">Пустое поле берётся из настроек магазина: там же задаётся налог (по умолчанию 3%).</p>

      {error && <p role="alert" className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}

      {result && <div className="mt-4 rounded-xl border border-slate-200 p-4">
        <Row label="Цена продажи" value={tenge(result.price)} />
        <Row label="Комиссия Kaspi" value={`− ${tenge(result.commission)}`} />
        <Row label="Налог" value={`− ${tenge(result.tax)}`} />
        <Row label="Доставка" value={`− ${tenge(result.delivery)}`} />
        <Row label="Себестоимость" value={`− ${tenge(result.purchase_price)}`} />
        <Row label="Прибыль с продажи" value={`${tenge(result.profit)} · ${Number(result.margin_percent).toFixed(1)}%`} tone={loss ? "bad" : "good"} />
        {result.markup_percent !== null && <Row label="Наценка к себестоимости" value={`${Number(result.markup_percent).toFixed(1)}%`} />}
        {result.break_even_price !== null && <Row label="Цена без убытка" value={tenge(result.break_even_price)} />}
        {result.estimated && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-900">Себестоимость не указана, поэтому прибыль завышена: укажите её в карточке товара.</p>}
        {result.break_even_price !== null && <p className="mt-3 text-xs leading-5 text-slate-500">Минимальную цену товара разумно держать не ниже {tenge(result.break_even_price)} — на этой цене вы выходите в ноль.</p>}
      </div>}
      {busy && !result && <p className="mt-4 flex items-center gap-2 text-sm text-slate-500"><Loader2 className="size-4 animate-spin" />Считаю…</p>}
    </div>
  </div>;
}
