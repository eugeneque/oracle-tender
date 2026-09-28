import { useEffect, useMemo, useState } from "react";
import { Loader2, UserCog } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { Region, RegionResponsible } from "../../api/types";
import {
  SettingCard,
  SettingsGroup,
  SettingsNotice,
  SettingsPanel,
  primaryButtonClass,
  settingsInputClass,
} from "./ui";

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

const inputClass = settingsInputClass;

export function RegionResponsiblesSection() {
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
    void load();
  }, []);

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
    <SettingsPanel
      title="Ответственные по регионам"
      description="ФИО ответственного и его руководителя для каждого региона. Эти два поля попадают в Excel-выгрузку (Приложение D ТЗ) — в тендерных данных их нет, и взять их больше неоткуда."
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}

      <SettingsGroup id="regions-assign" label="Назначение">
        <SettingCard
          icon={<UserCog size={18} />}
          title="Назначить или изменить"
          description="Выбор региона подставляет уже назначенных людей — так правка существующей строки не затрёт вторую фамилию."
        >
          <div className="grid gap-4 sm:grid-cols-3">
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
            className={`mt-4 ${primaryButtonClass}`}
          >
            {isSaving ? <Loader2 size={15} className="animate-spin" /> : <UserCog size={15} />}
            {isSaving ? "Сохраняю…" : "Сохранить назначение"}
          </button>
        </SettingCard>
      </SettingsGroup>

      <SettingsGroup id="regions-list" label={`Назначено · ${rows.length}`}>
        {rows.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
            Назначений пока нет — соответствующие столбцы выгрузки останутся пустыми.
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {rows.map((row) => (
              <button
                key={row.region_code}
                onClick={() => selectRegion(row.region_code)}
                className={`rounded-2xl border px-5 py-4 text-left transition-colors ${
                  row.region_code === regionCode
                    ? "border-indigo-500/25 bg-indigo-500/[0.08]"
                    : "border-white/[0.08] bg-white/[0.03] hover:border-white/[0.12]"
                }`}
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
                  <span className="rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[11px] font-medium text-zinc-400">
                    {row.region_code}
                  </span>
                  {row.region_name}
                </div>
                <div className="mt-1.5 text-xs text-zinc-500">
                  Ответственный: <span className="text-zinc-300">{row.responsible_name ?? "—"}</span>
                </div>
                <div className="text-xs text-zinc-500">
                  Руководитель: <span className="text-zinc-300">{row.manager_name ?? "—"}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </SettingsGroup>
    </SettingsPanel>
  );
}
