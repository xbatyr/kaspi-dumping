"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";

import { CityPicker } from "@/components/CityPicker";
import { MerchantTags } from "@/components/MerchantTags";
import { saveGlobalStrategy } from "@/lib/client";
import { STRATEGY_CARDS } from "@/lib/strategies";
import type { City, GlobalStrategy, Strategy } from "@/lib/types";

export function GlobalStrategyEditor({ current, cities, defaultCity }: {
  current: GlobalStrategy; cities: City[]; defaultCity: string;
}) {
  const router = useRouter();
  const [strategy, setStrategy] = useState<Strategy>(current.strategy ?? "beat_first");
  const [position, setPosition] = useState(String(current.target_position ?? 2));
  const [merchants, setMerchants] = useState(current.ignored_merchants);
  const [cityIds, setCityIds] = useState(current.city_ids.length ? current.city_ids : [defaultCity]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    if (cityIds.length === 0) { setError("Выберите хотя бы один город"); return; }
    setSaving(true); setError(null); setSaved(false);
    try {
      await saveGlobalStrategy({
        strategy,
        target_position: strategy === "target_position" ? Number(position) : null,
        ignored_merchants: merchants, city_ids: cityIds,
      });
      setSaved(true);
      router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось сохранить стратегию");
    } finally { setSaving(false); }
  }

  return <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
    <div className="border-b border-slate-200 px-5 py-4">
      <h3 className="font-semibold text-slate-900">Общие правила демпинга</h3>
      <p className="mt-1 text-sm text-slate-500">Стратегия, города и белый список общие. Min/Max и шаг задаются для каждого товара.</p>
    </div>
    <div className="space-y-5 px-5 py-5">
      <fieldset>
        <legend className="mb-2 text-sm font-medium text-slate-900">Стратегия для всех товаров</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {STRATEGY_CARDS.map((card) => <label key={card.id} className={`flex cursor-pointer gap-2 rounded-lg border p-3 text-sm ${strategy === card.id ? "border-slate-900 bg-slate-50" : "border-slate-200"}`}>
            <input type="radio" name="global-strategy" checked={strategy === card.id} onChange={() => setStrategy(card.id)} />
            <span><span className="block font-medium text-slate-900">{card.title}</span><span className="text-xs text-slate-500">{card.hint}</span></span>
          </label>)}
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          {strategy === "target_position" && <label className="text-sm text-slate-700">Место <select value={position} onChange={(event) => setPosition(event.target.value)} className="ml-2 rounded-lg border border-slate-300 px-2 py-1">{Array.from({ length: 19 }, (_, index) => index + 2).map((place) => <option key={place} value={place}>{place}</option>)}</select></label>}
        </div>
      </fieldset>
      <CityPicker cities={cities} selected={cityIds} onChange={setCityIds} configured={new Set(current.city_ids)} />
      <MerchantTags value={merchants} onChange={setMerchants} />
      <p className="text-xs text-slate-500">Товаров с собственными Min/Max: {current.configured_products}. Остальные сохраняют ручную цену до настройки границ.</p>
      {error && <p className="flex gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertTriangle className="size-4 shrink-0" />{error}</p>}
      {saved && <p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">Общая стратегия сохранена.</p>}
    </div>
    <div className="flex justify-end border-t border-slate-200 px-5 py-4">
      <button type="button" onClick={save} disabled={saving} className="inline-flex min-h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 sm:w-auto">{saving && <Loader2 className="size-4 animate-spin" />}Сохранить общую стратегию</button>
    </div>
  </section>;
}
