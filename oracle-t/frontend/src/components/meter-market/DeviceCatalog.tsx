import { Search } from "lucide-react";
import { Fragment, useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { MarketDeviceRow, MarketManufacturer, MarketType } from "../../api/types";
import { AttributeCell } from "./AttributeCell";
import { CatalogJobs } from "./CatalogJobs";
import { BUTTON, DESIGNS, INPUT, ModificationForm, NO_RIGHT, PRIMARY } from "./ModificationForm";
import { Panel } from "./common";
import { fmt } from "./format";
import { useCatalogJobs } from "./useCatalogJobs";

const DESIGN_LABEL = Object.fromEntries(DESIGNS.map((d) => [d.value, d.label]));
const REVIEW_LABEL: Record<MarketType["review_status"], string> = { new: "на решении", included: "в отчёте", excluded: "не в отчёте" };

interface Props {
  canEdit: boolean;
  report: (message: string) => void;
  fail: (err: unknown, fallback: string) => void;
}

export function DeviceCatalog({ canEdit, report, fail }: Props) {
  const [manufacturers, setManufacturers] = useState<MarketManufacturer[]>([]);
  const [manufacturerId, setManufacturerId] = useState<number | null>(null);
  const [manufacturerSearch, setManufacturerSearch] = useState("");
  const [search, setSearch] = useState("");
  const [draftSearch, setDraftSearch] = useState("");
  const [review, setReview] = useState("");
  const [rows, setRows] = useState<MarketDeviceRow[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<number | null>(null);
  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (manufacturerId !== null) params.set("manufacturer_id", String(manufacturerId));
    if (search) params.set("search", search);
    if (review) params.set("review", review);
    api.get<MarketDeviceRow[]>(`/meter-market/devices?${params}`).then(setRows).catch((err) => fail(err, "Не удалось загрузить таблицу приборов"));
  }, [manufacturerId, search, review, fail]);
  const loadManufacturers = useCallback(() => {
    api.get<MarketManufacturer[]>("/meter-market/manufacturers").then(setManufacturers).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  useEffect(loadManufacturers, [loadManufacturers]);
  const refresh = useCallback(() => {
    load();
    loadManufacturers();
  }, [load, loadManufacturers]);
  const { jobs, startJob, hasRunning } = useCatalogJobs(refresh, fail);
  const selectedManufacturer = manufacturers.find((item) => item.id === manufacturerId) ?? null;
  const visibleManufacturers = manufacturers.filter((item) => item.name.toLowerCase().includes(manufacturerSearch.toLowerCase()));
  const typeIds = [...new Set((rows ?? []).map((row) => row.type_id))];
  const allVisibleSelected = typeIds.length > 0 && typeIds.every((id) => selected.has(id));
  const toggleAll = () => {
    setSelected(allVisibleSelected ? new Set() : new Set(typeIds));
  };
  const markupAll = () => {
    if (window.confirm("Запустить полную разметку справочника? Сначала сработает скрипт, затем ИИ только для незаполненных полей. Ручные значения сохранятся.")) {
      void startJob("/meter-market/types/markup", { type_ids: null });
    }
  };
  const markupSelected = () => {
    void startJob("/meter-market/types/markup", { type_ids: [...selected] });
  };
  return (
    <Panel
      title="Производители и приборы"
      subtitle="Каждая строка — модификация прибора. Отметьте типы для выборочной разметки или запустите полную. Сначала работает скрипт, затем ИИ; ручные значения сохраняются."
      actions={
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setSearch(draftSearch); }}>
          <label className="relative">
            <Search size={13} className="absolute left-2.5 top-2.5 text-zinc-500" />
            <input className={`${INPUT} pl-7`} placeholder="ГРСИ, модификация, производитель" value={draftSearch} onChange={(e) => setDraftSearch(e.target.value)} />
          </label>
          <select className={INPUT} value={review} onChange={(e) => setReview(e.target.value)} aria-label="Статус характеристик">
            <option value="" className="bg-zinc-900">Все статусы</option>
            <option value="pending" className="bg-zinc-900">Требует проверки</option>
            <option value="unresolved" className="bg-zinc-900">Не определено</option>
          </select>
        </form>
      }
    >
      <div className="grid gap-4 xl:grid-cols-[14rem_minmax(0,1fr)]">
        <aside className="rounded-xl border border-white/[0.06] p-2">
          <label className="relative mb-2 block">
            <Search size={13} className="absolute left-2.5 top-2.5 text-zinc-500" />
            <input className={`${INPUT} w-full pl-7`} placeholder="Производитель" value={manufacturerSearch} onChange={(e) => setManufacturerSearch(e.target.value)} />
          </label>
          <button type="button" className={`mb-1 w-full rounded-lg px-2 py-1.5 text-left text-xs ${manufacturerId === null ? "bg-indigo-500/15 text-indigo-200" : "text-zinc-400 hover:bg-white/5"}`} onClick={() => setManufacturerId(null)}>
            Все производители
          </button>
          <div className="max-h-[36rem] space-y-0.5 overflow-y-auto">
            {visibleManufacturers.map((item) => (
              <button key={item.id} type="button" className={`w-full rounded-lg px-2 py-1.5 text-left text-xs ${manufacturerId === item.id ? "bg-indigo-500/15 text-indigo-200" : "text-zinc-400 hover:bg-white/5"}`} onClick={() => setManufacturerId(item.id)}>
                <span className="block">{item.name}</span>
                <span className="text-[10px] text-zinc-600">типов {item.types} · приборов {item.modifications}{item.attribute_review > 0 && <span className="text-amber-400"> · проверить {item.attribute_review}</span>}</span>
              </button>
            ))}
          </div>
        </aside>
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.06] p-2.5">
            <span className="mr-auto text-xs text-zinc-400">{selectedManufacturer ? `${selectedManufacturer.name}: приборов ${selectedManufacturer.modifications}` : `Строк в таблице: ${rows?.length ?? "…"}`}{selected.size > 0 && ` · выбрано типов ${selected.size}`}</span>
            {selectedManufacturer && <button type="button" className={BUTTON} disabled={!canEdit || hasRunning} onClick={() => void startJob(`/meter-market/manufacturers/${selectedManufacturer.id}/sync`)}>Синхронизировать с ФГИС</button>}
            <button type="button" className={BUTTON} disabled={!canEdit || hasRunning || selected.size === 0} title={selected.size ? "Разметить только типы, отмеченные в таблице" : "Сначала отметьте типы"} onClick={markupSelected}>Разметить выбранные через ИИ</button>
            <button type="button" className={PRIMARY} disabled={!canEdit || hasRunning} onClick={markupAll}>Разметить всё через ИИ</button>
          </div>
          <CatalogJobs jobs={jobs} />
          {!canEdit && <p className="text-xs text-zinc-500">{NO_RIGHT} — у вас просмотр.</p>}
          {!rows ? (
            <div className="py-12 text-center text-sm text-zinc-600">Загружаю таблицу…</div>
          ) : rows.length === 0 ? (
            <p className="py-8 text-center text-sm text-zinc-500">По текущим фильтрам приборов нет</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
              <table className="w-full min-w-[1250px] text-xs">
                <thead className="bg-white/[0.02] text-zinc-500">
                  <tr>
                    <th className="w-8 px-2 py-2"><input type="checkbox" className="accent-indigo-500" checked={allVisibleSelected} onChange={toggleAll} aria-label="Выбрать все типы в таблице" /></th>
                    <th className="px-3 py-2 text-left font-medium">ГРСИ / тип</th>
                    <th className="px-3 py-2 text-left font-medium">Модификация</th>
                    <th className="px-3 py-2 text-left font-medium">Тип ПУ</th>
                    <th className="px-3 py-2 text-left font-medium">Исполнение</th>
                    <th className="px-3 py-2 text-left font-medium">Интерфейс</th>
                    <th className="px-3 py-2 text-left font-medium">МПИ</th>
                    <th className="px-3 py-2 text-left font-medium">ПП 890</th>
                    <th className="px-3 py-2 text-left font-medium">РПП</th>
                    <th className="px-3 py-2 text-right font-medium">Поверок</th>
                    <th className="px-3 py-2 text-left font-medium">Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <Fragment key={row.id}>
                      <tr className="border-t border-white/[0.05] text-zinc-300">
                        <td className="px-2 py-1.5"><input type="checkbox" className="accent-indigo-500" checked={selected.has(row.type_id)} onChange={() => setSelected((current) => {
                          const next = new Set(current);
                          if (next.has(row.type_id)) next.delete(row.type_id); else next.add(row.type_id);
                          return next;
                        })} aria-label={`Выбрать ${row.mit_number}`} /></td>
                        <td className="px-3 py-1.5">
                          <div className="font-mono text-zinc-200">{row.mit_number}</div>
                          <div className="text-zinc-500">{row.notation}</div>
                          {manufacturerId === null && <div className="text-[10px] text-zinc-600">{row.manufacturer ?? "производитель не указан"}</div>}
                          <div className="text-[10px] text-zinc-600">{REVIEW_LABEL[row.review_status]}</div>
                        </td>
                        <td className="px-3 py-1.5 text-zinc-100">{row.name}</td>
                        <td className="px-3 py-1.5"><AttributeCell modificationId={row.id} field="phase" value={row.phase ?? "нет данных"} metadata={row.attributes.phase} canEdit={canEdit} onEdit={() => setEditing(row.id)} onDone={(message) => { report(message); load(); }} onError={fail} /></td>
                        <td className="px-3 py-1.5"><AttributeCell modificationId={row.id} field="design" value={DESIGN_LABEL[row.design] ?? row.design} metadata={row.attributes.design} canEdit={canEdit} onEdit={() => setEditing(row.id)} onDone={(message) => { report(message); load(); }} onError={fail} /></td>
                        <td className="px-3 py-1.5"><AttributeCell modificationId={row.id} field="interface" value={row.interface ?? "нет данных"} metadata={row.attributes.interface} canEdit={canEdit} onEdit={() => setEditing(row.id)} onDone={(message) => { report(message); load(); }} onError={fail} /></td>
                        <td className="px-3 py-1.5"><AttributeCell modificationId={row.id} field="mpi_years" value={row.mpi_years === null ? "нет данных" : `${row.mpi_years} лет`} metadata={row.attributes.mpi_years} canEdit={canEdit} onEdit={() => setEditing(row.id)} onDone={(message) => { report(message); load(); }} onError={fail} /></td>
                        <td className="px-3 py-1.5"><AttributeCell modificationId={row.id} field="smart_890" value={row.smart_890 === null ? "нет данных" : row.smart_890 ? "да" : "нет"} metadata={row.attributes.smart_890} canEdit={canEdit} onEdit={() => setEditing(row.id)} onDone={(message) => { report(message); load(); }} onError={fail} /></td>
                        <td className="px-3 py-1.5">{row.in_rpp ? `${row.score ?? "?"} б.${row.rpp_included_at ? ` с ${row.rpp_included_at.split("-").reverse().join(".")}` : ""}` : "нет"}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.count)}</td>
                        <td className="px-3 py-1.5"><button type="button" className="text-zinc-500 hover:text-zinc-200 disabled:opacity-40" disabled={!canEdit} onClick={() => setEditing(editing === row.id ? null : row.id)}>изменить</button></td>
                      </tr>
                      {editing === row.id && (
                        <tr>
                          <td colSpan={11} className="px-3 pb-3">
                            <ModificationForm initial={row} submitLabel="Сохранить" onCancel={() => setEditing(null)} onSubmit={async (values) => {
                              try {
                                await api.patch(`/meter-market/modifications/${row.id}`, values);
                                setEditing(null);
                                report(`«${values.name}» сохранена`);
                                load();
                              } catch (err) {
                                fail(err, "Не удалось сохранить модификацию");
                              }
                            }} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}
