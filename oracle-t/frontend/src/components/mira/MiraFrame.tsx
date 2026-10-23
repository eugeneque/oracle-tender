import type { ReactNode } from "react";

import { parseStep } from "../ui/StepProgress";

/**
 * Контур блока Миры (23.10.2026): по рамке бежит перелив в цветах её настроения
 * (`.mira-frame` в index.css), а во время работы рамка становится шкалой — по ней
 * заполняются шаги задачи («Шаг 2 из 3» — две трети контура, текущий шаг наполовину) и
 * бежит светящаяся «комета». Сегменты-подложки показывают, сколько всего шагов.
 *
 * Прямоугольник рисуется SVG с `pathLength=100`: доли контура считаются в процентах, без
 * знания реального размера блока, а `rx` совпадает со скруглением карточки.
 */
export function MiraFrame({
  tone,
  busy,
  progress,
  radius = 22,
  className = "",
  children,
}: {
  /** Настроение Миры или `working` — от него цвета перелива. */
  tone: string;
  busy: boolean;
  /** Ход задачи с сервера: «Шаг 1 из 3: …». */
  progress: string | null;
  radius?: number;
  className?: string;
  children: ReactNode;
}) {
  const step = parseStep(progress);
  const total = step?.total ?? 0;
  const segment = total ? 100 / total : 0;
  const filled = step ? Math.min(100, (step.step - 0.5) * segment) : 0;
  return (
    <section className={`mira-frame relative ${className}`} data-tone={tone}>
      {children}
      <svg className="pointer-events-none absolute inset-0 z-[3] h-full w-full overflow-visible" aria-hidden="true">
        <defs>
          <linearGradient id="mira-frame-fill" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" style={{ stopColor: "var(--m1)" }} />
            <stop offset="50%" style={{ stopColor: "var(--m2)" }} />
            <stop offset="100%" style={{ stopColor: "var(--m3)" }} />
          </linearGradient>
        </defs>
        {step && (
          <>
            <rect
              className="mira-frame__track"
              rx={radius - 1}
              pathLength={100}
              stroke="rgb(var(--c-white) / 0.1)"
              strokeWidth={2}
              strokeDasharray={`${segment - 1.2} 1.2`}
            />
            <rect
              className="mira-frame__fill"
              rx={radius - 1}
              pathLength={100}
              stroke="url(#mira-frame-fill)"
              strokeDasharray={`${filled} ${100 - filled}`}
            />
          </>
        )}
        {busy && <rect className="mira-frame__comet" rx={radius - 1} pathLength={100} stroke="var(--m4)" />}
      </svg>
    </section>
  );
}
