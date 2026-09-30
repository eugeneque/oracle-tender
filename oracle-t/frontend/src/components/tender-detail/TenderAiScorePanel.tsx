import { useState } from "react";
import {
  AlertOctagon,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Info,
  Loader2,
  MessageSquareReply,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

import type {
  AiConclusion,
  AiFeedback,
  AiProfileScore,
  ChecklistItem,
  ConclusionFit,
  ConclusionMetric,
  ConclusionRisk,
  EvidenceItem,
} from "../../api/types";
import { useAiProvider } from "../../hooks/useAiProvider";
import { formatDateTime, percentValue, verdictLabel } from "../../utils/format";
import { AiOrb } from "../AiOrb";
import { AiProviderPicker } from "../AiProviderPicker";

/**
 * «Заключение ИИ» (28.09.2026) — главный блок карточки закупки.
 *
 * Раньше здесь был «Разбор ИИ»: три шкалы профиля (История / Задача / Компетенции) и
 * шесть цветных карточек текста. Он отвечал на вопрос «похожа ли закупка на нас», а
 * тендерному отделу нужен другой ответ — проходим ли мы, каким прибором, а если нет — кто
 * проходит. Поэтому сверху стоит вывод, под ним приборы, затем стратегия входа, риски и
 * метрики. Измерения профиля не пропали — они в свёрнутом «Как считались измерения».
 *
 * С заключением специалист соглашается или спорит. Причина несогласия уходит модели на
 * пересмотр, ответ модели показывается здесь же, а вся переписка — в «Комментариях».
 */

const FIT_STYLE: Record<ConclusionFit, string> = {
  fit: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  fit_with_caveats: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  not_fit: "border-red-500/30 bg-red-500/10 text-red-300",
  unknown: "border-white/10 bg-white/5 text-zinc-400",
};

const PRODUCT_STATUS_STYLE: Record<string, string> = {
  fits: "text-emerald-300",
  partial: "text-amber-300",
  not_fits: "text-red-300",
  unchecked: "text-zinc-500",
};

const PRODUCT_STATUS_ICON: Record<string, typeof Check> = {
  fits: Check,
  partial: AlertTriangle,
  not_fits: AlertOctagon,
  unchecked: Info,
};

const SEVERITY_STYLE: Record<ConclusionRisk["severity"], { dot: string; label: string }> = {
  significant: { dot: "bg-red-400", label: "значительный" },
  moderate: { dot: "bg-amber-400", label: "умеренный" },
  minor: { dot: "bg-zinc-500", label: "незначительный" },
};

const METRIC_TONE: Record<ConclusionMetric["tone"], string> = {
  good: "text-emerald-300",
  warn: "text-amber-300",
  bad: "text-red-300",
  neutral: "text-zinc-100",
};

function SectionTitle({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-baseline justify-between gap-3">
      <h4 className="text-xs font-medium uppercase tracking-wide text-zinc-500">{children}</h4>
      {aside}
    </div>
  );
}

function Products({ conclusion }: { conclusion: AiConclusion }) {
  return (
    <div>
      <SectionTitle
        aside={
          !conclusion.matrix_built && (
            <span className="text-[11px] text-zinc-600">
              {conclusion.product_requirements
                ? "с каталогом не сравнивались"
                : "по ТЗ не проверялись"}
            </span>
          )
        }
      >
        Наши приборы
      </SectionTitle>
      {conclusion.our_products.length === 0 ? (
        <p className="text-sm text-zinc-500">Подходящего прибора в каталоге МИРТЕК не названо.</p>
      ) : (
        <ul className="divide-y divide-white/[0.06]">
          {conclusion.our_products.map((item, index) => {
            const Icon = PRODUCT_STATUS_ICON[item.status] ?? Info;
            return (
              <li key={item.product_id} className="flex gap-2.5 py-2 first:pt-0">
                <Icon size={15} className={`mt-0.5 shrink-0 ${PRODUCT_STATUS_STYLE[item.status]}`} />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className={`text-sm ${index === 0 ? "font-medium text-zinc-100" : "text-zinc-200"}`}>
                      {item.model}
                    </span>
                    <span className={`text-xs ${PRODUCT_STATUS_STYLE[item.status]}`}>{item.status_label}</span>
                  </div>
                  {item.note && <p className="text-xs leading-relaxed text-zinc-500">{item.note}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** Почему матрицы нет: от этого зависит, что делать — ждать документов или пересчитать. */
function matrixMissingText(conclusion: AiConclusion): string {
  const count = conclusion.product_requirements;
  if (count === 0) {
    return "В документации нет требований к товару — сравнивать приборы конкурентов не с чем.";
  }
  const extracted = count ? `ТЗ разобрано (требований к товару: ${count}), но ` : "";
  return (
    `${extracted}${count ? "м" : "М"}атрица соответствия не построена — ` +
    "«Обновить» достроит её и пересчитает заключение."
  );
}

function Competitors({ conclusion }: { conclusion: AiConclusion }) {
  return (
    <div>
      <SectionTitle>{conclusion.fit === "not_fit" ? "Кто проходит вместо нас" : "Кто ещё проходит"}</SectionTitle>
      {conclusion.competitors.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {conclusion.matrix_built ? "Проходящих конкурентов не названо." : matrixMissingText(conclusion)}
        </p>
      ) : (
        <ul className="divide-y divide-white/[0.06]">
          {conclusion.competitors.map((item) => {
            const Icon = PRODUCT_STATUS_ICON[item.status] ?? Info;
            return (
              <li key={item.manufacturer_id} className="flex gap-2.5 py-2 first:pt-0">
                <Icon size={15} className={`mt-0.5 shrink-0 ${PRODUCT_STATUS_STYLE[item.status]}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-sm text-zinc-200">
                      {item.manufacturer}
                      {item.model && <span className="text-zinc-400"> · {item.model}</span>}
                    </span>
                    <span className={`text-xs ${PRODUCT_STATUS_STYLE[item.status]}`}>{item.status_label}</span>
                  </div>
                  {item.note && <p className="text-xs leading-relaxed text-zinc-500">{item.note}</p>}
                </div>
                {item.matrix_percentage !== null && (
                  <span className="shrink-0 text-xs tabular-nums text-zinc-500" title="Соответствие по матрице">
                    {Math.round(item.matrix_percentage)}%
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Strategy({ strategy }: { strategy: NonNullable<AiConclusion["strategy"]> }) {
  return (
    <div>
      <SectionTitle>Стратегия входа</SectionTitle>
      <p className="text-sm leading-relaxed text-zinc-300">{strategy.approach}</p>
      {strategy.price && (
        <p className="mt-2 text-sm leading-relaxed text-zinc-400">
          <span className="text-zinc-500">Цена: </span>
          {strategy.price}
        </p>
      )}
      {strategy.steps.length > 0 && (
        <ol className="mt-3 space-y-1.5">
          {strategy.steps.map((step, index) => (
            <li key={index} className="flex gap-2.5 text-sm text-zinc-300">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/[0.06] text-[11px] tabular-nums text-zinc-400">
                {index + 1}
              </span>
              <span className="leading-relaxed">{step}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Risks({ risks }: { risks: ConclusionRisk[] }) {
  return (
    <div>
      <SectionTitle aside={<span className="text-[11px] text-zinc-600">{risks.length}</span>}>Риски</SectionTitle>
      {risks.length === 0 ? (
        <p className="text-sm text-zinc-500">Рисков не названо.</p>
      ) : (
        <ul className="divide-y divide-white/[0.06] rounded-lg border border-white/[0.06]">
          {risks.map((risk, index) => {
            const style = SEVERITY_STYLE[risk.severity] ?? SEVERITY_STYLE.moderate;
            return (
              <li key={index} className="grid grid-cols-[auto_1fr] gap-x-3 px-3 py-2.5">
                <span className={`mt-1.5 h-2 w-2 rounded-full ${style.dot}`} title={style.label} />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                      {risk.category_label}
                    </span>
                    <span className="text-sm leading-relaxed text-zinc-200">{risk.text}</span>
                  </div>
                  {risk.mitigation && (
                    <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">→ {risk.mitigation}</p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Metrics({ metrics }: { metrics: ConclusionMetric[] }) {
  if (metrics.length === 0) return null;
  return (
    <div>
      <SectionTitle>Метрики</SectionTitle>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
        {metrics.map((metric) => (
          <div key={metric.key} className="rounded-lg bg-white/[0.03] px-3 py-2" title={metric.hint ?? undefined}>
            <div className="text-[11px] text-zinc-500">{metric.label}</div>
            <div className={`text-sm font-medium tabular-nums ${METRIC_TONE[metric.tone]}`}>{metric.value}</div>
            {metric.hint && <div className="truncate text-[11px] text-zinc-600">{metric.hint}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

// --- как считались измерения профиля --------------------------------------------------------

const EVIDENCE_LABELS: Record<string, string> = {
  requirement: "требование",
  company_profile_field: "профиль компании",
  tender_document: "документ",
  similar_tender: "похожий тендер",
  company_participation: "наше участие",
  tender_field: "карточка",
};

const CHECK_STATUS_STYLE: Record<string, string> = {
  met: "text-emerald-300",
  partial: "text-amber-300",
  unknown: "text-amber-300",
  not_met: "text-red-300",
  not_applicable: "text-zinc-500",
};

function Dimension({
  title,
  score,
  comment,
  evidence,
  checklist = null,
}: {
  title: string;
  score: string | null;
  comment: string | null;
  evidence: EvidenceItem[];
  checklist?: ChecklistItem[] | null;
}) {
  const percent = percentValue(score);
  return (
    <div className="py-3 first:pt-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-zinc-200">{title}</span>
        <span className="text-sm tabular-nums text-zinc-400">
          {percent === null
            ? checklist !== null && checklist.length === 0
              ? "не применимо"
              : "нет данных"
            : `${percent}%`}
        </span>
      </div>
      {comment && <p className="mt-1 text-xs leading-relaxed text-zinc-400">{comment}</p>}
      {checklist && checklist.length > 0 && (
        <ul className="mt-2 space-y-1">
          {checklist.map((item, index) => (
            <li key={index} className="text-xs text-zinc-400">
              <span className={CHECK_STATUS_STYLE[item.status] ?? "text-zinc-500"}>{item.status_label}</span>
              <span className="text-zinc-300"> · {item.title}</span>
              {item.comment && <span className="text-zinc-500"> — {item.comment}</span>}
            </li>
          ))}
        </ul>
      )}
      {evidence.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {evidence.slice(0, 8).map((item, index) => (
            <li key={`${item.ref_id}-${index}`} className="text-[11px] text-zinc-500">
              <span className="text-zinc-600">{EVIDENCE_LABELS[item.type] ?? "карточка"}:</span>{" "}
              {item.note ?? item.ref_id}
            </li>
          ))}
          {evidence.length > 8 && <li className="text-[11px] text-zinc-600">и ещё {evidence.length - 8}</li>}
        </ul>
      )}
    </div>
  );
}

function DimensionsDetails({ score, defaultOpen = false }: { score: AiProfileScore; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-white/[0.06] pt-3">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        Как считались измерения профиля
      </button>
      {open && (
        <div className="mt-3 divide-y divide-white/[0.06]">
          <Dimension
            title="История — наши исходы в похожих закупках"
            score={score.history_score}
            comment={score.history_comment}
            evidence={score.history_evidence}
          />
          <Dimension
            title="Задача — предмет закупки против профиля"
            score={score.task_score}
            comment={score.task_comment}
            evidence={score.task_evidence}
            checklist={score.task_checklist}
          />
          <Dimension
            title="Компетенции — допуски и требования к участнику"
            score={score.competencies_score}
            comment={score.competencies_comment}
            evidence={score.competencies_evidence}
            checklist={score.competencies_checklist}
          />
        </div>
      )}
    </div>
  );
}

// --- ответ специалиста -------------------------------------------------------------------

function FeedbackBar({
  score,
  feedback,
  isReviewing,
  onAgree,
  onDisagree,
}: {
  score: AiProfileScore;
  feedback: AiFeedback[] | null;
  isReviewing: boolean;
  onAgree: () => Promise<void>;
  onDisagree: (text: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<"idle" | "disagree">("idle");
  const [text, setText] = useState("");
  const [isSending, setIsSending] = useState(false);

  const agreements = (feedback ?? []).filter(
    (item) => item.kind === "agree" && item.before?.score_id === score.id,
  );
  const lastError = (feedback ?? []).find((item) => item.kind === "disagree" && item.status === "error");

  const send = async (action: () => Promise<void>) => {
    setIsSending(true);
    try {
      await action();
      setMode("idle");
      setText("");
    } finally {
      setIsSending(false);
    }
  };

  if (isReviewing) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] px-3 py-2 text-xs text-indigo-300">
        <Loader2 size={13} className="animate-spin" />
        ИИ пересматривает заключение по замечанию — ниже прежний вариант.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {mode === "idle" ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-zinc-500">Согласны с заключением?</span>
          <button
            onClick={() => void send(onAgree)}
            disabled={isSending}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-emerald-500/40 hover:text-emerald-300 disabled:opacity-50"
          >
            <ThumbsUp size={13} />
            Согласен
          </button>
          <button
            onClick={() => setMode("disagree")}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-red-500/40 hover:text-red-300"
          >
            <ThumbsDown size={13} />
            Не согласен
          </button>
          {agreements.length > 0 && (
            <span className="text-[11px] text-zinc-600">
              Согласились: {agreements.map((item) => item.user_name ?? "специалист").join(", ")}
            </span>
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <label className="mb-1.5 block text-xs text-zinc-400">
            Почему вы не согласны? ИИ пересмотрит заключение по вашему замечанию.
          </label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            autoFocus
            placeholder="В п. 4.2 ТЗ требуется запись в ПП 719 — у МИР С-05 её нет"
            className="w-full resize-y rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={() => void send(() => onDisagree(text.trim()))}
              disabled={isSending || text.trim().length < 5}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-snow hover:bg-indigo-400 disabled:opacity-50"
            >
              {isSending ? <Loader2 size={13} className="animate-spin" /> : <MessageSquareReply size={13} />}
              Отправить на пересмотр
            </button>
            <button
              onClick={() => setMode("idle")}
              className="rounded-lg px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5"
            >
              Отмена
            </button>
          </div>
        </div>
      )}
      {lastError && (
        <p className="text-xs text-red-300">
          Пересмотр по замечанию не удался: {lastError.error ?? "ошибка модели"}. Отправьте замечание ещё раз.
        </p>
      )}
    </div>
  );
}

// --- панель ------------------------------------------------------------------------------

/** Честный срок: сама оценка — 1–3 минуты, но перед ней сервер достраивает матрицу
 *  соответствия, если её нет, — это по вызову модели на каждого из 17 производителей,
 *  10–15 минут на закупку с десятками требований (28.09.2026). */
function runningHint(progress: string | null): string {
  const tail = "Карточку можно закрыть — работа продолжится, в том числе после перезапуска сервера.";
  if (progress?.startsWith("Матрица")) {
    return `Сначала достраивается матрица соответствия — по вызову модели на каждого производителя, на закупке с десятками требований это 10–15 минут. ${tail}`;
  }
  return `Обычно 1–3 минуты; если перед оценкой нужно достроить матрицу соответствия — до 15 минут. ${tail}`;
}

export function TenderAiScorePanel({
  score,
  isRunning,
  isReviewing,
  progress = null,
  onRun,
  error,
  feedback,
  onAgree,
  onDisagree,
}: {
  score: AiProfileScore | null;
  isRunning: boolean;
  isReviewing: boolean;
  /** Ход задачи от сервера: «Матрица соответствия: 6 из 17 производителей — Энергомера». */
  progress?: string | null;
  onRun: () => void;
  error: string | null;
  feedback: AiFeedback[] | null;
  onAgree: () => Promise<void>;
  onDisagree: (text: string) => Promise<void>;
}) {
  const provider = useAiProvider();
  const providerKey = provider?.active_provider ?? "yandex";
  const conclusion = score?.conclusion ?? null;
  const busy = isRunning || isReviewing;

  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.02]">
      <div className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-3">
        <AiOrb size={28} busy={busy} variant={providerKey} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Заключение ИИ</h3>
            {provider && <AiProviderPicker status={provider} />}
          </div>
          <p className="truncate text-[11px] text-zinc-500">
            {isRunning
              ? progress ?? "Модель разбирает закупку…"
              : score
                ? `${formatDateTime(score.calculated_at)}${score.ai_model ? ` · ${score.ai_model}` : ""}`
                : "Проходим ли мы, каким прибором и кто ещё проходит"}
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={busy}
          title="Пересчитать заключение с учётом всех замечаний"
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
        >
          {isRunning ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          {score ? "Обновить" : "Разобрать"}
        </button>
      </div>

      <div className="space-y-5 px-4 py-4">
        {error && (
          <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>
        )}

        {isRunning && score && (
          <div className="flex items-center gap-2 text-xs text-indigo-300">
            <Loader2 size={13} className="animate-spin" />
            Идёт пересчёт на сервере — ниже прежний результат.
          </div>
        )}

        {!score && (
          <p className="text-sm text-zinc-500">
            {isRunning
              ? runningHint(progress)
              : error
                ? "Для заключения нужен заполненный профиль компании («Моя компания»)."
                : "Заключения ещё нет — нажмите «Разобрать». Модель прочитает документацию, сравнит закупку с каталогом и профилем компании и скажет, проходим ли мы и каким прибором."}
          </p>
        )}

        {score && conclusion && (
          <>
            <div>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${FIT_STYLE[conclusion.fit]}`}>
                  {conclusion.fit_label}
                </span>
                {score.verdict && (
                  <span className="text-xs text-zinc-500">
                    {score.verdict_label ?? verdictLabel(score.verdict)}
                  </span>
                )}
              </div>
              <p className="text-base font-medium leading-snug text-zinc-100">{conclusion.headline}</p>
              {conclusion.rationale && (
                <p className="mt-1.5 text-sm leading-relaxed text-zinc-400">{conclusion.rationale}</p>
              )}
            </div>

            {conclusion.feedback_response && (
              <div className="border-l-2 border-indigo-400/60 pl-3">
                <div className="text-[11px] font-medium uppercase tracking-wide text-indigo-300">
                  Ответ на замечание специалиста
                </div>
                <p className="mt-0.5 text-sm leading-relaxed text-zinc-300">{conclusion.feedback_response}</p>
              </div>
            )}

            <FeedbackBar
              score={score}
              feedback={feedback}
              isReviewing={isReviewing}
              onAgree={onAgree}
              onDisagree={onDisagree}
            />

            <div className="grid gap-5 border-t border-white/[0.06] pt-4 md:grid-cols-2">
              <Products conclusion={conclusion} />
              <Competitors conclusion={conclusion} />
            </div>

            {conclusion.strategy && (
              <div className="border-t border-white/[0.06] pt-4">
                <Strategy strategy={conclusion.strategy} />
              </div>
            )}

            <div className="border-t border-white/[0.06] pt-4">
              <Risks risks={conclusion.risks} />
            </div>

            <div className="border-t border-white/[0.06] pt-4">
              <Metrics metrics={conclusion.metrics} />
            </div>

            <DimensionsDetails score={score} />
          </>
        )}

        {score && !conclusion && (
          <>
            <div>
              {score.verdict && (
                <span className="mb-2 inline-block rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-zinc-300">
                  {score.verdict_label ?? verdictLabel(score.verdict)}
                </span>
              )}
              {score.summary && <p className="text-sm leading-relaxed text-zinc-300">{score.summary}</p>}
              <p className="mt-2 text-xs text-zinc-500">
                Разбор посчитан до появления заключения — нажмите «Обновить», чтобы узнать, каким
                прибором мы проходим и кто ещё проходит.
              </p>
            </div>
            <DimensionsDetails score={score} defaultOpen />
          </>
        )}
      </div>
    </section>
  );
}
