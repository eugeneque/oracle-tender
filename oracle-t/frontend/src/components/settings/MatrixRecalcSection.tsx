import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Calculator, Clock, Coins, FileSearch, Loader2, RefreshCw, Sigma } from "lucide-react";

import { ApiError, api } from "../../api/client";
import { SegmentedControl } from "../ui/SegmentedControl";
import {
  SettingCard,
  SettingsGroup,
  SettingsNotice,
  SettingsPanel,
  primaryButtonClass,
  secondaryButtonClass,
} from "./ui";

/**
 * Массовый пересчёт матрицы соответствия — только администратору (15.10.2026). Матрица и итог
 * «проходит / не проходит» сохраняются в момент расчёта: после изменения правил подсчёта
 * старые закупки показывают старые итоги, пока их не пересчитать. Раньше — по одной закупке
 * из карточки. Стоимость считается по фактическому расходу токенов за 60 дней
 * (`matrix_recalc_service` на сервере), запуск — только после подтверждения с цифрами.
 */

type Mode = "evaluation" | "review";
type Scope = "open" | "all";

interface Estimate {
  tenders: number;
  already_running: number;
  parallel: number;
  per_tender: { sample: number; tokens: number | null; cost: number | null; minutes: number | null };
  total: { tokens: number | null; cost: number | null; minutes: number | null };
}

const numberFormat = new Intl.NumberFormat("ru-RU");

function formatTokens(value: number | null): string {
  if (value === null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(".", ",")} млн`;
  if (value >= 10_000) return `${Math.round(value / 1_000)} тыс.`;
  return numberFormat.format(value);
}

function formatCost(value: number | null): string {
  return value === null ? "—" : `${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

function formatMinutes(value: number | null): string {
  if (value === null) return "—";
  if (value < 1) return "меньше минуты";
  if (value < 90) return `≈ ${Math.round(value)} мин`;
  return `≈ ${(value / 60).toFixed(1).replace(".", ",")} ч`;
}

const MODE_TEXT: Record<Mode, string> = {
  evaluation:
    "Модель заново сверяет уже извлечённые требования с каталогом производителей. Подхватывает новые правила подсчёта и пополненный каталог. Документацию заново не читает — дешевле и быстрее.",
  review:
    "Документация читается заново: требования извлекаются с позициями лота, затем матрица и AI-оценка. Нужен для закупок, разобранных до 15.10.2026, — без него закупки из нескольких позиций считаются по-старому. Дороже: к сверке добавляются чтение документации и AI-оценка.",
};

export function MatrixRecalcSection() {
  const [mode, setMode] = useState<Mode>("evaluation");
  const [scope, setScope] = useState<Scope>("open");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setIsConfirming(false);
    setError(null);
    api
      .get<Estimate>(`/matrix-recalc/estimate?mode=${mode}&scope=${scope}`)
      .then((result) => {
        if (!cancelled) setEstimate(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Не удалось оценить пересчёт");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, scope, notice]);

  const start = async () => {
    setIsStarting(true);
    setError(null);
    try {
      const result = await api.post<{ queued: number; already_running: number }>("/matrix-recalc", {
        mode,
        scope,
      });
      setNotice(
        `Поставлено в очередь: ${result.queued}` +
          (result.already_running ? `, уже в работе: ${result.already_running}` : "") +
          ". Ход разбора — в центре уведомлений и на странице «Логирование».",
      );
      setIsConfirming(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Пересчёт не запущен");
    } finally {
      setIsStarting(false);
    }
  };

  const hasHistory = (estimate?.per_tender.sample ?? 0) > 0;
  const count = estimate?.tenders ?? 0;

  return (
    <SettingsPanel
      title="Пересчёт матрицы соответствия"
      description="Итоги «проходит / не проходит» и проценты сохраняются в момент расчёта. После обновления правил подсчёта или каталога старые закупки показывают прежние итоги — их нужно пересчитать."
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
      {notice && <SettingsNotice tone="success">{notice}</SettingsNotice>}

      <SettingsGroup id="matrix-recalc-why" label="Зачем и когда">
        <SettingsNotice tone="info">
          <ul className="list-disc space-y-1.5 pl-4">
            <li>
              <span className="text-zinc-200">После обновления системы</span>, если в журнале
              изменений есть правки матрицы соответствия: новые правила работают только для
              закупок, посчитанных после обновления.
            </li>
            <li>
              <span className="text-zinc-200">После пополнения каталога</span> — новых моделей
              или характеристик производителей: матрица сверяет требования с каталогом на момент
              расчёта.
            </li>
            <li>
              <span className="text-zinc-200">Это платно.</span> Каждая закупка — обращения к
              модели ИИ: токены списываются с баланса провайдера. Оценка ниже — по
              фактическому расходу за последние 60 дней.
            </li>
            <li>
              <span className="text-zinc-200">Это долго и занимает очередь.</span> Задачи идут
              по {estimate?.parallel ?? 2} одновременно; разборы, которые запустят коллеги,
              встанут за ними. Большой пересчёт лучше запускать вечером.
            </li>
          </ul>
        </SettingsNotice>
      </SettingsGroup>

      <SettingsGroup id="matrix-recalc-run" label="Пересчёт">
        <SettingCard
          icon={mode === "review" ? <FileSearch size={18} /> : <Calculator size={18} />}
          title="Что пересчитать"
          description={MODE_TEXT[mode]}
          control={
            <SegmentedControl
              value={mode}
              onChange={setMode}
              size="sm"
              ariaLabel="Режим пересчёта"
              segments={[
                { value: "evaluation", label: "Только матрица" },
                { value: "review", label: "Полный разбор" },
              ]}
            />
          }
        />
        <SettingCard
          icon={<Sigma size={18} />}
          title="Какие закупки"
          description={
            scope === "open"
              ? "Только с матрицей и с ещё идущим приёмом заявок (или без указанного срока) — по прошедшим итог уже ничего не решает."
              : "Все закупки, у которых уже есть матрица, включая прошедшие."
          }
          control={
            <SegmentedControl
              value={scope}
              onChange={setScope}
              size="sm"
              ariaLabel="Охват пересчёта"
              segments={[
                { value: "open", label: "Идёт приём заявок" },
                { value: "all", label: "Все с матрицей" },
              ]}
            />
          }
        />

        <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] px-5 py-4">
          {isLoading || !estimate ? (
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <Loader2 size={14} className="animate-spin" />
              Считаю стоимость…
            </div>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-4">
                <Figure icon={<Sigma size={14} />} label="Закупок" value={numberFormat.format(count)}
                  hint={estimate.already_running ? `ещё ${estimate.already_running} уже в работе` : undefined} />
                <Figure icon={<Coins size={14} />} label="Токенов" value={formatTokens(estimate.total.tokens)}
                  hint={hasHistory ? `≈ ${formatTokens(estimate.per_tender.tokens)} на закупку` : undefined} />
                <Figure icon={<Coins size={14} />} label="Стоимость" value={formatCost(estimate.total.cost)}
                  hint={estimate.per_tender.cost !== null ? `≈ ${formatCost(estimate.per_tender.cost)} на закупку` : hasHistory ? "провайдер не сообщает цену" : undefined} />
                <Figure icon={<Clock size={14} />} label="Время" value={formatMinutes(estimate.total.minutes)}
                  hint={estimate.per_tender.minutes !== null ? `${formatMinutes(estimate.per_tender.minutes)} на закупку` : undefined} />
              </div>
              <div className="mt-3 text-xs leading-relaxed text-zinc-500">
                {hasHistory
                  ? `Оценка по ${estimate.per_tender.sample} ${plural(estimate.per_tender.sample)} за 60 дней; фактический расход зависит от объёма документации.`
                  : "Истории расхода для этого режима пока нет — оценка появится после первых разборов. Ориентир — «Модели ИИ → Детализация расхода»."}
              </div>
            </>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {!isConfirming ? (
            <button
              onClick={() => setIsConfirming(true)}
              disabled={isLoading || count === 0}
              className={primaryButtonClass}
            >
              <RefreshCw size={14} />
              {count === 0 ? "Пересчитывать нечего" : `Пересчитать ${numberFormat.format(count)}`}
            </button>
          ) : (
            <>
              <span className="text-sm text-zinc-300">
                Поставить {numberFormat.format(count)} {count === 1 ? "задачу" : "задач"} в очередь
                {estimate?.total.cost !== null && estimate?.total.cost !== undefined
                  ? ` — около ${formatCost(estimate.total.cost)}`
                  : ""}
                ?
              </span>
              <button onClick={() => void start()} disabled={isStarting} className={primaryButtonClass}>
                {isStarting ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Да, запустить
              </button>
              <button onClick={() => setIsConfirming(false)} disabled={isStarting} className={secondaryButtonClass}>
                Отмена
              </button>
            </>
          )}
        </div>
      </SettingsGroup>
    </SettingsPanel>
  );
}

function plural(count: number): string {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return "разборам";
  if (ones === 1) return "разбору";
  return "разборам";
}

function Figure({ icon, label, value, hint }: { icon: ReactNode; label: string; value: string; hint?: string }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-white">{value}</div>
      {hint && <div className="text-xs text-zinc-500">{hint}</div>}
    </div>
  );
}
