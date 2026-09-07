import { AlertTriangle, ListChecks, RefreshCw, Sparkles, TrendingUp } from "lucide-react";

import type { AiSummary } from "../../api/types";
import { formatDateTime } from "../../utils/format";

/**
 * Блок ИИ-сводки по всем разделам системы.
 *
 * Сводка запрашивается ТОЛЬКО по кнопке, а не при открытии страницы: каждый вызов — это
 * платный запрос к YandexGPT, и дашборд, который тратит деньги на каждое обновление
 * вкладки, быстро становится проблемой.
 *
 * Три группы разнесены визуально не для красоты: наблюдения, риски и действия читаются
 * разными людьми и в разное время, и слитый текст заставляет каждого вычитывать чужое.
 */

function Section({
  icon,
  title,
  items,
  accent,
}: {
  icon: React.ReactNode;
  title: string;
  items: string[];
  accent: string;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className={`mb-2 flex items-center gap-1.5 text-xs font-medium ${accent}`}>
        {icon}
        {title}
      </div>
      <ul className="space-y-1.5">
        {items.map((item) => (
          <li key={item} className="flex gap-2 text-sm leading-relaxed text-zinc-300">
            <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-zinc-600" />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AiSummaryCard({
  summary,
  isLoading,
  onGenerate,
}: {
  summary: AiSummary | null;
  isLoading: boolean;
  onGenerate: () => void;
}) {
  return (
    <div className="rounded-2xl border border-indigo-500/20 bg-gradient-to-br from-indigo-500/[0.07] to-violet-500/[0.03] p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-indigo-300">
            <Sparkles size={16} />
            Сводка ИИ по системе
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Модель разбирает показатели всех разделов — тендеры, документы, каталог,
            матрицу соответствия и журнал ошибок
          </p>
        </div>
        <button
          onClick={onGenerate}
          disabled={isLoading}
          className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <RefreshCw size={15} className={isLoading ? "animate-spin" : ""} />
          {isLoading ? "Анализирую…" : summary ? "Обновить сводку" : "Сформировать сводку"}
        </button>
      </div>

      {!summary && !isLoading && (
        <p className="text-sm text-zinc-500">
          Сводка ещё не формировалась. Нажмите «Сформировать сводку» — запрос уходит в
          YandexGPT и занимает несколько секунд.
        </p>
      )}

      {summary?.error && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-300">
          {summary.error}
        </div>
      )}

      {summary && !summary.error && (
        <div className="space-y-5">
          {summary.headline && (
            <p className="text-base font-medium leading-relaxed text-white">
              {summary.headline}
            </p>
          )}
          <div className="grid gap-5 md:grid-cols-3">
            <Section
              icon={<TrendingUp size={13} />}
              title="Наблюдения"
              items={summary.highlights}
              accent="text-emerald-400"
            />
            <Section
              icon={<AlertTriangle size={13} />}
              title="Риски"
              items={summary.risks}
              accent="text-amber-400"
            />
            <Section
              icon={<ListChecks size={13} />}
              title="Что сделать"
              items={summary.actions}
              accent="text-indigo-300"
            />
          </div>
          <div className="text-[11px] text-zinc-600">
            Сформировано {formatDateTime(summary.generated_at)} · выводы модели основаны
            только на показателях системы и требуют проверки человеком
          </div>
        </div>
      )}
    </div>
  );
}
