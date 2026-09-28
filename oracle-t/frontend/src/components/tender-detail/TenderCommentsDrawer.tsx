import { MessageSquare, X } from "lucide-react";

import type { AiFeedback } from "../../api/types";
import { AiFeedbackCard } from "./AiFeedbackCard";

/**
 * «Комментарии» (28.09.2026) — архив ответов специалистов на заключение ИИ по закупке.
 * Не основной блок, поэтому открывается иконкой из шапки карточки, поверх содержимого.
 * Видят все: это общая переписка команды с моделью, а не личные заметки.
 */
export function TenderCommentsDrawer({
  items,
  onClose,
}: {
  items: AiFeedback[] | null;
  onClose: () => void;
}) {
  return (
    <div className="absolute inset-0 z-30 flex justify-end">
      <button aria-label="Закрыть комментарии" onClick={onClose} className="flex-1 bg-scrim/40" />
      <aside className="flex h-full w-full max-w-[420px] flex-col border-l border-white/[0.08] bg-zinc-950 shadow-2xl">
        <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <MessageSquare size={15} />
            Комментарии
            {items && items.length > 0 && (
              <span className="text-xs font-normal tabular-nums text-zinc-500">{items.length}</span>
            )}
          </div>
          <button
            onClick={onClose}
            title="Закрыть"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
          >
            <X size={15} />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-4">
          {items === null ? (
            <p className="text-sm text-zinc-500">Загрузка…</p>
          ) : items.length === 0 ? (
            <p className="text-sm leading-relaxed text-zinc-500">
              Пока никто не отвечал на заключение ИИ. Нажмите «Согласен» или «Не согласен» под
              заключением — ответ появится здесь, а несогласие ИИ сразу пересмотрит.
            </p>
          ) : (
            items.map((item) => <AiFeedbackCard key={item.id} item={item} />)
          )}
        </div>
      </aside>
    </div>
  );
}
