"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpToLine,
  Eye,
  Filter,
  Footprints,
  Loader2,
  Play,
  SlidersHorizontal,
  X,
} from "lucide-react";

import { runBulkTools } from "@/lib/client";
import { tenge } from "@/lib/format";
import type { BulkTools, BulkToolsResult, Category, LimitMode } from "@/lib/types";

type Scope = "selected" | "on_sale" | "all";
type Direction = "keep" | "on" | "off";

interface SideState { on: boolean; mode: LimitMode; value: string; overwrite: boolean }

/** How each way of setting a side reads, per side: the button, the unit, the
 *  quick values and the formula on a 3000 ₸ example. */
const MODES: Record<LimitMode, {
  label: [string, string]; unit: "%" | "₸"; presets: number[];
  explain: (lower: boolean, value: number) => string;
}> = {
  percent: {
    label: ["− % от цены", "+ % от цены"], unit: "%", presets: [3, 5, 10, 15, 20],
    explain: (lower, value) => {
      const price = Math.round(3000 * (lower ? 1 - value / 100 : 1 + value / 100));
      return `Ваша цена ${lower ? "−" : "+"} ${value}%. Цена 3000 ₸ → ${price} ₸. Процент запоминается: поменяете цену — граница пересчитается сама.`;
    },
  },
  tenge_offset: {
    label: ["− ₸ от цены", "+ ₸ от цены"], unit: "₸", presets: [100, 500, 1000, 5000],
    explain: (lower, value) => `Ваша цена ${lower ? "−" : "+"} ${value} ₸. Цена 3000 ₸ → ${lower ? Math.max(1, 3000 - value) : 3000 + value} ₸.`,
  },
  fixed: {
    label: ["Фикс. цена", "Фикс. цена"], unit: "₸", presets: [],
    explain: (lower, value) => `У всех выбранных товаров ${lower ? "минимум" : "максимум"} станет ровно ${value || "…"} ₸.`,
  },
  cost_markup: {
    label: ["От себестоимости", "От себестоимости"], unit: "%", presets: [0, 5, 10, 20, 30],
    explain: (_lower, value) => {
      // Закуп 2000 ₸, комиссия 12% и налог 3% → безубыточность 2000 / 0.85.
      const breakEven = Math.ceil(2000 / 0.85);
      return `Безубыточность (закуп + комиссия Kaspi + налог + доставка) + ${value}%. Закуп 2000 ₸, комиссия 12%, налог 3% → в ноль ${breakEven} ₸ → ${Math.ceil(breakEven * (1 + value / 100))} ₸. Нужна цена закупа у товара.`;
    },
  },
};

const MODE_ORDER: LimitMode[] = ["percent", "tenge_offset", "fixed", "cost_markup"];

function Section({ icon: Icon, title, hint, on, onToggle, children }: {
  icon: typeof Filter; title: string; hint?: string; on?: boolean;
  onToggle?: (value: boolean) => void; children?: ReactNode;
}) {
  const open = on ?? true;
  return <section className={`rounded-xl border p-4 transition-colors ${onToggle && on ? "border-[#345c7f]/40 bg-[#f5f8fb]" : "border-slate-200 bg-white"}`}>
    <div className="flex items-start gap-3">
      {onToggle
        ? <button type="button" role="switch" aria-checked={on} aria-label={title} onClick={() => onToggle(!on)}
            className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-[#345c7f]" : "bg-slate-300"}`}>
            <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-transform ${on ? "translate-x-5" : "translate-x-0.5"}`} />
          </button>
        : <Icon className="mt-0.5 size-5 shrink-0 text-[#345c7f]" />}
      {onToggle
        // The title toggles too, for a bigger target; the switch itself is the
        // control screen readers announce, so this one stays out of tab order.
        ? <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onToggle(!on)} tabIndex={-1}>
            <span className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Icon className="size-4 text-[#345c7f]" />{title}</span>
            {hint && <span className="mt-0.5 block text-xs leading-5 text-slate-500">{hint}</span>}
          </button>
        : <div className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-slate-900">{title}</span>
            {hint && <span className="mt-0.5 block text-xs leading-5 text-slate-500">{hint}</span>}
          </div>}
    </div>
    {open && children && <div className="mt-4 space-y-3 sm:pl-14">{children}</div>}
  </section>;
}

function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: [T, string][]; onChange: (value: T) => void; label: string;
}) {
  return <div role="radiogroup" aria-label={label} className="grid grid-cols-2 gap-1 rounded-lg border border-slate-200 bg-white p-1 sm:flex">
    {options.map(([option, text]) => <button key={option} type="button" role="radio" aria-checked={value === option}
      onClick={() => onChange(option)}
      className={`min-h-10 flex-1 rounded-md px-3 text-sm font-medium transition-colors ${value === option ? "bg-[#345c7f] text-white shadow-sm" : "text-slate-600 hover:bg-slate-100"}`}>
      {text}
    </button>)}
  </div>;
}

function LimitSide({ lower, state, onChange }: {
  lower: boolean; state: SideState; onChange: (next: SideState) => void;
}) {
  const mode = MODES[state.mode];
  const number = Number(state.value.replace(",", "."));
  return <>
    <Segmented label={lower ? "Как считать минимум" : "Как считать максимум"} value={state.mode}
      options={MODE_ORDER.map((item) => [item, MODES[item].label[lower ? 0 : 1]])}
      onChange={(next) => onChange({ ...state, mode: next, value: next === "fixed" ? "" : String(MODES[next].presets[2] ?? 10) })} />
    <div className="flex flex-wrap items-center gap-2">
      <label className="relative block w-44">
        <span className="sr-only">{lower ? "Минимум" : "Максимум"}, {mode.unit}</span>
        <input type="number" inputMode="decimal" min={0} step={mode.unit === "%" ? "0.1" : "1"} value={state.value}
          onChange={(event) => onChange({ ...state, value: event.target.value })}
          placeholder={state.mode === "fixed" ? "Цена, ₸" : "0"} className="catalog-input pr-9" />
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-slate-400">{mode.unit}</span>
      </label>
      {mode.presets.map((preset) => <button key={preset} type="button" onClick={() => onChange({ ...state, value: String(preset) })}
        className={`min-h-9 rounded-full border px-3 text-xs font-medium ${String(preset) === state.value ? "border-[#345c7f] bg-[#345c7f] text-white" : "border-slate-300 bg-white text-slate-700 hover:border-slate-400"}`}>
        {state.mode === "tenge_offset" ? `${preset} ₸` : `${preset}%`}
      </button>)}
    </div>
    <p className="text-xs leading-5 text-slate-500">{mode.explain(lower, Number.isFinite(number) ? number : 0)}</p>
    <label className="flex min-h-10 cursor-pointer items-center gap-2 text-sm text-slate-600">
      <input type="checkbox" checked={state.overwrite} onChange={(event) => onChange({ ...state, overwrite: event.target.checked })} className="size-4 accent-emerald-600" />
      Применить также для товаров, которые уже имеют {lower ? "минимальную" : "максимальную"} цену
    </label>
  </>;
}

function Change({ before, after }: { before: string | null; after: string }) {
  if (before === null) return <span className="font-medium text-slate-900">{tenge(after)}</span>;
  const delta = Number(after) - Number(before);
  return <span className="whitespace-nowrap">
    <span className="text-slate-400">{tenge(before)}</span> → <span className={`font-medium ${delta < 0 ? "text-rose-700" : delta > 0 ? "text-emerald-700" : "text-slate-900"}`}>{tenge(after)}</span>
  </span>;
}

/** «Массовые настройки»: every catalogue-wide change in one form, with a
 *  preview of what each product will get before anything is written. */
export function BulkToolsPanel({ skus = [], ruleIds = [], categories = [], onApplied }: {
  skus?: string[]; ruleIds?: number[]; categories?: Category[]; onApplied?: () => void;
}) {
  const router = useRouter();
  const selection = skus.length || ruleIds.length;
  const [scope, setScope] = useState<Scope>(selection ? "selected" : "on_sale");
  const [category, setCategory] = useState("");
  const [min, setMin] = useState<SideState>({ on: false, mode: "percent", value: "10", overwrite: true });
  const [max, setMax] = useState<SideState>({ on: false, mode: "percent", value: "10", overwrite: true });
  const [stepOn, setStepOn] = useState(false);
  const [step, setStep] = useState("1");
  const [decrease, setDecrease] = useState<Direction>("keep");
  const [increase, setIncrease] = useState<Direction>("keep");
  const [raise, setRaise] = useState(false);
  const [stopOffSale, setStopOffSale] = useState(false);
  const [running, setRunning] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ key: string; data: BulkToolsResult } | null>(null);

  function payload(): BulkTools | string {
    const tools: BulkTools = {
      ...(scope === "selected" ? (skus.length ? { skus } : { rule_ids: ruleIds }) : {}),
      only_on_sale: scope === "on_sale",
      ...(scope !== "selected" && category ? { category } : {}),
    };
    for (const [side, state, key, overwriteKey] of [
      ["Минимум", min, "min_limit", "overwrite_min"],
      ["Максимум", max, "max_limit", "overwrite_max"],
    ] as const) {
      if (!state.on) continue;
      const value = state.value.replace(",", ".").trim();
      if (!value || !Number.isFinite(Number(value)) || Number(value) < 0) return `${side}: введите число`;
      if (state.mode === "fixed" && Number(value) <= 0) return `${side}: фиксированная цена должна быть больше нуля`;
      tools[key] = { mode: state.mode, value };
      tools[overwriteKey] = state.overwrite;
    }
    if (stepOn) {
      if (!Number.isInteger(Number(step)) || Number(step) < 1) return "Шаг — целое число тенге от 1";
      tools.step = Number(step);
    }
    if (decrease !== "keep") tools.auto_decrease = decrease === "on";
    if (increase !== "keep") tools.auto_increase = increase === "on";
    if (raise) tools.raise_to_max = true;
    if (stopOffSale) tools.disable_decrease_when_off_sale = true;
    const chosen = min.on || max.on || stepOn || decrease !== "keep" || increase !== "keep" || raise || stopOffSale;
    return chosen ? tools : "Включите хотя бы одну настройку";
  }

  const draft = payload();
  const key = typeof draft === "string" ? "" : JSON.stringify(draft);
  const current = result && result.key === key ? result.data : null;

  async function run(dryRun: boolean) {
    if (typeof draft === "string") { setError(draft); return; }
    setRunning(dryRun ? "preview" : "apply"); setError(null);
    try {
      const data = await runBulkTools({ ...draft, dry_run: dryRun });
      setResult({ key, data });
      if (!dryRun) { router.refresh(); onApplied?.(); }
    } catch (failure) {
      // All or nothing: a refusal means no product was changed.
      setError(failure instanceof Error ? failure.message : "Не удалось выполнить");
    } finally { setRunning(null); }
  }

  const scopes: [Scope, string][] = [
    ...(selection ? [["selected", skus.length ? `Выбранные (${skus.length})` : `Выбранные правила (${ruleIds.length})`] as [Scope, string]] : []),
    ["on_sale", "На продаже"],
    ["all", "Все товары"],
  ];
  const directions: [Direction, string][] = [["keep", "Не менять"], ["on", "Включить"], ["off", "Выключить"]];

  return <div className="space-y-3">
    <Section icon={Filter} title="Для каких товаров" hint="Сначала выберите круг товаров — все настройки ниже применятся только к нему.">
      <Segmented label="Товары" value={scope} options={scopes} onChange={setScope} />
      {scope !== "selected" && categories.length > 0 && <label className="block max-w-sm text-xs text-slate-600">Категория
        <select value={category} onChange={(event) => setCategory(event.target.value)} className="catalog-input mt-1">
          <option value="">Все категории</option>
          {categories.map((item) => <option key={item.name ?? "__none__"} value={item.name ?? "__none__"}>{item.name ?? "Без категории"} ({item.products})</option>)}
        </select>
      </label>}
    </Section>

    <Section icon={ArrowDownToLine} title="Минимальная цена и автоснижение" on={min.on} onToggle={(on) => setMin({ ...min, on })}
      hint="Ниже этой цены бот не опустится никогда. Включает автоснижение.">
      <LimitSide lower state={min} onChange={setMin} />
    </Section>

    <Section icon={ArrowUpToLine} title="Максимальная цена и автоповышение" on={max.on} onToggle={(on) => setMax({ ...max, on })}
      hint="Выше этой цены бот не поднимет. Включает автоповышение.">
      <LimitSide lower={false} state={max} onChange={setMax} />
    </Section>

    <Section icon={Footprints} title="Шаг цены" on={stepOn} onToggle={setStepOn}
      hint="На сколько тенге бот отличается от конкурента.">
      <div className="flex flex-wrap items-center gap-2">
        <label className="relative block w-32">
          <span className="sr-only">Шаг, ₸</span>
          <input type="number" min={1} step={1} value={step} onChange={(event) => setStep(event.target.value)} className="catalog-input pr-9" />
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-slate-400">₸</span>
        </label>
        {[1, 2, 5, 10, 50].map((preset) => <button key={preset} type="button" onClick={() => setStep(String(preset))}
          className={`min-h-9 rounded-full border px-3 text-xs font-medium ${String(preset) === step ? "border-[#345c7f] bg-[#345c7f] text-white" : "border-slate-300 bg-white text-slate-700"}`}>{preset} ₸</button>)}
      </div>
    </Section>

    <Section icon={SlidersHorizontal} title="Автоснижение и автоповышение" hint="Явно включить или выключить. Перекрывает то, что включили границы выше.">
      <div className="grid gap-3 sm:grid-cols-2">
        <div><p className="mb-1 text-xs text-slate-600">Автоснижение</p><Segmented label="Автоснижение" value={decrease} options={directions} onChange={setDecrease} /></div>
        <div><p className="mb-1 text-xs text-slate-600">Автоповышение</p><Segmented label="Автоповышение" value={increase} options={directions} onChange={setIncrease} /></div>
      </div>
    </Section>

    <Section icon={ArrowUpToLine} title="Поднять текущие цены до максимальных" on={raise} onToggle={setRaise}
      hint="Цена в следующем прайсе станет равна максимальной. Если конкурент ещё на месте, бот снова опустит её на ближайшем проходе." />
    <Section icon={ArrowDownToLine} title="Отключить автоснижение у товаров, снятых с продажи" on={stopOffSale} onToggle={setStopOffSale}
      hint="Снят с продажи — выключен или нигде нет остатка. Такие товары перестанут дешеветь впустую." />

    {error && <p role="alert" className="flex gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertTriangle className="size-4 shrink-0" /><span>{error}<span className="mt-1 block text-rose-600">Ни один товар не изменён.</span></span></p>}

    {current && <div role="status" className={`rounded-xl border p-4 text-sm ${current.dry_run ? "border-amber-200 bg-amber-50 text-amber-950" : "border-emerald-200 bg-emerald-50 text-emerald-950"}`}>
      <p className="font-semibold">{current.dry_run ? "Предпросмотр — пока ничего не изменено" : "Готово, изменения сохранены"}</p>
      <ul className="mt-2 grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
        <li>Просмотрено товаров: <b>{current.products_seen}</b></li>
        {current.limits_set > 0 && <li>Границы {current.dry_run ? "будут заданы" : "заданы"}: <b>{current.limits_set}</b></li>}
        {current.steps_set > 0 && <li>Шаг {current.dry_run ? "изменится" : "изменён"}: <b>{current.steps_set}</b></li>}
        {current.directions_set > 0 && <li>Направления {current.dry_run ? "изменятся" : "изменены"}: <b>{current.directions_set}</b></li>}
        {current.prices_raised > 0 && <li>Цены до максимума: <b>{current.prices_raised}</b></li>}
        {current.decrease_disabled > 0 && <li>Автоснижение выключено: <b>{current.decrease_disabled}</b></li>}
        {Object.entries(current.skipped).map(([reason, count]) => <li key={reason} className="text-amber-800">Пропущено ({reason}): <b>{count}</b></li>)}
      </ul>
      {current.changes.length > 0 && <div className="mt-3 max-h-80 overflow-auto rounded-lg border border-black/5 bg-white">
        <table className="w-full min-w-[520px] text-left text-xs">
          <thead className="sticky top-0 bg-slate-50 text-slate-500"><tr><th className="px-3 py-2 font-medium">Товар</th><th className="px-3 py-2 font-medium">Мин. цена</th><th className="px-3 py-2 font-medium">Макс. цена</th></tr></thead>
          <tbody className="tabular text-slate-700">
            {current.changes.map((change) => <tr key={change.sku} className="border-t border-slate-100">
              <td className="max-w-64 px-3 py-2"><span className="block truncate font-medium text-slate-900">{change.title}</span><span className="text-slate-400">{change.sku}</span></td>
              <td className="px-3 py-2"><Change before={change.min_before} after={change.min_after} /></td>
              <td className="px-3 py-2"><Change before={change.max_before} after={change.max_after} /></td>
            </tr>)}
          </tbody>
        </table>
        {current.changes_total > current.changes.length && <p className="border-t border-slate-100 px-3 py-2 text-xs text-slate-500">…и ещё {current.changes_total - current.changes.length} товаров</p>}
      </div>}
    </div>}

    <div className="sticky bottom-0 -mx-1 flex flex-col-reverse gap-2 bg-gradient-to-t from-white via-white to-white/80 px-1 pt-3 pb-1 sm:flex-row sm:items-center">
      <button type="button" onClick={() => void run(true)} disabled={running !== null || typeof draft === "string"}
        className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-[#345c7f] px-5 text-sm font-semibold text-[#345c7f] hover:bg-[#f5f8fb] disabled:opacity-50">
        {running === "preview" ? <Loader2 className="size-4 animate-spin" /> : <Eye className="size-4" />}Предпросмотр
      </button>
      <button type="button" onClick={() => void run(false)} disabled={running !== null || typeof draft === "string"}
        className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-6 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50">
        {running === "apply" ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}Выполнить
      </button>
      {typeof draft === "string" && <span className="text-xs text-slate-500 sm:ml-2">{draft}</span>}
    </div>
  </div>;
}

/** The same panel over the catalogue, for the products ticked there. */
export function BulkToolsDialog({ skus, categories, onClose }: {
  skus: string[]; categories?: Category[]; onClose: () => void;
}) {
  return <div role="dialog" aria-modal="true" aria-label="Массовые настройки" className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 p-0 sm:items-center sm:p-4">
    <div className="max-h-[94dvh] w-full max-w-3xl overflow-y-auto rounded-t-2xl bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-xl sm:rounded-2xl sm:p-6">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Массовые настройки</h2>
          <p className="mt-1 text-sm text-slate-500">Проценты, тенге, фиксированная цена или от себестоимости — сначала посмотрите, что изменится.</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100"><X className="size-5" /></button>
      </div>
      <BulkToolsPanel skus={skus} categories={categories} />
    </div>
  </div>;
}
