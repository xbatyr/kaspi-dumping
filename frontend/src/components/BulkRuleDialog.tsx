"use client";

import { useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";

import { updateRulesBulk } from "@/lib/client";
import type { BulkRuleChanges } from "@/lib/types";

export function BulkRuleDialog({
  ruleIds,
  onClose,
  onSaved,
}: {
  ruleIds: number[];
  onClose: () => void;
  onSaved: (count: number) => void;
}) {
  const [minimum, setMinimum] = useState("");
  const [maximum, setMaximum] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const changes: BulkRuleChanges = {};
    if (minimum.trim()) changes.min_price = minimum.trim();
    if (maximum.trim()) changes.max_price = maximum.trim();
    if (Object.keys(changes).length === 0) {
      setError("Выберите хотя бы один параметр для изменения.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const count = await updateRulesBulk(ruleIds, changes);
      onSaved(count);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось изменить правила");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="bulk-rule-title" className="max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl bg-white shadow-xl sm:rounded-2xl">
        <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 id="bulk-rule-title" className="font-semibold text-slate-900">Массовая настройка цен</h2>
            <p className="text-sm text-slate-500">Выбрано правил: {ruleIds.length}. Пустые поля останутся без изменений.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Закрыть" className="inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100"><X className="size-5" /></button>
        </div>
        <div className="max-h-[70vh] space-y-4 overflow-y-auto px-5 py-5">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Мин. цена, ₸" value={minimum} onChange={setMinimum} min={1} />
            <Field label="Макс. цена, ₸" value={maximum} onChange={setMaximum} min={1} />
          </div>
          <p className="text-xs text-slate-500">Если новая минимальная цена выше максимальной у одного из товаров, сохранение отменится для всех выбранных правил.</p>
          {error && <p className="flex items-start gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertTriangle className="size-4 shrink-0" />{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-200 px-5 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:py-4">
          <button type="button" onClick={onClose} className="min-h-11 cursor-pointer rounded-lg px-4 py-2 text-sm text-slate-600">Отмена</button>
          <button type="button" onClick={() => void save()} disabled={saving} className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {saving && <Loader2 className="size-4 animate-spin" />}Применить
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, min, max }: { label: string; value: string; onChange: (value: string) => void; min: number; max?: number }) {
  return <label className="block min-w-0 text-sm text-slate-700">{label}<input type="number" min={min} max={max} step="1" value={value} onChange={(event) => onChange(event.target.value)} placeholder="Не менять" className="mt-1 min-h-11 w-full rounded-lg border border-slate-300 px-3 py-2 text-base sm:text-sm" /></label>;
}
