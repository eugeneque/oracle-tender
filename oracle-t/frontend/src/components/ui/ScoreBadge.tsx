/**
 * AI-оценка закупки в списках (редизайн 23.10.2026): число на мягкой подложке без рамки —
 * зелёный ≥80%, жёлтый 50–80%, красный ниже (раздел 5.6 ТЗ). Без оценки — едва заметная
 * черта, а не серая плашка: пустая плашка в каждой второй строке читалась как «ноль».
 */
export function ScoreBadge({ score, size = "sm" }: { score: number | null; size?: "xs" | "sm" }) {
  const text = size === "xs" ? "text-[10px] px-1.5 py-px" : "text-[11px] px-2 py-0.5";
  if (score === null) {
    return (
      <span className={`shrink-0 font-medium text-zinc-600 ${text}`} title="AI-оценка не считалась">
        —
      </span>
    );
  }
  const tone =
    score >= 80
      ? "bg-emerald-500/15 text-emerald-300"
      : score >= 50
        ? "bg-amber-500/15 text-amber-300"
        : "bg-red-500/15 text-red-300";
  return (
    <span className={`shrink-0 rounded-full font-semibold tabular-nums ${text} ${tone}`} title="AI-оценка по профилю">
      {score}%
    </span>
  );
}
