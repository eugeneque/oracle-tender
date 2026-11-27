import { Plus, Upload, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api, uploadFile } from "../../api/client";
import type {
  MarketGroup,
  MarketManufacturer,
  MarketStatus,
  MarketUnmappedItem,
} from "../../api/types";
import { SegmentedControl } from "../ui/SegmentedControl";
import { DeviceCatalog } from "./DeviceCatalog";
import { NewTypesTable } from "./NewTypesTable";
import { Panel } from "./common";
import { BUTTON, INPUT, NO_RIGHT, PRIMARY } from "./ModificationForm";
import { InterfaceReviewQueue } from "./InterfaceReview";
import { VariantMapper } from "./VariantMapper";
import { fmt } from "./format";

/**
 * «Справочник рынка ПУ» (раздел 79 ARCHITECTURE.md, раздел 6 ТЗ): то, что аналитик раньше
 * вела в Excel. Очередь «Разметить» — написания модификаций, которые Сова не свела сама;
 * очередь новых типов — описания типа счётчиков ЭЭ, впервые встреченные в поверках. Без права
 * правки всё видно, но кнопки неактивны с подсказкой.
 */

type Tab = "unmapped" | "review" | "new" | "types" | "groups" | "thresholds" | "import";


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
          { value: "review", label: `На согласование${status.interface_review ? ` · ${fmt(status.interface_review)}` : ""}` },
          { value: "new", label: `Новые типы${status.new_types ? ` · ${status.new_types}` : ""}` },
          { value: "types", label: `Производители и приборы${status.attribute_review ? ` · ${fmt(status.attribute_review)}` : ""}` },
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
      {tab === "review" && <InterfaceReviewQueue {...props} />}
      {tab === "new" && <NewTypesTable {...props} />}
      {tab === "types" && <DeviceCatalog {...props} />}
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
  const [remapping, setRemapping] = useState(false);
  const remap = async () => {
    setRemapping(true);
    try {
      const result = await api.post<{ remapped: number }>("/meter-market/remap", {});
      report(`Пересчёт разметки: привязано автоматически ${result.remapped}`);
      load();
    } catch (err) {
      fail(err, "Не удалось пересчитать разметку");
    } finally {
      setRemapping(false);
    }
  };
  if (!data) return <div className="py-16 text-center text-sm text-zinc-600">Загружаю очередь…</div>;
  return (
    <Panel
      title={`Неразмеченных написаний: ${fmt(data.variants)} · поверок: ${fmt(data.verifications)}`}
      subtitle="Поверитель записал модификацию так, что Сова не свела её с известной. Привяжите к модификации или заведите новую — то же написание с точностью до регистра, пробелов и латиницы сведётся само."
    >
      <div className="mb-2 flex justify-end">
        <button
          type="button"
          className={BUTTON}
          disabled={!canEdit || remapping}
          title={canEdit ? "Сова ещё раз попробует привязать неразмеченные написания сама" : NO_RIGHT}
          onClick={() => void remap()}
        >
          {remapping ? "Пересчитываю…" : "Пересчитать разметку"}
        </button>
      </div>
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
  return (
    <div className="py-3">
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
      <div className="mt-2">
        <VariantMapper typeId={item.type_id} variantId={item.id} raw={item.raw} canEdit={canEdit} fail={fail} onDone={onDone} />
      </div>
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
