import { Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { MarketType } from "../../api/types";
import { CatalogJobs } from "./CatalogJobs";
import { BUTTON, INPUT, NO_RIGHT, PRIMARY } from "./ModificationForm";
import { Panel } from "./common";
import { fmt } from "./format";
import { useCatalogJobs } from "./useCatalogJobs";

interface Props {
  canEdit: boolean;
  report: (message: string) => void;
  fail: (err: unknown, fallback: string) => void;
}

export function NewTypesTable({ canEdit, report, fail }: Props) {
  const [types, setTypes] = useState<MarketType[] | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const load = useCallback(() => {
    const params = new URLSearchParams({ review_status: "new" });
    if (search) params.set("search", search);
    api.get<MarketType[]>(`/meter-market/types?${params}`).then(setTypes).catch((err) => fail(err, "Не удалось загрузить новые типы"));
  }, [search, fail]);
  useEffect(load, [load]);
  const { jobs, startJob, hasRunning } = useCatalogJobs(load, fail);
  const ids = (types ?? []).map((type) => type.id);
  const allSelected = ids.length > 0 && ids.every((id) => selected.has(id));
  const decide = async (type: MarketType, status: MarketType["review_status"]) => {
    try {
      await api.patch(`/meter-market/types/${type.id}`, { review_status: status });
      report(`${type.mit_number}: ${status === "included" ? "включён в отчёт" : status === "excluded" ? "исключён из отчёта" : "возвращён на решение"}`);
      load();
    } catch (err) {
      fail(err, "Не удалось сохранить решение по типу");
    }
  };
  return (
    <Panel
      title="Новые типы счётчиков"
      subtitle="Типы впервые встретились в поверках. Сначала разметьте характеристики, затем решите в последней колонке, включать ли тип в отчёт."
      actions={
        <label className="relative">
          <Search size={13} className="absolute left-2.5 top-2.5 text-zinc-500" />
          <input className={`${INPUT} pl-7`} placeholder="ГРСИ, обозначение, производитель" value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="mr-auto text-xs text-zinc-500">Новых типов: {types?.length ?? "…"}{selected.size > 0 && ` · выбрано ${selected.size}`}</span>
        <button type="button" className={BUTTON} disabled={!canEdit || hasRunning || selected.size === 0} onClick={() => void startJob("/meter-market/types/markup", { type_ids: [...selected] })}>Разметить выбранные через ИИ</button>
        <button type="button" className={PRIMARY} disabled={!canEdit || hasRunning || ids.length === 0} onClick={() => void startJob("/meter-market/types/markup", { type_ids: ids })}>Разметить все новые через ИИ</button>
      </div>
      <CatalogJobs jobs={jobs} />
      {!canEdit && <p className="mb-2 text-xs text-zinc-500">{NO_RIGHT} — у вас просмотр.</p>}
      {!types ? (
        <div className="py-12 text-center text-sm text-zinc-600">Загружаю…</div>
      ) : types.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">Новых типов нет</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
          <table className="w-full min-w-[1000px] text-xs">
            <thead className="bg-white/[0.02] text-zinc-500">
              <tr>
                <th className="w-8 px-2 py-2"><input type="checkbox" className="accent-indigo-500" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(ids))} aria-label="Выбрать все новые типы" /></th>
                <th className="px-3 py-2 text-left font-medium">ГРСИ</th>
                <th className="px-3 py-2 text-left font-medium">Обозначение</th>
                <th className="px-3 py-2 text-left font-medium">Наименование</th>
                <th className="px-3 py-2 text-left font-medium">Производитель</th>
                <th className="px-3 py-2 text-right font-medium">Поверок</th>
                <th className="px-3 py-2 text-right font-medium">Модификаций</th>
                <th className="px-3 py-2 text-right font-medium">Неразмечено</th>
                <th className="px-3 py-2 text-left font-medium">Решение</th>
              </tr>
            </thead>
            <tbody>
              {types.map((type) => (
                <tr key={type.id} className="border-t border-white/[0.05] text-zinc-300">
                  <td className="px-2 py-1.5"><input type="checkbox" className="accent-indigo-500" checked={selected.has(type.id)} onChange={() => setSelected((current) => {
                    const next = new Set(current);
                    if (next.has(type.id)) next.delete(type.id); else next.add(type.id);
                    return next;
                  })} aria-label={`Выбрать ${type.mit_number}`} /></td>
                  <td className="px-3 py-1.5 font-mono text-zinc-100">{type.mit_number}</td>
                  <td className="px-3 py-1.5">{type.notation ?? "—"}</td>
                  <td className="max-w-md px-3 py-1.5 text-zinc-400">{type.title ?? "—"}</td>
                  <td className="px-3 py-1.5">{type.manufacturer ?? "не указан"}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmt(type.count)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{type.modifications}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{type.unmapped}</td>
                  <td className="px-3 py-1.5">
                    <select className={`${INPUT} py-1`} value={type.review_status} disabled={!canEdit} onChange={(e) => void decide(type, e.target.value as MarketType["review_status"])}>
                      <option value="new" className="bg-zinc-900">на решении</option>
                      <option value="included" className="bg-zinc-900">в отчёте</option>
                      <option value="excluded" className="bg-zinc-900">не в отчёте</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
