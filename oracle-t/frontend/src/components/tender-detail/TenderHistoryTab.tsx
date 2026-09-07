import { useState } from "react";
import { Loader2, MessageSquare, PencilLine, Send } from "lucide-react";

import type { TenderHistoryEntry } from "../../api/types";
import { formatDateTime, relevanceLabel, tenderTypeLabel } from "../../utils/format";

/** Значение поля в человеческом виде: в истории хранятся коды (`supply_only`, `confirmed`),
 * а читать ленту должен человек. */
function displayValue(fieldName: string | null, value: string | null): string {
  if (!value) return "—";
  if (fieldName === "tender_type") return tenderTypeLabel(value) ?? value;
  if (fieldName === "relevance_status") return relevanceLabel(value);
  return value;
}

export function TenderHistoryTab({
  entries,
  onComment,
}: {
  entries: TenderHistoryEntry[] | null;
  onComment: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [isSending, setIsSending] = useState(false);

  const submit = async () => {
    if (!text.trim()) return;
    setIsSending(true);
    try {
      await onComment(text.trim());
      setText("");
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-start gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="Комментарий к тендеру…"
          className="flex-1 resize-y rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
        />
        <button
          onClick={() => void submit()}
          disabled={isSending || !text.trim()}
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          {isSending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          Добавить
        </button>
      </div>

      {entries === null ? (
        <div className="flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 size={14} className="animate-spin" />
          Загрузка истории…
        </div>
      ) : entries.length === 0 ? (
        <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500">
          Изменений пока не было. Здесь появятся правки классификации, отметки релевантности и
          комментарии — с указанием автора и времени (раздел 5.6 ТЗ).
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((entry) => (
            <div
              key={entry.id}
              className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5"
            >
              <div className="mb-1 flex items-center gap-2 text-[11px] text-zinc-600">
                {entry.kind === "comment" ? (
                  <MessageSquare size={12} className="text-indigo-400" />
                ) : (
                  <PencilLine size={12} className="text-zinc-500" />
                )}
                <span className="text-zinc-400">{entry.user_name ?? "Система"}</span>
                <span>{formatDateTime(entry.created_at)}</span>
              </div>
              {entry.kind === "comment" ? (
                <p className="whitespace-pre-line text-sm text-zinc-200">{entry.comment}</p>
              ) : (
                <p className="text-sm text-zinc-300">
                  <span className="text-zinc-500">{entry.field_label ?? entry.field_name}: </span>
                  <span className="text-zinc-500 line-through">
                    {displayValue(entry.field_name, entry.old_value)}
                  </span>
                  <span className="text-zinc-600"> → </span>
                  <span>{displayValue(entry.field_name, entry.new_value)}</span>
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
