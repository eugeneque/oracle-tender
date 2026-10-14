import { Sparkles } from "lucide-react";

// Пометка «это делает модель ИИ» (замечание 13.10.2026): у кнопок, которые вызывают модель,
// и у значений, которые она вычитала. Пользователь должен понимать, что результат нужно
// проверить и что запуск стоит запросов к провайдеру, — а у кнопок без пометки (обход сайта,
// опрос реестра, ремонт характеристик) модели нет.

export const AI_HINT = "Выполняет модель ИИ — результат нужно проверить";

export function AiBadge({ title = AI_HINT, compact = false }: { title?: string; compact?: boolean }) {
  return (
    <span
      title={title}
      className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-violet-500/15 px-1.5 py-px text-[10px] font-medium leading-4 text-violet-300"
    >
      <Sparkles size={9} aria-hidden="true" />
      {!compact && "ИИ"}
      <span className="sr-only">{title}</span>
    </span>
  );
}
