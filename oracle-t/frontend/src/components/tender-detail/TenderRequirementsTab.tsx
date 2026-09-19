import type { Requirement, RequirementKind } from "../../api/types";
import { criticalityLabel } from "../../utils/format";

/** Вид требования (18.09.2026). Подпись у товара не показывается — это умолчание, а у
 * закупок на поставку он у всех требований; чип нужен, чтобы отличить «к работам» и
 * «к участнику», которые в матрицу соответствия не идут. */
const KIND_LABELS: Record<RequirementKind, string> = {
  product: "к товару",
  service: "к работам",
  participant: "к участнику",
};

function KindBadge({ value }: { value: RequirementKind }) {
  if (value === "product") return null;
  return (
    <span className="shrink-0 rounded-md border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[11px] text-sky-300">
      {KIND_LABELS[value] ?? value}
    </span>
  );
}

/** Критичность определяет вес требования в проценте победителя (раздел 5.5 ТЗ), поэтому
 * она вынесена в цветной бейдж, а не спрятана в подпись. */
const CRITICALITY_CLASSES: Record<string, string> = {
  critical: "border-red-500/30 bg-red-500/10 text-red-300",
  important: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  minor: "border-white/10 bg-white/5 text-zinc-400",
};

export function CriticalityBadge({ value }: { value: string }) {
  return (
    <span
      className={`shrink-0 rounded-md border px-2 py-0.5 text-[11px] ${
        CRITICALITY_CLASSES[value] ?? CRITICALITY_CLASSES.minor
      }`}
    >
      {criticalityLabel(value)}
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
        Требования ещё не извлечены. Запустите «Разобрать закупку» — система разберёт
        документацию тендера и выделит требования с критичностью (раздел 5.4 ТЗ).
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

  const byKind = requirements.reduce<Record<string, number>>((acc, item) => {
    acc[item.kind] = (acc[item.kind] ?? 0) + 1;
    return acc;
  }, {});
  const kindSummary = (Object.keys(KIND_LABELS) as RequirementKind[])
    .filter((kind) => byKind[kind])
    .map((kind) => `${KIND_LABELS[kind]}: ${byKind[kind]}`)
    .join(", ");

  return (
    <div className="space-y-2">
      <div className="mb-3 text-xs text-zinc-500">
        Всего требований: {requirements.length} ({kindSummary}). Порядок — от критичных к
        второстепенным. В матрицу соответствия идут только требования к товару.
      </div>
      {requirements.map((requirement) => (
        <div
          key={requirement.id}
          className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5"
        >
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm leading-snug text-zinc-200">{requirement.text}</p>
            <div className="flex shrink-0 items-center gap-1.5">
              <KindBadge value={requirement.kind} />
              <CriticalityBadge value={requirement.criticality} />
            </div>
          </div>
          {(requirement.category || requirement.normalized_text) && (
            <div className="mt-1.5 space-y-0.5 text-[11px] text-zinc-600">
              {requirement.category && <div>Группа характеристик: {requirement.category}</div>}
              {/* Нормализованная формулировка — то, с чем сравнивается каталог; человеку
                  она нужна, когда вердикт кажется странным. */}
              {requirement.normalized_text &&
                requirement.normalized_text !== requirement.text && (
                  <div>Нормализовано: {requirement.normalized_text}</div>
                )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
