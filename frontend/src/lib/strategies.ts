import { Crown, Medal, Swords, TrendingDown, type LucideIcon } from "lucide-react";

import type { Strategy } from "@/lib/types";

export interface StrategyCard {
  id: Strategy;
  title: string;
  hint: string;
  icon: LucideIcon;
  /** Extra control the card reveals when it is picked. */
  extra?: "step" | "position";
}

/** The four strategies of the specification, in its order and wording.
 *  The API also accepts fixed_price and manual; they are deliberately not
 *  offered here. */
export const STRATEGY_CARDS: StrategyCard[] = [
  {
    id: "beat_first",
    title: "Стать первым (демпинг)",
    hint: "Цена на шаг ниже самого дешёвого конкурента.",
    icon: TrendingDown,
    extra: "step",
  },
  {
    id: "match_first",
    title: "Цена первого места",
    hint: "Совпасть по цене с первым местом: при равной цене выигрывает магазин с рейтингом выше.",
    icon: Crown,
  },
  {
    id: "follow_second",
    title: "Прижиматься к первому месту",
    hint: "На шаг дороже лидера: удержание второй позиции без ценовой войны.",
    icon: Medal,
  },
  {
    id: "target_position",
    title: "Борьба за 2-20 место",
    hint: "Держать выбранное место в выдаче, подрезая того, кто занимает его сейчас.",
    icon: Swords,
    extra: "position",
  },
];

export const STRATEGY_LABELS: Record<Strategy, string> = {
  beat_first: "Стать первым",
  match_first: "Цена первого места",
  follow_second: "Прижиматься к первому",
  target_position: "Борьба за место",
  fixed_price: "Фиксированная цена",
  manual: "Вручную",
};
