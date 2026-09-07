import type { ReactNode } from "react";

/**
 * Разметка `**жирный**` внутри строки, пришедшей от модели.
 *
 * YandexGPT возвращает пункты в markdown, даже когда просишь простой текст: «**Предмет
 * договора:** оказание услуг…». Показывать их как есть — значит показывать звёздочки, а
 * вычищать разметку на бэкенде было бы потерей: выделение несёт смысл, это подпись поля
 * внутри пункта.
 *
 * Свой разбор вместо библиотеки markdown сознательно: нужен ровно один инлайновый приём в
 * тексте, который к тому же приходит из внешнего источника. Полноценный рендерер здесь — это
 * и лишняя зависимость, и лишняя поверхность (ссылки, HTML, изображения из ответа модели).
 */

const BOLD_RE = /\*\*(.+?)\*\*/g;

export function InlineMarkdown({ text }: { text: string }) {
  const nodes: ReactNode[] = [];
  let cursor = 0;

  for (const match of text.matchAll(BOLD_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    nodes.push(
      <strong key={start} className="font-semibold text-zinc-100">
        {match[1]}
      </strong>,
    );
    cursor = start + match[0].length;
  }

  // Одиночные звёздочки, не сложившиеся в пару, убираем: в тексте закупки они всегда остаток
  // разметки, а не символ умножения — тот пишется как «х» или «*» между числами, и такие
  // случаи сюда не попадают, потому что не окружены буквами.
  const tail = text.slice(cursor);
  if (tail) nodes.push(tail);

  return <>{nodes.map((node) => node)}</>;
}
