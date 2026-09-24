import type { Money } from "@/lib/types";

const TENGE = new Intl.NumberFormat("ru-KZ", { maximumFractionDigits: 0 });

/** "362000" -> "362 000 ₸" */
export function tenge(amount: Money | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  const value = Number(amount);
  return Number.isFinite(value) ? `${TENGE.format(value)} ₸` : "—";
}

/** Same, without the sign, for inputs and tight columns. */
export function amount(value: Money | null | undefined): string {
  if (value === null || value === undefined) return "";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? TENGE.format(parsed) : "";
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "ещё не считалось";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  return `${Math.round(hours / 24)} дн назад`;
}
