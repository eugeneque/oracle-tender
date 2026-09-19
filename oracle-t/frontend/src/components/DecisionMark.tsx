import { Check, Minus, X } from "lucide-react";

import { decisionTitle } from "../utils/decision";

/**
 * Знак решения ИИ «смотреть / не смотреть» (замечание 17.09.2026). Без слов «да» и
 * «нет»: зелёный круг с галочкой — закупку стоит смотреть, красный с крестом — не стоит,
 * серый пунктирный с чертой — решение не выносилось (оценка не считалась или посчитана до
 * появления решения). Решение модель выносит по трём измерениям AI-оценки — Истории,
 * Задаче и Компетенциям — через служебную сводку, которая пользователю не показывается;
 * подробности — в карточке закупки, блок «AI-оценка по профилю».
 */
const SIZE_CLASSES = {
  sm: { ring: "h-5 w-5", icon: 12 },
  md: { ring: "h-7 w-7", icon: 15 },
  lg: { ring: "h-10 w-10", icon: 22 },
} as const;

export function DecisionMark({
  decision,
  size = "sm",
  className = "",
}: {
  decision: boolean | null | undefined;
  size?: keyof typeof SIZE_CLASSES;
  className?: string;
}) {
  const { ring, icon } = SIZE_CLASSES[size];
  const base = `inline-flex shrink-0 items-center justify-center rounded-full ${ring} ${className}`;
  if (decision === true) {
    return (
      <span
        className={`${base} bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-400/50 shadow-[0_0_10px_-2px_rgba(52,211,153,0.6)]`}
        title={decisionTitle(decision)}
        aria-label="Стоит смотреть"
      >
        <Check size={icon} strokeWidth={3} />
      </span>
    );
  }
  if (decision === false) {
    return (
      <span
        className={`${base} bg-red-500/15 text-red-300 ring-1 ring-red-400/50`}
        title={decisionTitle(decision)}
        aria-label="Не стоит смотреть"
      >
        <X size={icon} strokeWidth={3} />
      </span>
    );
  }
  return (
    <span
      className={`${base} border border-dashed border-zinc-600 text-zinc-600`}
      title={decisionTitle(decision)}
      aria-label="Решение не выносилось"
    >
      <Minus size={icon} strokeWidth={2.5} />
    </span>
  );
}
