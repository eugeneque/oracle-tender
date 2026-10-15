import { Fragment, useMemo, useState } from "react";
import { AlertTriangle, BookOpen, ChevronDown, ChevronRight } from "lucide-react";

import type { ComplianceEntry, ComplianceMatrix } from "../../api/types";
import { ParameterChip, PositionChip } from "./TenderRequirementsTab";
import {
  complianceLabel,
  complianceSourceLabel,
  percentTextClass,
  percentValue,
} from "../../utils/format";
import { VERDICT_CLASSES, VERDICT_LABELS } from "../../utils/meterKinds";

const STATUS_CLASSES: Record<string, string> = {
  meets: "bg-emerald-500/15 text-emerald-300",
  partial: "bg-amber-500/15 text-amber-300",
  not_meets: "bg-red-500/15 text-red-300",
  no_data: "bg-white/5 text-zinc-500",
};

const STATUS_SHORT: Record<string, string> = {
  meets: "✓",
  partial: "~",
  not_meets: "✕",
  no_data: "—",
};

/** Сводка по производителю: процент, обоснование и охват расчёта.
 *
 * Охват («учтено N из M») показывается всегда: процент считается только по требованиям с
 * определённым вердиктом (раздел 5.5 ТЗ), и без этой строки 100% по двум требованиям из
 * восьмидесяти выглядели бы как уверенная победа. */
function WinPercentageCard({
  row,
}: {
  row: ComplianceMatrix["win_percentages"][number];
}) {
  const percent = percentValue(row.percentage) ?? 0;
  const coverage =
    row.requirements_total > 0
      ? Math.round((row.requirements_scored / row.requirements_total) * 100)
      : 0;

  return (
    <div
      className={`rounded-xl border p-3 ${
        row.is_mirtek
          ? "border-indigo-500/30 bg-indigo-500/[0.06]"
          : "border-white/[0.08] bg-white/[0.02]"
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm text-zinc-200" title={row.manufacturer_name}>
          {row.manufacturer_name}
          {row.tz_reference && (
            <span
              className="ml-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[11px] text-emerald-300"
              title="ТЗ составлено по модели этого производителя"
            >
              эталон ТЗ
            </span>
          )}
        </span>
        {/* Процент — доля выполненного по оценённым требованиям; при «не проходит» он бывает
            и 94%, но зелёным его красить нельзя: заявку отклонят по критичному пункту. */}
        <span
          className={`text-lg font-semibold ${
            row.verdict === "fails"
              ? "text-red-400"
              : row.verdict === "unknown"
                ? "text-zinc-400"
                : percentTextClass(percent)
          }`}
        >
          {percent}%
        </span>
      </div>
      {row.verdict && (
        <span
          className={`mt-1 inline-block rounded-md px-1.5 py-0.5 text-[11px] ${VERDICT_CLASSES[row.verdict]}`}
        >
          {VERDICT_LABELS[row.verdict]}
        </span>
      )}
      <div className="mt-1 text-[11px] text-zinc-600">
        Учтено {row.requirements_scored} из {row.requirements_total} требований ({coverage}%)
      </div>
      {row.reason_summary && (
        <p className="mt-1.5 text-[11px] leading-snug text-zinc-500">{row.reason_summary}</p>
      )}
    </div>
  );
}

const VERDICT_ORDER = ["passes", "caveats", "fails", "unknown"] as const;

/** Итог по производителям — ответ на вопрос тендерного отдела «чьи приборы проходят по ТЗ,
 * а чьи нет» (файл «Параметры для ПУ», 25.09.2026). Не проходит — есть невыполненное
 * критичное требование; с оговорками — критичные выполнены, но не всё; не хватает данных —
 * по критичным требованиям нечего сравнить (пополнить справочник и пересчитать). */
function VerdictSummary({ rows }: { rows: ComplianceMatrix["win_percentages"] }) {
  const groups = VERDICT_ORDER.map((verdict) => ({
    verdict,
    rows: rows.filter((row) => row.verdict === verdict),
  })).filter((group) => group.rows.length > 0);
  if (groups.length === 0) return null;
  return (
    <div className="mb-4 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
      <div className="mb-2 text-xs font-medium text-zinc-400">Итог по производителям</div>
      <div className="space-y-1.5">
        {groups.map((group) => (
          <div key={group.verdict} className="flex flex-wrap items-baseline gap-1.5 text-[13px]">
            <span className={`rounded-md px-1.5 py-0.5 text-[11px] ${VERDICT_CLASSES[group.verdict]}`}>
              {VERDICT_LABELS[group.verdict]}
            </span>
            {group.rows.map((row, index) => (
              <span
                key={row.manufacturer_id}
                className={row.is_mirtek ? "text-indigo-300" : "text-zinc-300"}
                title={row.reason_summary ?? undefined}
              >
                {row.manufacturer_name}
                {row.tz_reference && <span className="ml-1 text-[11px] text-emerald-400">(эталон ТЗ)</span>}
                {index < group.rows.length - 1 ? "," : ""}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Матрица соответствия (раздел 5.5, 5.6 ТЗ): строки — требования, колонки — производители.
 *
 * Пояснения ТЗ требует показывать по каждой ячейке, но тринадцать производителей × полсотни
 * требований — это сотни предложений, которые нечитаемы разом. Поэтому в сетке стоят
 * короткие статусы, а пояснения раскрываются по клику на строку: пользователь смотрит их для
 * одного требования за раз, ровно когда вердикт вызывает вопрос.
 */
/** Методика оценки — коротко, там, где на неё смотрят (полный текст — `CRITERIA.md` в корне
 * проекта). Свёрнута по умолчанию: тендерщик читает её один раз, а матрицу — каждый день. */
function MethodologyHint() {
  const [open, setOpen] = useState(false);
  return (
    <div className="mb-3 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-xs">
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-1.5 text-zinc-400 hover:text-zinc-200"
      >
        <BookOpen size={13} className="text-indigo-400" />
        Как считается соответствие
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open && (
        <ul className="mt-2 space-y-1 text-zinc-400">
          <li>
            <span className="text-zinc-300">Источник истины по классам требований:</span>{" "}
            метрология и электрика — «Описание типа» ФГИС; интерфейсы, протоколы, конструктив
            — каталог, затем руководство; допуски (ПП 719, ЗАК Россетей, реестр ПО) — записи в
            карточке модели; интеграция в ПО верхнего уровня — списки разработчиков ПО.
          </li>
          <li>
            <span className="text-zinc-300">Вердикт:</span> подтверждающий факт — «соответствует»
            (1,0); часть исполнений или косвенно — «частично» (0,5); опровергающий факт — «не
            соответствует» (0). Отсутствие факта — «нет данных», оно не участвует в проценте и
            не считается несоответствием. Исключения: истекшая или отсутствующая запись в реестре
            допуска и отсутствие в официальном списке ПО — это «не соответствует».
          </li>
          <li>
            <span className="text-zinc-300">Вес:</span> 3 — ключевые параметры (класс точности,
            номиналы, фазы, тип прибора, Госреестр СИ, допуски), 2 — интерфейсы, протоколы, реле,
            МПИ, интеграция, 1 — остальное.
          </li>
          <li>
            <span className="text-zinc-300">Процент</span> = Σ(вес × вклад) / Σ(вес оценённых) ×
            100. Оценено меньше 30 % требований — процент ненадёжен; невыполненный ключевой
            параметр назван в пояснении: заявку отклонят независимо от процента.
          </li>
          <li>
            <span className="text-zinc-300">Уверенность</span> ниже 0,6 — вердикт помечен как
            требующий проверки человеком.
          </li>
          <li>
            <span className="text-zinc-300">Параметры для ПУ</span> (значок «П№» у требования) —
            перечень тендерного отдела из 39 параметров с правилами трактовки: тип прибора по
            напряжению, токам и креплению; трёхпозиционный переключатель реле — только
            Энергомера, Тайпит, Пульсар; ПП 719 — всегда ручная проверка в ГИСП; Astra Linux —
            по каталогу «Ready for Astra».
          </li>
          <li>
            <span className="text-zinc-300">Итог:</span> не проходит — не выполнен ключевой
            параметр; с оговорками — ключевые выполнены, но не всё; не хватает данных — без
            ответа больше половины ключевых.
          </li>
        </ul>
      )}
    </div>
  );
}

export function TenderComplianceTab({
  matrix,
  requirementsCount,
}: {
  matrix: ComplianceMatrix;
  /** Сколько требований извлечено из документации. Нужно, чтобы объяснить пустую матрицу:
   *  «нечего сопоставлять» и «расчёт ещё не запускали» — разные ситуации с разными
   *  следующими шагами. `null` — список ещё грузится. */
  requirementsCount: number | null;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const manufacturers = useMemo(() => {
    const seen = new Map<string, { id: string; name: string; isMirtek: boolean; percent: number | null }>();
    for (const row of matrix.win_percentages) {
      seen.set(row.manufacturer_id, {
        id: row.manufacturer_id,
        name: row.manufacturer_name,
        isMirtek: row.is_mirtek,
        percent: percentValue(row.percentage),
      });
    }
    for (const entry of matrix.entries) {
      if (!seen.has(entry.manufacturer_id))
        seen.set(entry.manufacturer_id, {
          id: entry.manufacturer_id,
          name: entry.manufacturer_name,
          isMirtek: false,
          percent: null,
        });
    }
    // МИРТЕК — первым столбцом, сразу после требования: с ним сравнивают всех остальных.
    return [...seen.values()].sort((a, b) => Number(b.isMirtek) - Number(a.isMirtek));
  }, [matrix]);

  const byRequirement = useMemo(() => {
    const map = new Map<string, Map<string, ComplianceEntry>>();
    for (const entry of matrix.entries) {
      const row = map.get(entry.requirement_id) ?? new Map<string, ComplianceEntry>();
      row.set(entry.manufacturer_id, entry);
      map.set(entry.requirement_id, row);
    }
    return map;
  }, [matrix]);

  if (matrix.entries.length === 0) {
    // Причина пустой матрицы называется прямо. Раньше здесь в любом случае предлагалось
    // «запустить расчёт соответствия» — и когда требования не извлечены, это отправляло
    // человека нажимать кнопку, которая только что честно отработала вхолостую: сравнивать
    // каталог не с чем, пока нет требований.
    const noRequirements = requirementsCount === 0;
    if (requirementsCount === null) {
      return (
        <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500">
          Загрузка…
        </div>
      );
    }
    return (
      <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500">
        {noRequirements ? (
          <>
            <p className="text-zinc-400">Матрицу соответствия не с чем сопоставлять.</p>
            <p className="mt-1.5">Требований к товару нет — смотрите AI-оценку на вкладке «Основное».</p>
          </>
        ) : (
          <>
            <p className="text-zinc-400">Матрица соответствия ещё не построена.</p>
            <p className="mt-1.5">Запустите «Разобрать закупку».</p>
          </>
        )}
      </div>
    );
  }

  const reviewCount = matrix.entries.filter((entry) => entry.needs_human_review).length;

  return (
    <div>
      <VerdictSummary rows={matrix.win_percentages} />

      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {matrix.win_percentages.map((row) => (
          <WinPercentageCard key={row.manufacturer_id} row={row} />
        ))}
      </div>

      <MethodologyHint />

      {reviewCount > 0 && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-300">
          <AlertTriangle size={14} className="shrink-0" />
          {reviewCount} вердикт(ов) с низкой уверенностью — отмечены значком и требуют
          проверки человеком
        </div>
      )}

      {/* Таблица не сжимается под ширину панели (14.10.2026): при десятке производителей
          колонки по 72 px обрезали названия, а текст требования ломался в столбик. Теперь у
          колонок есть минимальная ширина, таблица прокручивается вбок, а столбец требований
          закреплён слева — при прокрутке видно, о каком требовании речь. */}
      <div className="overflow-x-auto rounded-xl border border-white/[0.08]">
        <table className="w-full min-w-max border-separate border-spacing-0 text-[13px]">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 min-w-[320px] max-w-[440px] border-b border-r border-white/[0.08] bg-zinc-900 px-4 py-3 text-left text-xs font-medium text-zinc-400">
                Требование
                <span className="ml-1.5 font-normal tabular-nums text-zinc-600">{matrix.requirements.length}</span>
              </th>
              {manufacturers.map((manufacturer) => (
                <th
                  key={manufacturer.id}
                  className={`min-w-[104px] max-w-[132px] border-b border-white/[0.08] px-2 py-3 text-center align-bottom ${
                    manufacturer.isMirtek ? "bg-indigo-500/[0.08]" : ""
                  }`}
                  title={manufacturer.name}
                >
                  <span
                    className={`line-clamp-2 text-[11px] font-medium leading-tight ${
                      manufacturer.isMirtek ? "text-indigo-300" : "text-zinc-300"
                    }`}
                  >
                    {manufacturer.name}
                  </span>
                  {manufacturer.percent !== null && (
                    <span className={`mt-1 block text-xs font-semibold tabular-nums ${percentTextClass(manufacturer.percent)}`}>
                      {manufacturer.percent}%
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.requirements.map((requirement) => {
              const row = byRequirement.get(requirement.id);
              const isExpanded = expanded === requirement.id;
              return (
                <Fragment key={requirement.id}>
                  <tr
                    onClick={() => setExpanded(isExpanded ? null : requirement.id)}
                    className="group cursor-pointer"
                  >
                    <td className="sticky left-0 z-10 min-w-[320px] max-w-[440px] border-b border-r border-white/[0.06] bg-zinc-900 px-4 py-3 align-top transition-colors group-hover:bg-zinc-800">
                      <div className="flex items-start gap-2">
                        <ChevronRight
                          size={14}
                          className={`mt-0.5 shrink-0 text-zinc-600 transition-transform ${
                            isExpanded ? "rotate-90 text-zinc-400" : ""
                          }`}
                        />
                        <span className="flex-1 leading-snug text-zinc-200">{requirement.text}</span>
                        <PositionChip requirement={requirement} />
                        <ParameterChip requirement={requirement} />
                      </div>
                    </td>
                    {manufacturers.map((manufacturer) => {
                      const entry = row?.get(manufacturer.id);
                      const status = entry?.status ?? "no_data";
                      return (
                        <td
                          key={manufacturer.id}
                          className={`border-b border-white/[0.06] px-2 py-3 text-center align-middle transition-colors group-hover:bg-white/[0.03] ${
                            manufacturer.isMirtek ? "bg-indigo-500/[0.05]" : ""
                          }`}
                        >
                          <span
                            title={entry ? complianceLabel(entry.status) : "Нет данных"}
                            className={`relative inline-flex h-7 w-7 items-center justify-center rounded-lg text-sm ${STATUS_CLASSES[status]}`}
                          >
                            {STATUS_SHORT[status]}
                            {entry?.needs_human_review && (
                              <span
                                className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-amber-400"
                                title="Требует проверки человеком"
                              />
                            )}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td colSpan={manufacturers.length + 1} className="border-b border-white/[0.06] bg-white/[0.02] p-0">
                        {/* Пояснения — карточками в сетке, а не строкой через запятую: у
                            каждого производителя своё обоснование, и сравнивают их глазами. */}
                        <div className="sticky left-0 grid w-full max-w-[40rem] grid-cols-1 gap-2 p-3 sm:grid-cols-2">
                          {manufacturers.map((manufacturer) => {
                            const entry = row?.get(manufacturer.id);
                            if (!entry) return null;
                            const source = complianceSourceLabel(entry.source);
                            return (
                              <div
                                key={manufacturer.id}
                                className={`rounded-lg border px-3 py-2 text-[12px] leading-snug ${
                                  manufacturer.isMirtek
                                    ? "border-indigo-500/25 bg-indigo-500/[0.05]"
                                    : "border-white/[0.06] bg-zinc-900/60"
                                }`}
                              >
                                <div className="mb-1 flex items-center justify-between gap-2">
                                  <span className="truncate font-medium text-zinc-300">{manufacturer.name}</span>
                                  <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11px] ${STATUS_CLASSES[entry.status]}`}>
                                    {complianceLabel(entry.status)}
                                  </span>
                                </div>
                                {entry.explanation && <p className="text-zinc-400">{entry.explanation}</p>}
                                {source && <p className="mt-1 text-[11px] text-zinc-600">Источник: {source}</p>}
                                {entry.needs_human_review && (
                                  <p className="mt-1 text-[11px] text-amber-400">Низкая уверенность — проверьте вручную</p>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 px-1 text-[11px] text-zinc-500">
        {(["meets", "partial", "not_meets", "no_data"] as const).map((status) => (
          <span key={status} className="flex items-center gap-1.5">
            <span className={`inline-flex h-4 w-4 items-center justify-center rounded text-[10px] ${STATUS_CLASSES[status]}`}>
              {STATUS_SHORT[status]}
            </span>
            {complianceLabel(status)}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-amber-400" />
          требует проверки
        </span>
        <span className="text-zinc-600">Нажмите на строку — пояснения по каждому производителю.</span>
      </div>
    </div>
  );
}
