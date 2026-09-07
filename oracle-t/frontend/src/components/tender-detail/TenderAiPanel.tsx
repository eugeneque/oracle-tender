import { AlertTriangle, CheckCircle2, HelpCircle, Sparkles, Wand2 } from "lucide-react";

import { AiOrb } from "../AiOrb";
import type { InsightItem, TenderInsights } from "../../api/types";

const SEVERITY_STYLES: Record<InsightItem["severity"], { border: string; text: string; label: string }> = {
  high: { border: "border-red-500/30 bg-red-500/[0.07]", text: "text-red-300", label: "важно" },
  medium: { border: "border-amber-500/30 bg-amber-500/[0.07]", text: "text-amber-300", label: "внимание" },
  low: { border: "border-white/10 bg-white/[0.02]", text: "text-zinc-300", label: "мелочь" },
  info: { border: "border-white/10 bg-white/[0.02]", text: "text-zinc-300", label: "" },
};

/**
 * Блок ИИ-разбора карточки: краткая суть закупки, риски, пробелы в данных и чек-лист перед
 * подачей.
 *
 * Всё, что сгенерировала модель, живёт внутри одной рамки со светящейся сферой и цветовой
 * волной в шапке — так видно границу между данными источника (их можно сверить с сайтом) и
 * суждением модели (его нужно проверять). Смешивать их в одном списке полей нельзя: через
 * неделю никто не вспомнит, что из этого пришло из ЕИС, а что «показалось» модели.
 *
 * У каждого наблюдения показывается `evidence` — поле или формулировка, на которой оно
 * основано: вывод без опоры проверить невозможно, а решение об участии принимается по нему.
 */
export function TenderAiPanel({
  insights,
  isRunning,
  onRun,
  error,
}: {
  insights: TenderInsights | null;
  isRunning: boolean;
  onRun: () => void;
  error: string | null;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-white/[0.1] bg-white/[0.02]">
      <div className="ai-wave flex items-center gap-3 border-b border-white/[0.08] px-4 py-3">
        <AiOrb size={40} busy={isRunning} />
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-zinc-100">
            Разбор ИИ
          </h3>
          <p className="mt-0.5 text-xs text-zinc-400">
            {isRunning
              ? "Модель читает карточку закупки…"
              : insights
                ? `Риски, пробелы в данных и что проверить перед подачей${
                    insights.generated_at
                      ? ` · ${new Date(insights.generated_at).toLocaleString("ru-RU")}`
                      : ""
                  }`
                : "Модель прочитает карточку, подсветит риски и заполнит пробелы в данных"}
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={isRunning}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-zinc-100 hover:bg-white/10 disabled:opacity-50"
        >
          <Wand2 size={13} />
          {isRunning ? "Разбираю…" : insights ? "Обновить разбор" : "Разобрать карточку"}
        </button>
      </div>

      <div className="px-4 py-3">
        {error && (
          <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        {!insights && !isRunning && !error && (
          <p className="text-sm text-zinc-500">
            Разбор ещё не выполнялся. Он не заменяет анализ документов: здесь модель смотрит
            на карточку целиком — условия, сроки, требования к участникам — и говорит, на что
            обратить внимание.
          </p>
        )}

        {isRunning && !insights && (
          <p className="text-sm text-zinc-500">Обычно занимает несколько секунд.</p>
        )}

        {insights && (
          <div className="space-y-4">
            <p className="text-sm text-zinc-200">{insights.summary}</p>

            {insights.applied_fields && insights.applied_fields.length > 0 && (
              <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/[0.07] px-3 py-2 text-xs text-emerald-300">
                Заполнено в карточке: {insights.applied_fields.join("; ")}
              </div>
            )}

            <InsightGroup
              title="Риски"
              icon={<AlertTriangle size={13} />}
              items={insights.risks}
              emptyText="Явных рисков модель не увидела."
            />

            <InsightGroup
              title="Чего не хватает в данных"
              icon={<HelpCircle size={13} />}
              items={insights.data_gaps}
              emptyText="Пробелов в карточке нет."
            />

            {insights.filled_fields.length > 0 && (
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-zinc-400">
                  <Sparkles size={13} />
                  Восстановлено из текста карточки
                </div>
                <ul className="space-y-1">
                  {insights.filled_fields.map((field, index) => (
                    <li key={index} className="text-xs text-zinc-300">
                      <span className="text-zinc-500">{field.field}:</span> {field.value}
                      <span className="text-zinc-600"> — {field.source}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {insights.checklist.length > 0 && (
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-zinc-400">
                  <CheckCircle2 size={13} />
                  Проверить перед подачей
                </div>
                <ul className="space-y-1">
                  {insights.checklist.map((item, index) => (
                    <li key={index} className="flex gap-2 text-xs text-zinc-300">
                      <span className="text-zinc-600">{index + 1}.</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function InsightGroup({
  title,
  icon,
  items,
  emptyText,
}: {
  title: string;
  icon: React.ReactNode;
  items: InsightItem[];
  emptyText: string;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-zinc-400">
        {icon}
        {title}
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-zinc-600">{emptyText}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item, index) => {
            const style = SEVERITY_STYLES[item.severity] ?? SEVERITY_STYLES.info;
            return (
              <li key={index} className={`rounded-lg border px-3 py-2 ${style.border}`}>
                <div className={`text-xs font-medium ${style.text}`}>
                  {item.title}
                  {style.label && (
                    <span className="ml-2 rounded-full bg-black/30 px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
                      {style.label}
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-zinc-300">{item.detail}</div>
                {item.evidence && (
                  <div className="mt-1 border-l-2 border-white/10 pl-2 text-[11px] text-zinc-500">
                    {item.evidence}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
