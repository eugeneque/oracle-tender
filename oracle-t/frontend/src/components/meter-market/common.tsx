import { Download } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { ApiError, downloadFile } from "../../api/client";
import { fmt, fmtShare } from "./format";

/** Общие кирпичи вкладки «Рынок ПУ» (раздел 79 ARCHITECTURE.md). */

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5 ${className}`}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-zinc-200">{title}</h3>
          {subtitle && <p className="mt-1 text-xs text-zinc-600">{subtitle}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** Выгрузка таблицы экрана в Excel с теми же фильтрами (раздел 8 ТЗ). */
export function ExportButton({ query, onError }: { query: string; onError: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      title="Выгрузить таблицу в Excel"
      onClick={async () => {
        setBusy(true);
        try {
          await downloadFile(`/meter-market/export.xlsx?${query}`, "Рынок ПУ.xlsx");
        } catch (err) {
          onError(err instanceof ApiError ? err.message : "Не удалось выгрузить таблицу");
        } finally {
          setBusy(false);
        }
      }}
      className="flex shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-400 transition-colors hover:bg-white/5 hover:text-zinc-200 disabled:opacity-50"
    >
      <Download size={13} />
      {busy ? "Формирую…" : "Excel"}
    </button>
  );
}

// Сегменты кольца — один акцент разной насыщенности, «не размечено» и «н/д» — серым:
// цвет здесь не категория, а порядок по величине (правило «один акцент на экран»).
const SHADES = [1, 0.72, 0.5, 0.32, 0.2, 0.12];
const NEUTRAL = new Set(["не размечено", "н/д"]);

export function Donut({
  rows,
  selected,
  onSelect,
}: {
  rows: { key: string; count: number; share: number }[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  let shade = 0;
  return (
    <div className="flex items-center gap-6">
      <svg viewBox="0 0 140 140" className="h-36 w-36 shrink-0 -rotate-90 text-indigo-400" aria-hidden>
        <circle cx="70" cy="70" r={radius} fill="none" stroke="currentColor" strokeOpacity={0.06} strokeWidth="18" />
        {total > 0 &&
          rows.map((row) => {
            const length = (row.count / total) * circumference;
            const neutral = NEUTRAL.has(row.key);
            const opacity = neutral ? 0 : SHADES[Math.min(shade++, SHADES.length - 1)];
            const dim = selected !== null && selected !== row.key;
            const segment = (
              <circle
                key={row.key}
                cx="70"
                cy="70"
                r={radius}
                fill="none"
                stroke={neutral ? "rgb(113 113 122)" : "currentColor"}
                strokeOpacity={dim ? 0.12 : neutral ? 0.5 : opacity}
                strokeWidth="18"
                strokeDasharray={`${Math.max(0, length - 1.5)} ${circumference}`}
                strokeDashoffset={-offset}
                className="cursor-pointer transition-[stroke-opacity]"
                onClick={() => onSelect(selected === row.key ? null : row.key)}
              />
            );
            offset += length;
            return segment;
          })}
      </svg>
      <ul className="min-w-0 flex-1 space-y-1.5">
        {rows.map((row) => (
          <li key={row.key}>
            <button
              type="button"
              onClick={() => onSelect(selected === row.key ? null : row.key)}
              className={`flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1 text-left text-sm transition-colors ${
                selected === row.key ? "bg-indigo-500/10 text-zinc-100" : "text-zinc-400 hover:bg-white/[0.04]"
              }`}
            >
              <span className="truncate">{row.key}</span>
              <span className="shrink-0 tabular-nums text-zinc-500">{fmtShare(row.share)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Горизонтальные столбцы в тысячах — блок «Интерфейсы». */
export function BarList({
  rows,
  selected,
  onSelect,
}: {
  rows: { key: string; count: number }[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  return (
    <div className="max-h-[420px] space-y-1 overflow-y-auto pr-1">
      {rows.map((row) => {
        const dim = selected !== null && selected !== row.key;
        return (
          <button
            type="button"
            key={row.key}
            onClick={() => onSelect(selected === row.key ? null : row.key)}
            className={`grid w-full grid-cols-[minmax(0,11rem)_1fr_4.5rem] items-center gap-3 rounded-md px-2 py-1 text-left text-xs transition-colors ${
              selected === row.key ? "bg-indigo-500/10" : "hover:bg-white/[0.04]"
            }`}
          >
            <span className={`truncate ${dim ? "text-zinc-600" : "text-zinc-300"}`} title={row.key}>
              {row.key}
            </span>
            <span className="h-2 overflow-hidden rounded-full bg-white/[0.05]">
              <span
                className={`block h-full rounded-full ${NEUTRAL.has(row.key) ? "bg-zinc-500" : "bg-indigo-400"} ${
                  dim ? "opacity-25" : ""
                }`}
                style={{ width: `${Math.max(1, (row.count / max) * 100)}%` }}
              />
            </span>
            <span className="text-right tabular-nums text-zinc-400">{fmt(row.count / 1000)} тыс.</span>
          </button>
        );
      })}
    </div>
  );
}

/** Таблица «строка → вложенные строки» по годам или кварталам (экраны 2–4). */
export function MatrixTable({
  columns,
  columnLabel,
  rows,
  totals,
  total,
  firstHeader,
  onRowClick,
}: {
  columns: string[];
  columnLabel: (column: string) => string;
  rows: { key: string; label: ReactNode; values: Record<string, number>; total: number; children: { key: string; label: ReactNode; values: Record<string, number>; total: number }[] }[];
  totals: Record<string, number>;
  total: number;
  firstHeader: string;
  onRowClick?: (key: string) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const cell = "px-3 py-2 text-right tabular-nums";
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-sm">
        <thead>
          <tr className="border-b border-white/[0.08] text-xs text-zinc-500">
            <th className="px-3 py-2 text-left font-medium">{firstHeader}</th>
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 text-right font-medium">
                {columnLabel(column)}
              </th>
            ))}
            <th className="px-3 py-2 text-right font-medium">Всего</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <FragmentRows
              key={row.key}
              row={row}
              columns={columns}
              isOpen={open.has(row.key)}
              onToggle={() => toggle(row.key)}
              onRowClick={onRowClick}
              cell={cell}
            />
          ))}
          <tr className="border-t border-white/[0.12] font-semibold text-zinc-100">
            <td className="px-3 py-2">Всего</td>
            {columns.map((column) => (
              <td key={column} className={cell}>
                {fmt(totals[column] ?? 0)}
              </td>
            ))}
            <td className={cell}>{fmt(total)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function FragmentRows({
  row,
  columns,
  isOpen,
  onToggle,
  onRowClick,
  cell,
}: {
  row: { key: string; label: ReactNode; values: Record<string, number>; total: number; children: { key: string; label: ReactNode; values: Record<string, number>; total: number }[] };
  columns: string[];
  isOpen: boolean;
  onToggle: () => void;
  onRowClick?: (key: string) => void;
  cell: string;
}) {
  return (
    <>
      <tr className="border-t border-white/[0.05] text-zinc-100 hover:bg-white/[0.02]">
        <td className="px-3 py-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onToggle}
              className="flex h-5 w-5 items-center justify-center rounded text-zinc-500 hover:bg-white/[0.06]"
              aria-label={isOpen ? "Свернуть" : "Развернуть"}
            >
              {isOpen ? "−" : "+"}
            </button>
            {onRowClick ? (
              <button type="button" className="font-semibold hover:text-indigo-300" onClick={() => onRowClick(row.key)}>
                {row.label}
              </button>
            ) : (
              <span className="font-semibold">{row.label}</span>
            )}
          </div>
        </td>
        {columns.map((column) => (
          <td key={column} className={cell}>
            {fmt(row.values[column] ?? 0)}
          </td>
        ))}
        <td className={`${cell} font-medium`}>{fmt(row.total)}</td>
      </tr>
      {isOpen &&
        row.children.map((child) => (
          <tr key={child.key} className="text-zinc-400">
            <td className="py-1.5 pl-12 pr-3 text-xs">{child.label}</td>
            {columns.map((column) => (
              <td key={column} className={`${cell} py-1.5 text-xs`}>
                {fmt(child.values[column] ?? 0)}
              </td>
            ))}
            <td className={`${cell} py-1.5 text-xs`}>{fmt(child.total)}</td>
          </tr>
        ))}
    </>
  );
}
