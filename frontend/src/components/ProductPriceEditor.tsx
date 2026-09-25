"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";

import { saveProductLimits } from "@/lib/client";
import { tenge } from "@/lib/format";
import type { ProductRules } from "@/lib/types";

export function ProductPriceEditor({ product }: { product: ProductRules }) {
  const router = useRouter();
  const configured = product.rules.find((rule) => Number(rule.max_price) > Number(rule.min_price));
  const source = configured ?? product.rules[0];
  const [minimum, setMinimum] = useState(configured?.min_price ?? "");
  const [maximum, setMaximum] = useState(configured?.max_price ?? "");
  const [step, setStep] = useState(String(source?.step ?? 1));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    if (!Number.isFinite(Number(minimum)) || Number(minimum) <= 0 || !Number.isFinite(Number(maximum)) || Number(maximum) <= Number(minimum)) {
      setError("Укажите минимальную цену и максимальную цену выше неё"); return;
    }
    if (!Number.isInteger(Number(step)) || Number(step) < 1) { setError("Шаг должен быть целым числом от 1 ₸"); return; }
    setSaving(true); setError(null); setSaved(false);
    try {
      await saveProductLimits(product.sku, minimum, maximum, Number(step));
      setSaved(true);
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось сохранить цены");
    } finally { setSaving(false); }
  }

  return <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
    <div className="border-b border-slate-200 px-5 py-4">
      <h3 className="truncate font-semibold text-slate-900">Цены товара: {product.title}</h3>
      <p className="mt-1 font-mono text-xs text-slate-500">SKU {product.sku}</p>
    </div>
    <div className="space-y-4 px-5 py-5">
      <p className="text-sm text-slate-600">Текущая цена в прайсе: <b>{tenge(source?.current_price)}</b></p>
      {!configured && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Для этого товара демпинг ещё не настроен. Укажите безопасный минимум и верхнюю границу цены.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm text-slate-700">Минимальная цена, ₸<input type="number" min={1} value={minimum} onChange={(event) => setMinimum(event.target.value)} placeholder="Себестоимость + расходы" className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 px-3 py-2 text-base sm:text-sm" /></label>
        <label className="text-sm text-slate-700">Максимальная цена, ₸<input type="number" min={1} value={maximum} onChange={(event) => setMaximum(event.target.value)} placeholder="Верхняя граница" className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 px-3 py-2 text-base sm:text-sm" /></label>
      </div>
      <label className="block text-sm text-slate-700">Шаг изменения цены, ₸<input type="number" min={1} step={1} value={step} onChange={(event) => setStep(event.target.value)} className="mt-1 block min-h-11 w-28 rounded-lg border border-slate-300 px-3 py-2 text-base sm:text-sm" /></label>
      <p className="text-xs text-slate-500">Общая стратегия никогда не опустит цену ниже Min. Эти границы применятся к выбранным выше городам.</p>
      {error && <p className="flex gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertTriangle className="size-4 shrink-0" />{error}</p>}
      {saved && <p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">Ценовые границы сохранены.</p>}
    </div>
    <div className="flex justify-end border-t border-slate-200 px-5 py-4"><button type="button" onClick={save} disabled={saving} className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{saving && <Loader2 className="size-4 animate-spin" />}Сохранить цены товара</button></div>
  </section>;
}
