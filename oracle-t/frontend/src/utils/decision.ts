/** Подсказка к знаку решения ИИ (см. `components/DecisionMark.tsx`). */
export function decisionTitle(decision: boolean | null | undefined): string {
  if (decision === true) return "Решение ИИ: стоит смотреть — по Истории, Задаче и Компетенциям";
  if (decision === false) return "Решение ИИ: не стоит тратить время — по Истории, Задаче и Компетенциям";
  return "Решение ИИ не выносилось — рассчитайте AI-оценку в карточке";
}
