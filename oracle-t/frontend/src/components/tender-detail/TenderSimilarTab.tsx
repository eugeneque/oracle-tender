import { Loader2, RefreshCw } from "lucide-react";

import type { SimilarTender } from "../../api/types";
import { formatDate, formatPrice } from "../../utils/format";

/**
 * Вкладка «Похожие» (раздел 5.6 ТЗ): близкие по тексту закупки, они же вход измерения
 * History (раздел 5.5.1).
 *
 * Пока эмбеддинги по корпусу тендеров не посчитаны, вкладка показывает то же честное
 * «недостаточно данных», что и History, — а не молчаливую пустоту, из которой непонятно,
 * то ли похожих нет, то ли их не искали.
 */
export function TenderSimilarTab({
  items,
  isLoading,
  isRefreshing,
  onRefresh,
  error,
  onOpen,
}: {
  items: SimilarTender[] | null;
  isLoading: boolean;
  isRefreshing: boolean;
  onRefresh: () => void;
  error: string | null;
  onOpen?: (tenderId: string) => void;
}) {
  const header = (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <p className="text-xs text-zinc-500">
        Похожесть считается по тексту закупки и извлечённым требованиям.
      </p>
      <button
        onClick={onRefresh}
        disabled={isRefreshing}
        className="flex items-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs text-indigo-300 hover:bg-indigo-500/20 disabled:opacity-50"
      >
        {isRefreshing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
        Пересчитать
      </button>
    </div>
  );

  if (isLoading || items === null) {
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Loader2 size={14} className="animate-spin" />
        Поиск похожих тендеров…
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div>
        {header}
        {error && (
          <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            {error}
          </div>
        )}
        <div className="rounded-xl border border-dashed border-white/10 p-4 text-sm text-zinc-500">
          Недостаточно данных: похожие закупки ещё не рассчитаны. Поиск идёт по близости
          текста технического задания, и для него нужен накопленный корпус разобранных
          тендеров.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {header}
      {items.map((item) => (
        <div
          key={item.tender_id}
          role={onOpen ? "button" : undefined}
          tabIndex={onOpen ? 0 : undefined}
          onClick={() => onOpen?.(item.tender_id)}
          onKeyDown={(e) => {
            if (onOpen && (e.key === "Enter" || e.key === " ")) onOpen(item.tender_id);
          }}
          className={`rounded-xl border border-white/[0.08] bg-white/[0.02] px-4 py-3 ${
            onOpen ? "cursor-pointer hover:border-white/[0.15]" : ""
          }`}
        >
          <div className="mb-1 flex items-start justify-between gap-3">
            <span className="text-sm text-zinc-200">{item.title}</span>
            <span className="shrink-0 rounded-md bg-white/5 px-2 py-0.5 text-xs text-zinc-400">
              {Math.round(Number(item.similarity_score) * 100)}%
            </span>
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-zinc-500">
            {item.customer_name && <span>{item.customer_name}</span>}
            {formatDate(item.publish_date) && <span>{formatDate(item.publish_date)}</span>}
            {formatPrice(item.price, "RUB") && <span>{formatPrice(item.price, "RUB")}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
