import { useMemo } from "react";

/**
 * Узор из точек для обложек Сова Wiki (19.11.2026) — растровый «полутон» из синих точек разного
 * размера, как на референсе. Генерируется из числа-зерна: одна и та же статья всегда получает
 * один и тот же узор, а соседние — разные. Точки одного размера собраны в один `<path>` с
 * круглыми концами (`h0` + `stroke-linecap: round`) — так в DOM остаётся пять элементов, а не
 * тысяча кружков на карточку.
 */

const LEVELS = 5; // размеры точки: 0 — нет точки, 1…4 — от мелкой к крупной

function field(x: number, y: number, seed: number): number {
  // Сумма синусоид с разными частотами и фазами от зерна: даёт плавные «острова» без шума.
  const s = seed * 1.713;
  const v =
    Math.sin(x * 0.19 + s) +
    Math.sin(y * 0.27 - s * 1.3) +
    Math.sin((x + y) * 0.13 + s * 0.7) * 1.1 +
    Math.sin(Math.hypot(x - 30 - (seed % 7) * 3, y - 14) * 0.24 - s) * 1.2 +
    Math.sin((x - y) * 0.09 + s * 2.1) * 0.8;
  return (v + 4.4) / 8.8; // 0…1
}

export function DotCover({
  seed,
  cols = 56,
  rows = 28,
  className = "",
  fade = false,
}: {
  seed: number;
  cols?: number;
  rows?: number;
  className?: string;
  /** Растворять узор к краям — для большого фона героя. */
  fade?: boolean;
}) {
  const paths = useMemo(() => {
    const step = 10;
    const byLevel: string[][] = Array.from({ length: LEVELS }, () => []);
    for (let j = 0; j < rows; j += 1) {
      for (let i = 0; i < cols; i += 1) {
        let v = field(i, j, seed);
        if (fade) {
          // Эллиптическая «шапка»: плотное ядро в центре и плавное затухание к краям — так
          // узор читается одной фигурой, а не россыпью точек.
          const dx = (i / cols - 0.5) * 2;
          const dy = (j / rows - 0.5) * 2;
          const env = Math.max(0, 1 - Math.hypot(dx, dy * 1.15));
          v = Math.pow(v, 0.8) * Math.pow(env, 0.55) + env * 0.12;
        }
        const level = v > 0.72 ? 4 : v > 0.6 ? 3 : v > 0.5 ? 2 : v > 0.41 ? 1 : 0;
        if (level === 0) continue;
        // В шахматном порядке сдвигаем ряды — получается «сотовая» полутоновая сетка.
        const x = i * step + (j % 2 ? step / 2 : 0) + step / 2;
        const y = j * step + step / 2;
        byLevel[level].push(`M${x} ${y}h0`);
      }
    }
    return byLevel;
  }, [seed, cols, rows, fade]);

  const widths = [0, 2.2, 3.8, 5.4, 7];

  return (
    <svg
      viewBox={`0 0 ${cols * 10} ${rows * 10}`}
      preserveAspectRatio="xMidYMid slice"
      className={`text-indigo-500 ${className}`}
      aria-hidden="true"
    >
      {paths.map((segments, level) =>
        level === 0 || segments.length === 0 ? null : (
          <path
            key={level}
            d={segments.join("")}
            stroke="currentColor"
            strokeWidth={widths[level]}
            strokeLinecap="round"
            fill="none"
          />
        ),
      )}
    </svg>
  );
}
