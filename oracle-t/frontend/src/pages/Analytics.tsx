import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  Building2,
  CalendarClock,
  Download,
  Layers,
  MapPin,
  Sparkles,
  Trophy,
  Wallet,
} from "lucide-react";

import { ApiError, api, downloadFile } from "../api/client";
import type { AiSummary, AnalyticsOverview } from "../api/types";
import { AiSummaryCard } from "../components/analytics/AiSummaryCard";
import { RankingList } from "../components/analytics/RankingList";
import { SystemWidgets } from "../components/analytics/SystemWidgets";
import { TrendChart } from "../components/analytics/TrendChart";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { percentTextClass, tenderTypeLabel } from "../utils/format";

/**
 * Дашборд «Аналитика» (раздел 5.6 ТЗ).
 *
 * Страница задумана как будущая главная: сначала — что происходит с тендерами (сводные
 * карточки и график), затем — состояние самой системы (виджеты), затем — текстовая сводка
 * ИИ. Порядок именно такой, потому что первый вопрос утром — «что нового в закупках», а
 * «работает ли сбор» — второй; при обратном порядке дашборд открывается диагностикой.
 *
 * Период задаётся одним переключателем, а не полным набором фильтров списка: дашборд
 * отвечает на вопрос «как идут дела», и десяток фильтров здесь превращает его в ещё один
 * экран поиска. Точечная выборка остаётся за страницей «Тендеры».
 */

const PERIODS: { key: string; label: string; months: number }[] = [
  { key: "6m", label: "6 месяцев", months: 6 },
  { key: "12m", label: "12 месяцев", months: 12 },
  { key: "24m", label: "24 месяца", months: 24 },
];

function formatAmount(value: string | number): string {
  const amount = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(amount)) return "—";
  if (amount >= 1_000_000_000) return `${(amount / 1_000_000_000).toFixed(1)} млрд ₽`;
  if (amount >= 1_000_000) return `${(amount / 1_000_000).toFixed(1)} млн ₽`;
  if (amount >= 1_000) return `${(amount / 1_000).toFixed(0)} тыс ₽`;
  return `${new Intl.NumberFormat("ru-RU").format(Math.round(amount))} ₽`;
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}

function SummaryCard({
  icon,
  label,
  value,
  hint,
  valueClass,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
      <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
        {icon}
      </div>
      <div className={`text-2xl font-semibold ${valueClass ?? "text-white"}`}>{value}</div>
      <div className="mt-0.5 text-sm text-zinc-500">{label}</div>
      {hint && <div className="mt-2 text-xs text-zinc-600">{hint}</div>}
    </div>
  );
}

function Panel({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
      <div className="mb-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
          <span className="text-indigo-400">{icon}</span>
          {title}
        </div>
        {subtitle && <p className="mt-1 text-xs text-zinc-600">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

export function AnalyticsPage() {
  const [period, setPeriod] = useState(PERIODS[1]);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [aiSummary, setAiSummary] = useState<AiSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAiLoading, setIsAiLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    api
      .get<AnalyticsOverview>(`/analytics/overview?months=${period.months}`)
      .then((data) => {
        if (!cancelled) {
          setOverview(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Не удалось загрузить аналитику");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  const generateSummary = useCallback(() => {
    setIsAiLoading(true);
    api
      .post<AiSummary>("/analytics/ai-summary")
      .then(setAiSummary)
      .catch((err) => {
        setAiSummary({
          generated_at: new Date().toISOString(),
          headline: null,
          highlights: [],
          risks: [],
          actions: [],
          error: err instanceof ApiError ? err.message : "Не удалось получить сводку",
        });
      })
      .finally(() => setIsAiLoading(false));
  }, []);

  const exportXlsx = useCallback(() => {
    setIsExporting(true);
    setNotice(null);
    downloadFile("/export/tenders.xlsx", "sast-tenders.xlsx")
      .then(({ fileName, rows }) => {
        setNotice(
          rows === null
            ? `Файл ${fileName} выгружен`
            : `Выгружено ${formatCount(rows)} строк — файл ${fileName}`,
        );
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "Не удалось сформировать выгрузку");
      })
      .finally(() => setIsExporting(false));
  }, []);

  const summary = overview?.summary;

  // Максимумы для полос рейтингов считаем один раз на набор данных, а не в разметке:
  // иначе поиск максимума выполнялся бы на каждый рендер строки.
  const regionRows = useMemo(() => {
    const rows = overview?.regions ?? [];
    const max = Math.max(...rows.map((row) => row.count), 1);
    return rows.slice(0, 8).map((row) => ({
      label: row.name,
      sublabel: row.federal_district,
      value: formatCount(row.count),
      ratio: row.count / max,
    }));
  }, [overview]);

  const customerRows = useMemo(() => {
    const rows = overview?.customers ?? [];
    const max = Math.max(...rows.map((row) => row.count), 1);
    return rows.map((row) => ({
      label: row.name,
      sublabel: formatAmount(row.amount),
      value: formatCount(row.count),
      ratio: row.count / max,
    }));
  }, [overview]);

  const winTypeRows = useMemo(() => {
    const rows = overview?.win_breakdown.by_type ?? [];
    return rows.map((row) => ({
      label: tenderTypeLabel(row.label) ?? row.label,
      sublabel: `${row.count} тендеров`,
      value: row.percentage === null ? "—" : `${Number(row.percentage).toFixed(1)} %`,
      ratio: row.percentage === null ? 0 : Number(row.percentage) / 100,
    }));
  }, [overview]);

  const winRegionRows = useMemo(() => {
    const rows = overview?.win_breakdown.by_region ?? [];
    return rows.map((row) => ({
      label: row.label,
      sublabel: `${row.count} тендеров`,
      value: row.percentage === null ? "—" : `${Number(row.percentage).toFixed(1)} %`,
      ratio: row.percentage === null ? 0 : Number(row.percentage) / 100,
    }));
  }, [overview]);

  const averageWin =
    summary?.average_win_percentage === null || summary?.average_win_percentage === undefined
      ? null
      : Number(summary.average_win_percentage);

  const averageAiScore =
    summary?.average_ai_score === null || summary?.average_ai_score === undefined
      ? null
      : Number(summary.average_ai_score);

  return (
    <AppShell>
      <div className="mx-auto w-full max-w-[1400px] px-8 py-8">
        <PageHeader
          breadcrumb={["Sova Scanner", "Аналитика"]}
          title="Аналитика"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <BarChart3 size={18} />
            </span>
          }
          actions={
            <div className="flex items-center gap-2">
              <div className="inline-flex items-center gap-1 rounded-lg border border-white/[0.08] bg-white/[0.03] p-1">
                {PERIODS.map((option) => (
                  <button
                    key={option.key}
                    onClick={() => setPeriod(option)}
                    className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                      option.key === period.key
                        ? "bg-white/10 text-white"
                        : "text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <button
                onClick={exportXlsx}
                disabled={isExporting}
                className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-white/5 disabled:opacity-50"
              >
                <Download size={15} />
                {isExporting ? "Формирую…" : "Выгрузить в Excel"}
              </button>
            </div>
          }
        />

        {error && (
          <div className="mb-5 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-5 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-4 py-2.5 text-sm text-emerald-400">
            {notice}
          </div>
        )}

        {isLoading && !overview ? (
          <div className="py-24 text-center text-sm text-zinc-600">Загружаю аналитику…</div>
        ) : (
          overview &&
          summary && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <SummaryCard
                  icon={<Layers size={18} />}
                  label="Тендеров в базе"
                  value={formatCount(summary.total)}
                  hint={`проанализировано ИИ: ${formatCount(summary.analysed)}`}
                />
                <SummaryCard
                  icon={<Wallet size={18} />}
                  label="Суммарная НМЦК"
                  value={formatAmount(summary.total_amount)}
                  hint={`средний чек ${formatAmount(summary.average_amount)} · цена указана у ${formatCount(summary.with_price)}`}
                />
                <SummaryCard
                  icon={<CalendarClock size={18} />}
                  label="Истекают в ближайшие 5 дней"
                  value={formatCount(summary.deadline_soon)}
                  hint="приём заявок ещё идёт"
                  valueClass={summary.deadline_soon > 0 ? "text-amber-400" : "text-white"}
                />
                <SummaryCard
                  icon={<Sparkles size={18} />}
                  label="Средняя AI-оценка по профилю"
                  value={
                    averageAiScore === null ? "нет расчёта" : `${averageAiScore.toFixed(1)} %`
                  }
                  hint="готовность компании участвовать: история, задача, компетенции"
                  valueClass={
                    averageAiScore === null ? "text-zinc-500" : percentTextClass(averageAiScore)
                  }
                />
                <SummaryCard
                  icon={<Trophy size={18} />}
                  label="Средний % соответствия МИРТЕК"
                  value={averageWin === null ? "нет расчёта" : `${averageWin.toFixed(1)} %`}
                  hint="взвешенная оценка характеристик приборов, без учёта цены"
                  valueClass={
                    averageWin === null ? "text-zinc-500" : percentTextClass(averageWin)
                  }
                />
              </div>

              <TrendChart points={overview.monthly} />

              <AiSummaryCard
                summary={aiSummary}
                isLoading={isAiLoading}
                onGenerate={generateSummary}
              />

              <div>
                <h2 className="mb-3 text-sm font-semibold text-zinc-300">
                  Состояние системы
                </h2>
                <SystemWidgets widgets={overview.widgets} />
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Panel
                  icon={<MapPin size={15} />}
                  title="Распределение по регионам"
                  subtitle="регион заказчика, по количеству закупок"
                >
                  <RankingList rows={regionRows} emptyText="Нет данных по регионам" />
                </Panel>
                <Panel
                  icon={<Building2 size={15} />}
                  title="Топ заказчиков"
                  subtitle="по количеству закупок за период"
                >
                  <RankingList rows={customerRows} emptyText="Нет данных по заказчикам" />
                </Panel>
                <Panel
                  icon={<Trophy size={15} />}
                  title="% победителя по типам конкурса"
                  subtitle="средняя оценка соответствия МИРТЕК"
                >
                  <RankingList
                    rows={winTypeRows}
                    emptyText="Процент победителя ещё не рассчитан"
                  />
                </Panel>
                <Panel
                  icon={<Trophy size={15} />}
                  title="% победителя по регионам"
                  subtitle="средняя оценка соответствия МИРТЕК"
                >
                  <RankingList
                    rows={winRegionRows}
                    emptyText="Процент победителя ещё не рассчитан"
                  />
                </Panel>
              </div>
            </div>
          )
        )}
      </div>
    </AppShell>
  );
}
