import { CheckSquare, ChevronDown, ChevronRight, Columns3, Download, ExternalLink, Search, Square, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api, downloadFile } from "../../api/client";
import type { MarketExplain, MarketExplainPosition, MarketSample } from "../../api/types";
import { fmt } from "./format";

/**
 * Детализация итогового числа «Рынка ПУ» (замечания 09.11.2026).
 *
 * Клик по числу отчёта открывает эту панель: сколько учтено и исключено, по каким причинам,
 * перечень позиций (написание модификации или исполнение) с документом-основанием и
 * применёнными правилами, сравнение нескольких позиций рядом, сверка с ручным подсчётом
 * (расхождение в пределах допуска — не ошибка алгоритма) и выгрузка в Excel.
 */

export interface ExplainScope {
  kind: "overview" | "localization";
  title: string;
  params: Record<string, string | number | null | undefined>;
}

const SHOWN = 300;
const MAPPED_BY: Record<string, string> = {
  import: "по шаблону справочника аналитика",
  auto: "автоматически по ключу написания",
  manual: "вручную аналитиком",
  ai: "ИИ по структуре обозначения из описания типа",
};

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.toString();
}

function pct(value: number): string {
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} %`;
}

export function SmartBadge({ smart }: { smart: MarketExplainPosition["smart"] }) {
  const tone =
    smart.key === "yes"
      ? "border-indigo-400/30 bg-indigo-500/10 text-indigo-200"
      : smart.key === "no"
        ? "border-white/15 bg-white/[0.04] text-zinc-300"
        : "border-white/10 text-zinc-500";
  return (
    <span className={`inline-flex shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${tone}`} title={smart.reason}>
      ПП 890: {smart.status}
    </span>
  );
}

export function ExplainPanel({ scope, onClose }: { scope: ExplainScope; onClose: () => void }) {
  const params = useMemo(() => query({ kind: scope.kind, title: scope.title, ...scope.params }), [scope]);
  const [data, setData] = useState<MarketExplain | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"included" | "excluded">("included");
  const [reason, setReason] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [comparing, setComparing] = useState(false);
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    api
      .get<MarketExplain>(`/meter-market/explain?${params}`)
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Не удалось собрать детализацию"));
    return () => {
      cancelled = true;
    };
  }, [params]);

  const list = useMemo(() => {
    if (!data) return [];
    const needle = search.trim().toLowerCase();
    return data.positions.filter((p) => {
      if (tab === "included" ? !p.counted : !p.excluded) return false;
      if (reason && !(p.basis === reason && p.counted) && !p.exclusions.some((e) => e.reason === reason)) return false;
      if (!needle) return true;
      return [p.manufacturer, p.mit_number, p.notation, p.modification, p.raw].some((v) => v?.toLowerCase().includes(needle));
    });
  }, [data, tab, reason, search]);

  const togglePick = (key: string) =>
    setPicked((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : prev.length >= 4 ? prev : [...prev, key]));

  const exportXlsx = async () => {
    setBusy(true);
    try {
      await downloadFile(`/meter-market/explain.xlsx?${params}`, "Рынок ПУ — детализация.xlsx");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выгрузить детализацию");
    } finally {
      setBusy(false);
    }
  };

  const includedPositions = data?.positions.filter((p) => p.counted).length ?? 0;
  const excludedPositions = data?.positions.filter((p) => p.excluded).length ?? 0;
  const comparePositions = data ? picked.map((k) => data.positions.find((p) => p.key === k)).filter(Boolean) as MarketExplainPosition[] : [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-4xl flex-col border-l border-white/[0.08] bg-zinc-950 shadow-[0_20px_60px_rgba(0,0,0,.12)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-white/[0.08] px-6 py-4">
          <div className="min-w-0">
            <p className="text-xs text-zinc-500">Детализация показателя · {data?.period ?? "…"}</p>
            <h2 className="mt-0.5 text-base font-semibold text-zinc-100">{scope.title}</h2>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              disabled={!data || busy}
              onClick={exportXlsx}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-400 hover:bg-white/5 hover:text-zinc-200 disabled:opacity-50"
            >
              <Download size={13} />
              {busy ? "Формирую…" : "Excel"}
            </button>
            <button type="button" onClick={onClose} className="rounded p-1 text-zinc-500 hover:bg-white/[0.06]" aria-label="Закрыть">
              <X size={16} />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {error && <p className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-400">{error}</p>}
          {!data && !error && <p className="py-16 text-center text-sm text-zinc-600">Собираю позиции…</p>}
          {data && (
            <div className="space-y-5">
              <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
                <div>
                  <div className="text-4xl font-semibold tracking-tight text-white tabular-nums">{fmt(data.value)}</div>
                  <p className="mt-1 text-xs text-zinc-500">учтено в расчёте · позиций {fmt(includedPositions)}</p>
                </div>
                <div>
                  <div className="text-xl font-semibold tabular-nums text-zinc-400">{fmt(data.excluded)}</div>
                  <p className="mt-1 text-xs text-zinc-500">исключено · позиций {fmt(excludedPositions)}</p>
                </div>
              </div>

              <section>
                <h3 className="mb-2 text-xs font-medium text-zinc-500">Основания и причины — клик сужает перечень</h3>
                <ul className="space-y-1">
                  {data.reasons.map((r) => (
                    <li key={`${r.included}-${r.reason}`}>
                      <button
                        type="button"
                        onClick={() => {
                          setReason(reason === r.reason ? null : r.reason);
                          setTab(r.included ? "included" : "excluded");
                        }}
                        className={`flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1 text-left text-sm transition-colors ${
                          reason === r.reason ? "bg-indigo-500/10 text-zinc-100" : "text-zinc-400 hover:bg-white/[0.04]"
                        }`}
                      >
                        <span className="min-w-0">
                          <span className={`mr-2 text-[11px] ${r.included ? "text-indigo-300" : "text-zinc-500"}`}>
                            {r.included ? "учтено" : "исключено"}
                          </span>
                          {r.reason}
                        </span>
                        <span className="shrink-0 tabular-nums text-zinc-300">{fmt(r.count)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>

              <ManualCheck data={data} value={manual} onChange={setManual} />

              <section>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  {(["included", "excluded"] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setTab(t)}
                      className={`rounded-lg px-3 py-1.5 text-sm ${tab === t ? "bg-white/[0.08] text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`}
                    >
                      {t === "included" ? `Учтено · ${fmt(includedPositions)}` : `Исключено · ${fmt(excludedPositions)}`}
                    </button>
                  ))}
                  <label className="ml-auto flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm">
                    <Search size={13} className="text-zinc-500" />
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="ГРСИ, модификация, написание"
                      className="w-56 bg-transparent text-zinc-200 outline-none placeholder:text-zinc-600"
                    />
                  </label>
                  <button
                    type="button"
                    disabled={picked.length < 2}
                    onClick={() => setComparing(true)}
                    title="Отметьте от 2 до 4 позиций"
                    className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Columns3 size={13} />
                    Сравнить{picked.length ? ` · ${picked.length}` : ""}
                  </button>
                </div>
                {reason && (
                  <p className="mb-2 text-xs text-zinc-500">
                    только «{reason}» ·{" "}
                    <button type="button" className="text-zinc-400 hover:text-zinc-200" onClick={() => setReason(null)}>
                      показать все
                    </button>
                  </p>
                )}
                {list.length === 0 && <p className="py-8 text-center text-sm text-zinc-600">Позиций нет</p>}
                <ul className="divide-y divide-white/[0.05]">
                  {list.slice(0, SHOWN).map((p) => (
                    <PositionRow
                      key={p.key}
                      position={p}
                      tab={tab}
                      period={typeof scope.params.period === "string" ? scope.params.period : null}
                      isOpen={open === p.key}
                      onToggle={() => setOpen(open === p.key ? null : p.key)}
                      picked={picked.includes(p.key)}
                      onPick={() => togglePick(p.key)}
                    />
                  ))}
                </ul>
                {list.length > SHOWN && (
                  <p className="mt-3 text-center text-xs text-zinc-500">
                    Показаны первые {SHOWN} из {fmt(list.length)} — полный перечень в Excel
                  </p>
                )}
              </section>
            </div>
          )}
        </div>
      </aside>
      {comparing && comparePositions.length > 1 && (
        <ComparePanel positions={comparePositions} onClose={() => setComparing(false)} />
      )}
    </div>
  );
}

/** Сверка с ручным подсчётом: расхождение в пределах допуска — не ошибка алгоритма. */
function ManualCheck({ data, value, onChange }: { data: MarketExplain; value: string; onChange: (v: string) => void }) {
  const manual = Number(value.replace(/\s/g, "").replace(",", "."));
  const valid = value.trim() !== "" && Number.isFinite(manual) && manual > 0;
  const diff = valid ? data.value - manual : 0;
  const share = valid ? (Math.abs(diff) / manual) * 100 : 0;
  const within = share <= data.tolerance_pct;
  const excluded = data.reasons.filter((r) => !r.included).slice(0, 4);
  const nodata = data.reasons.filter((r) => r.included && r.reason.includes("не размечено"));
  return (
    <section className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-zinc-400">Ручная проверка:</span>
        <input
          inputMode="numeric"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="ваше число"
          className="w-36 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm tabular-nums text-zinc-200"
        />
        {valid && (
          <span className={within ? "text-zinc-300" : "text-amber-400"}>
            расхождение {diff > 0 ? "+" : ""}
            {fmt(diff)} ({pct(share)}) —{" "}
            {within ? `в пределах допуска ${pct(data.tolerance_pct)}, не ошибка алгоритма` : `больше допуска ${pct(data.tolerance_pct)}`}
          </span>
        )}
      </div>
      {valid && diff !== 0 && (
        <div className="mt-3 space-y-1 text-xs text-zinc-500">
          <p className="text-zinc-400">Откуда обычно берётся расхождение:</p>
          <p>· {data.late_publication};</p>
          {nodata.map((r) => (
            <p key={r.reason}>
              · {fmt(r.count)} учтённых поверок без разметки написания — при ручном подсчёте их часто относят к
              модификации вручную;
            </p>
          ))}
          {excluded.map((r) => (
            <p key={r.reason}>
              · исключено {fmt(r.count)}: {r.reason} — если в ручном подсчёте они учтены, число будет больше;
            </p>
          ))}
          <p>· сверить по позициям: выгрузите детализацию в Excel и сравните со своим списком по ГРСИ и написанию.</p>
        </div>
      )}
    </section>
  );
}

function PositionRow({
  position: p,
  tab,
  period,
  isOpen,
  onToggle,
  picked,
  onPick,
}: {
  position: MarketExplainPosition;
  tab: "included" | "excluded";
  period: string | null;
  isOpen: boolean;
  onToggle: () => void;
  picked: boolean;
  onPick: () => void;
}) {
  const value = tab === "included" ? p.counted : p.excluded;
  const why = tab === "included" ? p.basis : p.exclusions.map((e) => e.reason).join("; ");
  return (
    <li className="py-2">
      <div className="flex items-start gap-2">
        <button type="button" onClick={onPick} className="mt-0.5 text-zinc-500 hover:text-zinc-200" aria-label="Отметить для сравнения">
          {picked ? <CheckSquare size={15} className="text-indigo-300" /> : <Square size={15} />}
        </button>
        <button type="button" onClick={onToggle} className="min-w-0 flex-1 text-left">
          <div className="flex items-baseline justify-between gap-3">
            <span className="flex min-w-0 items-center gap-1.5 text-sm text-zinc-200">
              {isOpen ? <ChevronDown size={13} className="shrink-0 text-zinc-500" /> : <ChevronRight size={13} className="shrink-0 text-zinc-500" />}
              <span className="truncate">
                {p.modification ?? "нет данных — написание не размечено"}
                <span className="ml-2 text-xs text-zinc-500">
                  {p.mit_number} · {p.manufacturer}
                </span>
              </span>
            </span>
            <span className={`shrink-0 tabular-nums ${tab === "included" ? "text-zinc-200" : "text-zinc-400"}`}>{fmt(value)}</span>
          </div>
          {p.raw && <div className="ml-5 truncate font-mono text-xs text-zinc-500">«{p.raw}»</div>}
          <div className="ml-5 mt-1 flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
            <SmartBadge smart={p.smart} />
            {p.rpp.score !== null && <span className="rounded-full border border-white/10 px-2 py-0.5 text-[11px]">РПП {p.rpp.score} б.</span>}
            <span className="truncate">{why}</span>
          </div>
        </button>
      </div>
      {isOpen && <PositionDetails position={p} period={period} />}
    </li>
  );
}

function PositionDetails({ position: p, period }: { position: MarketExplainPosition; period: string | null }) {
  const [samples, setSamples] = useState<MarketSample[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canSample = p.variant_id !== null || p.modification_id !== null;
  const loadSamples = async () => {
    setError(null);
    try {
      const path =
        p.variant_id !== null
          ? `/meter-market/variants/${p.variant_id}/samples`
          : `/meter-market/modifications/${p.modification_id}/samples`;
      setSamples(await api.get<MarketSample[]>(`${path}?${query({ period })}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить записи поверок");
    }
  };
  return (
    <div className="ml-7 mt-2 space-y-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-xs">
      <Facts position={p} />
      <div>
        <p className="mb-1 font-medium text-zinc-400">Применённые правила</p>
        <ul className="space-y-0.5 text-zinc-400">
          {p.rules.map((rule) => (
            <li key={rule}>· {rule}</li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 font-medium text-zinc-400">Документ-основание</p>
        <ul className="space-y-0.5 text-zinc-400">
          {p.documents.map((d) => (
            <li key={d.label}>· {d.label}</li>
          ))}
        </ul>
        {canSample && !samples && (
          <button type="button" onClick={loadSamples} className="mt-2 text-indigo-300 hover:text-indigo-200">
            Показать записи поверок ФГИС «Аршин»
          </button>
        )}
        {error && <p className="mt-1 text-red-400">{error}</p>}
        {samples && (
          <table className="mt-2 w-full">
            <tbody>
              {samples.length === 0 && (
                <tr>
                  <td className="py-1 text-zinc-600">Записей за период нет</td>
                </tr>
              )}
              {samples.map((s) => (
                <tr key={s.global_id} className={s.annulled || !s.applicable ? "text-zinc-600 line-through" : "text-zinc-300"}>
                  <td className="py-0.5 pr-3">{s.verification_date.split("-").reverse().join(".")}</td>
                  <td className="py-0.5 pr-3">{s.org}</td>
                  <td className="py-0.5 pr-3 font-mono">{s.mi_number}</td>
                  <td className="py-0.5 text-right">
                    {s.url && (
                      <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-indigo-300 hover:text-indigo-200">
                        {s.global_id} <ExternalLink size={11} />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Facts({ position: p }: { position: MarketExplainPosition }) {
  const facts: [string, string][] = [
    ["Тип СИ", `${p.mit_number}${p.notation ? ` · ${p.notation}` : ""}`],
    ["Написание во ФГИС", p.raw ? `«${p.raw}»` : "—"],
    ["Модификация", p.modification ?? "нет данных — не размечено"],
    ["Сопоставлено", p.mapped_by ? MAPPED_BY[p.mapped_by] ?? p.mapped_by : p.modification ? "по справочнику" : "—"],
    ["Тип ПУ · исполнение · интерфейс", `${p.phase} · ${p.design} · ${p.interface}`],
    ["ПП 890", `${p.smart.status} — ${p.smart.reason}`],
    [
      "РПП",
      p.rpp.in_rpp === null
        ? "нет данных"
        : p.rpp.in_rpp
          ? `${p.rpp.score ?? "?"} баллов${p.rpp.requirement_year ? ` · требования ${p.rpp.requirement_year} г.` : ""}`
          : "не внесено",
    ],
    ["Учтено", fmt(p.counted)],
    ["Исключено", p.exclusions.length ? p.exclusions.map((e) => `${fmt(e.count)} — ${e.reason}`).join("; ") : "0"],
  ];
  return (
    <dl className="grid grid-cols-[minmax(0,12rem)_1fr] gap-x-3 gap-y-1">
      {facts.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-zinc-500">{label}</dt>
          <dd className="text-zinc-300">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Несколько позиций рядом: исходные данные, результат сопоставления, правила, документ. */
function ComparePanel({ positions, onClose }: { positions: MarketExplainPosition[]; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-6" onClick={onClose}>
      <div
        className="flex max-h-full w-full max-w-6xl flex-col rounded-2xl border border-white/[0.08] bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-white/[0.08] px-5 py-3">
          <h3 className="text-sm font-semibold text-zinc-100">Сравнение позиций · {positions.length}</h3>
          <button type="button" onClick={onClose} className="rounded p-1 text-zinc-500 hover:bg-white/[0.06]" aria-label="Закрыть">
            <X size={16} />
          </button>
        </header>
        <div className="overflow-auto p-5">
          <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${positions.length}, minmax(240px, 1fr))` }}>
            {positions.map((p) => (
              <div key={p.key} className="space-y-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 text-xs">
                <div>
                  <p className="text-sm font-medium text-zinc-100">{p.modification ?? "нет данных — не размечено"}</p>
                  <p className="text-zinc-500">
                    {p.mit_number} · {p.manufacturer}
                  </p>
                </div>
                <section>
                  <p className="mb-1 font-medium text-zinc-400">Исходные данные</p>
                  <p className="font-mono text-zinc-300">{p.raw ? `«${p.raw}»` : "—"}</p>
                  <p className="text-zinc-500">{p.type_title ?? p.notation ?? ""}</p>
                </section>
                <section>
                  <p className="mb-1 font-medium text-zinc-400">Результат сопоставления</p>
                  <p className="text-zinc-300">
                    {p.phase} · {p.design} · {p.interface}
                  </p>
                  <div className="mt-1">
                    <SmartBadge smart={p.smart} />
                  </div>
                  <p className="mt-1 text-zinc-500">{p.smart.reason}</p>
                  <p className="mt-1 text-zinc-300">
                    учтено {fmt(p.counted)}
                    {p.excluded ? ` · исключено ${fmt(p.excluded)}` : ""}
                  </p>
                  {p.exclusions.map((e) => (
                    <p key={e.reason} className="text-zinc-500">
                      {fmt(e.count)} — {e.reason}
                    </p>
                  ))}
                </section>
                <section>
                  <p className="mb-1 font-medium text-zinc-400">Применённые правила</p>
                  {p.rules.map((rule) => (
                    <p key={rule} className="text-zinc-400">
                      · {rule}
                    </p>
                  ))}
                </section>
                <section>
                  <p className="mb-1 font-medium text-zinc-400">Документ-основание</p>
                  {p.documents.map((d) => (
                    <p key={d.label} className="text-zinc-400">
                      · {d.label}
                    </p>
                  ))}
                </section>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
