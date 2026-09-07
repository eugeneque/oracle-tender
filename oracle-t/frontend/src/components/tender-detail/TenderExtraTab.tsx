import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, RefreshCw } from "lucide-react";

import type { TenderExtraSections } from "../../api/types";
import { formatDateTime } from "../../utils/format";
import { InlineMarkdown } from "./InlineMarkdown";

/**
 * Вкладка «Дополнительно» (раздел 5.6 ТЗ) — девять разделов извлечённых условий закупки.
 *
 * Это ответ на недочёт «система не погружается вовнутрь тендера» с созвона 02.09.2026:
 * человеку нужны не только реквизиты извещения, но и то, что написано в документации —
 * допуски, условия контракта, требования к заявке.
 *
 * Пустой раздел показывается пустым, а не прячется: «в документации об этом не сказано» —
 * это сведение, и по нему принимают решения (например, отсутствие требований к допускам
 * означает, что участвовать может кто угодно).
 */
export function TenderExtraTab({
  data,
  isLoading,
  isRunning,
  onRun,
  error,
}: {
  data: TenderExtraSections | null;
  isLoading: boolean;
  isRunning: boolean;
  onRun: () => void;
  error: string | null;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const toggle = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Loader2 size={14} className="animate-spin" />
        Загрузка разделов…
      </div>
    );
  }

  const hasContent = (data?.sections ?? []).some((section) => section.points.length > 0);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-zinc-500">
          {data?.generated_at
            ? `Извлечено моделью: ${formatDateTime(data.generated_at)}`
            : "Разделы ещё не извлекались."}
        </p>
        <button
          onClick={onRun}
          disabled={isRunning}
          className="flex items-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs text-indigo-300 hover:bg-indigo-500/20 disabled:opacity-50"
        >
          {isRunning ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          {hasContent ? "Извлечь заново" : "Извлечь условия"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {!hasContent && !isRunning && (
        <div className="rounded-xl border border-dashed border-white/10 p-4 text-sm text-zinc-500">
          Условия закупки ещё не извлечены. Разбор читает карточку и документацию целиком,
          включая технические приложения.
        </div>
      )}

      {(data?.sections ?? []).map((section) => {
        const isOpen = !collapsed.has(section.key);
        return (
          <section
            key={section.key}
            className="rounded-xl border border-white/[0.08] bg-white/[0.02]"
          >
            <button
              onClick={() => toggle(section.key)}
              className="flex w-full items-center gap-2 px-4 py-2.5 text-left"
            >
              {isOpen ? <ChevronDown size={14} className="text-zinc-500" /> : <ChevronRight size={14} className="text-zinc-500" />}
              <span className="text-sm font-medium text-zinc-200">{section.title}</span>
              <span className="ml-auto text-xs text-zinc-600">
                {section.points.length > 0 ? section.points.length : "—"}
              </span>
            </button>
            {isOpen && (
              <div className="border-t border-white/[0.06] px-4 py-3">
                {section.points.length === 0 ? (
                  <p className="text-xs text-zinc-600">В документации об этом не сказано.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {section.points.map((point, index) => (
                      <li key={index} className="flex gap-2 text-sm leading-relaxed text-zinc-300">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-zinc-600" />
                        <span>
                          <InlineMarkdown text={point} />
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
