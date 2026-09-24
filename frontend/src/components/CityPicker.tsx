"use client";

import { Check, MapPin } from "lucide-react";

import type { City } from "@/lib/types";

interface Props {
  cities: City[];
  selected: string[];
  onChange: (cityIds: string[]) => void;
  /** Cities that already have a rule, marked so the user sees what is at stake. */
  configured: Set<string>;
}

export function CityPicker({ cities, selected, onChange, configured }: Props) {
  const allSelected = selected.length === cities.length;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-sm font-medium text-slate-900">
          <MapPin className="size-4 text-slate-400" />
          Города
          <span className="text-xs font-normal text-slate-500">
            (выбрано: {selected.length})
          </span>
        </span>
        <button
          type="button"
          onClick={() => onChange(allSelected ? [] : cities.map((city) => city.id))}
          className="cursor-pointer text-xs font-medium text-slate-600 underline-offset-2 hover:underline"
        >
          {allSelected ? "Снять все" : "Выбрать все"}
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {cities.map((city) => {
          const active = selected.includes(city.id);
          return (
            <button
              key={city.id}
              type="button"
              aria-pressed={active}
              onClick={() =>
                onChange(
                  active
                    ? selected.filter((id) => id !== city.id)
                    : [...selected, city.id],
                )
              }
              className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
              }`}
            >
              {active && <Check className="size-3.5" />}
              {city.name}
              {configured.has(city.id) && !active && (
                <span
                  className="size-1.5 rounded-full bg-amber-400"
                  title="Правило для этого города будет отключено"
                />
              )}
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-slate-500">
        Снятый город не удаляет правило, а выключает его: история цен сохраняется.
      </p>
    </div>
  );
}
