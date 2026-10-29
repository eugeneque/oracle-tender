import { AlertTriangle, ChevronRight, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../api/client";
import type {
  MarketByYear,
  MarketDrillModification,
  MarketDrillType,
  MarketDrillVariant,
  MarketLocalization,
  MarketOverview,
  MarketSample,
  MarketStatus,
} from "../../api/types";
import { SegmentedControl } from "../ui/SegmentedControl";
import { BarList, Donut, ExportButton, MatrixTable, Panel } from "./common";
import { fmt, fmtShare, quarterLabel } from "./format";

/**
 * Отчёт «Рынок ПУ» по поверкам ФГИС «Аршин» (раздел 79 ARCHITECTURE.md, раздел 8 ТЗ).
 *
 * Четыре экрана повторяют страницы Power BI аналитика, чтобы руководство читало тот же
 * отчёт, только собранный Совой. Клик по производителю, исполнению, типу ПУ или интерфейсу
 * сужает остальные блоки экрана; из таблицы производителей можно уйти вглубь — тип ГРСИ →
 * модификация → варианты написания → записи поверок.
 */

type Screen = "overview" | "designs" | "groups" | "localization";
type PeriodKind = "ytd" | "quarter" | "month" | "all";

const SCREENS: { value: Screen; label: string }[] = [
  { value: "overview", label: "Общая информация" },
  { value: "designs", label: "Исполнения и типы" },
  { value: "groups", label: "Группы компаний" },
  { value: "localization", label: "Локализация" },
];

const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const INPUT = "rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm text-zinc-200";

function lastFullMonth(edge: string): { year: number; month: number } {
  const [y, m] = edge.split("-").map(Number);
  return m === 1 ? { year: y - 1, month: 12 } : { year: y, month: m - 1 };
}

function PeriodPicker({ edge, value, onChange }: { edge: string; value: string; onChange: (v: string) => void }) {
  const last = lastFullMonth(edge);
  const years = Array.from({ length: last.year - 2021 + 1 }, (_, i) => last.year - i);
  const [kind, arg] = value.split(":") as [PeriodKind, string | undefined];
  const year = Number((arg ?? String(last.year)).slice(0, 4));
  const quarter = kind === "quarter" ? Number(arg?.split("-")[1]) : Math.ceil(last.month / 3);
  const month = kind === "month" ? Number(arg?.split("-")[1]) : last.month;
  const set = (k: PeriodKind, y = year, q = quarter, m = month) => {
    if (k === "all") onChange("all");
    else if (k === "ytd") onChange(`ytd:${y}`);
    else if (k === "quarter") onChange(`quarter:${y}-${q}`);
    else onChange(`month:${y}-${String(m).padStart(2, "0")}`);
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SegmentedControl<PeriodKind>
        size="sm"
        value={kind}
        onChange={(k) => set(k)}
        ariaLabel="Период"
        segments={[
          { value: "ytd", label: "Год" },
          { value: "quarter", label: "Квартал" },
          { value: "month", label: "Месяц" },
          { value: "all", label: "Все годы" },
        ]}
      />
      {kind !== "all" && (
        <select className={INPUT} value={year} onChange={(e) => set(kind, Number(e.target.value))}>
          {years.map((y) => (
            <option key={y} value={y} className="bg-zinc-900">
              {y}
            </option>
          ))}
        </select>
      )}
      {kind === "quarter" && (
        <select className={INPUT} value={quarter} onChange={(e) => set(kind, year, Number(e.target.value))}>
          {[1, 2, 3, 4].map((q) => (
            <option key={q} value={q} className="bg-zinc-900" disabled={year === last.year && q > Math.ceil(last.month / 3)}>
              {q} кв.
            </option>
          ))}
        </select>
      )}
      {kind === "month" && (
        <select className={INPUT} value={month} onChange={(e) => set(kind, year, quarter, Number(e.target.value))}>
          {MONTHS.map((label, i) => (
            <option key={label} value={i + 1} className="bg-zinc-900" disabled={year === last.year && i + 1 > last.month}>
              {label}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

interface Filters {
  manufacturer_id: number | null;
  design: string | null;
  phase: string | null;
  interface: string | null;
}
const NO_FILTERS: Filters = { manufacturer_id: null, design: null, phase: null, interface: null };

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.toString();
}

export function MarketReport({ status }: { status: MarketStatus }) {
  const [screen, setScreen] = useState<Screen>("overview");
  const [period, setPeriod] = useState(() => `ytd:${lastFullMonth(status.edge!).year}`);
  const [error, setError] = useState<string | null>(null);
  const [drill, setDrill] = useState<{ manufacturerId: number; name: string } | null>(null);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegmentedControl<Screen> value={screen} onChange={setScreen} segments={SCREENS} ariaLabel="Экран отчёта" />
        {screen === "overview" && <PeriodPicker edge={status.edge!} value={period} onChange={setPeriod} />}
      </div>
      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">{error}</div>
      )}
      {screen === "overview" && (
        <OverviewScreen period={period} onError={setError} onDrill={(id, name) => setDrill({ manufacturerId: id, name })} />
      )}
      {screen === "designs" && <DesignsScreen onError={setError} />}
      {screen === "groups" && <GroupsScreen onError={setError} />}
      {screen === "localization" && <LocalizationScreen status={status} onError={setError} />}
      {drill && <DrillPanel period={period} start={drill} onClose={() => setDrill(null)} />}
    </div>
  );
}

function useLoad<T>(path: string, onError: (message: string) => void): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .get<T>(path)
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && onError(err instanceof ApiError ? err.message : "Не удалось загрузить отчёт"));
    return () => {
      cancelled = true;
    };
  }, [path, onError]);
  return data;
}

// ── Экран 1 ────────────────────────────────────────────────────────────────────────────────

function OverviewScreen({
  period,
  onError,
  onDrill,
}: {
  period: string;
  onError: (message: string) => void;
  onDrill: (manufacturerId: number, name: string) => void;
}) {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const params = useMemo(() => query({ period, ...filters }), [period, filters]);
  const data = useLoad<MarketOverview>(`/meter-market/overview?${params}`, onError);
  const set = (patch: Partial<Filters>) => setFilters((prev) => ({ ...prev, ...patch }));
  const active = Object.entries(filters).filter(([, v]) => v !== null);

  if (!data) return <div className="py-24 text-center text-sm text-zinc-600">Считаю поверки…</div>;
  const manufacturerName = data.manufacturers.find((m) => m.key === filters.manufacturer_id)?.name;
  const exportQuery = (table: string) => `table=${table}&${params}`;
  return (
    <div className="space-y-5">
      <div>
        <div className="text-5xl font-semibold tracking-tight text-white tabular-nums">{fmt(data.total)}</div>
        <p className="mt-2 text-sm text-zinc-400">
          поверенных счётчиков электроэнергии · {data.period.label}
          {active.length > 0 && " · с фильтром"}
        </p>
        {data.unmapped > 0 && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-400">
            <AlertTriangle size={13} />
            из них {fmt(data.unmapped)} — неразмеченные написания модификаций: исполнение и интерфейс для них не
            известны
          </p>
        )}
      </div>

      {active.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {filters.manufacturer_id !== null && (
            <Chip label={manufacturerName ?? "производитель"} onClear={() => set({ manufacturer_id: null })} />
          )}
          {filters.design && <Chip label={filters.design} onClear={() => set({ design: null })} />}
          {filters.phase && <Chip label={filters.phase} onClear={() => set({ phase: null })} />}
          {filters.interface && <Chip label={filters.interface} onClear={() => set({ interface: null })} />}
          <button type="button" className="text-zinc-500 hover:text-zinc-300" onClick={() => setFilters(NO_FILTERS)}>
            сбросить всё
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <Panel
          title="Производители"
          subtitle="владелец описания типа; клик — фильтр, стрелка — подробности"
          actions={<ExportButton query={exportQuery("manufacturers")} onError={onError} />}
        >
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.08] text-xs text-zinc-500">
                <th className="py-2 text-left font-medium">Производитель</th>
                <th className="py-2 text-right font-medium">Количество</th>
                <th className="py-2 text-right font-medium">Доля</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {data.manufacturers.map((row) => {
                const id = row.key as number;
                const selected = filters.manufacturer_id === id;
                return (
                  <tr
                    key={String(row.key)}
                    className={`border-t border-white/[0.04] ${selected ? "bg-indigo-500/10" : "hover:bg-white/[0.02]"}`}
                  >
                    <td className="py-1.5">
                      <button
                        type="button"
                        className={`text-left ${selected ? "text-zinc-100" : "text-zinc-300 hover:text-zinc-100"}`}
                        onClick={() => set({ manufacturer_id: selected ? null : id })}
                      >
                        {row.name}
                      </button>
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-zinc-200">{fmt(row.count)}</td>
                    <td className="py-1.5 text-right tabular-nums text-zinc-500">{fmtShare(row.share)}</td>
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        title="Типы и модификации"
                        onClick={() => onDrill(id, row.name ?? "")}
                        className="rounded p-1 text-zinc-600 hover:bg-white/[0.06] hover:text-zinc-300"
                      >
                        <ChevronRight size={14} />
                      </button>
                    </td>
                  </tr>
                );
              })}
              <tr className="border-t border-white/[0.12] font-semibold text-zinc-100">
                <td className="py-2">Всего</td>
                <td className="py-2 text-right tabular-nums">
                  {fmt(data.manufacturers.reduce((sum, row) => sum + row.count, 0))}
                </td>
                <td className="py-2 text-right tabular-nums text-zinc-500">100,00 %</td>
                <td />
              </tr>
            </tbody>
          </table>
        </Panel>

        <div className="space-y-5">
          <Panel title="Исполнения" actions={<ExportButton query={exportQuery("designs")} onError={onError} />}>
            <Donut
              rows={data.designs.map((r) => ({ key: String(r.key), count: r.count, share: r.share }))}
              selected={filters.design}
              onSelect={(design) => set({ design })}
            />
          </Panel>
          <Panel title="Тип ПУ" actions={<ExportButton query={exportQuery("phases")} onError={onError} />}>
            <Donut
              rows={data.phases.map((r) => ({ key: String(r.key), count: r.count, share: r.share }))}
              selected={filters.phase}
              onSelect={(phase) => set({ phase })}
            />
          </Panel>
        </div>
      </div>

      <Panel
        title="Интерфейсы"
        subtitle="тыс. поверок; составные значения — как в справочнике"
        actions={<ExportButton query={exportQuery("interfaces")} onError={onError} />}
      >
        <BarList
          rows={data.interfaces.map((r) => ({ key: String(r.key), count: r.count }))}
          selected={filters.interface}
          onSelect={(value) => set({ interface: value })}
        />
      </Panel>
    </div>
  );
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-1 text-indigo-300">
      {label}
      <button type="button" onClick={onClear} aria-label="Снять фильтр" className="hover:text-white">
        <X size={12} />
      </button>
    </span>
  );
}

// ── Экраны 2–3 ─────────────────────────────────────────────────────────────────────────────

function DesignsScreen({ onError }: { onError: (message: string) => void }) {
  const [phase, setPhase] = useState<string>("");
  const data = useLoad<MarketByYear>(`/meter-market/designs?${query({ phase })}`, onError);
  return (
    <Panel
      title={`Исполнения и типы приборов учёта ЭЭ. ${data?.label ?? ""}`}
      subtitle="производитель → исполнения, по годам поверки"
      actions={
        <div className="flex items-center gap-2">
          <SegmentedControl<string>
            size="sm"
            value={phase}
            onChange={setPhase}
            segments={[
              { value: "", label: "Все" },
              { value: "1ф", label: "1ф" },
              { value: "3ф", label: "3ф" },
            ]}
          />
          <ExportButton query={query({ table: "designs_by_year", phase })} onError={onError} />
        </div>
      }
    >
      {data ? (
        <MatrixTable
          firstHeader="Производитель / исполнение"
          columns={data.years.map(String)}
          columnLabel={(c) => c}
          rows={data.rows.map((row) => ({
            key: String(row.manufacturer_id ?? row.name),
            label: row.name,
            values: row.years,
            total: row.total,
            children: (row.designs ?? []).map((d) => ({ key: d.design, label: d.design, values: d.years, total: d.total })),
          }))}
          totals={data.totals}
          total={data.total}
        />
      ) : (
        <div className="py-16 text-center text-sm text-zinc-600">Считаю…</div>
      )}
    </Panel>
  );
}

function GroupsScreen({ onError }: { onError: (message: string) => void }) {
  const data = useLoad<MarketByYear>("/meter-market/groups", onError);
  return (
    <Panel
      title={`Данные о поверке ПУ ЭЭ за ${data?.label ?? ""} в рамках групп компаний`}
      subtitle="состав группы — на год поверки (справочник «Группы компаний»)"
      actions={<ExportButton query="table=groups" onError={onError} />}
    >
      {data ? (
        <MatrixTable
          firstHeader="Группа / компания"
          columns={data.years.map(String)}
          columnLabel={(c) => c}
          rows={data.rows.map((row) => ({
            key: row.name,
            label: row.name,
            values: row.years,
            total: row.total,
            children: (row.companies ?? []).map((c) => ({ key: c.name, label: c.name, values: c.years, total: c.total })),
          }))}
          totals={data.totals}
          total={data.total}
        />
      ) : (
        <div className="py-16 text-center text-sm text-zinc-600">Считаю…</div>
      )}
    </Panel>
  );
}

// ── Экран 4 ────────────────────────────────────────────────────────────────────────────────

function LocalizationScreen({ status, onError }: { status: MarketStatus; onError: (message: string) => void }) {
  const [requirement, setRequirement] = useState<string>("");
  const data = useLoad<MarketLocalization>(`/meter-market/localization?${query({ requirement })}`, onError);
  const years = status.thresholds.map((t) => String(t.year));
  return (
    <div className="space-y-4">
      <Panel
        title={`Количество поверенных счётчиков ЭЭ в 2025 – ${status.edge_label ?? ""}, соответствующих требованиям локализации`}
        subtitle="Точкой отсчёта является дата включения исполнения в реестр российской промышленной продукции"
        actions={
          <div className="flex items-center gap-2">
            <SegmentedControl<string>
              size="sm"
              value={requirement}
              onChange={setRequirement}
              ariaLabel="Соответствие требованиям года"
              segments={[{ value: "", label: "Все" }, ...years.map((y) => ({ value: y, label: `Требования ${y}` }))]}
            />
            <ExportButton query={query({ table: "localization", requirement })} onError={onError} />
          </div>
        }
      >
        {data ? (
          <>
            {data.undated > 0 && (
              <p className="mb-3 flex items-center gap-1.5 text-xs text-amber-400">
                <AlertTriangle size={13} />
                {fmt(data.undated)} поверок — у исполнений без даты внесения в РПП: посчитаны с начала периода. Дата
                приходит из ГИСП или вносится в справочнике.
              </p>
            )}
            <MatrixTable
              firstHeader="Производитель / баллы"
              columns={data.quarters}
              columnLabel={quarterLabel}
              rows={data.rows.map((row) => ({
                key: String(row.manufacturer_id ?? row.name),
                label: row.name,
                values: row.quarters,
                total: row.total,
                children: row.scores.map((s) => ({
                  key: String(s.score),
                  label: `${s.score} баллов${s.requirement_year ? ` · требования ${s.requirement_year}` : ""}`,
                  values: s.quarters,
                  total: s.total,
                })),
              }))}
              totals={data.totals}
              total={data.total}
            />
          </>
        ) : (
          <div className="py-16 text-center text-sm text-zinc-600">Считаю…</div>
        )}
      </Panel>
      <p className="text-xs text-zinc-600">
        * данные о реальном количестве локализованных ПУ могут отличаться от данных ФГИС «Аршин» и зависят от
        «честности» производителя
      </p>
    </div>
  );
}

// ── Вглубь: производитель → тип → модификация → варианты → поверки ─────────────────────────

type DrillLevel =
  | { kind: "types"; manufacturerId: number; title: string }
  | { kind: "modifications"; typeId: number; title: string }
  | { kind: "variants"; modificationId: number | null; typeId: number; title: string }
  | { kind: "samples"; variantId: number; title: string };

function DrillPanel({
  period,
  start,
  onClose,
}: {
  period: string;
  start: { manufacturerId: number; name: string };
  onClose: () => void;
}) {
  const [stack, setStack] = useState<DrillLevel[]>([
    { kind: "types", manufacturerId: start.manufacturerId, title: start.name },
  ]);
  const [rows, setRows] = useState<unknown[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const level = stack[stack.length - 1];

  const load = useCallback(async () => {
    setRows(null);
    setError(null);
    try {
      if (level.kind === "samples") {
        setRows(await api.get<MarketSample[]>(`/meter-market/variants/${level.variantId}/samples?${query({ period })}`));
        return;
      }
      const params =
        level.kind === "types"
          ? { manufacturer_id: level.manufacturerId }
          : level.kind === "modifications"
            ? { type_id: level.typeId }
            : level.modificationId !== null
              ? { modification_id: level.modificationId }
              : { unmapped_type_id: level.typeId };
      const result = await api.get<{ rows: unknown[] }>(`/meter-market/drill?${query({ period, ...params })}`);
      setRows(result.rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить");
    }
  }, [level, period]);

  useEffect(() => {
    void load();
  }, [load]);

  const push = (next: DrillLevel) => setStack((prev) => [...prev, next]);
  const rowClass = "flex w-full items-baseline justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-white/[0.04]";

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-xl flex-col border-l border-white/[0.08] bg-zinc-950 shadow-[0_20px_60px_rgba(0,0,0,.12)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-white/[0.08] px-5 py-4">
          <nav className="flex flex-wrap items-center gap-1 text-sm">
            {stack.map((item, index) => (
              <span key={index} className="flex items-center gap-1">
                {index > 0 && <ChevronRight size={13} className="text-zinc-600" />}
                <button
                  type="button"
                  disabled={index === stack.length - 1}
                  onClick={() => setStack((prev) => prev.slice(0, index + 1))}
                  className={index === stack.length - 1 ? "font-semibold text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}
                >
                  {item.title}
                </button>
              </span>
            ))}
          </nav>
          <button type="button" onClick={onClose} className="rounded p-1 text-zinc-500 hover:bg-white/[0.06]" aria-label="Закрыть">
            <X size={16} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-3">
          {error && <p className="px-3 py-2 text-sm text-red-400">{error}</p>}
          {!rows && !error && <p className="px-3 py-6 text-center text-sm text-zinc-600">Загружаю…</p>}
          {rows && rows.length === 0 && <p className="px-3 py-6 text-center text-sm text-zinc-600">Нет поверок за период</p>}
          {rows &&
            level.kind === "types" &&
            (rows as MarketDrillType[]).map((row) => (
              <button
                key={row.id}
                type="button"
                className={rowClass}
                onClick={() => push({ kind: "modifications", typeId: row.id, title: `${row.mit_number} ${row.notation ?? ""}` })}
              >
                <span className="min-w-0">
                  <span className="text-zinc-200">{row.mit_number}</span>
                  <span className="ml-2 text-zinc-500">{row.notation}</span>
                  <span className="block truncate text-xs text-zinc-600">{row.title}</span>
                </span>
                <span className="shrink-0 tabular-nums text-zinc-300">{fmt(row.count)}</span>
              </button>
            ))}
          {rows &&
            level.kind === "modifications" &&
            (rows as MarketDrillModification[]).map((row) => (
              <button
                key={row.id ?? "unmapped"}
                type="button"
                className={rowClass}
                onClick={() =>
                  push({ kind: "variants", modificationId: row.id, typeId: level.typeId, title: row.name })
                }
              >
                <span className="min-w-0">
                  <span className={row.id === null ? "text-amber-400" : "text-zinc-200"}>{row.name}</span>
                  {row.id !== null && (
                    <span className="block text-xs text-zinc-600">
                      {[row.phase, row.design, row.interface].filter(Boolean).join(" · ")}
                    </span>
                  )}
                </span>
                <span className="shrink-0 tabular-nums text-zinc-300">{fmt(row.count)}</span>
              </button>
            ))}
          {rows &&
            level.kind === "variants" &&
            (rows as MarketDrillVariant[]).map((row) => (
              <button
                key={row.id}
                type="button"
                className={rowClass}
                onClick={() => push({ kind: "samples", variantId: row.id, title: `«${row.raw}»` })}
              >
                <span className="min-w-0 whitespace-pre-wrap font-mono text-xs text-zinc-200">
                  «{row.raw}»
                  {row.mapped_by && <span className="ml-2 font-sans text-zinc-600">{MAPPED_BY[row.mapped_by]}</span>}
                </span>
                <span className="shrink-0 tabular-nums text-zinc-300">{fmt(row.count)}</span>
              </button>
            ))}
          {rows && level.kind === "samples" && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-zinc-500">
                  <th className="px-3 py-2 text-left font-medium">Дата</th>
                  <th className="px-3 py-2 text-left font-medium">Поверитель</th>
                  <th className="px-3 py-2 text-left font-medium">Зав. №</th>
                  <th className="px-3 py-2 text-right font-medium">ID ФГИС</th>
                </tr>
              </thead>
              <tbody>
                {(rows as MarketSample[]).map((row) => (
                  <tr key={row.global_id} className={`border-t border-white/[0.05] ${row.annulled || !row.applicable ? "text-zinc-600 line-through" : "text-zinc-300"}`}>
                    <td className="px-3 py-1.5">{row.verification_date.split("-").reverse().join(".")}</td>
                    <td className="px-3 py-1.5">{row.org}</td>
                    <td className="px-3 py-1.5 font-mono">{row.mi_number}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{row.global_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </aside>
    </div>
  );
}

const MAPPED_BY: Record<string, string> = {
  import: "из справочника",
  auto: "сведено автоматически",
  manual: "размечено вручную",
};
