"use client";

import { useState } from "react";
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

  return <div className="group relative inline-block text-right" onMouseEnter={() => setOpen(true)} onMouseLeave={() => { if (!saving) setOpen(false); }}>
    <button type="button" onClick={() => setOpen(true)} aria-expanded={open} aria-label={`Изменить Min, Max и шаг для ${product.sku}`} className="inline-flex cursor-pointer items-center gap-1 font-medium text-slate-900 hover:text-blue-700">
      {tenge(rule?.current_price)} <Pencil className="size-3 text-slate-400 group-hover:text-blue-600" />
    </button>
    {open && <div className="absolute right-0 top-full z-30 w-64 rounded-xl border border-slate-200 bg-white p-3 text-left shadow-xl" onClick={(event) => event.stopPropagation()}>
      <p className="mb-2 text-xs font-semibold text-slate-900">Цены товара · {product.sku}</p>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-slate-600">Min, ₸<input aria-label={`Min ${product.sku}`} type="number" min={1} value={minimum} onChange={(event) => setMinimum(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" /></label>
        <label className="text-xs text-slate-600">Max, ₸<input aria-label={`Max ${product.sku}`} type="number" min={1} value={maximum} onChange={(event) => setMaximum(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" /></label>
      </div>
      <label className="mt-2 block text-xs text-slate-600">Шаг, ₸<input aria-label={`Шаг ${product.sku}`} type="number" min={1} step={1} value={step} onChange={(event) => setStep(event.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" /></label>
      {error && <p role="alert" className="mt-2 text-xs text-rose-700">{error}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" onClick={() => setOpen(false)} className="cursor-pointer px-2 py-1 text-xs text-slate-500">Закрыть</button>
        <button type="button" onClick={() => void save()} disabled={saving} className="inline-flex cursor-pointer items-center gap-1 rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">{saving && <Loader2 className="size-3 animate-spin" />}Сохранить</button>
      </div>
    </div>}
  </div>;
}
