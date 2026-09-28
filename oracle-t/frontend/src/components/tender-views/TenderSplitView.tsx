import type { ReactNode } from "react";
import { Clock, Sparkles, Star } from "lucide-react";

import type { Tender } from "../../api/types";
import { DecisionMark } from "../DecisionMark";
import { TenderDetailPanel } from "../TenderDetailPanel";
import { TagRow } from "../tags/TagChip";
import {
  daysLeft,
  formatPrice,
  percentValue,
  scoreBadgeClass,
  stageLabel,
} from "../../utils/format";

/**
 * Двухпанельный режим (раздел 5.6 ТЗ, решение 03.09.2026) — основной вид приложения.
 *
 * Слева узкий список закупок, справа карточка выбранной: работа тендерного отдела — это
 * перебор десятков закупок подряд, и открывать-закрывать модалку на каждую значит терять
 * место в списке при каждом возврате. Остальные режимы (Kanban, таблица, таймплан) никуда
 * не делись — переключатель тот же.
 */

function SplitListItem({
  tender,
  isActive,
  onSelect,
}: {
  tender: Tender;
  isActive: boolean;
  onSelect: () => void;
}) {
  const left = daysLeft(tender.application_end);
  const price = formatPrice(tender.price, tender.currency);
  const score = percentValue(tender.ai_score);

  // Строка списка упрощена 28.09.2026: без рамки, плашки этапа «Новая» (её получает каждая
  // собранная закупка — это не информация) и крупной метки «Наша тематика». Список — это
  // оглавление; всё подробное живёт в карточке справа.
  return (
    <button
      onClick={onSelect}
      aria-current={isActive}
      className={`relative w-full rounded-lg px-3 py-2.5 text-left transition-colors ${
        isActive ? "bg-white/[0.07]" : "hover:bg-white/[0.03]"
      }`}
    >
      {isActive && (
        <span className="absolute inset-y-2.5 left-0 w-0.5 rounded-full bg-indigo-400" />
      )}
      <div className="flex items-start gap-2">
        <DecisionMark decision={tender.ai_decision} size="sm" className="mt-0.5" />
        <span
          className={`line-clamp-2 min-w-0 flex-1 text-[13px] leading-snug ${
            isActive ? "text-white" : "text-zinc-200"
          }`}
        >
          {tender.is_bookmarked && (
            <Star
              size={11}
              fill="currentColor"
              className="mr-1 inline -translate-y-px text-amber-400"
              aria-label="В избранном"
            />
          )}
          {tender.title}
        </span>
        {/* Цветная AI-оценка — раздел 5.6 ТЗ: зелёный ≥80%, жёлтый 50-80%, красный ниже.
            «—» означает «не считалась», а не ноль. */}
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${scoreBadgeClass(score)}`}
          title="AI-оценка по профилю"
        >
          {score === null ? "—" : `${score}%`}
        </span>
      </div>

      {tender.customer_name && (
        <div className="mt-1 truncate pl-6 text-[11px] text-zinc-500" title={tender.customer_name}>
          {tender.customer_name}
        </div>
      )}

      <div className="mt-1 flex items-center gap-2 pl-6 text-[11px] text-zinc-500">
        {left !== null && (
          <span className={`flex items-center gap-1 ${left <= 5 ? "text-red-400" : ""}`}>
            <Clock size={11} />
            {left >= 0 ? `${left} дн.` : "срок истёк"}
          </span>
        )}
        {price && <span className="tabular-nums text-zinc-400">{price}</span>}
        {tender.stage !== "ai_selected" && (
          <span className="text-zinc-400">· {stageLabel(tender.stage)}</span>
        )}
        {/* «Наша тематика» — иконкой: модель признала предмет профильным. Это не
            рекомендация участвовать — за неё отвечает знак решения слева от названия. */}
        {tender.ai_relevant === true && (
          <span
            className="ml-auto text-indigo-400"
            title={
              tender.ai_relevance_reason
                ? `Наша тематика: ${tender.ai_relevance_reason}`
                : "Наша тематика — модель признала предмет закупки профильным"
            }
          >
            <Sparkles size={12} />
          </span>
        )}
      </div>

      {tender.tags.length > 0 && (
        <div className="mt-1.5 pl-6">
          <TagRow tags={tender.tags} max={2} />
        </div>
      )}
    </button>
  );
}

export function TenderSplitView({
  tenders,
  selected,
  onSelect,
  onChanged,
  footer,
}: {
  tenders: Tender[];
  selected: Tender | null;
  onSelect: (tender: Tender) => void;
  onChanged?: (tender: Tender) => void;
  /** Пагинация — под списком, а не под обеими панелями: она листает именно список, а
   * отдельная полоса на всю ширину отнимала высоту у карточки. */
  footer?: ReactNode;
}) {
  // Высота берётся у родителя (`flex-1` страницы), а не считается как `100vh - 320px`:
  // подобранная константа ломалась от любой правки шапки или фильтров — панель то выпирала
  // за экран, то оставляла под собой пустую полосу.
  return (
    <div className="flex min-h-0 flex-1 gap-3">
      <div className="flex w-[300px] shrink-0 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          <div className="flex flex-col gap-0.5">
            {tenders.map((tender) => (
              <SplitListItem
                key={tender.id}
                tender={tender}
                isActive={selected?.id === tender.id}
                onSelect={() => onSelect(tender)}
              />
            ))}
          </div>
        </div>
        {footer}
      </div>

      <div className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02]">
        {selected ? (
          <TenderDetailPanel key={selected.id} tender={selected} onChanged={onChanged} />
        ) : (
          <div className="flex h-full items-center justify-center px-6 text-center text-sm text-zinc-600">
            Выберите закупку в списке слева
          </div>
        )}
      </div>
    </div>
  );
}
