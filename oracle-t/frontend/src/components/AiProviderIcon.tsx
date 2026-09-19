import type { AiProviderKey } from "../api/types";

/**
 * Значки провайдеров ИИ — в переключателе настроек и в шапке блока «Разбор ИИ».
 *
 * Inline-SVG, без картинок: значок рисуется в двух размерах (16 и 28 px) и должен
 * оставаться чётким на любом экране. Фирменные цвета — красный Яндекса и терракотовый
 * Claude — те же, что задают раскраску блока (см. `.ai-wave--claude` в index.css), чтобы
 * значок и подсветка читались как одно.
 */
export function AiProviderIcon({
  provider,
  size = 16,
  className,
}: {
  provider: AiProviderKey;
  size?: number;
  className?: string;
}) {
  if (provider === "claude") {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 32 32"
        className={className}
        aria-hidden="true"
      >
        <circle cx="16" cy="16" r="16" fill="#D97757" />
        {/* Лучистая звезда: восемь лучей разной длины, как у фирменного знака. */}
        <g stroke="#FFF6EE" strokeWidth="2.6" strokeLinecap="round">
          <line x1="16" y1="7" x2="16" y2="12.5" />
          <line x1="16" y1="19.5" x2="16" y2="25" />
          <line x1="7" y1="16" x2="12.5" y2="16" />
          <line x1="19.5" y1="16" x2="25" y2="16" />
          <line x1="9.6" y1="9.6" x2="13.2" y2="13.2" />
          <line x1="18.8" y1="18.8" x2="22.4" y2="22.4" />
          <line x1="22.4" y1="9.6" x2="18.8" y2="13.2" />
          <line x1="13.2" y1="18.8" x2="9.6" y2="22.4" />
        </g>
      </svg>
    );
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className={className}
      aria-hidden="true"
    >
      <circle cx="16" cy="16" r="16" fill="#FC3F1D" />
      {/* Буква «Я» контуром: ножка, дуга петли, диагональ. */}
      <path
        d="M19.6 24.5V8.5h-3.4c-3.6 0-5.7 1.8-5.7 4.6 0 2.2 1.1 3.5 3 4.4l-3.9 7h3.2l3.4-6.3h1.1v6.3h2.3z
           M17.3 15.9h-1.2c-1.9 0-3-.9-3-2.7 0-1.8 1.2-2.6 3-2.6h1.2v5.3z"
        fill="#FFFFFF"
      />
    </svg>
  );
}
