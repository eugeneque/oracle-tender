import { useCallback, useEffect, useState } from "react";
import { BadgeCheck, ExternalLink, Loader2, ShieldCheck, Trash2 } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { ProductRegistryRow, RegistryRecordUpsert } from "../../api/types";
import { formatDate, registryStateStyle } from "../../utils/format";

// Реестры допуска модели: ПП 719 (ГИСП), ЗАК ПАО «Россети», реестр российского ПО
// (замечание тестировщика 16.09.2026).
//
// Записи вводятся руками: оба реестра закрыты для автоматической сверки (ГИСП отвечает
// роботам 403, ezak.rosseti.ru открыт только из российских сетей), поэтому у каждой
// строки есть официальная ссылка — сверить и занести. Состояние («действует», «истекает»,
// «истекла») сервер считает по датам; в форме его нет, чтобы не заводить противоречий
// вида «истекла, но действует».
//
// «Проверено: записи нет» — отдельный выбор, а не пустая строка: пустая строка означает
// «не смотрели» и даёт в сопоставлении «нет данных», а «записи нет» — «не соответствует».

type Draft = {
  presence: "present" | "absent";
  record_number: string;
  issued_at: string;
  valid_to: string;
  url: string;
  note: string;
  verified: boolean;
};

const EMPTY_DRAFT: Draft = {
  presence: "present",
  record_number: "",
  issued_at: "",
  valid_to: "",
  url: "",
  note: "",
  verified: true,
};

function draftFrom(row: ProductRegistryRow): Draft {
  if (!row.record) return EMPTY_DRAFT;
  return {
    presence: row.record.presence,
    record_number: row.record.record_number ?? "",
    issued_at: row.record.issued_at ?? "",
    valid_to: row.record.valid_to ?? "",
    url: row.record.url ?? "",
    note: row.record.note ?? "",
    verified: row.record.verified_by_user,
  };
}

export function ProductRegistrySection({
  productId,
  isAdmin,
}: {
  productId: string;
  isAdmin: boolean;
}) {
  const [rows, setRows] = useState<ProductRegistryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await api.get<ProductRegistryRow[]>(`/products/${productId}/registry-records`));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить записи реестров");
    }
  }, [productId]);

  useEffect(() => {
    setRows(null);
    setEditing(null);
    void load();
  }, [load]);

  const startEdit = (row: ProductRegistryRow) => {
    setEditing(row.registry);
    setDraft(draftFrom(row));
    setError(null);
  };

  const save = async (registry: string) => {
    setBusy(true);
    setError(null);
    const payload: RegistryRecordUpsert = {
      presence: draft.presence,
      record_number: draft.record_number || null,
      issued_at: draft.issued_at || null,
      valid_to: draft.valid_to || null,
      url: draft.url || null,
      note: draft.note || null,
      verified: draft.verified,
    };
    try {
      await api.put(`/products/${productId}/registry-records/${registry}`, payload);
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить запись");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (registry: string) => {
    if (!window.confirm("Удалить запись? Модель снова будет считаться непроверенной по этому реестру.")) return;
    setBusy(true);
    try {
      await api.delete(`/products/${productId}/registry-records/${registry}`);
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить запись");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-4 rounded-lg border border-white/[0.06] bg-black/20 px-4 py-3">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
        <ShieldCheck size={13} className="text-indigo-400" />
        Реестры допуска
      </div>
      <p className="mb-2 text-[11px] text-zinc-600">
        По этим записям проверяются требования ТЗ «включён в реестр промышленной продукции
        (ПП 719)», «действующее ЗАК ПАО „Россети“», «ПО в реестре Минцифры». Сверка ручная —
        по ссылке на реестр; состояние по датам считается автоматически.
      </p>

      {error && (
        <div className="mb-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {rows === null ? (
        <p className="text-xs text-zinc-500">Загрузка…</p>
      ) : (
        <table className="w-full text-left text-sm">
          <tbody>
            {rows.map((row) => {
              const style = registryStateStyle(row.state);
              const isEditing = editing === row.registry;
              return (
                <tr key={row.registry} className="border-t border-white/[0.06] align-top">
                  <td className="py-1.5 pr-4 text-zinc-400">
                    <div title={row.official_name}>{row.label}</div>
                    {row.url && (
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-[11px] text-indigo-400 hover:underline"
                      >
                        <ExternalLink size={11} /> сверить в реестре
                      </a>
                    )}
                  </td>
                  <td className="py-1.5 pr-4">
                    {isEditing ? (
                      <RecordForm draft={draft} onChange={setDraft} />
                    ) : (
                      <>
                        <span className={`rounded-md px-2 py-0.5 text-[11px] font-medium ${style.className}`}>
                          {style.label}
                        </span>
                        {row.record && (
                          <div className="mt-1 text-xs text-zinc-300">
                            {row.record.summary}
                            {row.record.url && (
                              <a
                                href={row.record.url}
                                target="_blank"
                                rel="noreferrer"
                                className="ml-1.5 inline-flex items-center gap-0.5 text-[11px] text-indigo-400 hover:underline"
                              >
                                <ExternalLink size={10} /> запись
                              </a>
                            )}
                            {row.record.note && (
                              <div className="text-[11px] text-zinc-500">{row.record.note}</div>
                            )}
                            <div className="text-[11px] text-zinc-600">
                              {row.record.verified_by_user && row.record.verified_at ? (
                                <span className="inline-flex items-center gap-0.5 text-emerald-500/80">
                                  <BadgeCheck size={11} /> сверено {formatDate(row.record.verified_at)}
                                </span>
                              ) : (
                                "не сверялось с реестром"
                              )}
                            </div>
                          </div>
                        )}
                      </>
                    )}
                  </td>
                  {isAdmin && (
                    <td className="py-1.5 text-right text-[11px] whitespace-nowrap">
                      {isEditing ? (
                        <>
                          <button
                            onClick={() => save(row.registry)}
                            disabled={busy}
                            className="mr-2 text-indigo-400 hover:underline disabled:opacity-50"
                          >
                            {busy ? <Loader2 size={11} className="inline animate-spin" /> : "сохранить"}
                          </button>
                          <button onClick={() => setEditing(null)} className="text-zinc-400 hover:underline">
                            отмена
                          </button>
                        </>
                      ) : (
                        <>
                          <button onClick={() => startEdit(row)} className="mr-2 text-indigo-400 hover:underline">
                            {row.record ? "править" : "внести"}
                          </button>
                          {row.record && (
                            <button
                              onClick={() => remove(row.registry)}
                              disabled={busy}
                              title="Удалить запись"
                              className="text-zinc-500 hover:text-red-400 disabled:opacity-50"
                            >
                              <Trash2 size={11} className="inline" />
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RecordForm({ draft, onChange }: { draft: Draft; onChange: (draft: Draft) => void }) {
  const input =
    "w-full rounded-md border border-white/10 bg-black/30 px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600";
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch });
  const absent = draft.presence === "absent";

  return (
    <div className="space-y-1.5">
      <div className="flex gap-3 text-xs text-zinc-300">
        <label className="flex items-center gap-1">
          <input
            type="radio"
            checked={!absent}
            onChange={() => set({ presence: "present" })}
          />
          запись есть
        </label>
        <label className="flex items-center gap-1">
          <input
            type="radio"
            checked={absent}
            onChange={() => set({ presence: "absent" })}
          />
          проверено: записи нет
        </label>
      </div>
      {!absent && (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
          <input
            className={input}
            placeholder="Реестровый номер"
            value={draft.record_number}
            onChange={(e) => set({ record_number: e.target.value })}
          />
          <label className="text-[11px] text-zinc-500">
            выдана
            <input
              type="date"
              className={input}
              value={draft.issued_at}
              onChange={(e) => set({ issued_at: e.target.value })}
            />
          </label>
          <label className="text-[11px] text-zinc-500">
            действует до
            <input
              type="date"
              className={input}
              value={draft.valid_to}
              onChange={(e) => set({ valid_to: e.target.value })}
            />
          </label>
          <input
            className={`${input} sm:col-span-3`}
            placeholder="Ссылка на карточку записи в реестре"
            value={draft.url}
            onChange={(e) => set({ url: e.target.value })}
          />
        </div>
      )}
      <input
        className={input}
        placeholder="Заметка (что и когда сверяли)"
        value={draft.note}
        onChange={(e) => set({ note: e.target.value })}
      />
      <label className="flex items-center gap-1 text-[11px] text-zinc-400">
        <input
          type="checkbox"
          checked={draft.verified}
          onChange={(e) => set({ verified: e.target.checked })}
        />
        сверено с реестром сегодня
      </label>
    </div>
  );
}
