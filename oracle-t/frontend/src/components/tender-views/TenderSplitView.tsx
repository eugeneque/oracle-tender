import { Building2, Clock, Sparkles, Star } from "lucide-react";

import type { Tender } from "../../api/types";
import { TenderDetailPanel } from "../TenderDetailPanel";
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

  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-xl border px-3 py-2.5 text-left transition-colors ${
        isActive
          ? "border-indigo-500/40 bg-indigo-500/[0.08]"
          : "border-white/[0.06] bg-white/[0.02] hover:border-white/[0.14]"
      }`}
    >
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <span className="line-clamp-2 text-sm leading-snug text-zinc-100">
          {tender.is_bookmarked && (
            <Star
              size={12}
              fill="currentColor"
              className="mr-1 inline -translate-y-px text-amber-400"
              aria-label="В избранном"
            />
          )}
          {tender.title}
        </span>
        {/* Цветной бейдж AI-оценки — раздел 5.6 ТЗ: зелёный ≥80%, жёлтый 50-80%, красный
            ниже. «—» означает «не считалась», а не ноль. */}
        <span
          className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${scoreBadgeClass(score)}`}
          title="AI-оценка по профилю"
        >
          {score === null ? "—" : `${score}%`}
        </span>
      </div>

      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] text-zinc-500">
        {/* Метка модели показывается только при положительном решении: «ИИ посмотрел и
            отклонил» — состояние списка, а не свойство закупки, и загромождать им каждую
            карточку незачем. Отклонённые и так уходят вниз выдачи. */}
        {tender.ai_relevant === true && (
          <span
            className="flex items-center gap-1 rounded-md border border-indigo-400/40 bg-indigo-500/25 px-1.5 py-0.5 font-medium text-indigo-100 shadow-[0_0_0_1px_rgba(99,102,241,0.15)]"
            title={
              tender.ai_relevance_reason
                ? `Модель признала закупку профильной: ${tender.ai_relevance_reason}`
                : "Модель признала закупку профильной"
            }
          >
            <Sparkles size={11} />
            Подобрано ИИ
          </span>
        )}
        <span className="rounded bg-white/5 px-1.5 py-0.5">{stageLabel(tender.stage)}</span>
        {tender.customer_name && (
          <span className="flex min-w-0 items-center gap-1">
            <Building2 size={11} className="shrink-0" />
            <span className="truncate">{tender.customer_name}</span>
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        {left !== null && (
          <span className={`flex items-center gap-1 ${left <= 5 ? "text-red-400" : "text-zinc-500"}`}>
            <Clock size={11} />
            {left >= 0 ? `${left} дн.` : "срок истёк"}
          </span>
        )}
        {price && <span className="text-zinc-400">{price}</span>}
      </div>
    </button>
  );
}

export function TenderSplitView({
  tenders,
  selected,
  onSelect,
  onChanged,
}: {
  tenders: Tender[];
  selected: Tender | null;
  onSelect: (tender: Tender) => void;
  onChanged?: (tender: Tender) => void;
}) {
  // Высота берётся у родителя (`flex-1` страницы), а не считается как `100vh - 320px`:
  // подобранная константа ломалась от любой правки шапки или фильтров — панель то выпирала
  // за экран, то оставляла под собой пустую полосу.
  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <div className="w-[320px] shrink-0 overflow-y-auto pr-1">
        <div className="flex flex-col gap-2">
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

      <div className="min-w-0 flex-1 overflow-y-auto rounded-2xl border border-white/[0.08] bg-white/[0.02]">
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
