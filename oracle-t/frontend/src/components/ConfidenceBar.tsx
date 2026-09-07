const SEGMENTS = 20;

/**
 * Пороги цвета — раздел 5.6 ТЗ: зелёный ≥80%, жёлтый 50–80%, красный <50%.
 */
function colorClass(percent: number): string {
  if (percent >= 80) return "bg-emerald-400";
  if (percent >= 50) return "bg-amber-400";
  return "bg-red-400";
}

export function ConfidenceBar({
  percent,
  label = "МИРТЕК",
}: {
  percent: number | null;
  label?: string;
}) {
  if (percent === null) {
    return (
      <div className="flex items-center gap-2 text-xs text-zinc-600">
        <span className="text-zinc-500">{label}</span>
        <span>нет данных</span>
      </div>
    );
  }

  const filled = Math.round((percent / 100) * SEGMENTS);
  const fillColor = colorClass(percent);

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-[2px]" title={`${label}: ${percent}%`}>
        {Array.from({ length: SEGMENTS }).map((_, i) => (
          <span
            key={i}
            className={`h-3.5 w-[3px] rounded-full ${i < filled ? fillColor : "bg-zinc-700/70"}`}
          />
        ))}
      </div>
      <span className="text-xs font-medium text-zinc-300">{percent}%</span>
    </div>
  );
}
