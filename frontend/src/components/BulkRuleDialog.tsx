"use client";

import { X } from "lucide-react";

import { BulkToolsPanel } from "@/components/BulkToolsDialog";

/** «Массовая настройка цен» for rules picked one by one: the same panel as
 *  everywhere else, so percentages, tenge, a fixed price or a floor from the
 *  cost are all on offer here too, with the same preview. */
export function BulkRuleDialog({ ruleIds, onClose }: { ruleIds: number[]; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="bulk-rule-title" className="max-h-[94dvh] w-full max-w-3xl overflow-y-auto rounded-t-2xl bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-xl sm:rounded-2xl sm:p-6">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 id="bulk-rule-title" className="text-xl font-semibold text-slate-900">Массовая настройка цен</h2>
            <p className="mt-1 text-sm text-slate-500">Выбрано правил: {ruleIds.length}. Выключенные настройки останутся без изменений.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Закрыть" className="inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100"><X className="size-5" /></button>
        </div>
        <BulkToolsPanel ruleIds={ruleIds} />
      </div>
    </div>
  );
}
