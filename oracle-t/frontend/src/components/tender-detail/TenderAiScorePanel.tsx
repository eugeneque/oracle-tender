import { useState } from "react";
import {
  AlertOctagon,
  AlertTriangle,
  BookOpenText,
  ChevronDown,
  ChevronRight,
  Coins,
  Compass,
  FileText,
  Flag,
  Footprints,
  History,
  Info,
  Loader2,
  ShieldCheck,
  Target,
  Wand2,
} from "lucide-react";

import type { AiProfileScore, EvidenceItem } from "../../api/types";
import { useAiProvider } from "../../hooks/useAiProvider";
import { formatDateTime, percentValue, verdictLabel } from "../../utils/format";
import { AiOrb } from "../AiOrb";
import { AiProviderPicker } from "../AiProviderPicker";
import { DecisionMark } from "../DecisionMark";
import { decisionTitle } from "../../utils/decision";

/**
 * «Разбор ИИ» — AI-оценка по профилю (раздел 5.5.1 ТЗ), главный блок шапки карточки.
 *
 * Три измерения, у каждого своя цифра, комментарий и список обоснований. Обоснования
 * («на чём это основано») не украшение: раздел 5.5.1 прямо требует, чтобы каждое число
 * было прослеживаемо до источника, иначе проверить его нельзя, а решение об участии
 * принимается именно по нему.
 *
 * Измерение без данных показывается как «недостаточно данных», а не как 0% — это разные
 * утверждения, и второе клевещет на компанию.
 *
 * Шкалы измерений двух состояний (18.09.2026, по образцу индикатора усилия в Claude
 * Code): выше 50% — зелёная сетка точек с бегущей волной, интерфейс прямо сигнализирует
 * «можем участвовать»; ниже — сплошная красная полоса с лёгким переливом, без точек.
 * Ниже — резюме, слабые места с пометкой веса, стратегия
 * тремя плитками и сноска об источниках «Истории»; каждый раздел — своя карточка с иконкой.
 *
 * Гамма шапки — по модели текущего пользователя (выбор персональный, 18.09.2026):
 * розово-голубая у YandexGPT, оранжевая у Claude. Рядом с заголовком — бейдж с именем
 * модели, он же переключатель.
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

const DIMENSIONS = {
  История: {
    hint: "Наши прошлые участия в похожих закупках и у того же заказчика",
    icon: History,
  },
  Задача: {
    hint: "Насколько предмет закупки соответствует опыту и продукции компании",
    icon: Target,
  },
  Компетенции: {
    hint: "Обязательные допуски и стаж: требования тендера против профиля компании",
    icon: ShieldCheck,
  },
} as const;

type DimensionTitle = keyof typeof DIMENSIONS;

/** Порог «высокой» оценки: выше — зелёное состояние шкалы, иначе красное. */
const GOOD_THRESHOLD = 50;

function isGood(percent: number | null): boolean {
  return percent !== null && percent > GOOD_THRESHOLD;
}

/** Сетка зелёной шкалы: 4 ряда × 64 колонки, по точке на ~1,6%. */
const DOT_COLUMNS = 64;
const DOT_ROWS = 4;

/**
 * Шкала измерения. Зелёное состояние — плотная сетка точек с бегущей волной; красное —
 * сплошная полоса с медленным переливом. Без данных — пустая полоса.
 */
function ScoreBar({ percent }: { percent: number | null }) {
  if (!isGood(percent)) {
    return (
      <div className="solidbar" role="progressbar" aria-valuenow={percent ?? undefined} aria-valuemin={0} aria-valuemax={100}>
        {percent !== null && <div className="solidbar__fill" style={{ width: `${percent}%` }} />}
      </div>
    );
  }
  const filledExact = ((percent as number) / 100) * DOT_COLUMNS;
  const dots: JSX.Element[] = [];
  // Сетка строчная (ряд за рядом), чтобы колонки растягивались на всю ширину карточки;
  // волна бежит по колонкам — задержка считается от номера колонки.
  for (let row = 0; row < DOT_ROWS; row += 1) {
    for (let col = 0; col < DOT_COLUMNS; col += 1) {
      const colFill = Math.max(0, Math.min(1, filledExact - col));
      // Верхний ряд гаснет первым — граница заполнения читается как наклонный срез.
      const filled = colFill >= (DOT_ROWS - row) / DOT_ROWS - 0.001;
      dots.push(
        <span
          key={row * DOT_COLUMNS + col}
          className="dotbar__dot"
          data-filled={filled ? "true" : "false"}
          style={{ "--i": col } as React.CSSProperties}
        />,
      );
    }
  }
  return (
    <div
      className="dotbar"
      style={{ "--dot-columns": DOT_COLUMNS } as React.CSSProperties}
      role="progressbar"
      aria-valuenow={percent ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      {dots}
    </div>
  );
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
        <li key={`${item.type}-${item.ref_id}-${index}`} className="flex gap-2 text-xs text-zinc-400">
          <span className="mt-[3px] h-1.5 w-1.5 shrink-0 rounded-full bg-zinc-600" />
          <span>
            <span className="rounded bg-white/[0.05] px-1 py-px text-[10px] uppercase tracking-wide text-zinc-500">
              {EVIDENCE_LABELS[item.type] ?? "карточка"}
            </span>{" "}
            {item.note ?? item.ref_id}
          </span>
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
  title: DimensionTitle;
  score: string | null;
  comment: string | null;
  evidence: EvidenceItem[];
}) {
  const [isOpen, setIsOpen] = useState(false);
  const percent = percentValue(score);
  const meta = DIMENSIONS[title];
  const Icon = meta.icon;
  const good = isGood(percent);

  return (
    <div
      className={`rounded-xl border p-3 transition-colors ${
        good
          ? "border-emerald-400/25 bg-gradient-to-br from-emerald-500/[0.08] to-transparent"
          : percent === null
            ? "border-white/[0.08] bg-white/[0.02]"
            : "border-red-500/20 bg-gradient-to-br from-red-500/[0.06] to-transparent"
      }`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-medium text-zinc-300" title={meta.hint}>
          <span
            className={`flex h-5 w-5 items-center justify-center rounded-md ${
              good
                ? "bg-emerald-500/20 text-emerald-200"
                : percent === null
                  ? "bg-white/[0.06] text-zinc-400"
                  : "bg-red-500/15 text-red-300"
            }`}
          >
            <Icon size={11} />
          </span>
          {title}
        </span>
        <span
          className={`text-base font-semibold leading-none ${
            percent === null ? "" : good ? "text-emerald-200" : "text-red-300"
          }`}
        >
          {percent === null ? (
            <span className="text-[11px] font-normal text-zinc-600">нет данных</span>
          ) : (
            `${percent}%`
          )}
        </span>
      </div>

      <ScoreBar percent={percent} />

      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className="mt-2 flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
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

/** Карточка текстового раздела ниже шкал — с иконкой и своим оттенком, чтобы разделы не
 * сливались в одну колонку серого текста. */
function InfoSection({
  icon,
  title,
  tone,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  tone: "neutral" | "indigo" | "amber" | "emerald";
  children: React.ReactNode;
}) {
  const tones = {
    neutral: { card: "border-white/[0.08] bg-white/[0.02]", icon: "bg-white/[0.06] text-zinc-300" },
    indigo: { card: "border-indigo-500/20 bg-indigo-500/[0.05]", icon: "bg-indigo-500/15 text-indigo-300" },
    amber: { card: "border-amber-500/20 bg-amber-500/[0.04]", icon: "bg-amber-500/15 text-amber-300" },
    emerald: { card: "border-emerald-500/20 bg-emerald-500/[0.04]", icon: "bg-emerald-500/15 text-emerald-300" },
  }[tone];
  return (
    <section className={`rounded-xl border p-3.5 ${tones.card}`}>
      <h4 className="mb-2 flex items-center gap-2 text-xs font-semibold text-zinc-100">
        <span className={`flex h-6 w-6 items-center justify-center rounded-md ${tones.icon}`}>{icon}</span>
        {title}
      </h4>
      {children}
    </section>
  );
}

const SEVERITY_STYLE = {
  significant: {
    icon: AlertOctagon,
    chip: "border-red-500/30 bg-red-500/10 text-red-300",
    iconColor: "text-red-400",
  },
  moderate: {
    icon: AlertTriangle,
    chip: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    iconColor: "text-amber-400",
  },
  minor: {
    icon: Info,
    chip: "border-white/10 bg-white/5 text-zinc-400",
    iconColor: "text-zinc-500",
  },
} as const;

function severityStyle(severity: string) {
  return SEVERITY_STYLE[severity as keyof typeof SEVERITY_STYLE] ?? SEVERITY_STYLE.minor;
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
  const provider = useAiProvider();
  const providerKey = provider?.active_provider ?? "yandex";
  const isClaude = providerKey === "claude";

  return (
    <section
      className={`overflow-hidden rounded-xl border bg-white/[0.02] ${
        isClaude ? "border-orange-400/25" : "border-white/[0.1]"
      }`}
    >
      <div
        className={`ai-wave flex items-center gap-3 border-b px-4 py-3 ${
          isClaude ? "ai-wave--claude border-orange-400/15" : "border-white/[0.08]"
        }`}
      >
        <AiOrb size={40} busy={isRunning} variant={providerKey} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Разбор ИИ</h3>
            {provider && <AiProviderPicker status={provider} />}
            {overall !== null && (
              <span className="text-lg font-semibold leading-none text-zinc-100">{overall}%</span>
            )}
            {score?.verdict && (
              <span className="rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] font-medium tracking-wide text-zinc-300">
                {score.verdict_label ?? verdictLabel(score.verdict)}
              </span>
            )}
            {score && (
              <span
                className="flex items-center gap-1.5 text-[11px] text-zinc-500"
                title={decisionTitle(score.decision)}
              >
                <DecisionMark decision={score.decision} size="sm" />
                по трём измерениям
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-zinc-400">
            {isRunning
              ? "Модель сравнивает закупку с профилем компании…"
              : score
                ? `История, задача и компетенции против профиля компании · ${formatDateTime(
                    score.calculated_at,
                  )}`
                : "Модель сравнит закупку с профилем компании и скажет, стоит ли участвовать"}
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={isRunning}
          className={`flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs text-zinc-100 disabled:opacity-50 ${
            isClaude
              ? "border-orange-400/30 bg-orange-500/10 hover:bg-orange-500/20"
              : "border-white/15 bg-white/5 hover:bg-white/10"
          }`}
        >
          {isRunning ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
          {isRunning ? "Разбираю…" : score ? "Обновить разбор" : "Разобрать карточку"}
        </button>
      </div>

      <div className="px-4 py-3">
        {error && (
          <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        {isRunning && !score && (
          <p className="text-sm text-zinc-500">
            Идёт расчёт на сервере — карточку можно закрыть, работа продолжится. Обычно занимает
            около минуты: перед оценкой обновляются разделы вкладки «Дополнительно».
          </p>
        )}

        {isRunning && score && (
          <div
            className={`mb-3 flex items-center gap-2 text-xs ${isClaude ? "text-orange-300" : "text-indigo-300"}`}
          >
            <Loader2 size={13} className="animate-spin" />
            Идёт пересчёт на сервере — ниже прежний результат.
          </div>
        )}

        {!score && !isRunning && (
          <p className="text-sm text-zinc-500">
            {error
              ? "Разбор запускается сам при открытии карточки. Для него нужен заполненный профиль компании («Настройки → Профиль компании») — измерения «Задача» и «Компетенции» сравнивают требования закупки именно с ним."
              : "Разбор ещё не выполнялся. Он не заменяет анализ документов: здесь модель смотрит на закупку целиком — историю участий, предмет, требования к участникам — и сравнивает с профилем компании."}
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

            <div className="grid gap-3 lg:grid-cols-5">
              <div className="space-y-3 lg:col-span-3">
                {score.summary && (
                  <InfoSection icon={<FileText size={13} />} title="Резюме" tone="indigo">
                    <p className="text-[13px] leading-relaxed text-zinc-300">{score.summary}</p>
                  </InfoSection>
                )}

                <InfoSection icon={<AlertTriangle size={13} />} title="Слабые места" tone="amber">
                  {score.weak_points.length === 0 ? (
                    <p className="text-xs text-zinc-600">Слабых мест не отмечено.</p>
                  ) : (
                    <ol className="space-y-1.5">
                      {score.weak_points.map((point, index) => {
                        const style = severityStyle(point.severity);
                        const Icon = style.icon;
                        return (
                          <li
                            key={index}
                            className="flex gap-2.5 rounded-lg border border-white/[0.05] bg-black/20 px-3 py-2"
                          >
                            <Icon size={14} className={`mt-0.5 shrink-0 ${style.iconColor}`} />
                            <div className="min-w-0">
                              <span
                                className={`mb-1 inline-block rounded-full border px-1.5 py-px text-[10px] font-medium ${style.chip}`}
                              >
                                {point.severity_label}
                              </span>
                              <p className="text-[13px] leading-relaxed text-zinc-300">{point.text}</p>
                            </div>
                          </li>
                        );
                      })}
                    </ol>
                  )}
                </InfoSection>
              </div>

              <div className="space-y-3 lg:col-span-2">
                {score.recommended_strategy && (
                  <InfoSection icon={<Compass size={13} />} title="Рекомендуемая стратегия" tone="emerald">
                    <dl className="space-y-2">
                      {[
                        { icon: Flag, label: "Вердикт", value: score.recommended_strategy.verdict },
                        { icon: Coins, label: "Цена", value: score.recommended_strategy.price },
                        { icon: Footprints, label: "Первый шаг", value: score.recommended_strategy.first_step },
                      ].map(({ icon: Icon, label, value }) => (
                        <div
                          key={label}
                          className="flex gap-2.5 rounded-lg border border-white/[0.05] bg-black/20 px-3 py-2"
                        >
                          <Icon size={14} className="mt-0.5 shrink-0 text-emerald-300" />
                          <div className="min-w-0">
                            <dt className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</dt>
                            <dd className="text-[13px] leading-relaxed text-zinc-200">{value}</dd>
                          </div>
                        </div>
                      ))}
                    </dl>
                  </InfoSection>
                )}

                <InfoSection icon={<BookOpenText size={13} />} title="На чём построена «История»" tone="neutral">
                  {score.participation_ids.length === 0 && score.similar_tender_ids.length === 0 ? (
                    <p className="text-xs leading-relaxed text-zinc-500">
                      Недостаточно данных: по этой закупке нет ни наших прошлых участий, ни похожих
                      закупок с известным исходом. Историю участий можно подтянуть в разделе
                      «Моя компания».
                    </p>
                  ) : (
                    <ul className="space-y-1.5 text-xs text-zinc-400">
                      {score.participation_ids.length > 0 && (
                        <li className="flex items-center gap-2">
                          <History size={12} className="shrink-0 text-zinc-500" />
                          Наших участий в расчёте:{" "}
                          <span className="font-semibold text-zinc-200">{score.participation_ids.length}</span>
                          <span className="text-zinc-600">· «Моя компания → История участий»</span>
                        </li>
                      )}
                      {score.similar_tender_ids.length > 0 && (
                        <li className="flex items-center gap-2">
                          <Target size={12} className="shrink-0 text-zinc-500" />
                          Похожих тендеров:{" "}
                          <span className="font-semibold text-zinc-200">{score.similar_tender_ids.length}</span>
                          <span className="text-zinc-600">· вкладка «Похожие»</span>
                        </li>
                      )}
                    </ul>
                  )}
                </InfoSection>
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
