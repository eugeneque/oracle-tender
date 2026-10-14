import type { Requirement, RequirementKind } from "../../api/types";

/** Вид требования (18.09.2026) — заголовки разделов вкладки. Порядок — по тому, что
 * тендерщик читает первым: требования к товару идут в матрицу соответствия, остальное —
 * к работам, участнику и условиям поставки — проверяется вручную. */
const KIND_SECTIONS: { kind: RequirementKind; title: string; hint: string }[] = [
  { kind: "product", title: "К товару", hint: "сопоставляются с каталогом на вкладке «Конкуренция»" },
  { kind: "service", title: "К работам и услугам", hint: "проверяются вручную" },
  { kind: "participant", title: "К участнику", hint: "проверяются вручную" },
  { kind: "supply", title: "Условия поставки", hint: "проверяются вручную" },
];

/** Значок параметра файла «Параметры для ПУ» — по нему требование сопоставляется с
 * правилами тендерного отдела. */
export function ParameterChip({ requirement }: { requirement: Pick<Requirement, "parameter_no" | "parameter_name"> }) {
  if (!requirement.parameter_no) return null;
  return (
    <span
      className="shrink-0 rounded-md bg-indigo-500/10 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-indigo-300"
      title={`Параметр ${requirement.parameter_no} файла «Параметры для ПУ»: ${requirement.parameter_name ?? ""}`}
    >
      П{requirement.parameter_no}
    </span>
  );
}

/** Итог последнего запуска анализа. `null` — сведений нет (ещё грузятся). */
export type AnalysisState = {
  /** Анализ хотя бы раз выполнялся до конца. */
  ran: boolean;
  /** Что он сообщил — например, «требований сохранено: 0; неразобранных фрагментов: 12». */
  message: string | null;
  /** Документов, из которых удалось извлечь текст. Ноль объясняет пустоту сам по себе. */
  documentsWithText: number | null;
};


/** Пустая вкладка должна называть причину, а не советовать нажать кнопку, которая только что
 * отработала вхолостую. Причин ровно три, и они требуют разных действий от человека:
 * анализ не запускали; документы не разобраны (значит дело в них, а не в анализе); анализ
 * отработал и требований к товару в документации не нашёл — так бывает у закупок на работы,
 * где техническое задание не опубликовано. */
function EmptyRequirements({ analysis }: { analysis: AnalysisState | null }) {
  const shell =
    "rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500";

  // Сведения о документах приходят отдельным запросом, и он небыстрый: у закупки без
  // документов сервер сначала пробует запасной путь через ЕИС, а когда тот сбрасывает
  // соединение — ждёт все повторы. Пока ответа нет, `documentsWithText === null`, и
  // выбирать между «не из чего извлекать» и «документы разобраны» нельзя: второе
  // утверждение через полминуты окажется ложью.
  if (analysis === null || (analysis.ran && analysis.documentsWithText === null)) {
    return <div className={shell}>Загрузка…</div>;
  }

  if (!analysis.ran) {
    return (
      <div className={shell}>
        Требования ещё не извлечены. Запустите «Разобрать закупку».
      </div>
    );
  }

  if (analysis.documentsWithText === 0) {
    return (
      <div className={shell}>
        <p className="text-zinc-400">Извлекать требования не из чего.</p>
        <p className="mt-2">
          Анализ выполнен, но ни из одного документа закупки не удалось получить текст —
          посмотрите вкладку «Документы»: файлы могли не скачаться, быть сканами без
          текстового слоя или лежать в формате, который система не разбирает.
        </p>
        {analysis.message && <p className="mt-2 text-xs text-zinc-600">{analysis.message}</p>}
      </div>
    );
  }

  return (
    <div className={shell}>
      <p className="text-zinc-400">Анализ выполнен, но требований в документации нет.</p>
      <p className="mt-2">
        Документы разобраны, и ни одного проверяемого требования — к товару, к работам или к
        участнику — в них не нашлось. Так бывает, когда техническое задание публикуют
        отдельным приложением или не публикуют вовсе. Проверьте по вкладке «Документы», есть
        ли среди файлов техническое задание; если оно там есть, запустите разбор ещё раз.
      </p>
      {analysis.message && <p className="mt-2 text-xs text-zinc-600">{analysis.message}</p>}
    </div>
  );
}

/** Требования одного вида, разложенные по группам характеристик. Без группы — в конце:
 * это обычно общие формулировки («товар новый», «гарантия»), их читают последними. */
function groupByCategory(items: Requirement[]): { category: string | null; items: Requirement[] }[] {
  const groups = new Map<string | null, Requirement[]>();
  for (const item of items) {
    const key = item.category?.trim() || null;
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(item);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : 0))
    .map(([category, list]) => ({ category, items: list }));
}

function RequirementRow({ requirement, index }: { requirement: Requirement; index: number }) {
  return (
    <li
      className="flex gap-3 px-4 py-2.5 transition-colors hover:bg-white/[0.03]"
      // Нормализованная формулировка — то, с чем сравнивается каталог. Нужна, когда вердикт в
      // «Конкуренции» кажется странным, — поэтому в подсказке, а не отдельной строкой: под
      // каждым требованием она почти дословно повторяла текст и удваивала длину списка.
      title={
        requirement.normalized_text && requirement.normalized_text !== requirement.text
          ? `Для сравнения с каталогом: ${requirement.normalized_text}`
          : undefined
      }
    >
      <span className="w-6 shrink-0 pt-0.5 text-right text-[11px] tabular-nums text-zinc-600">{index}</span>
      <p className="min-w-0 flex-1 text-sm leading-relaxed text-zinc-200">{requirement.text}</p>
      <ParameterChip requirement={requirement} />
    </li>
  );
}

/** Вкладка «Требования». Классификация критичности («Критичное», «Важное») из интерфейса
 * убрана (14.10.2026): тендерщики читали её как оценку самой закупки, а не как вес в
 * расчёте процента, и спорили с ней. Вес по-прежнему считается на сервере — для процента
 * победителя, — но наружу не выносится. Вместо цветных бейджей требования разложены по виду
 * и группам характеристик, с нумерацией — по ней удобно сверяться с документацией. */
export function TenderRequirementsTab({
  requirements,
  analysis,
}: {
  requirements: Requirement[];
  analysis: AnalysisState | null;
}) {
  if (requirements.length === 0) {
    return <EmptyRequirements analysis={analysis} />;
  }

  const sections = KIND_SECTIONS.map((section) => ({
    ...section,
    items: requirements.filter((item) => item.kind === section.kind),
  })).filter((section) => section.items.length > 0);

  let counter = 0;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-sm text-zinc-300">
          <span className="font-semibold tabular-nums text-zinc-100">{requirements.length}</span>{" "}
          {requirements.length % 10 === 1 && requirements.length % 100 !== 11
            ? "требование"
            : [2, 3, 4].includes(requirements.length % 10) && ![12, 13, 14].includes(requirements.length % 100)
              ? "требования"
              : "требований"}{" "}
          из документации
        </span>
        <span className="flex flex-wrap gap-x-3 text-xs text-zinc-500">
          {sections.map((section) => (
            <span key={section.kind}>
              {section.title.toLowerCase()} · <span className="tabular-nums text-zinc-400">{section.items.length}</span>
            </span>
          ))}
        </span>
      </div>

      {sections.map((section) => (
        <section key={section.kind}>
          <div className="mb-2 flex items-baseline gap-2 px-1">
            <h3 className="text-[13px] font-semibold text-zinc-200">{section.title}</h3>
            <span className="text-xs tabular-nums text-zinc-500">{section.items.length}</span>
            <span className="text-xs text-zinc-600">— {section.hint}</span>
          </div>
          <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.02]">
            {groupByCategory(section.items).map((group, groupIndex) => (
              <div key={group.category ?? "—"} className={groupIndex > 0 ? "border-t border-white/[0.08]" : ""}>
                {/* Подзаголовок группы — только когда групп несколько: одна группа на весь
                    раздел ничего не разделяет и только съедает строку. */}
                {(group.category || groupIndex > 0) && (
                  <div className="bg-white/[0.02] px-4 py-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                    {group.category ?? "Прочее"}
                  </div>
                )}
                <ul className="divide-y divide-white/[0.05]">
                  {group.items.map((requirement) => {
                    counter += 1;
                    return <RequirementRow key={requirement.id} requirement={requirement} index={counter} />;
                  })}
                </ul>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
