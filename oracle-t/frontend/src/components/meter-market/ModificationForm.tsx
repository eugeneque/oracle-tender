import { useState } from "react";

import type { MarketModification } from "../../api/types";

/** Общие стили и форма модификации справочника «Рынка ПУ»: очередь «Разметить», справочник и детализация. */

export const INPUT = "rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm text-zinc-200";
export const BUTTON =
  "inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40";
export const PRIMARY =
  "inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-40";
export const DESIGNS: { value: string; label: string }[] = [
  { value: "din", label: "DIN-рейка" },
  { value: "cabinet", label: "Шкафное" },
  { value: "split", label: "Сплит" },
  { value: "nd", label: "нет данных" },
];
export const NO_RIGHT = "Справочник правят аналитик и администратор";

export type ModValues = Partial<Pick<MarketModification, "name" | "phase" | "design" | "interface" | "mpi_years" | "comment" | "in_rpp" | "score" | "rpp_included_at" | "smart_890">>;

export function ModificationForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: ModValues;
  submitLabel: string;
  onSubmit: (values: ModValues) => Promise<void>;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<ModValues>({
    design: "nd",
    phase: "1ф",
    ...initial,
    // В форме — только ручное решение; вердикт ИИ показан в таблице и не должен стать ручным при правке.
    smart_890: (initial as Partial<MarketModification>).smart_890_source === "manual" ? initial.smart_890 : null,
  });
  const [busy, setBusy] = useState(false);
  const set = (patch: ModValues) => setValues((prev) => ({ ...prev, ...patch }));
  return (
    <form
      className="mt-3 grid grid-cols-2 gap-2 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 md:grid-cols-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        await onSubmit(values);
        setBusy(false);
      }}
    >
      <label className="col-span-2 text-xs text-zinc-500">
        Название
        <input className={`${INPUT} mt-1 w-full`} value={values.name ?? ""} onChange={(e) => set({ name: e.target.value })} required />
      </label>
      <label className="text-xs text-zinc-500">
        Тип ПУ
        <select className={`${INPUT} mt-1 w-full`} value={values.phase ?? ""} onChange={(e) => set({ phase: e.target.value || null })}>
          <option value="1ф" className="bg-zinc-900">1ф</option>
          <option value="3ф" className="bg-zinc-900">3ф</option>
        </select>
      </label>
      <label className="text-xs text-zinc-500">
        Исполнение
        <select className={`${INPUT} mt-1 w-full`} value={values.design ?? "nd"} onChange={(e) => set({ design: e.target.value })}>
          {DESIGNS.map((d) => (
            <option key={d.value} value={d.value} className="bg-zinc-900">{d.label}</option>
          ))}
        </select>
      </label>
      <label className="col-span-2 text-xs text-zinc-500">
        Интерфейс
        <input className={`${INPUT} mt-1 w-full`} placeholder="GSM и RS" value={values.interface ?? ""} onChange={(e) => set({ interface: e.target.value })} />
      </label>
      <label className="text-xs text-zinc-500">
        МПИ, лет
        <input className={`${INPUT} mt-1 w-full`} type="number" value={values.mpi_years ?? ""} onChange={(e) => set({ mpi_years: e.target.value ? Number(e.target.value) : null })} />
      </label>
      <label className="flex items-end gap-2 pb-2 text-xs text-zinc-400">
        <input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={!!values.in_rpp} onChange={(e) => set({ in_rpp: e.target.checked })} />
        в РПП
      </label>
      <label className="text-xs text-zinc-500">
        Соответствие ПП 890
        <select
          className={`${INPUT} mt-1 w-full`}
          value={values.smart_890 === true ? "yes" : values.smart_890 === false ? "no" : ""}
          onChange={(e) => set({ smart_890: e.target.value === "" ? null : e.target.value === "yes" })}
        >
          <option value="" className="bg-zinc-900">решает ИИ</option>
          <option value="yes" className="bg-zinc-900">да (интеллектуальный)</option>
          <option value="no" className="bg-zinc-900">нет (простейший)</option>
        </select>
      </label>
      {values.in_rpp && (
        <>
          <label className="text-xs text-zinc-500">
            Баллы ПП 719
            <input className={`${INPUT} mt-1 w-full`} type="number" value={values.score ?? ""} onChange={(e) => set({ score: e.target.value ? Number(e.target.value) : null })} />
          </label>
          <label className="text-xs text-zinc-500">
            Внесено в РПП
            <input className={`${INPUT} mt-1 w-full`} type="date" value={values.rpp_included_at ?? ""} onChange={(e) => set({ rpp_included_at: e.target.value || null })} />
          </label>
        </>
      )}
      <label className="col-span-2 text-xs text-zinc-500 md:col-span-4">
        Дополнительно (в отчёт не идёт)
        <input className={`${INPUT} mt-1 w-full`} value={values.comment ?? ""} onChange={(e) => set({ comment: e.target.value })} />
      </label>
      <div className="col-span-2 flex justify-end gap-2 md:col-span-4">
        <button type="button" className={BUTTON} onClick={onCancel}>Отмена</button>
        <button type="submit" className={PRIMARY} disabled={busy}>{submitLabel}</button>
      </div>
    </form>
  );
}
