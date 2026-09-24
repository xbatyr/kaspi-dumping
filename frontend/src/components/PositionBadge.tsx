import { Trophy } from "lucide-react";

/** Place on the Kaspi product card. The number comes from the last decision the
 *  engine made, not from a live check, so it is what we expected to land in. */
export function PositionBadge({ position }: { position: number | null }) {
  if (position === null) {
    return <span className="text-sm text-slate-400">—</span>;
  }
  const tone =
    position === 1
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : position <= 3
        ? "bg-sky-50 text-sky-700 ring-sky-200"
        : position <= 10
          ? "bg-amber-50 text-amber-700 ring-amber-200"
          : "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span
      className={`tabular inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${tone}`}
      title="Ожидаемое место при последнем пересчёте цены"
    >
      {position === 1 && <Trophy className="size-3" />}№{position}
    </span>
  );
}
