/**
 * Рейтинговый список (регионы, заказчики, разрезы процента победителя).
 *
 * Полоса заполнения рисуется относительно максимума в списке, а не абсолютной шкалы: у
 * топ-10 заказчиков разброс кратный, и общая шкала превратила бы хвост в невидимые
 * ниточки. Число рядом с полосой всегда абсолютное — доля читается формой, значение цифрой.
 */
export function RankingList({
  rows,
  emptyText,
}: {
  rows: { label: string; sublabel?: string | null; value: string; ratio: number }[];
  emptyText: string;
}) {
  if (rows.length === 0) {
    return <p className="py-6 text-center text-sm text-zinc-600">{emptyText}</p>;
  }

  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <div key={row.label}>
          <div className="mb-1.5 flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate text-sm text-zinc-300" title={row.label}>
              {row.label}
              {row.sublabel && (
                <span className="ml-2 text-xs text-zinc-600">{row.sublabel}</span>
              )}
            </span>
            <span className="shrink-0 text-sm font-medium text-zinc-400">{row.value}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500"
              style={{ width: `${Math.max(2, Math.round(row.ratio * 100))}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
