import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { ApiError, api } from "../../api/client";
import type { CompanyProfile, CompanyResources } from "../../api/types";

// Ресурсы компании (19.11.2026, раздел 115 ARCHITECTURE.md): персонал, площади, оборудование.
// По ним квалификационные требования закупки («не менее 10 электромонтёров с III группой»,
// «наличие склада», «поверочное оборудование») сверяются с компанией, а не уходят в Bitrix24
// сплошным «нужно проверить». Численность и выручку даёт досье Rusprofile — здесь их нет.

const EMPTY: CompanyResources = { staff: [], facilities: [], equipment: [] };

type ListKey = keyof CompanyResources;

const COLUMNS: Record<ListKey, Array<{ key: string; label: string; placeholder: string; number?: boolean }>> = {
  staff: [
    { key: "role", label: "Должность", placeholder: "Электромонтёр" },
    { key: "count", label: "Человек", placeholder: "12", number: true },
    { key: "qualification", label: "Квалификация", placeholder: "IV группа по электробезопасности" },
  ],
  facilities: [
    { key: "kind", label: "Вид", placeholder: "Склад" },
    { key: "address", label: "Адрес", placeholder: "г. Ростов-на-Дону, ул. …" },
    { key: "area", label: "Площадь", placeholder: "1200 м²" },
  ],
  equipment: [
    { key: "name", label: "Оборудование", placeholder: "Поверочная установка" },
    { key: "count", label: "Шт.", placeholder: "2", number: true },
    { key: "note", label: "Примечание", placeholder: "" },
  ],
};

const TITLES: Record<ListKey, string> = {
  staff: "Персонал",
  facilities: "Склады и площадки",
  equipment: "Оборудование",
};

const REQUIRED: Record<ListKey, string> = { staff: "role", facilities: "kind", equipment: "name" };

export function CompanyResourcesTab({ profile, canEdit }: { profile: CompanyProfile; canEdit: boolean }) {
  const [resources, setResources] = useState<CompanyResources>({ ...EMPTY, ...(profile.resources ?? {}) });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  function update(list: ListKey, index: number, key: string, value: string, number?: boolean) {
    setResources((current) => {
      const items = [...(current[list] as Array<Record<string, unknown>>)];
      items[index] = { ...items[index], [key]: number ? (value === "" ? null : Number(value)) : value };
      return { ...current, [list]: items };
    });
    setDirty(true);
  }

  function add(list: ListKey) {
    setResources((current) => ({ ...current, [list]: [...current[list], { [REQUIRED[list]]: "" }] }));
    setDirty(true);
  }

  function remove(list: ListKey, index: number) {
    setResources((current) => ({ ...current, [list]: current[list].filter((_, i) => i !== index) }));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    setMessage(null);
    const cleaned: CompanyResources = {
      staff: resources.staff.filter((item) => item.role?.trim()),
      facilities: resources.facilities.filter((item) => item.kind?.trim()),
      equipment: resources.equipment.filter((item) => item.name?.trim()),
    };
    try {
      const saved = await api.put<CompanyProfile>(`/company-profiles/${profile.id}`, { resources: cleaned });
      setResources({ ...EMPTY, ...(saved.resources ?? {}) });
      setDirty(false);
      setMessage("Сохранено");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-xs text-zinc-500">
        С этими данными сверяются требования закупок к персоналу, складам и оборудованию. Численность и
        выручка берутся из досье Rusprofile.
      </p>
      {(Object.keys(COLUMNS) as ListKey[]).map((list) => (
        <section key={list}>
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-sm font-medium text-zinc-200">{TITLES[list]}</h4>
            {canEdit && (
              <button
                type="button"
                onClick={() => add(list)}
                className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-white"
              >
                <Plus className="h-3.5 w-3.5" /> Добавить
              </button>
            )}
          </div>
          {resources[list].length === 0 ? (
            <div className="text-xs text-zinc-600">Не указано</div>
          ) : (
            <div className="space-y-2">
              {(resources[list] as Array<Record<string, unknown>>).map((item, index) => (
                <div key={index} className="flex items-center gap-2">
                  {COLUMNS[list].map((column) => (
                    <input
                      key={column.key}
                      aria-label={column.label}
                      disabled={!canEdit}
                      type={column.number ? "number" : "text"}
                      min={column.number ? 0 : undefined}
                      value={(item[column.key] as string | number | null | undefined) ?? ""}
                      placeholder={column.placeholder}
                      onChange={(e) => update(list, index, column.key, e.target.value, column.number)}
                      className={`${column.number ? "w-20" : "min-w-0 flex-1"} rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-white/25 focus:outline-none disabled:opacity-70`}
                    />
                  ))}
                  {canEdit && (
                    <button
                      type="button"
                      aria-label="Удалить"
                      onClick={() => remove(list, index)}
                      className="shrink-0 p-1 text-zinc-500 hover:text-red-400"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
      {canEdit && (
        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={!dirty || saving}
            onClick={save}
            className="rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-black disabled:opacity-40"
          >
            {saving ? "Сохранение…" : "Сохранить"}
          </button>
          {message && <span className="text-xs text-zinc-500">{message}</span>}
        </div>
      )}
    </div>
  );
}
