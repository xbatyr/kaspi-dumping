"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Pencil } from "lucide-react";

import { saveProductLimits } from "@/lib/client";
import { tenge } from "@/lib/format";
import type { ProductRules, Rule } from "@/lib/types";

export function PriceQuickEdit({ product, rule }: { product: ProductRules; rule?: Rule }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [minimum, setMinimum] = useState(rule?.min_price ?? "");
  const [maximum, setMaximum] = useState(rule?.max_price ?? "");
  const [step, setStep] = useState(String(rule?.step ?? 1));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  async function save() {
    if (!product.kaspi_product_id) { setError("Сначала привяжите карточку Kaspi"); return; }
    if (!Number.isFinite(Number(minimum)) || Number(minimum) <= 0 || !Number.isFinite(Number(maximum)) || Number(maximum) <= Number(minimum)) {
      setError("Max должен быть больше Min, обе цены положительные"); return;
    }
    if (!Number.isInteger(Number(step)) || Number(step) < 1) { setError("Шаг должен быть целым числом от 1 ₸"); return; }
    setSaving(true); setError(null);
    try {
      await saveProductLimits(product.sku, minimum, maximum, Number(step));
      setOpen(false);
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось сохранить цены");
    } finally { setSaving(false); }
  }

  return <div className="group relative inline-block text-right" onMouseEnter={() => { if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) setOpen(true); }} onMouseLeave={() => { if (!saving && window.matchMedia("(hover: hover) and (pointer: fine)").matches) setOpen(false); }}>
    <button type="button" onClick={() => setOpen(true)} aria-expanded={open} aria-label={`Изменить Min, Max и шаг для ${product.sku}`} className="inline-flex min-h-11 cursor-pointer items-center gap-1 font-medium text-slate-900 hover:text-blue-700 md:min-h-0">
      {tenge(rule?.current_price)} <Pencil className="size-3 text-slate-400 group-hover:text-blue-600" />
    </button>
    {open && <>
      <button type="button" aria-label="Закрыть редактирование цены" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-slate-900/40 md:hidden" />
      <div role="dialog" aria-label={`Цены товара ${product.sku}`} className="fixed inset-x-0 bottom-0 z-50 max-h-[90dvh] overflow-y-auto rounded-t-2xl bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] text-left shadow-xl md:absolute md:inset-x-auto md:right-0 md:top-full md:bottom-auto md:w-64 md:overflow-visible md:rounded-xl md:border md:border-slate-200 md:p-3" onClick={(event) => event.stopPropagation()}>
      <p className="mb-3 text-base font-semibold text-slate-900 md:mb-2 md:text-xs">Цены товара · {product.sku}</p>
      <div className="grid grid-cols-2 gap-2">
        <label className="min-w-0 text-sm text-slate-600 md:text-xs">Min, ₸<input aria-label={`Min ${product.sku}`} type="number" min={1} value={minimum} onChange={(event) => setMinimum(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-base md:py-1.5 md:text-sm" /></label>
        <label className="min-w-0 text-sm text-slate-600 md:text-xs">Max, ₸<input aria-label={`Max ${product.sku}`} type="number" min={1} value={maximum} onChange={(event) => setMaximum(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 text-base md:py-1.5 md:text-sm" /></label>
      </div>
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
