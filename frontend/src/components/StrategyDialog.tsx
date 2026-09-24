"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowDownToLine, ArrowUpToLine, Loader2, X } from "lucide-react";

import { CityPicker } from "@/components/CityPicker";
import { MerchantTags } from "@/components/MerchantTags";
import { saveRuleForCities } from "@/lib/client";
import { STRATEGY_CARDS } from "@/lib/strategies";
import type { City, ProductRules, Strategy } from "@/lib/types";

interface Props {
  product: ProductRules;
  cities: City[];
  /** City the table is showing, preselected when the product has no rules yet. */
  focusCity: string;
  onClose: () => void;
}

export function StrategyDialog({ product, cities, focusCity, onClose }: Props) {
  const router = useRouter();
  // The rule of the city in view is the best template for the others.
  const template = useMemo(
    () => product.rules.find((rule) => rule.city_id === focusCity) ?? product.rules[0],
    [product.rules, focusCity],
  );

  const [strategy, setStrategy] = useState<Strategy>(template?.strategy ?? "beat_first");
  const [step, setStep] = useState(String(template?.step ?? 1));
  const [targetPosition, setTargetPosition] = useState(String(template?.target_position ?? 2));
  const [minPrice, setMinPrice] = useState(template?.min_price ?? "");
  const [maxPrice, setMaxPrice] = useState(template?.max_price ?? "");
  const [merchants, setMerchants] = useState<string[]>(template?.ignored_merchants ?? []);
  const [selectedCities, setSelectedCities] = useState<string[]>(() => {
    const active = product.rules.filter((rule) => rule.is_active).map((rule) => rule.city_id);
    return active.length > 0 ? active : [focusCity];
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const configured = useMemo(
    () => new Set(product.rules.map((rule) => rule.city_id)),
    [product.rules],
  );

  function validate(): string | null {
    const min = Number(minPrice);
    const max = Number(maxPrice);
    if (!Number.isFinite(min) || min <= 0) return "Укажите минимальную цену больше нуля";
    if (!Number.isFinite(max) || max < min) return "Максимальная цена не может быть ниже минимальной";
    if (strategy === "beat_first" && Number(step) < 1) return "Шаг не может быть меньше 1 ₸";
    if (selectedCities.length === 0) return "Выберите хотя бы один город";
    return null;
  }

  async function save() {
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await saveRuleForCities(
        product.sku,
        {
          strategy,
          min_price: minPrice,
          max_price: maxPrice,
          step: Number(step) || 1,
          target_position: strategy === "target_position" ? Number(targetPosition) : null,
          ignored_merchants: merchants,
          cityIds: selectedCities,
        },
        product.rules,
      );
      router.refresh();
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  }

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
        aria-labelledby="strategy-dialog-title"
        className="flex max-h-[92vh] w-full max-w-2xl flex-col rounded-t-2xl bg-white shadow-xl sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <h2 id="strategy-dialog-title" className="truncate text-base font-semibold text-slate-900">
              {product.title}
            </h2>
            <p className="mt-0.5 font-mono text-xs text-slate-500">SKU {product.sku}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="cursor-pointer rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
          <fieldset>
            <legend className="mb-2 text-sm font-medium text-slate-900">Стратегия</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {STRATEGY_CARDS.map((card) => {
                const active = strategy === card.id;
                const Icon = card.icon;
                return (
                  <label
                    key={card.id}
                    className={`flex cursor-pointer gap-3 rounded-xl border p-3 transition-colors ${
                      active
                        ? "border-slate-900 bg-slate-50 ring-1 ring-slate-900"
                        : "border-slate-200 hover:border-slate-300"
                    }`}
                  >
                    <input
                      type="radio"
                      name="strategy"
                      value={card.id}
                      checked={active}
                      onChange={() => setStrategy(card.id)}
                      className="sr-only"
                    />
                    <Icon
                      className={`mt-0.5 size-5 shrink-0 ${active ? "text-slate-900" : "text-slate-400"}`}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-slate-900">{card.title}</span>
                      <span className="mt-0.5 block text-xs leading-snug text-slate-500">
                        {card.hint}
                      </span>

                      {active && card.extra === "step" && (
                        <span className="mt-2 flex items-center gap-2">
                          <input
                            type="number"
                            min={1}
                            value={step}
                            onChange={(event) => setStep(event.target.value)}
                            onClick={(event) => event.preventDefault()}
                            aria-label="Шаг демпинга в тенге"
                            className="tabular w-24 rounded-lg border border-slate-300 px-2 py-1 text-sm outline-none focus:border-slate-900"
                          />
                          <span className="text-xs text-slate-500">₸ — шаг</span>
                        </span>
                      )}

                      {active && card.extra === "position" && (
                        <span className="mt-2 flex items-center gap-2">
                          <select
                            value={targetPosition}
                            onChange={(event) => setTargetPosition(event.target.value)}
                            aria-label="Целевая позиция"
                            className="tabular rounded-lg border border-slate-300 px-2 py-1 text-sm outline-none focus:border-slate-900"
                          >
                            {Array.from({ length: 19 }, (_, index) => index + 2).map((place) => (
                              <option key={place} value={place}>
                                №{place}
                              </option>
                            ))}
                          </select>
                          <span className="text-xs text-slate-500">целевое место</span>
                        </span>
                      )}
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <CityPicker
            cities={cities}
            selected={selectedCities}
            onChange={setSelectedCities}
            configured={configured}
          />

          <MerchantTags value={merchants} onChange={setMerchants} />

          <fieldset>
            <legend className="mb-2 text-sm font-medium text-slate-900">Жёсткие ограничения</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 flex items-center gap-1.5 text-xs font-medium text-slate-600">
                  <ArrowDownToLine className="size-3.5 text-rose-500" />
                  Min price (стоп-лосс)
                </span>
                <input
                  type="number"
                  min={1}
                  value={minPrice}
                  onChange={(event) => setMinPrice(event.target.value)}
                  placeholder="330000"
                  className="tabular w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900"
                />
              </label>
              <label className="block">
                <span className="mb-1 flex items-center gap-1.5 text-xs font-medium text-slate-600">
                  <ArrowUpToLine className="size-3.5 text-emerald-600" />
                  Max price
                </span>
                <input
                  type="number"
                  min={1}
                  value={maxPrice}
                  onChange={(event) => setMaxPrice(event.target.value)}
                  placeholder="420000"
                  className="tabular w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900"
                />
              </label>
            </div>
            <p className="mt-1.5 text-xs text-slate-500">
              Ниже Min price бот не опустится ни при какой стратегии: заложите себестоимость,
              комиссию Kaspi и доставку.
            </p>
          </fieldset>

          {error && (
            <p className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-lg px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}
