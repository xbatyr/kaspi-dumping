"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Eye, FileSpreadsheet, Loader2, Play, X } from "lucide-react";

import { importAlgaTop } from "@/lib/client";
import { CITY_FALLBACK } from "@/lib/cities";
import { tenge } from "@/lib/format";
import type { AlgaTopImportResult } from "@/lib/types";

function Moved({ before, after }: { before: string | null; after: string }) {
  if (before === null) return <span className="font-medium text-slate-900">{tenge(after)}</span>;
  if (Number(before) === Number(after)) return <span className="text-slate-500">{tenge(after)}</span>;
  return <span className="whitespace-nowrap"><span className="text-slate-400">{tenge(before)}</span> → <span className="font-medium text-slate-900">{tenge(after)}</span></span>;
}

/** «Импорт из AlgaTop»: the shop's AlgaTop export in, every product priced
 *  and limited exactly as it is there. Checked first, then applied. */
export function AlgaTopImportDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [running, setRunning] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AlgaTopImportResult | null>(null);

  async function run(preview: boolean) {
    if (!file) return;
    setRunning(preview ? "preview" : "apply"); setError(null);
    try {
      setResult(await importAlgaTop(file, preview));
      if (!preview) router.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось прочитать файл");
    } finally { setRunning(null); }
  }

  return <div role="dialog" aria-modal="true" aria-label="Импорт из AlgaTop" className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 sm:items-center sm:p-4">
    <div className="max-h-[94dvh] w-full max-w-3xl overflow-y-auto rounded-t-2xl bg-white p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] shadow-xl sm:rounded-2xl sm:p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Импорт из AlgaTop</h2>
          <p className="mt-1 text-sm leading-6 text-slate-500">
            Выгрузка товаров из AlgaTop (.xlsx или .csv). У каждого товара возьмутся цена, мин. и макс. цена,
            шаг, автоснижение и автоповышение, цена закупа, статус, остатки и предзаказ — ровно как в AlgaTop.
            Цены в XML станут такими же, как сейчас у AlgaTop.
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Закрыть" className="flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100"><X className="size-5" /></button>
      </div>

      <label className="mt-4 flex min-h-24 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 p-4 text-center text-sm text-slate-600 hover:border-[#345c7f]">
        <FileSpreadsheet className="size-6 text-[#345c7f]" />
        {file ? <span className="font-medium text-slate-900">{file.name}</span> : <span>Выберите файл выгрузки AlgaTop</span>}
        <input type="file" accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv" className="sr-only"
          onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); setError(null); }} />
      </label>

      {error && <p role="alert" className="mt-4 flex gap-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-700"><AlertTriangle className="size-4 shrink-0" />{error}</p>}

      {result && <div role="status" className={`mt-4 rounded-xl border p-4 text-sm ${result.preview ? "border-amber-200 bg-amber-50 text-amber-950" : "border-emerald-200 bg-emerald-50 text-emerald-950"}`}>
        <p className="font-semibold">{result.preview ? "Предпросмотр — пока ничего не изменено" : "Импорт выполнен"}</p>
        <ul className="mt-2 grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
          <li>Строк в файле: <b>{result.rows}</b></li>
          <li>Товаров {result.preview ? "обновится" : "обновлено"}: <b>{result.updated}</b></li>
          {result.created > 0 && <li>Новых товаров: <b>{result.created}</b></li>}
          <li>Цена в XML {result.preview ? "изменится" : "изменилась"}: <b>{result.prices_changed}</b></li>
          <li>Мин./макс. {result.preview ? "изменятся" : "изменились"}: <b>{result.limits_changed}</b></li>
        </ul>
        {result.switched_off.length > 0 && <p className="mt-2 font-medium text-rose-800">В AlgaTop сняты с продажи — {result.preview ? "снимутся" : "сняты"} и здесь ({result.switched_off.length}): {result.switched_off.slice(0, 20).join(", ")}{result.switched_off.length > 20 ? " …" : ""}</p>}
        {result.switched_on.length > 0 && <p className="mt-2 text-emerald-900">В AlgaTop на продаже — {result.preview ? "включатся" : "включены"} и здесь ({result.switched_on.length}): {result.switched_on.slice(0, 20).join(", ")}{result.switched_on.length > 20 ? " …" : ""}</p>}
        {result.not_found_total > 0 && <p className="mt-2 text-amber-800">Нет в каталоге ({result.not_found_total}): {result.not_found.join(", ")}{result.not_found_total > result.not_found.length ? " …" : ""}</p>}
        {result.errors.length > 0 && <div className="mt-2 text-rose-800"><p>Строки с ошибками пропущены ({result.errors.length}):</p>
          <ul className="mt-1 max-h-32 list-disc overflow-y-auto pl-5 text-xs">{result.errors.map((item, index) => <li key={index}>{item.sku}: {item.reason}</li>)}</ul></div>}
        {result.changes.length > 0 && <div className="mt-3 max-h-80 overflow-auto rounded-lg border border-black/5 bg-white">
          <table className="w-full min-w-[620px] text-left text-xs">
            <thead className="sticky top-0 bg-slate-50 text-slate-500"><tr><th className="px-3 py-2 font-medium">Товар</th><th className="px-3 py-2 font-medium">Город</th><th className="px-3 py-2 font-medium">Цена в XML</th><th className="px-3 py-2 font-medium">Мин.</th><th className="px-3 py-2 font-medium">Макс.</th></tr></thead>
            <tbody className="tabular text-slate-700">
              {result.changes.map((change) => <tr key={`${change.sku}:${change.city_id}`} className="border-t border-slate-100">
                <td className="max-w-56 px-3 py-2"><span className="block truncate font-medium text-slate-900">{change.title}</span><span className="text-slate-400">{change.sku}</span></td>
                <td className="px-3 py-2">{CITY_FALLBACK[change.city_id] ?? change.city_id}</td>
                <td className="px-3 py-2"><Moved before={change.price_before} after={change.price_after} /></td>
                <td className="px-3 py-2"><Moved before={change.min_before} after={change.min_after} /></td>
                <td className="px-3 py-2"><Moved before={change.max_before} after={change.max_after} /></td>
              </tr>)}
            </tbody>
          </table>
          {result.changes_total > result.changes.length && <p className="border-t border-slate-100 px-3 py-2 text-xs text-slate-500">…и ещё {result.changes_total - result.changes.length}</p>}
        </div>}
      </div>}

      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row">
        <button type="button" onClick={() => void run(true)} disabled={!file || running !== null}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-[#345c7f] px-5 text-sm font-semibold text-[#345c7f] hover:bg-[#f5f8fb] disabled:opacity-50">
          {running === "preview" ? <Loader2 className="size-4 animate-spin" /> : <Eye className="size-4" />}Проверить
        </button>
        <button type="button" onClick={() => void run(false)} disabled={!file || running !== null || !result?.preview}
          title={result?.preview ? undefined : "Сначала нажмите «Проверить»"}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-6 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50">
          {running === "apply" ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}Импортировать
        </button>
        <button type="button" onClick={onClose} className="min-h-11 px-4 text-sm text-slate-600 sm:ml-auto">{result && !result.preview ? "Закрыть" : "Отмена"}</button>
      </div>
    </div>
  </div>;
}
