"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { toggleRules } from "@/lib/client";

interface Props {
  ruleIds: number[];
  isActive: boolean;
  label: string;
  onError: (message: string) => void;
}

/** The bot switch. Disabled when the product has no rule to switch. */
export function ActiveToggle({ ruleIds, isActive, label, onError }: Props) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  // Flip straight away; the refresh below confirms it from the server.
  const [optimistic, setOptimistic] = useState<boolean | null>(null);
  const checked = optimistic ?? isActive;
  const disabled = ruleIds.length === 0 || pending;

  async function flip() {
    const next = !checked;
    setOptimistic(next);
    try {
      await toggleRules(ruleIds, next);
      startTransition(() => router.refresh());
    } catch (error) {
      setOptimistic(null);
      onError(error instanceof Error ? error.message : "Не удалось переключить бота");
    }
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={flip}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2 ${
        checked ? "bg-emerald-500" : "bg-slate-300"
      } ${disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}
    >
      <span
        className={`inline-block size-4 rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}
