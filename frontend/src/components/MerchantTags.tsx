"use client";

import { useState, type KeyboardEvent } from "react";
import { ShieldCheck, X } from "lucide-react";

interface Props {
  value: string[];
  onChange: (merchants: string[]) => void;
}

/** Merchant IDs that are never treated as competitors: partner and sister
 *  stores. Accepts typing, Enter, commas and a pasted comma-separated list. */
export function MerchantTags({ value, onChange }: Props) {
  const [draft, setDraft] = useState("");

  function commit(raw: string) {
    const added = raw
      .split(/[,\s]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (added.length > 0) {
      onChange([...new Set([...value, ...added])]);
    }
    setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit(draft);
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  return (
    <div>
      <label
        htmlFor="merchant-tags"
        className="mb-2 flex items-center gap-1.5 text-sm font-medium text-slate-900"
      >
        <ShieldCheck className="size-4 text-slate-400" />
        Белый список мерчантов
      </label>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-200 bg-white p-2 focus-within:border-slate-400">
        {value.map((merchant) => (
          <span
            key={merchant}
            className="inline-flex items-center gap-1 rounded-md bg-slate-100 py-1 pl-2 pr-1 text-xs font-medium text-slate-700"
          >
            {merchant}
            <button
              type="button"
              aria-label={`Убрать ${merchant}`}
              onClick={() => onChange(value.filter((item) => item !== merchant))}
              className="cursor-pointer rounded text-slate-400 hover:text-slate-700"
            >
              <X className="size-3.5" />
            </button>
          </span>
        ))}
        <input
          id="merchant-tags"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => commit(draft)}
          placeholder={value.length === 0 ? "ID магазина, через запятую" : ""}
          className="min-w-40 flex-1 bg-transparent px-1 py-0.5 text-sm outline-none"
        />
      </div>
      <p className="mt-1.5 text-xs text-slate-500">
        Их цены игнорируются при расчёте: партнёрские и собственные магазины.
      </p>
    </div>
  );
}
