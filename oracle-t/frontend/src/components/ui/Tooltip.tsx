import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Подсказка при наведении и фокусе (14.10.2026, редизайн каталога).
 *
 * Заменяет две вещи, на которые жаловались: значок-«звёздочку» у кнопок с моделью ИИ и
 * подписи-пояснения прямо в интерфейсе («нет данных, где искать…», «руководство
 * пользователя ↗» у каждого значения). Пояснение нужно тому, кто спросил, — по наведению,
 * а не всем и всегда.
 *
 * Почему не атрибут `title`: браузерная подсказка появляется через секунду, пропадает сама и
 * не умеет в несколько строк. Порталом в `body` и `fixed` — потому что подсказки стоят внутри
 * контейнеров с `overflow` (окно модели, таблицы), которые обрезали бы её.
 */
export function Tooltip({
  content,
  children,
  className = "",
}: {
  content: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [position, setPosition] = useState<{ x: number; y: number; above: boolean } | null>(null);
  const timer = useRef<number | null>(null);

  const show = (target: HTMLElement) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const rect = target.getBoundingClientRect();
      // У нижнего края окна — над элементом, иначе под ним.
      const above = rect.bottom > window.innerHeight - 140;
      setPosition({ x: rect.left + rect.width / 2, y: above ? rect.top - 8 : rect.bottom + 8, above });
    }, 150);
  };
  const hide = () => {
    if (timer.current) window.clearTimeout(timer.current);
    setPosition(null);
  };

  if (content === null || content === undefined || content === "") return <>{children}</>;

  return (
    <span
      className={`inline-flex ${className}`}
      onMouseEnter={(e) => show(e.currentTarget)}
      onMouseLeave={hide}
      onFocus={(e) => show(e.currentTarget)}
      onBlur={hide}
    >
      {children}
      {position &&
        createPortal(
          <div
            role="tooltip"
            className="pointer-events-none fixed z-[70] max-w-xs whitespace-pre-line rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-xs leading-relaxed text-zinc-200 shadow-[0_20px_60px_rgba(0,0,0,.35)]"
            style={{
              left: Math.max(168, Math.min(position.x, window.innerWidth - 168)),
              top: position.y,
              transform: `translate(-50%, ${position.above ? "-100%" : "0"})`,
            }}
          >
            {content}
          </div>,
          document.body,
        )}
    </span>
  );
}

// Строка подсказки у действий, которые вызывают модель ИИ: вместо значка — прямым текстом,
// что это стоит запросов к провайдеру и что результат нужно проверить.
export const AI_COST_NOTE = "Использует модель ИИ — расходует токены, результат стоит проверить.";
