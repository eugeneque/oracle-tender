import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Download, RefreshCw } from "lucide-react";

import { ApiError, api, downloadFile } from "../../api/client";
import type { AiUsageCall, AiUsageGroup, AiUsageSummary, AiUsageTotals, AiUserUsage } from "../../api/types";
import { formatDateTime } from "../../utils/format";
import { SettingsGroup, SettingsNotice, SettingsPanel, secondaryButtonClass } from "./ui";

/**
 * «Детализация ИИ» (14.10.2026) — сколько токенов тратит Сова: по дням, моделям, шагам
 * разбора, видам задач, пользователям и закупкам. Руководству нужен был прогноз расхода и
 * ответ, какое окно контекста требуется ускорителю с локальной моделью; оценка «на глаз»
 * расходилась в разы, а провайдеры возвращают точный счётчик в каждом ответе.
 *
 * Только администратору (вкладка страницы «Интеграции»). Данные — `GET /ai-usage/summary`
 * и `GET /ai-usage/calls`; «Excel» (15.10.2026) — `GET /ai-usage/export.xlsx` за тот же период
 * и модель: лист на раздел сводки и каждое обращение со скоростью вывода и долей кэша.
 */

const PERIODS = [
  { days: 1, label: "Сегодня" },
  { days: 7, label: "7 дней" },
  { days: 30, label: "30 дней" },
  { days: 90, label: "90 дней" },
] as const;

const PROVIDERS = [
  { key: "", label: "Все модели" },
  { key: "yandex", label: "YandexGPT" },
  { key: "claude", label: "Claude" },
  { key: "deepseek", label: "DeepSeek" },
  { key: "gigachat", label: "GigaChat" },
];

const CALLS_PAGE = 50;

const numberFormat = new Intl.NumberFormat("ru-RU");

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1).replace(".", ",")} млн`;
  if (value >= 10_000) return `${Math.round(value / 1_000)} тыс.`;
  return numberFormat.format(value);
}

function formatCost(value: number | null): string {
  return value === null ? "—" : `${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

/** Дата по Москве со сдвигом в днях: сутки — московские, как и на сервере. */
function moscowDate(offsetDays: number): string {
  return new Date(Date.now() + 3 * 3_600_000 - offsetDays * 86_400_000).toISOString().slice(0, 10);
}

export function AiUsageSection() {
  const [days, setDays] = useState<number>(30);
  const [provider, setProvider] = useState("");
  const [summary, setSummary] = useState<AiUsageSummary | null>(null);
  const [calls, setCalls] = useState<AiUsageCall[]>([]);
  const [callsTotal, setCallsTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);

  const query = useMemo(() => {
    const params = new URLSearchParams({ date_from: moscowDate(days - 1), date_to: moscowDate(0) });
    if (provider) params.set("provider", provider);
    return params.toString();
  }, [days, provider]);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [summaryData, callsData] = await Promise.all([
        api.get<AiUsageSummary>(`/ai-usage/summary?${query}`),
        api.get<{ total: number; items: AiUsageCall[] }>(`/ai-usage/calls?${query}&limit=${CALLS_PAGE}`),
      ]);
      setSummary(summaryData);
      setCalls(callsData.items);
      setCallsTotal(callsData.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить детализацию");
    } finally {
      setIsLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadMoreCalls = async () => {
    try {
      const data = await api.get<{ total: number; items: AiUsageCall[] }>(
        `/ai-usage/calls?${query}&limit=${CALLS_PAGE}&offset=${calls.length}`,
      );
      setCalls((current) => [...current, ...data.items]);
      setCallsTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить обращения");
    }
  };

  const exportCsv = async () => {
    setIsExporting(true);
    setError(null);
    try {
      await downloadFile(`/ai-usage/export.xlsx?${query}`, "sova_ai_usage.xlsx");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выгрузить детализацию");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <SettingsPanel
      title="Детализация ИИ"
      description="Сколько токенов ушло на модели ИИ: каждое обращение, включая повторы и сбои. Числа — из счётчика провайдера; где его нет (сбой до ответа) — оценка по длине текста."
      actions={
        <>
          <div className="inline-flex items-center gap-1 rounded-xl border border-white/[0.08] bg-white/[0.03] p-1">
            {PERIODS.map((period) => (
              <button
                key={period.days}
                onClick={() => setDays(period.days)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  period.days === days ? "bg-white/10 text-white" : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {period.label}
              </button>
            ))}
          </div>
          <select
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            aria-label="Модель"
            className="rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-xs text-zinc-200 focus:border-indigo-500/50 focus:outline-none"
          >
            {PROVIDERS.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
          <button onClick={() => void load()} disabled={isLoading} className={secondaryButtonClass}>
            <RefreshCw size={13} className={isLoading ? "animate-spin" : ""} />
            Обновить
          </button>
          <button
            onClick={() => void exportCsv()}
            disabled={isExporting || !summary || summary.totals.calls === 0}
            title="Лист на раздел и каждое обращение: токены на входе и выходе, кэш, время ответа, скорость вывода"
            className={secondaryButtonClass}
          >
            <Download size={13} className={isExporting ? "animate-pulse" : ""} />
            Excel
          </button>
        </>
      }
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}

      {summary && summary.totals.calls === 0 && (
        <SettingsNotice tone="info">
          За выбранный период обращений к модели не было. Учёт ведётся с 14.10.2026 — более ранние
          разборы в детализацию не попали.
        </SettingsNotice>
      )}

      {summary && summary.totals.calls > 0 && (
        <>
          <Totals summary={summary} />
          <DayChart days={summary.by_day} />
          <Forecast key={`${days}:${provider}`} summary={summary} periodDays={days} />

          <SettingsGroup
            label="Шаги разбора"
            hint="Макс. запрос — сколько окна контекста реально нужно модели на этом шаге."
          >
            <UsageTable
              rows={summary.by_operation}
              extra={[
                { title: "Ср. запрос", value: (row) => formatTokens(row.avg_prompt_tokens ?? 0) },
                { title: "Макс. запрос", value: (row) => formatTokens(row.max_prompt_tokens ?? 0) },
              ]}
            />
          </SettingsGroup>

          <SettingsGroup
            label="Разборы закупок"
            hint="Среднее на закупку — основа прогноза: число закупок в месяц × столько токенов."
          >
            <UsageTable
              rows={summary.by_job_kind}
              extra={[
                { title: "Закупок", value: (row) => (row.tenders ? numberFormat.format(row.tenders) : "—") },
                {
                  title: "На закупку",
                  value: (row) => (row.avg_tokens_per_tender ? formatTokens(row.avg_tokens_per_tender) : "—"),
                },
              ]}
            />
          </SettingsGroup>

          <SettingsGroup
            label="Модели"
            hint={`Символов на токен — по счётчикам провайдера. От этого отношения считается, сколько текста помещается в окно модели; заложено ${String(summary.chars_per_token_estimate).replace(".", ",")}.`}
          >
            <UsageTable
              rows={summary.by_model.map((row) => ({
                ...row,
                key: `${row.provider}:${row.model ?? ""}`,
                label: row.model ? `${row.provider_label} · ${row.model}` : row.provider_label,
                charsPerToken: row.chars_per_token,
              }))}
              extra={[
                {
                  title: "Симв./токен",
                  value: (row) =>
                    row.charsPerToken === null || row.charsPerToken === undefined
                      ? "—"
                      : String(row.charsPerToken).replace(".", ","),
                },
                { title: "Макс. запрос", value: (row) => formatTokens(row.max_prompt_tokens ?? 0) },
              ]}
            />
          </SettingsGroup>

          <SettingsGroup
            label="Пользователи"
            hint="Нажмите на пользователя — на какие шаги ушли его токены и какие закупки он разбирал."
          >
            <UserUsageList key={query} rows={summary.by_user} query={query} />
          </SettingsGroup>

          {summary.top_tenders.length > 0 && (
            <SettingsGroup label="Самые затратные закупки">
              <UsageTable
                rows={summary.top_tenders.map((row) => ({
                  ...row,
                  key: row.tender_id,
                  label: row.title,
                  href: `/tenders/${row.tender_id}`,
                  sublabel: row.external_id ?? undefined,
                }))}
              />
            </SettingsGroup>
          )}

          <SettingsGroup label={`Обращения · ${numberFormat.format(callsTotal)}`}>
            <CallsList calls={calls} />
            {calls.length < callsTotal && (
              <button onClick={() => void loadMoreCalls()} className={secondaryButtonClass}>
                Показать ещё
              </button>
            )}
          </SettingsGroup>
        </>
      )}
    </SettingsPanel>
  );
}

function Totals({ summary }: { summary: AiUsageSummary }) {
  const { totals } = summary;
  const tiles = [
    {
      label: "Токенов всего",
      value: formatTokens(totals.total_tokens),
      hint: `вход ${formatTokens(totals.prompt_tokens)} · ответ ${formatTokens(totals.completion_tokens)}`,
    },
    {
      label: "Обращений",
      value: numberFormat.format(totals.calls),
      hint: totals.failed ? `из них сбоев и повторов: ${numberFormat.format(totals.failed)}` : "без сбоев",
    },
    {
      label: "Закупок разобрано",
      value: numberFormat.format(totals.tenders),
      hint: totals.estimated_calls
        ? `оценка по тексту — у ${numberFormat.format(totals.estimated_calls)} обращ.`
        : "все числа — от провайдера",
    },
    totals.cost !== null
      ? { label: "Стоимость", value: formatCost(totals.cost), hint: "по данным провайдера" }
      : {
          label: "Из кэша провайдера",
          value: formatTokens(totals.cached_tokens),
          hint: "входящие токены по сниженной цене",
        },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4">
          <div className="text-xs text-zinc-500">{tile.label}</div>
          <div className="mt-1 text-2xl font-semibold tracking-tight text-white">{tile.value}</div>
          <div className="mt-1 text-[11px] text-zinc-500">{tile.hint}</div>
        </div>
      ))}
    </div>
  );
}

/** Столбики по дням: вход и ответ друг на друге. Своя отрисовка, как у `TrendChart`, —
 * графических библиотек в проекте нет, а столбики проще линии. */
function DayChart({ days }: { days: (AiUsageTotals & { day: string })[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...days.map((day) => day.total_tokens), 1);
  const shown = days[hover ?? days.length - 1];
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-400">По дням</div>
        {shown && (
          <div className="text-xs text-zinc-400">
            {new Date(`${shown.day}T12:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}:{" "}
            <span className="font-medium text-zinc-100">{formatTokens(shown.total_tokens)}</span> токенов ·{" "}
            {numberFormat.format(shown.calls)} обращ.
          </div>
        )}
      </div>
      <div className="flex h-40 items-end gap-1" onMouseLeave={() => setHover(null)}>
        {days.map((day, index) => (
          <div
            key={day.day}
            onMouseEnter={() => setHover(index)}
            className="flex h-full min-w-[4px] max-w-[48px] flex-1 flex-col justify-end"
          >
            <div
              className={`rounded-t-sm transition-colors ${hover === index ? "bg-indigo-300" : "bg-indigo-400/80"}`}
              style={{ height: `${(day.completion_tokens / max) * 100}%` }}
            />
            <div
              className={`transition-colors ${hover === index ? "bg-indigo-500" : "bg-indigo-500/50"}`}
              style={{ height: `${(day.prompt_tokens / max) * 100}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-3 flex gap-4 text-[11px] text-zinc-500">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-indigo-500/50" /> вход
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-indigo-400/80" /> ответ
        </span>
      </div>
    </div>
  );
}

/** Прогноз на месяц: среднее на закупку × число закупок плюс фоновый расход (ИИ-отбор,
 * каталог), пересчитанный с периода на 30 дней. */
function Forecast({ summary, periodDays }: { summary: AiUsageSummary; periodDays: number }) {
  const tenders = summary.totals.tenders;
  const tenderTokens = summary.by_job_kind
    .filter((row) => row.key !== "none")
    .reduce((sum, row) => sum + row.total_tokens, 0);
  const background = summary.by_job_kind.find((row) => row.key === "none")?.total_tokens ?? 0;
  const perTender = tenders ? tenderTokens / tenders : 0;
  const [perMonth, setPerMonth] = useState(() => Math.max(1, Math.round((tenders / periodDays) * 30)));
  const total = perTender * perMonth + (background / periodDays) * 30;

  if (!tenders) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-2xl border border-indigo-500/20 bg-indigo-500/[0.06] px-5 py-4">
      <div className="text-sm text-zinc-300">
        Прогноз на месяц: в среднем <b className="text-white">{formatTokens(Math.round(perTender))}</b> токенов на
        разобранную закупку
      </div>
      <label className="flex items-center gap-2 text-sm text-zinc-400">
        закупок в месяц
        <input
          type="number"
          min={1}
          value={perMonth}
          onChange={(event) => setPerMonth(Math.max(1, Number(event.target.value) || 1))}
          className="w-20 rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-sm text-zinc-100 focus:border-indigo-500/50 focus:outline-none"
        />
      </label>
      <div className="text-sm text-zinc-300">
        ≈ <b className="text-white">{formatTokens(Math.round(total))}</b> токенов в месяц
        {background > 0 && <span className="text-zinc-500"> — с фоновым ИИ-отбором и каталогом</span>}
      </div>
    </div>
  );
}

type UsageRow = AiUsageGroup & { href?: string; sublabel?: string; charsPerToken?: number | null };

function UsageTable({
  rows,
  extra = [],
}: {
  rows: UsageRow[];
  extra?: { title: string; value: (row: UsageRow) => string }[];
}) {
  const max = Math.max(...rows.map((row) => row.total_tokens), 1);
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/[0.08] bg-white/[0.02]">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-white/[0.06] text-left text-[11px] uppercase tracking-wider text-zinc-500">
            <th className="px-4 py-2.5 font-medium">&nbsp;</th>
            <th className="px-3 py-2.5 text-right font-medium">Обращ.</th>
            <th className="px-3 py-2.5 text-right font-medium">Вход</th>
            <th className="px-3 py-2.5 text-right font-medium">Ответ</th>
            {extra.map((column) => (
              <th key={column.title} className="px-3 py-2.5 text-right font-medium">
                {column.title}
              </th>
            ))}
            <th className="w-40 px-4 py-2.5 text-right font-medium">Всего</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-b border-white/[0.04] last:border-0">
              <td className="max-w-[320px] px-4 py-2.5">
                {row.href ? (
                  <Link to={row.href} className="line-clamp-1 text-zinc-100 hover:text-indigo-300">
                    {row.label}
                  </Link>
                ) : (
                  <span className="line-clamp-1 text-zinc-100">{row.label}</span>
                )}
                {row.sublabel && <div className="text-[11px] text-zinc-500">{row.sublabel}</div>}
              </td>
              <td className="px-3 py-2.5 text-right tabular-nums text-zinc-300">
                {numberFormat.format(row.calls)}
                {row.failed > 0 && (
                  <span className="ml-1 text-[11px] text-amber-400" title="сбои и повторы">
                    ({row.failed})
                  </span>
                )}
              </td>
              <td className="px-3 py-2.5 text-right tabular-nums text-zinc-400">{formatTokens(row.prompt_tokens)}</td>
              <td className="px-3 py-2.5 text-right tabular-nums text-zinc-400">
                {formatTokens(row.completion_tokens)}
              </td>
              {extra.map((column) => (
                <td key={column.title} className="px-3 py-2.5 text-right tabular-nums text-zinc-400">
                  {column.value(row)}
                </td>
              ))}
              <td className="px-4 py-2.5">
                <div className="flex items-center justify-end gap-2">
                  <div className="h-1.5 w-16 overflow-hidden rounded-full bg-white/[0.06]">
                    <div
                      className="h-full rounded-full bg-indigo-400"
                      style={{ width: `${(row.total_tokens / max) * 100}%` }}
                    />
                  </div>
                  <span className="w-16 text-right font-medium tabular-nums text-zinc-100">
                    {formatTokens(row.total_tokens)}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Пользователи с раскрытием (14.10.2026): по строке — на какие шаги разбора ушли токены
 * пользователя и какие закупки он разбирал, со ссылками. Разбивка грузится при раскрытии —
 * `GET /ai-usage/users/{key}`, чтобы сводка не тащила закупки всех пользователей сразу. */
function UserUsageList({ rows, query }: { rows: AiUsageGroup[]; query: string }) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, AiUserUsage>>({});
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const max = Math.max(...rows.map((row) => row.total_tokens), 1);

  const toggle = async (key: string) => {
    if (openKey === key) {
      setOpenKey(null);
      return;
    }
    setOpenKey(key);
    setError(null);
    if (details[key]) return;
    setLoadingKey(key);
    try {
      const data = await api.get<AiUserUsage>(`/ai-usage/users/${encodeURIComponent(key)}?${query}`);
      setDetails((current) => ({ ...current, [key]: data }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить расход пользователя");
    } finally {
      setLoadingKey(null);
    }
  };

  return (
    <div className="divide-y divide-white/[0.04] overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02]">
      {rows.map((row) => {
        const isOpen = openKey === row.key;
        const detail = details[row.key];
        return (
          <div key={row.key}>
            <button
              onClick={() => void toggle(row.key)}
              aria-expanded={isOpen}
              className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors hover:bg-white/[0.03]"
            >
              <ChevronRight
                size={14}
                className={`shrink-0 text-zinc-500 transition-transform ${isOpen ? "rotate-90" : ""}`}
              />
              <span className="min-w-0 flex-1 truncate text-zinc-100">{row.label}</span>
              <span className="hidden text-xs tabular-nums text-zinc-500 sm:inline">
                {numberFormat.format(row.calls)} обращ.
                {row.failed > 0 && <span className="ml-1 text-amber-400">({row.failed})</span>}
              </span>
              <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-white/[0.06] sm:block">
                <span
                  className="block h-full rounded-full bg-indigo-400"
                  style={{ width: `${(row.total_tokens / max) * 100}%` }}
                />
              </span>
              <span className="w-16 text-right font-medium tabular-nums text-zinc-100">
                {formatTokens(row.total_tokens)}
              </span>
            </button>
            {isOpen && (
              <div className="border-t border-white/[0.04] bg-black/10 px-4 py-4">
                {loadingKey === row.key && <div className="text-xs text-zinc-500">Загрузка…</div>}
                {error && !detail && loadingKey !== row.key && (
                  <div className="text-xs text-amber-400">{error}</div>
                )}
                {detail && <UserUsageDetail detail={detail} />}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function UserUsageDetail({ detail }: { detail: AiUserUsage }) {
  const total = Math.max(detail.totals.total_tokens, 1);
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
          На что ушли токены
        </div>
        <div className="space-y-2">
          {detail.by_operation.map((step) => (
            <div key={step.label}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <span className="truncate text-zinc-200">{step.label}</span>
                <span className="shrink-0 tabular-nums text-zinc-400">
                  {formatTokens(step.total_tokens)} · {Math.round((step.total_tokens / total) * 100)}%
                </span>
              </div>
              <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/[0.06]">
                <div
                  className="h-full rounded-full bg-indigo-400"
                  style={{ width: `${(step.total_tokens / total) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
        {detail.by_job_kind.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {detail.by_job_kind.map((kind) => (
              <span
                key={kind.label}
                className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[11px] text-zinc-400"
              >
                {kind.label}: <span className="text-zinc-200">{formatTokens(kind.total_tokens)}</span>
                {kind.tenders ? ` · ${numberFormat.format(kind.tenders)} закуп.` : ""}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="min-w-0">
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
          Разобранные закупки · {numberFormat.format(detail.tenders_total)}
        </div>
        {detail.tenders.length === 0 ? (
          <div className="text-xs text-zinc-500">Обращений по закупкам не было — только фоновые (отбор, каталог).</div>
        ) : (
          <ul className="max-h-80 space-y-1 overflow-y-auto pr-1">
            {detail.tenders.map((tender) => (
              <li
                key={tender.tender_id}
                className="flex items-start justify-between gap-3 rounded-lg px-2 py-1.5 hover:bg-white/[0.03]"
              >
                <div className="min-w-0">
                  {tender.exists ? (
                    <Link
                      to={`/tenders/${tender.tender_id}`}
                      className="line-clamp-1 text-xs text-zinc-100 hover:text-indigo-300"
                    >
                      {tender.title}
                    </Link>
                  ) : (
                    <span className="line-clamp-1 text-xs text-zinc-500">{tender.title}</span>
                  )}
                  <div className="text-[11px] text-zinc-500">
                    {tender.external_id && <span>№ {tender.external_id} · </span>}
                    {formatDateTime(tender.last_at)} · {tender.operations.join(", ")}
                  </div>
                </div>
                <span className="shrink-0 text-xs tabular-nums text-zinc-300">
                  {formatTokens(tender.total_tokens)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {detail.tenders_total > detail.tenders.length && (
          <div className="mt-2 text-[11px] text-zinc-500">
            Показаны последние {numberFormat.format(detail.tenders.length)} — сократите период, чтобы увидеть остальные.
          </div>
        )}
      </div>
    </div>
  );
}

function CallsList({ calls }: { calls: AiUsageCall[] }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-white/[0.08] bg-white/[0.02]">
      <table className="w-full min-w-[760px] text-xs">
        <thead>
          <tr className="border-b border-white/[0.06] text-left text-[11px] uppercase tracking-wider text-zinc-500">
            <th className="px-4 py-2.5 font-medium">Когда</th>
            <th className="px-3 py-2.5 font-medium">Шаг</th>
            <th className="px-3 py-2.5 font-medium">Модель</th>
            <th className="px-3 py-2.5 font-medium">Закупка</th>
            <th className="px-3 py-2.5 text-right font-medium">Вход</th>
            <th className="px-3 py-2.5 text-right font-medium">Ответ</th>
            <th className="px-3 py-2.5 text-right font-medium">Время</th>
            <th className="px-4 py-2.5 font-medium">Исход</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <tr key={call.id} className="border-b border-white/[0.04] last:border-0">
              <td className="whitespace-nowrap px-4 py-2 text-zinc-400">{formatDateTime(call.created_at)}</td>
              <td className="px-3 py-2 text-zinc-200">
                {call.operation_label}
                {call.user && <div className="text-[11px] text-zinc-500">{call.user}</div>}
              </td>
              <td className="px-3 py-2 text-zinc-400">{call.provider_label}</td>
              <td className="px-3 py-2">
                {call.tender_id ? (
                  <Link to={`/tenders/${call.tender_id}`} className="text-zinc-300 hover:text-indigo-300">
                    {call.tender_external_id ?? "закупка"}
                  </Link>
                ) : (
                  <span className="text-zinc-600">—</span>
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                {call.tokens_estimated && <span title="оценка по длине текста">≈</span>}
                {numberFormat.format(call.prompt_tokens)}
                {call.cached_tokens > 0 && (
                  <div className="text-[11px] text-zinc-500">из кэша {numberFormat.format(call.cached_tokens)}</div>
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                {numberFormat.format(call.completion_tokens)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-zinc-400">
                {(call.duration_ms / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} с
              </td>
              <td className="px-4 py-2">
                <span
                  className={call.status === "success" ? "text-emerald-400" : "text-amber-400"}
                  title={call.error ?? undefined}
                >
                  {call.status_label}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
