import Link from "next/link";
import { AlertTriangle, ArrowLeft, Layers } from "lucide-react";

import { BulkToolsPanel } from "@/components/BulkToolsDialog";
import { ApiError, fetchCategories, fetchRules } from "@/lib/api";
import type { Category } from "@/lib/types";

type Loaded =
  | { ok: true; categories: Category[]; counts: { all: number; on: number; off: number } }
  | { ok: false; message: string };

async function load(): Promise<Loaded> {
  try {
    const [categories, list] = await Promise.all([
      fetchCategories(),
      fetchRules({ limit: 1, offset: 0 }),
    ]);
    return { ok: true, categories, counts: list.sale_counts };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof ApiError ? error.message : "Не удалось загрузить каталог",
    };
  }
}

/** «Массовые настройки» as a page of its own: the change a merchant makes to
 *  hundreds of products at once deserves more room than a dialog. */
export default async function BulkPage() {
  const result = await load();

  return (
    <div className="mx-auto max-w-3xl">
      <Link href="/" className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-900">
        <ArrowLeft className="size-4" />
        К товарам
      </Link>
      <div className="mb-5 flex items-start gap-3">
        <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-[#345c7f] text-white"><Layers className="size-5" /></span>
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Массовые настройки</h2>
          <p className="mt-1 text-sm leading-6 text-slate-500">
            Минимальная и максимальная цена сразу для многих товаров — в процентах, в тенге, одной
            фиксированной ценой или от себестоимости. Нажмите «Предпросмотр», чтобы увидеть, что
            изменится у каждого товара, прежде чем сохранять.
          </p>
          {result.ok && <p className="mt-2 text-xs text-slate-500">
            Товаров в магазине: <b>{result.counts.all}</b> · на продаже: <b>{result.counts.on}</b> · сняты: <b>{result.counts.off}</b>
          </p>}
        </div>
      </div>

      {result.ok ? (
        <BulkToolsPanel categories={result.categories} />
      ) : (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-6">
          <p className="flex items-center gap-2 font-medium text-rose-800">
            <AlertTriangle className="size-5" />
            {result.message}
          </p>
        </div>
      )}
    </div>
  );
}
