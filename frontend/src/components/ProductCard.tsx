"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ExternalLink, History, Package, Pencil } from "lucide-react";
import { ActiveToggle } from "@/components/ActiveToggle";
import { PriceQuickEdit } from "@/components/PriceQuickEdit";
import { manageProduct } from "@/lib/client";
import { tenge, relativeTime } from "@/lib/format";
import { STRATEGY_LABELS } from "@/lib/strategies";
import type { ProductRules, Rule, ProductManagement } from "@/lib/types";

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <div className="flex min-w-0 items-center justify-between gap-4 border-b border-slate-200/60 py-3 last:border-0"><dt className="text-sm text-slate-500">{label}</dt><dd className="min-w-0 text-right text-sm tabular text-slate-800">{children}</dd></div>;
}

function Margin({ price, cost }: { price?: string | null; cost: string | null }) {
  if (!price || !cost || Number(cost) <= 0) return <div className="text-xs text-slate-500">закупка не указана</div>;
  const value = Number(price) - Number(cost);
  return <div className={`mt-1 text-xs ${value < 0 ? "text-rose-700" : "text-emerald-700"}`} title="(Цена − закупка) / цена. Без комиссии, налогов и доставки.">{tenge(String(value))} · {(value / Number(price) * 100).toFixed(1)}%</div>;
}

export function ProductCard({ product, rule, cityName, selected, onSelect, onHistory, onLink }: {
  product: ProductRules; rule?: Rule; cityName: string; selected: boolean;
  onSelect: () => void; onHistory: () => void; onLink: () => void;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [cost, setCost] = useState(product.purchase_price ?? "");
  const [stocks, setStocks] = useState(product.availabilities);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const market = rule?.market_snapshot;
  async function save(changes: ProductManagement) {
    setBusy(true); setError(null);
    try { await manageProduct(product.sku, changes); setEditing(false); router.refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Не удалось сохранить"); }
    finally { setBusy(false); }
  }
  function openEditor() {
    setCost(product.purchase_price ?? ""); setStocks(product.availabilities); setEditing(!editing);
  }
  return <article className="product-card rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="min-w-0">
        <div className="flex items-start gap-3">
          <label className="flex min-h-11 shrink-0 items-center"><input type="checkbox" aria-label={`Выбрать ${product.sku}`} checked={selected} disabled={!rule} onChange={onSelect} className="size-5 accent-emerald-600" /></label>
          <div className="hidden size-11 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-slate-400 sm:flex"><Package className="size-6" /></div>
          <div className="min-w-0 pt-1">
            {product.kaspi_product_id ? <a className="break-words text-base font-medium text-[#345c7f] hover:underline" href={`https://kaspi.kz/shop/p/-${product.kaspi_product_id}/`} target="_blank" rel="noreferrer">{product.title} <ExternalLink className="inline size-3" /></a> : <button onClick={onLink} className="text-left text-base font-medium text-[#345c7f]">{product.title}</button>}
            <p className="mt-3 break-all text-sm text-slate-500">Артикул: {product.sku}</p>
            {product.brand && <p className="mt-1 text-sm text-slate-500">Бренд: {product.brand}</p>}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm"><span>Цена закупки</span><button type="button" onClick={openEditor} className="editable-value min-h-11">{tenge(product.purchase_price)} <Pencil className="ml-1 inline size-3" /></button></div>
        <p className="text-xs leading-5 text-slate-500">Маржа ниже — до комиссии, налогов и доставки.</p>
        <div className="mt-4 flex items-center justify-between gap-2 border-t border-slate-100 pt-4"><span className="text-sm text-slate-600">Автоматический бот</span>{rule?.strategy === "manual" ? <a href={`/strategies?sku=${encodeURIComponent(product.sku)}`} className="editable-value text-sm">Настроить цены</a> : <ActiveToggle ruleIds={rule ? [rule.id] : []} isActive={rule?.is_active ?? false} label={`Репрайсер для ${product.sku}`} onError={setError} />}</div>
        {rule && <p className="mt-2 text-xs text-slate-500">{STRATEGY_LABELS[rule.strategy]}</p>}
        <button onClick={onHistory} className="mt-3 inline-flex min-h-11 items-center gap-2 text-sm text-slate-600 hover:text-emerald-800"><History className="size-4" />История цен</button>
      </div>
      <dl className="min-w-0 rounded-xl bg-[#f7f8f8] px-4">
        <Detail label="Город">{cityName}</Detail>
        <Detail label="Место по цене"><span title="Оценка по цене и рейтингу среди загруженных предложений Kaspi">{market?.position ? `${market.position} из ${market.offer_count}` : "Ещё нет данных"}</span></Detail>
        <Detail label="Цена первого места">{tenge(market?.leader_price)}</Detail>
        <Detail label="Цена на Kaspi">{tenge(market?.observed_price)}</Detail>
        <Detail label="Цена в XML / Маржа"><PriceQuickEdit key={`${rule?.id}:${rule?.min_price}:${rule?.max_price}:${rule?.step}`} product={product} rule={rule} /><Margin price={rule?.current_price ?? product.base_price} cost={product.purchase_price} /></Detail>
        <Detail label="Мин. цена / Маржа">{tenge(rule?.min_price)}<Margin price={rule?.min_price} cost={product.purchase_price} /></Detail>
        <Detail label="Макс. цена / Маржа">{tenge(rule?.max_price)}<Margin price={rule?.max_price} cost={product.purchase_price} /></Detail>
        <Detail label="Шаг">{rule ? tenge(String(rule.step)) : "—"}</Detail>
      </dl>
      <div className="min-w-0 rounded-xl bg-[#f7f8f8] px-4 pb-4">
        <div className="flex items-center justify-between gap-3 py-3"><h3 className="text-sm text-slate-500">Точки продаж / Остатки</h3><button aria-label={`Изменить остатки ${product.sku}`} onClick={openEditor} className="flex size-11 items-center justify-center text-[#345c7f]"><Pencil className="size-4" /></button></div>
        {product.availabilities.map(stock => <div key={stock.store_id} className="border-b border-slate-200/60 py-3 text-sm"><p className="break-all font-medium">{stock.store_id}</p><div className="mt-2 flex justify-between gap-3 text-slate-600"><span>Остаток</span><span>{stock.stock_count === null ? "Не указан" : `${stock.stock_count} шт.`}</span></div><div className="mt-2 flex justify-between gap-3 text-slate-600"><span>Предзаказ</span><span>{stock.preorder_days == null ? "Не указан" : `${stock.preorder_days} дн.`}</span></div><p className={`mt-2 text-xs ${stock.available ? "text-emerald-700" : "text-slate-500"}`}>{stock.available ? "Доступен для продажи" : "Снят с продажи на складе"}</p></div>)}
        {!product.availabilities.length && <p className="py-3 text-sm text-amber-800">Импортируйте склады из XML магазина.</p>}
        <div className="mt-3 space-y-1">
          <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm"><input type="checkbox" className="size-5 accent-emerald-600" checked={product.auto_decrease} disabled={busy} onChange={event => void save({auto_decrease: event.target.checked})} />Вкл. автоснижение</label>
          <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm"><input type="checkbox" className="size-5 accent-emerald-600" checked={product.auto_increase} disabled={busy} onChange={event => void save({auto_increase: event.target.checked})} />Вкл. автоповышение</label>
          <p className="text-xs leading-5 text-slate-500">{product.auto_increase ? "Может повысить цену до цели стратегии, в пределах Max." : "Если цена XML уже обеспечивает первое место, она сохраняется."}</p>
          <p className="mt-2 text-xs text-slate-500">Проверка: {relativeTime(rule?.last_evaluated_at ?? null)}</p>
        </div>
      </div>
    </div>
    {error && <p role="alert" className="mt-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
    {editing && <form className="mt-5 border-t border-slate-200 pt-5" onSubmit={event => { event.preventDefault(); void save({ purchase_price: cost === "" ? null : cost, ...(stocks.length ? {availabilities: stocks} : {}) }); }}>
      <h3 className="mb-4 font-medium">Закупка и остатки · {product.sku}</h3>
      <label className="block max-w-xs text-sm text-slate-600">Цена закупки, ₸<input autoFocus type="number" min="0" step="0.01" value={cost} onChange={event => setCost(event.target.value)} className="catalog-input mt-1" placeholder="Не указана" /></label>
      {stocks.map((stock, index) => <fieldset key={stock.store_id} className="mt-4 grid gap-3 sm:grid-cols-3"><legend className="mb-2 break-all text-sm font-medium">{stock.store_id}</legend>
        <label className="text-sm text-slate-600">Остаток, шт.<input className="catalog-input mt-1" type="number" min="0" step="1" value={stock.stock_count ?? ""} onChange={event => setStocks(stocks.map((item, i) => i === index ? {...item, stock_count: event.target.value === "" ? null : Number(event.target.value)} : item))} /></label>
        <label className="text-sm text-slate-600">Предзаказ, дней (0–30)<input className="catalog-input mt-1" type="number" min="0" max="30" step="1" value={stock.preorder_days ?? ""} onChange={event => setStocks(stocks.map((item, i) => i === index ? {...item, preorder_days: event.target.value === "" ? null : Number(event.target.value)} : item))} /></label>
        <label className="flex min-h-11 items-center gap-2 self-end text-sm"><input className="size-5 accent-emerald-600" type="checkbox" checked={stock.available} onChange={event => setStocks(stocks.map((item, i) => i === index ? {...item, available: event.target.checked} : item))} />Доступен для продажи</label>
      </fieldset>)}
      <p className="mt-4 text-xs leading-5 text-slate-500">Остатки и предзаказ попадут в следующий XML. Kaspi применит их после загрузки прайс-листа. Изменение закупки не меняет Min автоматически.</p>
      <div className="mt-4 flex justify-end gap-3"><button type="button" disabled={busy} onClick={() => setEditing(false)} className="min-h-11 px-4 text-sm">Отмена</button><button disabled={busy} className="min-h-11 rounded-lg bg-emerald-700 px-5 text-sm font-medium text-white disabled:opacity-50">{busy ? "Сохраняю…" : "Сохранить"}</button></div>
    </form>}
  </article>;
}
