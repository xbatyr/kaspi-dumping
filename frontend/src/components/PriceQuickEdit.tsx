"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Pencil } from "lucide-react";

import { saveProductLimits } from "@/lib/client";
import { tenge } from "@/lib/format";
import type { PriceLimitsDraft, ProductRules, Rule } from "@/lib/types";

/** The price percentages are taken from: the merchant's own price, not the one
 *  the bot last published — otherwise the floor would drift down with it. */
function anchorPrice(product: ProductRules, rule?: Rule): number | null {
  const value = Number(product.base_price ?? rule?.current_price ?? "");
  return Number.isFinite(value) && value > 0 ? value : null;
}

function fromPercent(anchor: number, percent: string, direction: "down" | "up"): number | null {
  const share = Number(percent);
  if (!Number.isFinite(share) || share < 0) return null;
  const value = anchor * (direction === "down" ? 1 - share / 100 : 1 + share / 100);
  return direction === "down" ? Math.ceil(value) : Math.floor(value);
}

export function PriceQuickEdit({ product, rule, field }: { product: ProductRules; rule?: Rule; field?: "min_price" | "max_price" | "step" }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [unit, setUnit] = useState<"tenge" | "percent">(
    rule?.min_percent || rule?.max_percent ? "percent" : "tenge",
  );
  const [minimum, setMinimum] = useState(rule?.min_price ?? "");
  const [maximum, setMaximum] = useState(rule?.max_price ?? "");
  const [minPercent, setMinPercent] = useState(rule?.min_percent ?? "");
  const [maxPercent, setMaxPercent] = useState(rule?.max_percent ?? "");
  const [step, setStep] = useState(String(rule?.step ?? 1));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const anchor = anchorPrice(product, rule);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  function draft(): PriceLimitsDraft | string {
    if (!Number.isInteger(Number(step)) || Number(step) < 1) return "Шаг должен быть целым числом от 1 ₸";
    if (unit === "tenge") {
      if (!Number.isFinite(Number(minimum)) || Number(minimum) <= 0 || !Number.isFinite(Number(maximum)) || Number(maximum) <= Number(minimum)) {
        return "Max должен быть больше Min, обе цены положительные";
      }
      return { min_price: minimum, max_price: maximum, step: Number(step) };
    }
    if (anchor === null) return "У товара нет своей цены — задайте границы в тенге";
    const low = fromPercent(anchor, minPercent, "down");
    const high = fromPercent(anchor, maxPercent, "up");
    if (low === null || high === null) return "Проценты должны быть числами от 0";
    if (Number(minPercent) > 90 || Number(maxPercent) > 500) return "Слишком большой процент: вниз до 90%, вверх до 500%";
    if (high <= low) return "Максимум должен быть выше минимума";
    return { min_percent: minPercent, max_percent: maxPercent, step: Number(step) };
  }

  async function save() {
    if (!product.kaspi_product_id) { setError("Сначала привяжите карточку Kaspi"); return; }
    const payload = draft();
    if (typeof payload === "string") { setError(payload); return; }
    setSaving(true); setError(null);
    try {
      await saveProductLimits(product.sku, payload);
      setOpen(false);
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось сохранить цены");
    } finally { setSaving(false); }
  }

  const preview = unit === "percent" && anchor !== null
    ? { low: fromPercent(anchor, minPercent, "down"), high: fromPercent(anchor, maxPercent, "up") }
    : null;

  return <div className="group relative inline-block text-right" onMouseEnter={() => { if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) setOpen(true); }} onMouseLeave={() => { if (!saving && window.matchMedia("(hover: hover) and (pointer: fine)").matches) setOpen(false); }}>
    <button type="button" onClick={() => setOpen(true)} aria-expanded={open} aria-label={`Изменить Min, Max и шаг для ${product.sku}`} className="inline-flex min-h-11 cursor-pointer items-center gap-1 font-medium text-slate-900 hover:text-blue-700 md:min-h-0">
      {tenge(field ? String(rule?.[field] ?? "") || null : rule?.current_price ?? product.base_price)}
      {field !== "step" && rule?.[field === "max_price" ? "max_percent" : "min_percent"] && field
        ? <span className="text-xs font-normal text-slate-500"> ({field === "max_price" ? "+" : "−"}{Number(rule[field === "max_price" ? "max_percent" : "min_percent"])}%)</span>
        : null}
      <Pencil className="size-3 text-slate-400 group-hover:text-blue-600" />
    </button>
    {open && <>
      <button type="button" aria-label="Закрыть редактирование цены" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-slate-900/40 md:hidden" />
      <div role="dialog" aria-label={`Цены товара ${product.sku}`} className="fixed inset-x-0 bottom-0 z-50 max-h-[90dvh] overflow-y-auto rounded-t-2xl bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] text-left shadow-xl md:absolute md:inset-x-auto md:right-0 md:top-full md:bottom-auto md:w-72 md:overflow-visible md:rounded-xl md:border md:border-slate-200 md:p-3" onClick={(event) => event.stopPropagation()}>
      <p className="mb-3 text-base font-semibold text-slate-900 md:mb-2 md:text-xs">Цены товара · {product.sku}</p>
      <div role="group" aria-label="Единицы границ" className="mb-3 flex rounded-lg border border-slate-200 p-0.5 text-xs md:mb-2">
        {([["tenge", "В тенге"], ["percent", "В процентах"]] as const).map(([value, label]) =>
          <button key={value} type="button" onClick={() => setUnit(value)} aria-pressed={unit === value}
            className={`min-h-9 flex-1 cursor-pointer rounded-md px-2 md:min-h-0 md:py-1 ${unit === value ? "bg-slate-900 text-white" : "text-slate-600"}`}>{label}</button>)}
      </div>
      {unit === "tenge" ? <div className="grid grid-cols-2 gap-2">
        <label className="min-w-0 text-sm text-slate-600 md:text-xs">Min, ₸<input aria-label={`Min ${product.sku}`} type="number" min={1} value={minimum} onChange={(event) => setMinimum(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-base md:py-1.5 md:text-sm" /></label>
        <label className="min-w-0 text-sm text-slate-600 md:text-xs">Max, ₸<input aria-label={`Max ${product.sku}`} type="number" min={1} value={maximum} onChange={(event) => setMaximum(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-base md:py-1.5 md:text-sm" /></label>
      </div> : <>
        <div className="grid grid-cols-2 gap-2">
          <label className="min-w-0 text-sm text-slate-600 md:text-xs">Ниже цены, %<input aria-label={`Минимум в процентах ${product.sku}`} type="number" min={0} max={90} step="0.1" value={minPercent} onChange={(event) => setMinPercent(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-base md:py-1.5 md:text-sm" /></label>
          <label className="min-w-0 text-sm text-slate-600 md:text-xs">Выше цены, %<input aria-label={`Максимум в процентах ${product.sku}`} type="number" min={0} max={500} step="0.1" value={maxPercent} onChange={(event) => setMaxPercent(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-base md:py-1.5 md:text-sm" /></label>
        </div>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          {anchor === null
            ? "У товара нет своей цены — проценты считать не от чего."
            : <>От вашей цены {tenge(String(anchor))}: {preview?.low ? tenge(String(preview.low)) : "—"} … {preview?.high ? tenge(String(preview.high)) : "—"}. Границы пересчитаются, когда изменится ваша цена.</>}
        </p>
      </>}
      <label className="mt-3 block text-sm text-slate-600 md:mt-2 md:text-xs">Шаг, ₸<input aria-label={`Шаг ${product.sku}`} type="number" min={1} step={1} value={step} onChange={(event) => setStep(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-base md:py-1.5 md:text-sm" /></label>
      {error && <p role="alert" className="mt-2 text-xs text-rose-700">{error}</p>}
      <div className="mt-4 flex justify-end gap-2 md:mt-3">
        <button type="button" onClick={() => setOpen(false)} className="min-h-11 cursor-pointer px-3 text-sm text-slate-600 md:min-h-0 md:px-2 md:py-1 md:text-xs">Закрыть</button>
        <button type="button" onClick={() => void save()} disabled={saving} className="inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-md bg-slate-900 px-4 text-sm font-medium text-white disabled:opacity-50 md:min-h-0 md:px-3 md:py-1.5 md:text-xs">{saving && <Loader2 className="size-3 animate-spin" />}Сохранить</button>
      </div>
      </div>
    </>}
  </div>;
}
