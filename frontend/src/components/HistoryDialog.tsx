"use client";

import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Minus, X } from "lucide-react";

import { CITY_FALLBACK } from "@/lib/cities";
import { tenge } from "@/lib/format";
import { STRATEGY_LABELS } from "@/lib/strategies";
import type { City, HistoryPage } from "@/lib/types";

const REASONS: Record<string, string> = {
  already_first: "уже на первом месте",
  direction_disabled: "изменение в этом направлении выключено",
  strategy_target: "по стратегии",
  capped_at_max: "упёрлись в максимум",
  fallback_position: "боролись за место ниже",
  pinned_to_min: "стоп-лосс: ниже нельзя",
  no_competitors: "конкурентов нет",
  fixed_price: "фиксированная цена",
};

/** What the bot actually did with this product, newest first. */
export function HistoryDialog({
  sku,
  cities,
  onClose,
}: {
  sku: string;
  cities: City[];
  onClose: () => void;
}) {
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let alive = true;
    fetch(`/api/history/${encodeURIComponent(sku)}?limit=50`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`сервер ответил ${response.status}`);
        return (await response.json()) as HistoryPage;
      })
      .then((data) => alive && setPage(data))
      .catch((failure: Error) => alive && setError(failure.message));
    return () => {
      alive = false;
    };
  }, [sku]);

  const cityName = (id: string) =>
    cities.find((city) => city.id === id)?.name ?? CITY_FALLBACK[id] ?? id;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 p-0 sm:items-center sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-title"
        className="flex max-h-[92dvh] w-full max-w-2xl flex-col rounded-t-2xl bg-white pb-[env(safe-area-inset-bottom)] shadow-xl sm:rounded-2xl sm:pb-0"
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <div>
            <h2 id="history-title" className="text-base font-semibold text-slate-900">
              История цен
            </h2>
            <p className="mt-0.5 font-mono text-xs text-slate-500">{sku}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="inline-flex size-11 cursor-pointer items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {error && <p className="text-sm text-rose-700">Не удалось загрузить: {error}</p>}
          {!page && !error && (
            <p className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="size-4 animate-spin" />
              Загружаем…
            </p>
          )}
          {page && page.items.length === 0 && (
            <p className="py-8 text-center text-sm text-slate-500">
              Бот ещё не менял цену этого товара.
            </p>
          )}
          {page && page.items.length > 0 && (
            <ul className="space-y-2">
              {page.items.map((entry) => {
                const before = entry.old_price === null ? null : Number(entry.old_price);
                const after = Number(entry.new_price);
                const direction =
                  before === null ? "same" : after < before ? "down" : after > before ? "up" : "same";
                return (
                  <li
                    key={entry.id}
                    className="flex flex-col gap-2 rounded-lg border border-slate-100 p-3 sm:flex-row sm:items-start sm:justify-between sm:gap-3"
                  >
                    <div className="min-w-0">
                      <p className="tabular flex flex-wrap items-center gap-1.5 text-sm">
                        {direction === "down" && <ArrowDown className="size-4 text-emerald-600" />}
                        {direction === "up" && <ArrowUp className="size-4 text-rose-600" />}
                        {direction === "same" && <Minus className="size-4 text-slate-300" />}
                        <span className="text-slate-400">{tenge(entry.old_price)}</span>
                        <span className="text-slate-300">→</span>
                        <span className="font-medium text-slate-900">{tenge(entry.new_price)}</span>
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {cityName(entry.city_id)} · №{entry.expected_position} ·{" "}
                        {STRATEGY_LABELS[entry.strategy_used]} · {REASONS[entry.reason] ?? entry.reason}
                      </p>
                      {entry.competitor_top1_price && (
                        <p className="text-xs text-slate-400">
                          топ-1 был {tenge(entry.competitor_top1_price)}
                          {entry.competitor_top1_merchant_id
                            ? ` (${entry.competitor_top1_merchant_id})`
                            : ""}
                          , конкурентов: {entry.competitor_count}
                        </p>
                      )}
                    </div>
                    <time className="shrink-0 text-xs text-slate-400">
                      {new Date(entry.created_at).toLocaleString("ru-KZ", {
                        day: "2-digit",
                        month: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </time>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
