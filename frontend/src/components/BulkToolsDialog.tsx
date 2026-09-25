"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2, X } from "lucide-react";

import { runBulkTools } from "@/lib/client";
import type { BulkTools, BulkToolsResult } from "@/lib/types";

/** One switch with the field it reveals, so the dialog reads as a list of
 *  actions rather than a form of numbers that may or may not apply. */
function Tool({ on, onToggle, title, hint, children }: {
  on: boolean; onToggle: (value: boolean) => void; title: string; hint?: string; children?: ReactNode;
}) {
  return <div className="border-b border-slate-100 py-3 last:border-0">
    <label className="flex cursor-pointer items-start gap-3">
      <input type="checkbox" checked={on} onChange={(event) => onToggle(event.target.checked)} className="mt-0.5 size-5 shrink-0 accent-emerald-600" />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-slate-900">{title}</span>
        {hint && <span className="mt-0.5 block text-xs leading-5 text-slate-500">{hint}</span>}
      </span>
    </label>
    {on && children && <div className="mt-3 pl-8">{children}</div>}
  </div>;
}

export function BulkToolsDialog({ skus, onClose }: { skus: string[]; onClose: () => void }) {
  const router = useRouter();
  const [setMin, setSetMin] = useState(false);
  const [minPercent, setMinPercent] = useState("10");
  const [setMax, setSetMax] = useState(false);
  const [maxPercent, setMaxPercent] = useState("10");
  const [raise, setRaise] = useState(false);
  const [stopOffSale, setStopOffSale] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkToolsResult | null>(null);

  const chosen = setMin || setMax || raise || stopOffSale;
  const scope = skus.length ? `выбранным товарам (${skus.length})` : "всем товарам магазина";

  async function run() {
    const tools: BulkTools = { ...(skus.length ? { skus } : {}) };
    if (setMin) tools.set_min_percent = minPercent;
    if (setMax) tools.set_max_percent = maxPercent;
    if (raise) tools.raise_to_max = true;
    if (stopOffSale) tools.disable_decrease_when_off_sale = true;
    setRunning(true); setError(null);
    try {
      setResult(await runBulkTools(tools));
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось выполнить");
    } finally { setRunning(false); }
  }

  return <div role="dialog" aria-modal="true" aria-label="Массовые настройки" className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 p-0 sm:items-center sm:p-4">
    <div className="max-h-[92dvh] w-full max-w-xl overflow-y-auto rounded-t-2xl bg-white p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] shadow-xl sm:rounded-2xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Массовые настройки</h2>
          <p className="mt-1 text-sm text-slate-500">Применится к {scope}.</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100"><X className="size-5" /></button>
      </div>

      <div className="mt-4">
        <Tool on={setMin} onToggle={setSetMin}
          title="Установить автоснижение и минимальные цены"
          hint="Минимум считается от вашей цены и дальше следует за ней. Автоснижение включится.">
          <label className="block text-sm text-slate-700">Ниже вашей цены, %
            <input type="number" min={0} max={90} step="0.1" value={minPercent} onChange={(event) => setMinPercent(event.target.value)} className="catalog-input mt-1 max-w-32" />
          </label>
        </Tool>
        <Tool on={setMax} onToggle={setSetMax}
          title="Установить автоповышение и максимальные цены"
          hint="Максимум считается от вашей цены. Автоповышение включится.">
          <label className="block text-sm text-slate-700">Выше вашей цены, %
            <input type="number" min={0} max={500} step="0.1" value={maxPercent} onChange={(event) => setMaxPercent(event.target.value)} className="catalog-input mt-1 max-w-32" />
          </label>
        </Tool>
        <Tool on={raise} onToggle={setRaise}
          title="Поднять текущие цены до максимальных"
          hint="Цена в следующем прайсе станет равна максимальной. Если конкурент ещё на месте, бот снова опустит её на ближайшем проходе." />
        <Tool on={stopOffSale} onToggle={setStopOffSale}
          title="Отключить автоснижение у товаров, снятых с продажи"
          hint="Снят с продажи — выключен или нигде нет остатка. Такие товары перестанут дешеветь впустую." />
      </div>

      {error && <p role="alert" className="mt-4 flex gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertTriangle className="size-4 shrink-0" />{error}</p>}
      {result && <div role="status" className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900">
        <p>Просмотрено товаров: <b>{result.products_seen}</b>.</p>
        <ul className="mt-1 space-y-0.5 text-emerald-800">
          {result.limits_set > 0 && <li>Границы заданы: {result.limits_set}</li>}
          {result.prices_raised > 0 && <li>Цены подняты до максимума: {result.prices_raised}</li>}
          {result.decrease_disabled > 0 && <li>Автоснижение выключено: {result.decrease_disabled}</li>}
          {Object.entries(result.skipped).map(([reason, count]) => <li key={reason} className="text-amber-800">Пропущено ({reason}): {count}</li>)}
        </ul>
      </div>}

      <div className="mt-5 flex justify-end gap-3">
        <button type="button" onClick={onClose} className="min-h-11 cursor-pointer px-4 text-sm text-slate-600">{result ? "Закрыть" : "Отмена"}</button>
        <button type="button" onClick={() => void run()} disabled={!chosen || running}
          className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg bg-emerald-700 px-5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
          {running && <Loader2 className="size-4 animate-spin" />}Выполнить
        </button>
      </div>
    </div>
  </div>;
}
