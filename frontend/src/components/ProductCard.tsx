"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ExternalLink, History, Package } from "lucide-react";
import { ActiveToggle } from "@/components/ActiveToggle";
import { PriceQuickEdit } from "@/components/PriceQuickEdit";
import { manageProduct } from "@/lib/client";
import { tenge, relativeTime } from "@/lib/format";
import type { ProductRules, Rule, ProductManagement } from "@/lib/types";

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <div className="catalog-cell"><dt className="catalog-label">{label}</dt><dd className="min-w-0 tabular">{children}</dd></div>;
}

function Margin({ price, cost }: { price?: string | null; cost: string | null }) {
  if (!price || !cost || Number(cost) <= 0) return null;
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
    try { await manageProduct(product.sku, changes); if ("purchase_price" in changes) setEditing(false); router.refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Не удалось сохранить"); }
    finally { setBusy(false); }
  }
  function openEditor() {
    setCost(product.purchase_price ?? ""); setStocks(product.availabilities); setEditing(!editing);
  }
  return <article className="product-card rounded-xl border border-slate-200 bg-white p-3 sm:p-4">
    <header className="flex items-start gap-3">
      <label className="flex min-h-8 shrink-0 items-center"><input type="checkbox" aria-label={`Выбрать ${product.sku}`} checked={selected} disabled={!rule} onChange={onSelect} className="size-4 accent-emerald-600" /></label>
      <Package aria-hidden="true" className="mt-1 hidden size-6 shrink-0 text-slate-400 sm:block" />
      <div className="min-w-0 flex-1">
        {product.kaspi_product_id ? <a className="break-words text-sm font-medium text-[#345c7f] hover:underline" href={`https://kaspi.kz/shop/p/-${product.kaspi_product_id}/`} target="_blank" rel="noreferrer">{product.title} <ExternalLink className="inline size-3" /></a> : <button onClick={onLink} className="text-left text-sm font-medium text-[#345c7f]">{product.title}</button>}
        <p className="mt-1 break-all text-xs leading-5 text-slate-500">Артикул: {product.sku}{product.brand && ` · ${product.brand}`}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">Цена закупки <button type="button" aria-label={`Изменить закупку ${product.sku}`} onClick={openEditor} className="editable-value min-h-9">{tenge(product.purchase_price)}</button></div>
      </div>
      <button onClick={onHistory} aria-label={`История цен ${product.sku}`} title="История цен" className="flex size-9 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-50"><History className="size-4" /></button>
    </header>
    <dl className="catalog-grid mt-2 rounded-lg bg-[#f7f8f8]">
      <Detail label="Город">{cityName}</Detail>
      <Detail label="Место"><span title={`Оценка по загруженным предложениям. Проверка: ${relativeTime(rule?.last_evaluated_at ?? null)}`}>{market?.position ? `${market.position} из ${market.offer_count}` : "—"}</span></Detail>
      <Detail label="Цена 1 места"><span>{tenge(market?.leader_price)}</span>{market?.leader_name ? <span className="mt-1 block break-words text-xs text-[#345c7f]">{market.leader_name}</span> : market?.leader_merchant_id ? <span className="mt-1 block break-all text-xs text-slate-500">ID {market.leader_merchant_id}</span> : null}</Detail>
      <Detail label="Текущая цена (XML) / Маржа"><span title={market?.observed_price ? `На Kaspi: ${tenge(market.observed_price)}. Проверка: ${relativeTime(rule?.last_evaluated_at ?? null)}` : "Цена для следующей загрузки Kaspi"}><PriceQuickEdit key={`${rule?.id}:${rule?.min_price}:${rule?.max_price}:${rule?.step}`} product={product} rule={rule} /></span><Margin price={rule?.current_price ?? product.base_price} cost={product.purchase_price} /></Detail>
      <Detail label="Мин. цена / Маржа"><PriceQuickEdit key={`min:${rule?.min_price}:${rule?.max_price}:${rule?.step}`} product={product} rule={rule} field="min_price" /><Margin price={rule?.min_price} cost={product.purchase_price} /></Detail>
      <Detail label="Макс. цена / Маржа"><PriceQuickEdit key={`max:${rule?.min_price}:${rule?.max_price}:${rule?.step}`} product={product} rule={rule} field="max_price" /><Margin price={rule?.max_price} cost={product.purchase_price} /></Detail>
      <Detail label="Шаг"><PriceQuickEdit key={`step:${rule?.min_price}:${rule?.max_price}:${rule?.step}`} product={product} rule={rule} field="step" /></Detail>
      <Detail label="Точки продаж / Остатки">
        {product.availabilities.map(stock => <div key={stock.store_id} className="mb-2 last:mb-0"><p className="break-all text-xs">{stock.store_id}</p><button type="button" aria-label={`Изменить остатки ${product.sku} ${stock.store_id}`} onClick={openEditor} className="editable-value min-h-8 text-xs">Остаток: {stock.stock_count === null ? "—" : `${stock.stock_count} шт.`}</button>{!stock.available && <p className="text-xs text-slate-500">Снят с продажи</p>}</div>)}
        {!product.availabilities.length && <button onClick={openEditor} className="editable-value text-xs">Нет складов</button>}
      </Detail>
      <Detail label="Действия">
        {product.availabilities.map(stock => <button key={stock.store_id} onClick={openEditor} title={stock.store_id} className="block min-h-8 text-xs text-slate-600">Предзаказ: <span className="editable-value">{stock.preorder_days ?? "—"} дн.</span></button>)}
        <label className="catalog-direction"><input type="checkbox" checked={product.auto_decrease} disabled={busy} onChange={event => void save({auto_decrease: event.target.checked})} />Автоснижение</label>
        <label className="catalog-direction" title="Повышать цену до цели стратегии, не выше Max"><input type="checkbox" checked={product.auto_increase} disabled={busy} onChange={event => void save({auto_increase: event.target.checked})} />Автоповышение</label>
        <div className="flex items-center justify-end gap-1 lg:justify-start"><span className="text-xs text-slate-500">Бот</span>{rule?.strategy === "manual" || !rule ? <a href={`/strategies?sku=${encodeURIComponent(product.sku)}`} className="editable-value text-xs">Настроить</a> : <ActiveToggle key={`${rule.id}:${rule.is_active}`} ruleIds={[rule.id]} isActive={rule.is_active} label={`Репрайсер для ${product.sku}`} onError={setError} />}</div>
      </Detail>
    </dl>
    <details className="mt-2 text-xs text-slate-500"><summary className="w-fit cursor-pointer py-1">Подробнее</summary><p className="mt-2 leading-5">Проверка: {relativeTime(rule?.last_evaluated_at ?? null)}. На Kaspi: {tenge(market?.observed_price)}. Маржа рассчитана до комиссии, налогов и доставки. Автоснижение и автоповышение действуют при включённом боте товара.</p></details>
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
