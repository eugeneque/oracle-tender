/**
 * Шкала хода задачи (редизайн 23.10.2026). Полный разбор пишет в ход «Шаг 1 из 3: анализ
 * документов…» — раньше это был просто текст, и было неясно, сколько осталось. Если в
 * тексте есть «Шаг N из M», шкала делится на M сегментов: пройденные залиты, текущий
 * переливается. Иначе — доля `value` (0…1) или бегущий отрезок, когда хода не знаем.
 */
export function parseStep(text: string | null | undefined): { step: number; total: number; label: string } | null {
  const match = /шаг\s+(\d+)\s+из\s+(\d+)\s*[:—-]?\s*(.*)/i.exec(text ?? "");
  if (!match) return null;
  const label = match[3].replace(/…$/, "").trim();
  return { step: Number(match[1]), total: Number(match[2]), label: label.charAt(0).toUpperCase() + label.slice(1) };
}

export function StepProgress({
  text,
  value = null,
  className = "",
}: {
  text?: string | null;
  value?: number | null;
  className?: string;
}) {
  const parsed = parseStep(text);
  if (parsed) {
    return (
      <span className={`flex gap-1 ${className}`}>
        {Array.from({ length: parsed.total }, (_, index) => {
          const n = index + 1;
          const state = n < parsed.step ? "done" : n === parsed.step ? "active" : "todo";
          return <span key={n} className="step-seg" data-state={state} />;
        })}
      </span>
    );
  }
  return (
    <span className={`relative block h-1.5 overflow-hidden rounded-full bg-white/[0.06] ${className}`}>
      {value === null ? (
        <span className="block h-full w-1/3 animate-[activity-indeterminate_1.4s_ease-in-out_infinite] rounded-full bg-indigo-400/70" />
      ) : (
        <span
          className="step-fill block h-full rounded-full transition-[width] duration-500"
          style={{ width: `${Math.max(3, Math.round(value * 100))}%` }}
        />
      )}
    </span>
  );
}
