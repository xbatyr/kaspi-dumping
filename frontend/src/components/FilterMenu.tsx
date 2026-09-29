"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

/** The grouped filter menu above the catalogue, laid out like AlgaTop's: one
 *  choice at a time, every entry with the number of products it would show. */
export const FILTER_GROUPS: { title: string; options: [string, string][] }[] = [
  { title: "Статус", options: [["on", "В наличии"], ["off", "Сняты с продажи"], ["all", "Все товары"]] },
  { title: "Демпинг", options: [
    ["dumping_on", "Демпинг вкл."], ["dumping_off", "Демпинг выкл."],
    ["raise_on", "Автоповышение вкл."], ["raise_off", "Автоповышение выкл."],
  ] },
  { title: "Мин. и макс. цены", options: [
    ["min_short", "Не хватает мин. цены"], ["with_min", "С мин. ценой"], ["without_min", "Без мин. цены"],
    ["with_max", "С макс. ценой"], ["without_max", "Без макс. цены"],
  ] },
  { title: "Место", options: [["first_place", "На первом месте"], ["below_first", "Ниже 1 места"]] },
  { title: "Конкуренция", options: [["no_competitors", "Без конкурентов"], ["with_competitors", "С конкурентами"]] },
  { title: "Закупочная цена", options: [["no_cost", "Без закуп. цены"], ["with_cost", "С закуп. ценой"]] },
  { title: "Предзаказ", options: [["no_preorder", "Без предзаказа"], ["with_preorder", "С предзаказом"]] },
];

const LABELS = new Map(FILTER_GROUPS.flatMap((group) => group.options));

export function FilterMenu({ value, counts, onChange }: {
  value: string; counts: Record<string, number>; onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) { if (event.key === "Escape") setOpen(false); }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const label = `${LABELS.get(value) ?? "Все товары"} (${counts[value] ?? 0})`;

  return <div className="relative min-w-0 text-xs text-slate-600">
    <span id="filter-menu-label">Фильтры</span>
    <button type="button" aria-haspopup="listbox" aria-expanded={open} aria-labelledby="filter-menu-label filter-menu-value"
      onClick={() => setOpen(!open)}
      className="catalog-input mt-1 flex items-center justify-between gap-2 text-left text-sm text-slate-900">
      <span id="filter-menu-value" className="truncate">{label}</span>
      <ChevronDown className={`size-4 shrink-0 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`} />
    </button>
    {open && <>
      <button type="button" aria-hidden="true" tabIndex={-1} onClick={() => setOpen(false)} className="fixed inset-0 z-30 cursor-default" />
      <div role="listbox" aria-label="Фильтры" className="absolute left-0 z-40 mt-1 max-h-[70vh] w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-slate-200 bg-white py-2 shadow-xl">
        {FILTER_GROUPS.map((group) => <div key={group.title} className="py-1">
          <p className="px-4 pt-2 pb-1 text-sm text-slate-400">{group.title}</p>
          {group.options.map(([option, text]) => {
            const selected = option === value;
            return <button key={option} type="button" role="option" aria-selected={selected}
              onClick={() => { setOpen(false); onChange(option); }}
              className={`flex min-h-10 w-full items-center justify-between gap-2 px-4 text-left text-sm ${selected ? "bg-emerald-50 font-medium text-emerald-900" : "text-slate-800 hover:bg-slate-50"}`}>
              <span>{text} <span className="tabular text-slate-500">({counts[option] ?? 0})</span></span>
              {selected && <Check className="size-4 shrink-0 text-emerald-700" />}
            </button>;
          })}
        </div>)}
      </div>
    </>}
  </div>;
}
