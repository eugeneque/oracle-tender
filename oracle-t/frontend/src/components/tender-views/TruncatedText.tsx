import { useState } from "react";
import { createPortal } from "react-dom";

/**
 * Текст, обрезанный по ширине ячейки, с показом полного значения при наведении.
 *
 * Почему собственная подсказка, а не атрибут `title`: подсказка браузера появляется с
 * секундной задержкой и исчезает сама через несколько секунд — при просмотре таблицы
 * наименований это неудобно. Плюс `title` невозможно оформить, а наименования тендеров
 * длинные, и их нужно показывать в несколько строк.
 *
 * Подсказка рендерится порталом в `body` и позиционируется `fixed`, потому что таблица
 * лежит в контейнере с `overflow-x-auto`: абсолютно позиционированный элемент внутри такого
 * контейнера обрезался бы его границами.
 */
export function TruncatedText({
  text,
  className = "",
}: {
  text: string | null;
  className?: string;
}) {
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null);

  if (!text) return <span className="text-zinc-600">—</span>;

  return (
    <>
      <span
        className={`block truncate ${className}`}
        onMouseEnter={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          // Показываем подсказку только если текст реально не помещается — иначе она
          // мешает: всплывать над коротким «Отменено» незачем.
          if (e.currentTarget.scrollWidth <= e.currentTarget.clientWidth) return;
          setPosition({ x: rect.left, y: rect.bottom + 6 });
        }}
        onMouseLeave={() => setPosition(null)}
      >
        {text}
      </span>

      {position !== null &&
        createPortal(
          <div
            className="pointer-events-none fixed z-50 max-w-md rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-xs leading-relaxed text-zinc-100 shadow-xl"
            style={{
              left: Math.min(position.x, window.innerWidth - 420),
              top: position.y,
            }}
          >
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}
