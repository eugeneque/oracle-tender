import { Fragment, useMemo, useState } from "react";
import { AlertTriangle, BookOpen, ChevronDown, ChevronRight } from "lucide-react";

import type { ComplianceEntry, ComplianceMatrix } from "../../api/types";
import { CriticalityBadge } from "./TenderRequirementsTab";
import {
  complianceLabel,
  complianceSourceLabel,
  percentTextClass,
  percentValue,
} from "../../utils/format";

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

function pluralRequirements(count: number): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "требований";
  switch (count % 10) {
    case 1:
      return "требование";
    case 2:
    case 3:
    case 4:
      return "требования";
    default:
      return "требований";
  }
}

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
          {row.is_mirtek && <span className="ml-1.5 text-[11px] text-indigo-400">наш</span>}
        </span>
        <span className={`text-lg font-semibold ${percentTextClass(percent)}`}>{percent}%</span>
      </div>
      <div className="mt-1 text-[11px] text-zinc-600">
        Учтено {row.requirements_scored} из {row.requirements_total} требований ({coverage}%)
      </div>
      {row.reason_summary && (
        <p className="mt-1.5 text-[11px] leading-snug text-zinc-500">{row.reason_summary}</p>
      )}
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
            <span className="text-zinc-300">Вес:</span> критичное 3 (класс точности, номиналы,
            фазы, тип прибора, Госреестр СИ, допуски), важное 2 (интерфейсы, протоколы, реле, МПИ,
            интеграция), второстепенное 1.
          </li>
          <li>
            <span className="text-zinc-300">Процент</span> = Σ(вес × вклад) / Σ(вес оценённых) ×
            100. Оценено меньше 30 % требований — процент ненадёжен; невыполненное критичное
            требование названо в пояснении: заявку отклонят независимо от процента.
          </li>
          <li>
            <span className="text-zinc-300">Уверенность</span> ниже 0,6 — вердикт помечен как
            требующий проверки человеком.
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
    const seen = new Map<string, string>();
    for (const row of matrix.win_percentages) {
      seen.set(row.manufacturer_id, row.manufacturer_name);
    }
    for (const entry of matrix.entries) {
      if (!seen.has(entry.manufacturer_id)) seen.set(entry.manufacturer_id, entry.manufacturer_name);
    }
    return [...seen.entries()].map(([id, name]) => ({ id, name }));
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
            <p className="mt-1.5">
              Из документации не извлечено ни одного требования к товару, а расчёт сравнивает
              каталог продукции именно с ними. Если «Разобрать закупку» уже отработал, а
              требований к товару нет — это закупка на работы или услуги: её соответствие
              оценивает AI-оценка по профилю на вкладке «Основное», а матрица здесь не
              строится.
            </p>
          </>
        ) : (
          <>
            <p className="text-zinc-400">Матрица соответствия ещё не построена.</p>
            <p className="mt-1.5">
              Запустите «Разобрать закупку» — система сопоставит {requirementsCount}{" "}
              {pluralRequirements(requirementsCount)} к товару с каталогом продукции и
              посчитает процент победителя (раздел 5.5 ТЗ).
            </p>
          </>
        )}
      </div>
    );
  }

  const reviewCount = matrix.entries.filter((entry) => entry.needs_human_review).length;

  return (
    <div>
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

      <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
        <table className="w-full min-w-[640px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-white/[0.08]">
              <th className="px-2.5 py-2 text-left text-xs font-medium text-zinc-500">
                Требование
              </th>
              {manufacturers.map((manufacturer) => (
                <th
                  key={manufacturer.id}
                  className="px-1.5 py-2 text-center text-[11px] font-medium text-zinc-500"
                  title={manufacturer.name}
                >
                  <span className="block max-w-[72px] truncate">{manufacturer.name}</span>
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
                    className="cursor-pointer border-b border-white/[0.04] transition-colors hover:bg-white/[0.04]"
                  >
                    <td className="px-2.5 py-2">
                      <div className="flex items-start gap-1.5">
                        <ChevronRight
                          size={13}
                          className={`mt-0.5 shrink-0 text-zinc-600 transition-transform ${
                            isExpanded ? "rotate-90" : ""
                          }`}
                        />
                        <span className="text-zinc-300">{requirement.text}</span>
                        <CriticalityBadge value={requirement.criticality} />
                      </div>
                    </td>
                    {manufacturers.map((manufacturer) => {
                      const entry = row?.get(manufacturer.id);
                      return (
                        <td key={manufacturer.id} className="px-1.5 py-2 text-center">
                          <span
                            title={entry ? complianceLabel(entry.status) : "Нет данных"}
                            className={`inline-flex h-6 w-6 items-center justify-center rounded-md text-xs ${
                              STATUS_CLASSES[entry?.status ?? "no_data"]
                            }`}
                          >
                            {STATUS_SHORT[entry?.status ?? "no_data"]}
                          </span>
                          {entry?.needs_human_review && (
                            <span className="ml-0.5 text-[10px] text-amber-400" title="Требует проверки человеком">
                              !
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                  {isExpanded && (
                    <tr className="border-b border-white/[0.04]">
                      <td colSpan={manufacturers.length + 1} className="bg-white/[0.02] px-3 py-2.5">
                        <div className="space-y-1.5">
                          {manufacturers.map((manufacturer) => {
                            const entry = row?.get(manufacturer.id);
                            if (!entry) return null;
                            const source = complianceSourceLabel(entry.source);
                            return (
                              <div key={manufacturer.id} className="text-[12px] leading-snug">
                                <span className="text-zinc-400">{manufacturer.name}: </span>
                                <span
                                  className={
                                    STATUS_CLASSES[entry.status]?.split(" ").pop() ?? "text-zinc-400"
                                  }
                                >
                                  {complianceLabel(entry.status)}
                                </span>
                                {entry.explanation && (
                                  <span className="text-zinc-500"> — {entry.explanation}</span>
                                )}
                                {source && (
                                  <span className="text-zinc-600"> (источник: {source})</span>
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
    </div>
  );
}
