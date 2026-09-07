import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Loader2, UserCog } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { Region, RegionResponsible } from "../../api/types";

/**
 * Справочник «регион → ответственный / руководитель» (раздел 5.6 ТЗ).
 *
 * Два поля Приложения D — «Ответственный за регион» и «Руководитель ответственный за
 * регион» — заполняются только отсюда: в тендерных данных этой информации нет и взять её
 * больше неоткуда, поэтому без справочника выгрузка отдавала бы две пустые колонки.
 *
 * Список показывает только заполненные назначения, а форма выбирает регион из полного
 * справочника: 89 строк, из которых заняты единицы, — это не таблица, а шум.
 */

const inputClass =
  "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

export function RegionResponsiblesSection() {
  const [isExpanded, setIsExpanded] = useState(false);
  const [rows, setRows] = useState<RegionResponsible[]>([]);
  const [regions, setRegions] = useState<Region[]>([]);
  const [regionCode, setRegionCode] = useState("");
  const [responsible, setResponsible] = useState("");
  const [manager, setManager] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [assignments, dictionary] = await Promise.all([
        api.get<RegionResponsible[]>("/dictionaries/region-responsibles"),
        api.get<Region[]>("/dictionaries/regions"),
      ]);
      setRows(assignments);
      setRegions(dictionary);
      setRegionCode((current) => current || dictionary[0]?.code || "");
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить справочник");
    }
  };

  useEffect(() => {
    if (isExpanded) void load();
  }, [isExpanded]);

  const assigned = useMemo(
    () => new Map(rows.map((row) => [row.region_code, row])),
    [rows],
  );

  // Выбор региона подставляет уже назначенных людей: иначе редактирование существующей
  // строки выглядело бы как создание новой и молча затирало бы вторую фамилию.
  const selectRegion = (code: string) => {
    setRegionCode(code);
    const existing = assigned.get(code);
    setResponsible(existing?.responsible_name ?? "");
    setManager(existing?.manager_name ?? "");
  };

  const save = async () => {
    if (!regionCode) return;
    setIsSaving(true);
    setError(null);
    try {
      await api.put<RegionResponsible>(`/dictionaries/region-responsibles/${regionCode}`, {
        responsible_name: responsible,
        manager_name: manager,
      });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить назначение");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="mt-6 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">Ответственные по регионам</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            ФИО ответственного и его руководителя для каждого региона. Эти два поля
            попадают в Excel-выгрузку (Приложение D ТЗ) — больше их взять неоткуда.
          </p>
        </div>
        <ChevronDown
          size={18}
          className={`shrink-0 text-zinc-500 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {isExpanded && (
        <div className="border-t border-white/[0.08] px-5 py-4">
          {error && (
            <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
              {error}
            </div>
          )}

          <div className="mb-5 grid max-w-4xl gap-4 sm:grid-cols-3">
            <label className="block text-xs text-zinc-400">
              Регион
              <select
                value={regionCode}
                onChange={(e) => selectRegion(e.target.value)}
                className={`${inputClass} bg-zinc-900`}
              >
                {regions.map((region) => (
                  <option key={region.code} value={region.code}>
                    {region.code} — {region.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-zinc-400">
              Ответственный
              <input
                value={responsible}
                onChange={(e) => setResponsible(e.target.value)}
                placeholder="Иванов И.И."
                className={inputClass}
              />
            </label>
            <label className="block text-xs text-zinc-400">
              Руководитель
              <input
                value={manager}
                onChange={(e) => setManager(e.target.value)}
                placeholder="Петров П.П."
                className={inputClass}
              />
            </label>
          </div>

          <button
            onClick={() => void save()}
            disabled={isSaving || !regionCode}
            className="mb-5 flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {isSaving ? <Loader2 size={15} className="animate-spin" /> : <UserCog size={15} />}
            {isSaving ? "Сохраняю…" : "Сохранить назначение"}
          </button>

          {rows.length === 0 ? (
            <p className="text-sm text-zinc-600">
              Назначений пока нет — соответствующие столбцы выгрузки останутся пустыми.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[600px] text-sm">
                <thead>
                  <tr className="border-b border-white/[0.08] text-xs text-zinc-500">
                    <th className="pb-2 text-left font-medium">Регион</th>
                    <th className="pb-2 text-left font-medium">Ответственный</th>
                    <th className="pb-2 text-left font-medium">Руководитель</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.region_code}
                      onClick={() => selectRegion(row.region_code)}
                      className="cursor-pointer border-b border-white/[0.04] last:border-0 hover:bg-white/[0.03]"
                    >
                      <td className="py-2.5 text-zinc-300">
                        <span className="mr-2 text-zinc-600">{row.region_code}</span>
                        {row.region_name}
                      </td>
                      <td className="py-2.5 text-zinc-400">{row.responsible_name ?? "—"}</td>
                      <td className="py-2.5 text-zinc-400">{row.manager_name ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
