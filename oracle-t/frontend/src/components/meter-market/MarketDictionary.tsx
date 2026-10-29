import { Check, Plus, Search, Upload, X } from "lucide-react";
import { Fragment, useCallback, useEffect, useState } from "react";

import { ApiError, api, uploadFile } from "../../api/client";
import type {
  MarketGroup,
  MarketManufacturer,
  MarketModification,
  MarketStatus,
  MarketType,
  MarketUnmappedItem,
} from "../../api/types";
import { SegmentedControl } from "../ui/SegmentedControl";
import { Panel } from "./common";
import { fmt } from "./format";

/**
 * «Справочник рынка ПУ» (раздел 79 ARCHITECTURE.md, раздел 6 ТЗ): то, что аналитик раньше
 * вела в Excel. Очередь «Разметить» — написания модификаций, которые Сова не свела сама;
 * очередь новых типов — описания типа счётчиков ЭЭ, впервые встреченные в поверках. Без права
 * правки всё видно, но кнопки неактивны с подсказкой.
 */

type Tab = "unmapped" | "new" | "types" | "groups" | "thresholds" | "import";

const INPUT = "rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm text-zinc-200";
const BUTTON =
  "inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40";
const PRIMARY =
  "inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-40";
const DESIGNS: { value: string; label: string }[] = [
  { value: "din", label: "DIN-рейка" },
  { value: "cabinet", label: "Шкафное" },
  { value: "split", label: "Сплит" },
  { value: "nd", label: "н/д" },
];
const NO_RIGHT = "Справочник правят аналитик и администратор";

export function MarketDictionary({ status, onChanged }: { status: MarketStatus; onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>("unmapped");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const report = (message: string) => {
    setError(null);
    setNotice(message);
    onChanged();
  };
  const fail = (err: unknown, fallback: string) => {
    setNotice(null);
    setError(err instanceof ApiError ? err.message : fallback);
  };
  const props = { canEdit: status.can_edit, report, fail };
  return (
    <div className="space-y-5">
      <SegmentedControl<Tab>
        value={tab}
        onChange={setTab}
        ariaLabel="Раздел справочника"
        segments={[
          { value: "unmapped", label: `Разметить${status.unmapped_variants ? ` · ${fmt(status.unmapped_variants)}` : ""}` },
          { value: "new", label: `Новые типы${status.new_types ? ` · ${status.new_types}` : ""}` },
          { value: "types", label: "Типы и модификации" },
          { value: "groups", label: "Группы компаний" },
          { value: "thresholds", label: "Пороги баллов" },
          { value: "import", label: "Импорт" },
        ]}
      />
      {!status.can_edit && <p className="text-xs text-zinc-500">{NO_RIGHT} — у вас просмотр.</p>}
      {error && <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">{error}</div>}
      {notice && (
        <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-4 py-2.5 text-sm text-emerald-400">{notice}</div>
      )}
      {tab === "unmapped" && <UnmappedQueue {...props} />}
      {tab === "new" && <TypesList {...props} reviewStatus="new" />}
      {tab === "types" && <TypesList {...props} />}
      {tab === "groups" && <Groups {...props} />}
      {tab === "thresholds" && <Thresholds {...props} status={status} />}
      {tab === "import" && <Import {...props} />}
    </div>
  );
}

interface EditProps {
  canEdit: boolean;
  report: (message: string) => void;
  fail: (err: unknown, fallback: string) => void;
}

// ── Разметить ──────────────────────────────────────────────────────────────────────────────

function UnmappedQueue({ canEdit, report, fail }: EditProps) {
  const [data, setData] = useState<{ items: MarketUnmappedItem[]; variants: number; verifications: number } | null>(null);
  const load = useCallback(() => {
    api.get<typeof data>("/meter-market/unmapped").then(setData).catch((err) => fail(err, "Не удалось загрузить очередь"));
  }, [fail]);
  useEffect(load, [load]);
  if (!data) return <div className="py-16 text-center text-sm text-zinc-600">Загружаю очередь…</div>;
  return (
    <Panel
      title={`Неразмеченных написаний: ${fmt(data.variants)} · поверок: ${fmt(data.verifications)}`}
      subtitle="Поверитель записал модификацию так, что Сова не свела её с известной. Привяжите к модификации или заведите новую — то же написание с точностью до регистра, пробелов и латиницы сведётся само."
    >
      {data.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">Всё размечено</p>
      ) : (
        <div className="divide-y divide-white/[0.05]">
          {data.items.map((item) => (
            <UnmappedRow key={item.id} item={item} canEdit={canEdit} fail={fail} onDone={(m) => { report(m); load(); }} />
          ))}
        </div>
      )}
    </Panel>
  );
}

function UnmappedRow({
  item,
  canEdit,
  fail,
  onDone,
}: {
  item: MarketUnmappedItem;
  canEdit: boolean;
  fail: (err: unknown, fallback: string) => void;
  onDone: (message: string) => void;
}) {
  const [mods, setMods] = useState<MarketModification[] | null>(null);
  const [target, setTarget] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  const openMods = () => {
    if (mods) return;
    api.get<MarketModification[]>(`/meter-market/types/${item.type_id}/modifications`).then(setMods).catch((err) => fail(err, "Не удалось загрузить модификации"));
  };
  const map = async () => {
    setBusy(true);
    try {
      const result = await api.put<{ same_key_mapped: number }>(`/meter-market/variants/${item.id}`, {
        modification_id: Number(target),
      });
      onDone(`«${item.raw}» размечено${result.same_key_mapped ? `, ещё ${result.same_key_mapped} написаний сведено` : ""}`);
    } catch (err) {
      fail(err, "Не удалось разметить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="whitespace-pre-wrap font-mono text-sm text-zinc-100">«{item.raw}»</div>
          <div className="mt-0.5 text-xs text-zinc-500">
            {item.mit_number} {item.notation} · {item.manufacturer ?? "производитель не указан"} ·{" "}
            <span className="tabular-nums text-zinc-300">{fmt(item.count)}</span> поверок
          </div>
          {(item.orgs.length > 0 || item.mi_numbers.length > 0) && (
            <div className="mt-0.5 truncate text-xs text-zinc-600">
              {item.orgs.join("; ")}
              {item.mi_numbers.length > 0 && ` · зав. № ${item.mi_numbers.join(", ")}`}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className={`${INPUT} max-w-[18rem]`}
            value={target}
            onFocus={openMods}
            onMouseDown={openMods}
            disabled={!canEdit}
            title={canEdit ? undefined : NO_RIGHT}
            onChange={(e) => setTarget(e.target.value)}
          >
            <option value="" className="bg-zinc-900">
              {mods ? "привязать к модификации…" : "выбрать модификацию…"}
            </option>
            {(mods ?? []).map((m) => (
              <option key={m.id} value={m.id} className="bg-zinc-900">
                {m.name} · {[m.phase, m.interface].filter(Boolean).join(", ")}
              </option>
            ))}
          </select>
          <button type="button" className={PRIMARY} disabled={!canEdit || !target || busy} onClick={() => void map()}>
            <Check size={13} />
            Привязать
          </button>
          <button
            type="button"
            className={BUTTON}
            disabled={!canEdit}
            title={canEdit ? undefined : NO_RIGHT}
            onClick={() => setCreating((v) => !v)}
          >
            <Plus size={13} />
            Новая
          </button>
        </div>
      </div>
      {creating && (
        <ModificationForm
          initial={{ name: item.raw.replace(/\s+/g, " ").trim() }}
          submitLabel="Создать и привязать"
          onCancel={() => setCreating(false)}
          onSubmit={async (values) => {
            try {
              await api.post(`/meter-market/types/${item.type_id}/modifications`, { ...values, variant_id: item.id });
              onDone(`Модификация «${values.name}» создана`);
            } catch (err) {
              fail(err, "Не удалось создать модификацию");
            }
          }}
        />
      )}
    </div>
  );
}

type ModValues = Partial<Pick<MarketModification, "name" | "phase" | "design" | "interface" | "mpi_years" | "comment" | "in_rpp" | "score" | "rpp_included_at">>;

function ModificationForm({
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
  const [values, setValues] = useState<ModValues>({ design: "nd", phase: "1ф", ...initial });
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

// ── Типы и модификации ─────────────────────────────────────────────────────────────────────

const REVIEW_LABEL: Record<MarketType["review_status"], string> = {
  new: "на решении",
  included: "в отчёте",
  excluded: "не в отчёте",
};

function TypesList({ canEdit, report, fail, reviewStatus }: EditProps & { reviewStatus?: "new" }) {
  const [search, setSearch] = useState("");
  const [types, setTypes] = useState<MarketType[] | null>(null);
  const [manufacturers, setManufacturers] = useState<MarketManufacturer[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (reviewStatus) params.set("review_status", reviewStatus);
    if (search) params.set("search", search);
    api.get<MarketType[]>(`/meter-market/types?${params}`).then(setTypes).catch((err) => fail(err, "Не удалось загрузить типы"));
  }, [reviewStatus, search, fail]);
  useEffect(load, [load]);
  useEffect(() => {
    api.get<MarketManufacturer[]>("/meter-market/manufacturers").then(setManufacturers).catch(() => undefined);
  }, []);

  const patch = async (type: MarketType, body: Record<string, unknown>, message: string) => {
    try {
      await api.patch(`/meter-market/types/${type.id}`, body);
      report(message);
      load();
    } catch (err) {
      fail(err, "Не удалось сохранить тип");
    }
  };

  return (
    <Panel
      title={reviewStatus ? "Новые описания типа счётчиков ЭЭ" : "Описания типа (ГРСИ)"}
      subtitle={
        reviewStatus
          ? "Впервые встретились в поверках ФГИС. Интеллектуальный ли это счётчик по ПП 890 и включать ли его в отчёт — решение аналитика."
          : "Производитель в отчёте — владелец описания типа"
      }
      actions={
        !reviewStatus && (
          <label className="relative">
            <Search size={13} className="absolute left-2.5 top-2.5 text-zinc-500" />
            <input className={`${INPUT} pl-7`} placeholder="номер, обозначение, производитель" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
        )
      }
    >
      {!types ? (
        <div className="py-12 text-center text-sm text-zinc-600">Загружаю…</div>
      ) : types.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">{reviewStatus ? "Новых типов нет" : "Ничего не нашлось"}</p>
      ) : (
        <div className="divide-y divide-white/[0.05]">
          {types.map((type) => (
            <div key={type.id} className="py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <button type="button" className="min-w-0 text-left" onClick={() => setOpenId(openId === type.id ? null : type.id)}>
                  <span className="text-sm text-zinc-100">{type.mit_number}</span>
                  <span className="ml-2 text-sm text-zinc-400">{type.notation}</span>
                  <span className="block truncate text-xs text-zinc-600">
                    {[type.title, type.manufacturer ?? "производитель не указан", `${fmt(type.count)} поверок`].filter(Boolean).join(" · ")}
                    {type.unmapped > 0 && <span className="text-amber-400"> · неразмечено {type.unmapped}</span>}
                  </span>
                </button>
                <div className="flex items-center gap-2">
                  <select
                    className={`${INPUT} max-w-[14rem] py-1`}
                    value={type.manufacturer_id ?? ""}
                    disabled={!canEdit}
                    title={canEdit ? "Владелец описания типа" : NO_RIGHT}
                    onChange={(e) => void patch(type, { manufacturer_id: e.target.value ? Number(e.target.value) : null }, `Производитель ${type.mit_number} изменён`)}
                  >
                    <option value="" className="bg-zinc-900">производитель…</option>
                    {manufacturers.map((m) => (
                      <option key={m.id} value={m.id} className="bg-zinc-900">{m.name}</option>
                    ))}
                  </select>
                  <span className="text-xs text-zinc-500">{REVIEW_LABEL[type.review_status]}</span>
                  {type.review_status !== "included" && (
                    <button type="button" className={PRIMARY} disabled={!canEdit} title={canEdit ? undefined : NO_RIGHT}
                      onClick={() => void patch(type, { review_status: "included" }, `${type.mit_number} включён в отчёт`)}>
                      В отчёт
                    </button>
                  )}
                  {type.review_status !== "excluded" && (
                    <button type="button" className={BUTTON} disabled={!canEdit} title={canEdit ? undefined : NO_RIGHT}
                      onClick={() => void patch(type, { review_status: "excluded" }, `${type.mit_number} исключён из отчёта`)}>
                      Не включать
                    </button>
                  )}
                </div>
              </div>
              {openId === type.id && <Modifications typeId={type.id} canEdit={canEdit} report={report} fail={fail} />}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

const DESIGN_LABEL = Object.fromEntries(DESIGNS.map((d) => [d.value, d.label]));

function Modifications({ typeId, canEdit, report, fail }: EditProps & { typeId: number }) {
  const [mods, setMods] = useState<MarketModification[] | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [variants, setVariants] = useState<Record<number, { id: number; raw: string; count: number; mapped_by: string | null }[]>>({});
  const load = useCallback(() => {
    api.get<MarketModification[]>(`/meter-market/types/${typeId}/modifications`).then(setMods).catch((err) => fail(err, "Не удалось загрузить модификации"));
  }, [typeId, fail]);
  useEffect(load, [load]);
  const showVariants = (id: number) => {
    if (variants[id]) {
      setVariants(({ [id]: _, ...rest }) => rest);
      return;
    }
    api.get<(typeof variants)[number]>(`/meter-market/modifications/${id}/variants`).then((rows) => setVariants((prev) => ({ ...prev, [id]: rows })));
  };
  if (!mods) return <p className="py-3 text-xs text-zinc-600">Загружаю модификации…</p>;
  return (
    <div className="mt-2 overflow-x-auto rounded-xl border border-white/[0.06]">
      <table className="w-full min-w-[760px] text-xs">
        <thead>
          <tr className="text-zinc-500">
            <th className="px-3 py-2 text-left font-medium">Модификация</th>
            <th className="px-3 py-2 text-left font-medium">Тип</th>
            <th className="px-3 py-2 text-left font-medium">Исполнение</th>
            <th className="px-3 py-2 text-left font-medium">Интерфейс</th>
            <th className="px-3 py-2 text-left font-medium">РПП</th>
            <th className="px-3 py-2 text-right font-medium">Написаний</th>
            <th className="px-3 py-2 text-right font-medium">Поверок</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {mods.map((m) => (
            <Fragment key={m.id}>
              <tr className="border-t border-white/[0.05] text-zinc-300">
                <td className="px-3 py-1.5 text-zinc-100">{m.name}</td>
                <td className="px-3 py-1.5">{m.phase ?? "—"}</td>
                <td className="px-3 py-1.5">{DESIGN_LABEL[m.design] ?? m.design}</td>
                <td className="px-3 py-1.5">{m.interface ?? "—"}</td>
                <td className="px-3 py-1.5">
                  {m.in_rpp ? `${m.score ?? "?"} б.${m.rpp_included_at ? ` с ${m.rpp_included_at.split("-").reverse().join(".")}` : ""}` : "нет"}
                </td>
                <td className="px-3 py-1.5 text-right">
                  <button type="button" className="tabular-nums text-indigo-300 hover:underline" onClick={() => showVariants(m.id)}>
                    {m.variants}
                  </button>
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{fmt(m.count)}</td>
                <td className="px-3 py-1.5 text-right">
                  <button type="button" className="text-zinc-500 hover:text-zinc-200 disabled:opacity-40" disabled={!canEdit} title={canEdit ? "Изменить" : NO_RIGHT}
                    onClick={() => setEditing(editing === m.id ? null : m.id)}>
                    изменить
                  </button>
                </td>
              </tr>
              {variants[m.id] && (
                <tr key={`${m.id}-v`}>
                  <td colSpan={8} className="px-6 pb-2 text-zinc-500">
                    {variants[m.id].map((v) => (
                      <div key={v.id} className="flex justify-between gap-3">
                        <span className="whitespace-pre-wrap font-mono">«{v.raw}»</span>
                        <span className="tabular-nums">{fmt(v.count)}</span>
                      </div>
                    ))}
                  </td>
                </tr>
              )}
              {editing === m.id && (
                <tr key={`${m.id}-e`}>
                  <td colSpan={8} className="px-3 pb-3">
                    <ModificationForm
                      initial={m}
                      submitLabel="Сохранить"
                      onCancel={() => setEditing(null)}
                      onSubmit={async (values) => {
                        try {
                          await api.patch(`/meter-market/modifications/${m.id}`, values);
                          setEditing(null);
                          report(`«${values.name}» сохранена`);
                          load();
                        } catch (err) {
                          fail(err, "Не удалось сохранить модификацию");
                        }
                      }}
                    />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Группы компаний ────────────────────────────────────────────────────────────────────────

function Groups({ canEdit, report, fail }: EditProps) {
  const [groups, setGroups] = useState<MarketGroup[] | null>(null);
  const [manufacturers, setManufacturers] = useState<MarketManufacturer[]>([]);
  const [draft, setDraft] = useState<MarketGroup | null>(null);
  const load = useCallback(() => {
    api.get<MarketGroup[]>("/meter-market/company-groups").then(setGroups).catch((err) => fail(err, "Не удалось загрузить группы"));
  }, [fail]);
  useEffect(load, [load]);
  useEffect(() => {
    api.get<MarketManufacturer[]>("/meter-market/manufacturers").then(setManufacturers).catch(() => undefined);
  }, []);

  const save = async () => {
    if (!draft) return;
    try {
      const body = { name: draft.name, members: draft.members.map(({ manufacturer_id, year_from, year_to }) => ({ manufacturer_id, year_from, year_to })) };
      if (draft.id) await api.put(`/meter-market/company-groups/${draft.id}`, body);
      else await api.post("/meter-market/company-groups", body);
      setDraft(null);
      report(`Группа «${draft.name}» сохранена`);
      load();
    } catch (err) {
      fail(err, "Не удалось сохранить группу");
    }
  };
  const yearInput = (value: number | null, onChange: (v: number | null) => void, placeholder: string) => (
    <input className={`${INPUT} w-20 py-1`} type="number" placeholder={placeholder} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} />
  );

  return (
    <Panel
      title="Группы компаний"
      subtitle="Компания в группе с годами участия; производитель без группы показывается группой из одной компании"
      actions={
        <button type="button" className={BUTTON} disabled={!canEdit} title={canEdit ? undefined : NO_RIGHT} onClick={() => setDraft({ id: 0, name: "", members: [] })}>
          <Plus size={13} /> Группа
        </button>
      }
    >
      {draft && (
        <div className="mb-4 space-y-2 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
          <input className={`${INPUT} w-full`} placeholder="Название группы" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          {draft.members.map((member, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2">
              <select className={`${INPUT} py-1`} value={member.manufacturer_id} onChange={(e) => {
                const members = [...draft.members];
                members[index] = { ...member, manufacturer_id: Number(e.target.value) };
                setDraft({ ...draft, members });
              }}>
                {manufacturers.map((m) => (
                  <option key={m.id} value={m.id} className="bg-zinc-900">{m.name}</option>
                ))}
              </select>
              {yearInput(member.year_from, (v) => { const members = [...draft.members]; members[index] = { ...member, year_from: v }; setDraft({ ...draft, members }); }, "с года")}
              {yearInput(member.year_to, (v) => { const members = [...draft.members]; members[index] = { ...member, year_to: v }; setDraft({ ...draft, members }); }, "по год")}
              <button type="button" className="text-zinc-500 hover:text-red-400" onClick={() => setDraft({ ...draft, members: draft.members.filter((_, i) => i !== index) })}>
                <X size={14} />
              </button>
            </div>
          ))}
          <div className="flex justify-between">
            <button type="button" className={BUTTON} disabled={manufacturers.length === 0}
              onClick={() => setDraft({ ...draft, members: [...draft.members, { manufacturer_id: manufacturers[0].id, name: null, year_from: null, year_to: null }] })}>
              <Plus size={13} /> Компания
            </button>
            <div className="flex gap-2">
              <button type="button" className={BUTTON} onClick={() => setDraft(null)}>Отмена</button>
              <button type="button" className={PRIMARY} disabled={!draft.name.trim()} onClick={() => void save()}>Сохранить</button>
            </div>
          </div>
        </div>
      )}
      {!groups ? (
        <div className="py-12 text-center text-sm text-zinc-600">Загружаю…</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {groups.map((group) => (
            <div key={group.id} className="rounded-xl border border-white/[0.06] p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-zinc-100">{group.name}</span>
                <div className="flex gap-2">
                  <button type="button" className="text-xs text-zinc-500 hover:text-zinc-200 disabled:opacity-40" disabled={!canEdit} onClick={() => setDraft(group)}>изменить</button>
                  <button type="button" className="text-xs text-zinc-500 hover:text-red-400 disabled:opacity-40" disabled={!canEdit}
                    onClick={async () => {
                      try {
                        await api.delete(`/meter-market/company-groups/${group.id}`);
                        report(`Группа «${group.name}» удалена`);
                        load();
                      } catch (err) {
                        fail(err, "Не удалось удалить группу");
                      }
                    }}>удалить</button>
                </div>
              </div>
              <ul className="mt-2 space-y-0.5 text-xs text-zinc-400">
                {group.members.map((m) => (
                  <li key={`${m.manufacturer_id}-${m.year_from}`}>
                    {m.name}
                    {(m.year_from || m.year_to) && (
                      <span className="text-zinc-600"> · {m.year_from ? `с ${m.year_from}` : ""}{m.year_from && m.year_to ? " " : ""}{m.year_to ? `по ${m.year_to}` : ""}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// ── Пороги и импорт ────────────────────────────────────────────────────────────────────────

function Thresholds({ canEdit, report, fail, status }: EditProps & { status: MarketStatus }) {
  const [rows, setRows] = useState(status.thresholds);
  return (
    <Panel
      title="Пороги баллов ПП 719"
      subtitle="«Соответствие требованиям года» — наибольший год, чей минимум не выше баллов исполнения. Нижняя граница 2024 и порог 2026 у аналитика не подтверждены."
    >
      <div className="space-y-2">
        {rows.map((row, index) => (
          <div key={index} className="flex items-center gap-2 text-sm text-zinc-400">
            требования
            <input className={`${INPUT} w-24`} type="number" value={row.year} disabled={!canEdit}
              onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, year: Number(e.target.value) } : r)))} />
            года — от
            <input className={`${INPUT} w-24`} type="number" value={row.min_score} disabled={!canEdit}
              onChange={(e) => setRows(rows.map((r, i) => (i === index ? { ...r, min_score: Number(e.target.value) } : r)))} />
            баллов
            <button type="button" className="text-zinc-500 hover:text-red-400 disabled:opacity-40" disabled={!canEdit || rows.length === 1}
              onClick={() => setRows(rows.filter((_, i) => i !== index))}>
              <X size={14} />
            </button>
          </div>
        ))}
        <div className="flex gap-2 pt-2">
          <button type="button" className={BUTTON} disabled={!canEdit}
            onClick={() => setRows([...rows, { year: (rows[rows.length - 1]?.year ?? 2025) + 1, min_score: rows[rows.length - 1]?.min_score ?? 113 }])}>
            <Plus size={13} /> Год
          </button>
          <button type="button" className={PRIMARY} disabled={!canEdit}
            onClick={async () => {
              try {
                await api.put("/meter-market/thresholds", { thresholds: rows });
                report("Пороги сохранены");
              } catch (err) {
                fail(err, "Не удалось сохранить пороги");
              }
            }}>
            Сохранить
          </button>
        </div>
      </div>
    </Panel>
  );
}

function Import({ canEdit, report, fail }: EditProps) {
  const [busy, setBusy] = useState(false);
  return (
    <Panel
      title="Импорт справочника из Excel"
      subtitle="Лист «Лист1» с колонками файла «3 кв 2026.xlsx»: производитель, номер ГРСИ, тип ПУ, исполнение, интерфейс, модификация, локализация. Ручная разметка и ручная локализация не перезаписываются."
    >
      <label className={`${PRIMARY} cursor-pointer ${!canEdit || busy ? "pointer-events-none opacity-40" : ""}`}>
        <Upload size={13} />
        {busy ? "Загружаю…" : "Выбрать файл .xlsx"}
        <input
          type="file"
          accept=".xlsx"
          className="hidden"
          disabled={!canEdit || busy}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (!file) return;
            setBusy(true);
            try {
              const stats = await uploadFile<{ rows: number; types: number; modifications: number; variants: number; remapped: number }>(
                "/meter-market/import",
                file,
              );
              report(
                `Строк ${fmt(stats.rows)}: новых типов ${stats.types}, модификаций ${stats.modifications}, написаний ${stats.variants}; пересведено ${stats.remapped}`,
              );
            } catch (err) {
              fail(err, "Не удалось импортировать файл");
            } finally {
              setBusy(false);
            }
          }}
        />
      </label>
    </Panel>
  );
}
