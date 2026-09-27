"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2, X } from "lucide-react";

import { runBulkTools } from "@/lib/client";
import type { BulkTools, BulkToolsResult } from "@/lib/types";

type Scope = "selected" | "on_sale" | "all";

/** A switch rather than a checkbox: each block is an action that runs or not,
 *  and the fields it reveals only matter while it is on. */
function Toggle({ on, onChange, title, children }: {
  on: boolean; onChange: (value: boolean) => void; title: string; children?: ReactNode;
}) {
  return <div className="border-b border-slate-100 py-4 last:border-0">
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className="flex min-h-11 w-full items-center gap-3 text-left">
      <span className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-[#345c7f]" : "bg-slate-300"}`}>
        <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-transform ${on ? "translate-x-5" : "translate-x-0.5"}`} />
      </span>
      <span className="text-sm font-medium text-slate-900">{title}</span>
    </button>
    {on && children && <div className="mt-3 space-y-3 sm:pl-14">{children}</div>}
  </div>;
}

/** The percentage field with the formula spelled out on a live example, so the
 *  merchant sees 3000 − 10% = 2700 before anything is written. */
function PercentField({ value, onChange, direction, max, label }: {
  value: string; onChange: (value: string) => void; direction: "down" | "up"; max: number; label: string;
}) {
  const percent = Number(value.replace(",", "."));
  const example = Number.isFinite(percent)
    ? Math.round(3000 * (direction === "down" ? 1 - percent / 100 : 1 + percent / 100))
    : null;
  const sign = direction === "down" ? "−" : "+";
  return <>
    <p className="text-xs leading-5 text-slate-500">
      Введите значение в процентах. {label} цена установится как (ваша цена товара {sign} значение в процентах).
      Например, цена товара 3000 ₸, вы ввели {value || "0"}% — {label.toLowerCase()} цена станет
      {example !== null ? ` (3000 ${sign} ${value || 0}%) = ${example} ₸` : " …"}.
    </p>
    <label className="relative block max-w-60">
      <span className="sr-only">{label} цена, %</span>
      <input type="number" inputMode="decimal" min={0} max={max} step="0.1" value={value}
        onChange={(event) => onChange(event.target.value)} className="catalog-input pr-9" />
      <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-slate-400">%</span>
    </label>
  </>;
}

function Check({ checked, onChange, children }: {
  checked: boolean; onChange: (value: boolean) => void; children: ReactNode;
}) {
  return <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-slate-600">
    <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="size-4 shrink-0 accent-emerald-600" />
    {children}
  </label>;
}

export function BulkToolsDialog({ skus, onClose }: { skus: string[]; onClose: () => void }) {
  const router = useRouter();
  const [scope, setScope] = useState<Scope>(skus.length ? "selected" : "on_sale");
  const [setMin, setSetMin] = useState(false);
  const [minPercent, setMinPercent] = useState("10");
  const [overwriteMin, setOverwriteMin] = useState(true);
  const [setMax, setSetMax] = useState(false);
  const [maxPercent, setMaxPercent] = useState("10");
  const [overwriteMax, setOverwriteMax] = useState(true);
  const [raise, setRaise] = useState(false);
  const [stopOffSale, setStopOffSale] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkToolsResult | null>(null);

  const chosen = setMin || setMax || raise || stopOffSale;

  async function run() {
    const tools: BulkTools = {
      ...(scope === "selected" ? { skus } : {}),
      only_on_sale: scope === "on_sale",
    };
    if (setMin) { tools.set_min_percent = minPercent.replace(",", "."); tools.overwrite_min = overwriteMin; }
    if (setMax) { tools.set_max_percent = maxPercent.replace(",", "."); tools.overwrite_max = overwriteMax; }
    if (raise) tools.raise_to_max = true;
    if (stopOffSale) tools.disable_decrease_when_off_sale = true;
    setRunning(true); setError(null); setResult(null);
    try {
      setResult(await runBulkTools(tools));
      router.refresh();
    } catch (failure) {
      // All or nothing: a refusal means no product was changed.
      setError(failure instanceof Error ? failure.message : "Не удалось выполнить");
    } finally { setRunning(false); }
  }

  const scopes: [Scope, string][] = [
    ...(skus.length ? [["selected", `Выбранные товары (${skus.length})`] as [Scope, string]] : []),
    ["on_sale", "Для товаров на продаже"],
    ["all", "Для всех товаров"],
  ];

  return <div role="dialog" aria-modal="true" aria-label="Массовые настройки" className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 p-0 sm:items-center sm:p-4">
    <div className="max-h-[92dvh] w-full max-w-2xl overflow-y-auto rounded-t-2xl bg-white p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] shadow-xl sm:rounded-2xl sm:p-6">
      <div className="flex items-start justify-between gap-4">
        <h2 className="text-xl font-semibold text-slate-900">Массовые настройки</h2>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100"><X className="size-5" /></button>
      </div>

      <fieldset className="mt-4">
        <legend className="text-sm text-slate-600">Выберите, для каких товаров применять</legend>
        <div className="mt-1 flex flex-wrap gap-x-6">
          {scopes.map(([value, label]) => <label key={value} className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-slate-800">
            <input type="radio" name="bulk-scope" checked={scope === value} onChange={() => setScope(value)} className="size-4 accent-emerald-600" />{label}
          </label>)}
        </div>
      </fieldset>

      <div className="mt-2">
        <Toggle on={setMin} onChange={setSetMin} title="Установить автоснижение и минимальные цены">
          <PercentField value={minPercent} onChange={setMinPercent} direction="down" max={90} label="Минимальная" />
          <Check checked={overwriteMin} onChange={setOverwriteMin}>Применить также для товаров, которые уже имеют минимальную цену</Check>
        </Toggle>
        <Toggle on={setMax} onChange={setSetMax} title="Установить автоповышение и максимальные цены">
          <PercentField value={maxPercent} onChange={setMaxPercent} direction="up" max={500} label="Максимальная" />
          <Check checked={overwriteMax} onChange={setOverwriteMax}>Применить также для товаров, которые уже имеют максимальную цену</Check>
        </Toggle>
        <Toggle on={raise} onChange={setRaise} title="Поднять текущие цены до максимальных">
          <p className="text-xs leading-5 text-slate-500">Цена в следующем прайсе станет равна максимальной. Если конкурент ещё на месте, бот снова опустит её на ближайшем проходе.</p>
        </Toggle>
        <Toggle on={stopOffSale} onChange={setStopOffSale} title="Отключить автоснижение у товаров, снятых с продажи">
          <p className="text-xs leading-5 text-slate-500">Снят с продажи — выключен или нигде нет остатка. Такие товары перестанут дешеветь впустую.</p>
        </Toggle>
      </div>

      {error && <p role="alert" className="mt-4 flex gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertTriangle className="size-4 shrink-0" /><span>{error}<span className="mt-1 block text-rose-600">Ни один товар не изменён.</span></span></p>}
      {result && <div role="status" className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900">
        <p>Просмотрено товаров: <b>{result.products_seen}</b>.</p>
        <ul className="mt-1 space-y-0.5 text-emerald-800">
          {result.limits_set > 0 && <li>Границы заданы: {result.limits_set}</li>}
          {result.prices_raised > 0 && <li>Цены подняты до максимума: {result.prices_raised}</li>}
          {result.decrease_disabled > 0 && <li>Автоснижение выключено: {result.decrease_disabled}</li>}
          {Object.entries(result.skipped).map(([reason, count]) => <li key={reason} className="text-amber-800">Пропущено ({reason}): {count}</li>)}
        </ul>
      </div>}

      <div className="mt-5 flex flex-col-reverse gap-3 sm:flex-row sm:justify-start">
        <button type="button" onClick={() => void run()} disabled={!chosen || running}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-6 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50">
          {running && <Loader2 className="size-4 animate-spin" />}Выполнить
        </button>
        <button type="button" onClick={onClose} className="min-h-11 px-4 text-sm text-slate-600">{result ? "Закрыть" : "Отмена"}</button>
      </div>
    </div>
  </div>;
}
