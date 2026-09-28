"use client";

import { useSyncExternalStore, useState } from "react";
import Link from "next/link";
import { Check, ChevronDown, ChevronUp, GraduationCap, X } from "lucide-react";

/** Three short tours that explain the bot the way a merchant meets it: the
 *  catalogue, the bulk tools, then the shop settings. */
const TOURS = [
  {
    id: "catalog",
    title: "Общие и настройки товара",
    href: "/",
    tips: [
      "Каталог показывает товары в выбранном городе: ваше место на Kaspi, цену первого места, вашу текущую цену и маржу.",
      "Мин. и макс. цена — границы, внутри которых бот двигает цену. Нажмите на подчёркнутое число, чтобы изменить его в тенге или в процентах.",
      "Цена закупки, остаток и предзаказ тоже меняются по клику. От закупки, комиссии, налога и доставки считается маржа.",
      "«Автоснижение» разрешает боту опускать цену до минимальной, «Автоповышение» — поднимать до максимальной.",
    ],
  },
  {
    id: "bulk",
    title: "Массовые настройки",
    href: "/bulk",
    tips: [
      "«Массовые» в меню (или зелёная кнопка в каталоге) задают границы сразу для многих товаров: на продаже, всех, одной категории или выбранных галочками.",
      "Мин. и макс. считаются как удобно: % от цены, ₸ от цены, одна фиксированная цена или от себестоимости. Цена 3000 ₸ и 10% дают 2700 ₸ и 3300 ₸.",
      "«Предпросмотр» показывает, что изменится у каждого товара, и ничего не сохраняет. «Выполнить» применяет.",
      "Если хоть у одного товара минимум окажется выше максимума, не изменится ни один товар. Поправьте проценты и повторите.",
      "Выделите товары галочками, и снизу появится панель: снять их с продажи или применить настройки только к ним.",
    ],
  },
  {
    id: "shop",
    title: "Настройки магазина",
    href: "/settings",
    tips: [
      "Укажите ID магазина на Kaspi и название компании: без них бот не запустится.",
      "Налог (3%), комиссия Kaspi из договора и средняя доставка нужны, чтобы маржа в каталоге была настоящей.",
      "«Конкурировать только в своём городе»: бот меняет цену только там, а остальные города получают вашу базовую цену.",
      "Включите «Бот включён» и сохраните — цены начнут меняться со следующего цикла.",
    ],
  },
] as const;

type TourId = (typeof TOURS)[number]["id"];
interface GuideState { done: TourId[]; dismissed: boolean; collapsed: boolean }

const KEY = "kaspi-bot-guide";
const CHANGED = "kaspi-bot-guide-changed";
const INITIAL: GuideState = { done: [], dismissed: false, collapsed: true };

// Progress is a per-browser convenience, so it lives in localStorage. Every
// access is guarded: a private window or blocked storage must not break the page.
function readRaw(): string | null {
  try { return window.localStorage.getItem(KEY); } catch { return null; }
}

function parse(raw: string | null): GuideState {
  if (!raw) return INITIAL;
  try { return { ...INITIAL, ...(JSON.parse(raw) as Partial<GuideState>) }; } catch { return INITIAL; }
}

function write(state: GuideState) {
  try { window.localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* stays in memory only */ }
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

export function SetupGuide() {
  // undefined on the server, so the widget only appears once the browser has
  // said what the merchant already went through.
  const raw = useSyncExternalStore(subscribe, readRaw, () => undefined);
  const [active, setActive] = useState<{ tour: TourId; tip: number } | null>(null);
  if (raw === undefined) return null;
  const state = parse(raw);
  const update = (changes: Partial<GuideState>) => write({ ...state, ...changes });

  if (state.dismissed) {
    return <button type="button" onClick={() => update({ dismissed: false, collapsed: false })}
      className="fixed right-4 bottom-20 z-30 inline-flex min-h-10 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 shadow-md hover:bg-slate-50 md:bottom-6">
      <GraduationCap className="size-4" />Обучение
    </button>;
  }

  const finished = state.done.length;
  const tour = active ? TOURS.find(item => item.id === active.tour) : undefined;

  function finishTour(id: TourId) {
    update({ done: state.done.includes(id) ? state.done : [...state.done, id] });
    setActive(null);
  }

  return <aside aria-label="Настройки Kaspi-Бот" className="fixed right-4 bottom-20 z-30 w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_12px_40px_-12px_rgba(15,23,42,0.35)] md:bottom-6">
    <div className="bg-slate-50 px-4 pt-3 pb-3">
      <div className="flex items-start justify-between gap-2">
        <button type="button" aria-expanded={!state.collapsed} onClick={() => update({ collapsed: !state.collapsed })}
          className="flex min-h-8 items-center gap-1 text-left text-base font-semibold text-slate-900">
          Настройки Kaspi-Бот {state.collapsed ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        </button>
        <button type="button" aria-label="Скрыть обучение" onClick={() => update({ dismissed: true })}
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-slate-200/60"><X className="size-4" /></button>
      </div>
      {!state.collapsed && <p className="mt-0.5 text-sm leading-5 text-slate-500">Пройдите данные туры, чтобы понять, как настроить бот</p>}
    </div>

    {!state.collapsed && <>
      <div className="flex items-center gap-3 border-y border-slate-100 px-4 py-3">
        <span className="text-sm text-slate-600 tabular">{finished}/{TOURS.length}</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuemin={0} aria-valuemax={TOURS.length} aria-valuenow={finished}>
          <div className="h-full rounded-full bg-[#4a6fc4] transition-[width]" style={{ width: `${(finished / TOURS.length) * 100}%` }} />
        </div>
      </div>

      {tour && active ? <div className="px-4 py-4">
        <p className="text-xs font-medium text-slate-500">{tour.title} · {active.tip + 1} из {tour.tips.length}</p>
        <p className="mt-2 text-sm leading-6 text-slate-800">{tour.tips[active.tip]}</p>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <Link href={tour.href} className="text-sm text-[#345c7f] underline-offset-2 hover:underline">Открыть раздел</Link>
          <div className="flex gap-2">
            <button type="button" onClick={() => active.tip === 0 ? setActive(null) : setActive({ ...active, tip: active.tip - 1 })}
              className="min-h-9 rounded-lg px-3 text-sm text-slate-600 hover:bg-slate-100">Назад</button>
            {active.tip < tour.tips.length - 1
              ? <button type="button" onClick={() => setActive({ ...active, tip: active.tip + 1 })} className="min-h-9 rounded-lg bg-[#4a6fc4] px-3 text-sm font-medium text-white hover:bg-[#3d5fae]">Далее</button>
              : <button type="button" onClick={() => finishTour(tour.id)} className="min-h-9 rounded-lg bg-[#4a6fc4] px-3 text-sm font-medium text-white hover:bg-[#3d5fae]">Понятно</button>}
          </div>
        </div>
      </div> : <ul>
        {TOURS.map(item => {
          const done = state.done.includes(item.id);
          return <li key={item.id} className="border-b border-slate-100 last:border-0">
            <button type="button" onClick={() => setActive({ tour: item.id, tip: 0 })}
              className="flex min-h-12 w-full items-center gap-3 px-4 text-left text-sm text-slate-800 hover:bg-slate-50">
              <span className={`flex size-5 shrink-0 items-center justify-center rounded-full ${done ? "bg-[#4a6fc4] text-white" : "border-2 border-slate-300"}`}>
                {done && <Check className="size-3.5" strokeWidth={3} />}
              </span>
              {item.title}
            </button>
          </li>;
        })}
      </ul>}

      <div className="flex justify-end border-t border-slate-100 bg-slate-50 px-4 py-3">
        <button type="button" onClick={() => update({ dismissed: true })}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-[#4a6fc4] px-4 text-sm font-medium text-white hover:bg-[#3d5fae]">
          Готово <Check className="size-4" />
        </button>
      </div>
    </>}
  </aside>;
}
