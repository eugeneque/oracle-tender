import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, RefreshCw, Sparkles } from "lucide-react";

import type { AiProfileScore, EvidenceItem } from "../../api/types";
import { formatDateTime, percentValue, verdictLabel } from "../../utils/format";

/**
 * AI-оценка по профилю (раздел 5.5.1 ТЗ) — главный блок шапки карточки.
 *
 * Три измерения, у каждого своя цифра, комментарий и список обоснований. Обоснования
 * («на чём это основано») не украшение: раздел 5.5.1 прямо требует, чтобы каждое число
 * было прослеживаемо до источника, иначе проверить его нельзя, а решение об участии
 * принимается именно по нему.
 *
 * Измерение без данных показывается как «недостаточно данных», а не как 0% — это разные
 * утверждения, и второе клевещет на компанию.
 */

/** Как назвать источник числа. Отдельной картой, а не цепочкой тернарников: список типов
 * растёт (03.09.2026 добавились участия в закупках), и в цепочке новый тип молча
 * превращался бы в «карточку». */
const EVIDENCE_LABELS: Record<string, string> = {
  requirement: "требование",
  company_profile_field: "профиль компании",
  tender_document: "документ",
  similar_tender: "похожий тендер",
  company_participation: "наше участие в закупке",
  tender_field: "карточка",
};

const DIMENSION_HINTS: Record<string, string> = {
  История: "Наши прошлые участия в похожих закупках и у того же заказчика",
  Задача: "Насколько предмет закупки соответствует опыту и продукции компании",
  Компетенции: "Обязательные допуски и стаж: требования тендера против профиля компании",
};

function barColor(percent: number): string {
  if (percent >= 80) return "bg-emerald-400";
  if (percent >= 50) return "bg-amber-400";
  return "bg-red-400";
}

function EvidenceList({ items }: { items: EvidenceItem[] }) {
  if (items.length === 0) {
    return (
      <p className="text-xs text-zinc-600">
        Модель не указала источников — число можно считать только ориентиром.
      </p>
    );
  }
  return (
    <ul className="space-y-1">
      {items.map((item, index) => (
        <li key={`${item.type}-${item.ref_id}-${index}`} className="text-xs text-zinc-500">
          <span className="text-zinc-600">
            {EVIDENCE_LABELS[item.type] ?? "карточка"}
            :{" "}
          </span>
          {item.note ?? item.ref_id}
        </li>
      ))}
    </ul>
  );
}

function Dimension({
  title,
  score,
  comment,
  evidence,
}: {
  title: string;
  score: string | null;
  comment: string | null;
  evidence: EvidenceItem[];
}) {
  const [isOpen, setIsOpen] = useState(false);
  const percent = percentValue(score);

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-zinc-300" title={DIMENSION_HINTS[title]}>
          {title}
        </span>
        <span className="text-sm font-semibold text-zinc-100">
          {percent === null ? <span className="text-xs text-zinc-600">нет данных</span> : `${percent}%`}
        </span>
      </div>

      <div className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
        {percent !== null && (
          <div className={`h-full rounded-full ${barColor(percent)}`} style={{ width: `${percent}%` }} />
        )}
      </div>

      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
      >
        {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {isOpen ? "Свернуть" : "Почему такая оценка"}
      </button>

      {isOpen && (
        <div className="mt-2 space-y-2 border-t border-white/[0.06] pt-2">
          <p className="text-xs leading-relaxed text-zinc-400">
            {comment ?? "Комментарий не сформирован."}
          </p>
          <EvidenceList items={evidence} />
        </div>
      )}
    </div>
  );
}

export function TenderAiScorePanel({
  score,
  isRunning,
  onRun,
  error,
}: {
  score: AiProfileScore | null;
  isRunning: boolean;
  onRun: () => void;
  error: string | null;
}) {
  const overall = percentValue(score?.overall_score ?? null);

  return (
    <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/[0.04] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Sparkles size={15} className="text-indigo-400" />
          <h3 className="text-sm font-semibold text-zinc-100">AI-оценка по профилю</h3>
          {overall !== null && (
            <span className="text-lg font-semibold text-zinc-100">{overall}%</span>
          )}
          {score?.verdict && (
            <span className="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] font-medium tracking-wide text-zinc-300">
              {score.verdict_label ?? verdictLabel(score.verdict)}
            </span>
          )}
        </div>
        <button
          onClick={onRun}
          disabled={isRunning}
          className="flex items-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs text-indigo-300 hover:bg-indigo-500/20 disabled:opacity-50"
        >
          {isRunning ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          {score ? "Пересчитать" : "Рассчитать"}
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {isRunning && (
        <div className="mb-3 flex items-center gap-2 text-xs text-indigo-300">
          <Loader2 size={13} className="animate-spin" />
          Идёт расчёт на сервере — карточку можно закрыть, работа продолжится.
        </div>
      )}

      {!score && !isRunning && (
        <p className="text-xs text-zinc-500">
          Оценка ещё не считалась. Для расчёта нужен заполненный профиль компании
          («Настройки → Профиль компании») — измерения «Задача» и «Компетенции» сравнивают
          требования закупки именно с ним.
        </p>
      )}

      {score && (
        <>
          <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Dimension
              title="История"
              score={score.history_score}
              comment={score.history_comment}
              evidence={score.history_evidence}
            />
            <Dimension
              title="Задача"
              score={score.task_score}
              comment={score.task_comment}
              evidence={score.task_evidence}
            />
            <Dimension
              title="Компетенции"
              score={score.competencies_score}
              comment={score.competencies_comment}
              evidence={score.competencies_evidence}
            />
          </div>

          <div className="space-y-3">
            {score.summary && (
              <section>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  Резюме
                </h4>
                <p className="text-sm leading-relaxed text-zinc-300">{score.summary}</p>
              </section>
            )}

            <section>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                Слабые места
              </h4>
              {score.weak_points.length === 0 ? (
                <p className="text-xs text-zinc-600">Слабых мест не отмечено.</p>
              ) : (
                <ol className="space-y-1">
                  {score.weak_points.map((point, index) => (
                    <li key={index} className="text-sm text-zinc-300">
                      <span
                        className={`mr-1.5 text-xs font-medium ${
                          point.severity === "significant"
                            ? "text-red-400"
                            : point.severity === "moderate"
                              ? "text-amber-400"
                              : "text-zinc-500"
                        }`}
                      >
                        {point.severity_label}
                      </span>
                      {point.text}
                    </li>
                  ))}
                </ol>
              )}
            </section>

            {score.recommended_strategy && (
              <section>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                  Рекомендуемая стратегия
                </h4>
                <dl className="space-y-0.5 text-sm text-zinc-300">
                  <div>
                    <span className="text-zinc-500">Вердикт: </span>
                    {score.recommended_strategy.verdict}
                  </div>
                  <div>
                    <span className="text-zinc-500">Цена: </span>
                    {score.recommended_strategy.price}
                  </div>
                  <div>
                    <span className="text-zinc-500">Первый шаг: </span>
                    {score.recommended_strategy.first_step}
                  </div>
                </dl>
              </section>
            )}

            <section>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                На чём построена «История»
              </h4>
              {score.participation_ids.length === 0 && score.similar_tender_ids.length === 0 ? (
                <p className="text-xs text-zinc-600">
                  Недостаточно данных: по этой закупке нет ни наших прошлых участий, ни
                  похожих закупок с известным исходом. Историю участий можно подтянуть в
                  разделе «Настройки → Моя компания».
                </p>
              ) : (
                <ul className="space-y-0.5 text-xs text-zinc-500">
                  {score.participation_ids.length > 0 && (
                    <li>
                      Наших участий в похожих закупках: {score.participation_ids.length} —
                      см. «Настройки → Моя компания → История участий».
                    </li>
                  )}
                  {score.similar_tender_ids.length > 0 && (
                    <li>
                      Похожих тендеров: {score.similar_tender_ids.length} — см. вкладку
                      «Похожие».
                    </li>
                  )}
                </ul>
              )}
            </section>
          </div>

          <p className="mt-3 text-[11px] text-zinc-600">
            Рассчитано: {formatDateTime(score.calculated_at)}
          </p>
        </>
      )}
    </div>
  );
}
