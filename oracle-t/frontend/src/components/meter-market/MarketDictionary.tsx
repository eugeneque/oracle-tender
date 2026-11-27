import { Plus, Search, Upload, X } from "lucide-react";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, uploadFile } from "../../api/client";
import type {
  MarketCatalogJob,
  MarketGroup,
  MarketManufacturer,
  MarketModification,
  MarketStatus,
  MarketType,
  MarketUnmappedItem,
} from "../../api/types";
import { SegmentedControl } from "../ui/SegmentedControl";
import { AttributeCell } from "./AttributeCell";
import { Panel } from "./common";
import { BUTTON, DESIGNS, INPUT, ModificationForm, NO_RIGHT, PRIMARY } from "./ModificationForm";
import { InterfaceCodes, InterfaceReviewQueue } from "./InterfaceReview";
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

// ── Типы и модификации ─────────────────────────────────────────────────────────────────────

const REVIEW_LABEL: Record<MarketType["review_status"], string> = {
  new: "на решении",
  included: "в отчёте",
  excluded: "не в отчёте",
};

function catalogJobText(job: MarketCatalogJob): string {
  if (job.state === "running") return "выполняется…";
  if (job.state === "error") return job.error ?? "Ошибка";
  const result = job.result ?? {};
  const parts = [
    ["типов", result.types],
    ["скриптом", result.script],
    ["сопоставлено", result.mapped],
    ["новых", result.created],
    ["ошибок", result.failed],
  ].filter(([, value]) => typeof value === "number").map(([label, value]) => `${label} ${String(value)}`);
  return `готово${parts.length ? `: ${parts.join(", ")}` : ""}`;
}

function TypesList({ canEdit, report, fail, reviewStatus }: EditProps & { reviewStatus?: "new" }) {
  const [search, setSearch] = useState("");
  const [manufacturerSearch, setManufacturerSearch] = useState("");
  const [manufacturerId, setManufacturerId] = useState<number | null>(null);
  const [types, setTypes] = useState<MarketType[] | null>(null);
  const [manufacturers, setManufacturers] = useState<MarketManufacturer[]>([]);
  const [jobs, setJobs] = useState<MarketCatalogJob[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (reviewStatus) params.set("review_status", reviewStatus);
    if (search) params.set("search", search);
    if (manufacturerId !== null) params.set("manufacturer_id", String(manufacturerId));
    api.get<MarketType[]>(`/meter-market/types?${params}`).then(setTypes).catch((err) => fail(err, "Не удалось загрузить типы"));
  }, [reviewStatus, search, manufacturerId, fail]);
  const loadManufacturers = useCallback(() => {
    api.get<MarketManufacturer[]>("/meter-market/manufacturers").then(setManufacturers).catch(() => undefined);
  }, []);
  const loadJobs = useCallback(() => {
    api.get<MarketCatalogJob[]>("/meter-market/catalog-jobs").then(setJobs).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  useEffect(loadManufacturers, [loadManufacturers]);
  useEffect(loadJobs, [loadJobs]);
  const jobsRunning = jobs.some((job) => job.state === "running");
  const jobsRef = useRef(jobsRunning);
  jobsRef.current = jobsRunning;
  useEffect(() => {
    if (!jobsRunning) return;
    const timer = window.setInterval(() => {
      api.get<MarketCatalogJob[]>("/meter-market/catalog-jobs").then((next) => {
        setJobs(next);
        if (jobsRef.current && !next.some((job) => job.state === "running")) {
          load();
          loadManufacturers();
        }
      }).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [jobsRunning, load, loadManufacturers]);
  const startJob = async (path: string) => {
    try {
      await api.post(path, {});
      loadJobs();
    } catch (err) {
      fail(err, "Не удалось запустить задачу");
    }
  };

  const patch = async (type: MarketType, body: Record<string, unknown>, message: string) => {
    try {
      await api.patch(`/meter-market/types/${type.id}`, body);
      report(message);
      load();
    } catch (err) {
      fail(err, "Не удалось сохранить тип");
    }
  };
  const selectedManufacturer = manufacturers.find((item) => item.id === manufacturerId) ?? null;
  const visibleManufacturers = manufacturers.filter((item) => item.name.toLowerCase().includes(manufacturerSearch.toLowerCase()));

  return (
    <Panel
      title={reviewStatus ? "Новые описания типа счётчиков ЭЭ" : "Производители и приборы"}
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
      <div className={reviewStatus ? "" : "grid gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]"}>
        {!reviewStatus && (
          <aside className="rounded-xl border border-white/[0.06] p-2">
            <label className="relative mb-2 block">
              <Search size={13} className="absolute left-2.5 top-2.5 text-zinc-500" />
              <input className={`${INPUT} w-full pl-7`} placeholder="Производитель" value={manufacturerSearch} onChange={(e) => setManufacturerSearch(e.target.value)} />
            </label>
            <button type="button" className={`mb-1 w-full rounded-lg px-2 py-1.5 text-left text-xs ${manufacturerId === null ? "bg-indigo-500/15 text-indigo-200" : "text-zinc-400 hover:bg-white/5"}`} onClick={() => setManufacturerId(null)}>
              Все производители
            </button>
            <div className="max-h-[32rem] space-y-0.5 overflow-y-auto">
              {visibleManufacturers.map((item) => (
                <button key={item.id} type="button" className={`w-full rounded-lg px-2 py-1.5 text-left text-xs ${manufacturerId === item.id ? "bg-indigo-500/15 text-indigo-200" : "text-zinc-400 hover:bg-white/5"}`} onClick={() => setManufacturerId(item.id)}>
                  <span className="block">{item.name}</span>
                  <span className="text-[10px] text-zinc-600">
                    типов {item.types} · приборов {item.modifications}
                    {item.attribute_review > 0 && <span className="text-amber-400"> · проверить {item.attribute_review}</span>}
                  </span>
                </button>
              ))}
            </div>
          </aside>
        )}
        <div className="min-w-0">
          {selectedManufacturer && (
            <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.06] p-2.5">
              <span className="mr-auto text-xs text-zinc-400">{selectedManufacturer.name}: типов {selectedManufacturer.types}, приборов {selectedManufacturer.modifications}</span>
              <button type="button" className={BUTTON} disabled={!canEdit || jobsRunning} onClick={() => void startJob(`/meter-market/manufacturers/${selectedManufacturer.id}/sync`)}>Синхронизировать с ФГИС</button>
              <button type="button" className={PRIMARY} disabled={!canEdit || jobsRunning} onClick={() => void startJob(`/meter-market/manufacturers/${selectedManufacturer.id}/markup`)}>Разобрать характеристики</button>
            </div>
          )}
          {jobs.slice(-3).map((job) => (
            <div key={job.key} className={`mb-2 rounded-lg px-3 py-2 text-xs ${job.state === "error" ? "bg-red-500/10 text-red-300" : job.state === "done" ? "bg-emerald-500/10 text-emerald-300" : "bg-indigo-500/10 text-indigo-200"}`}>
              {job.label}: {catalogJobText(job)}
            </div>
          ))}
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
                    {type.interface_review > 0 && <span className="text-amber-400"> · интерфейс на согласовании {type.interface_review}</span>}
                    {type.attribute_review > 0 && <span className="text-amber-400"> · характеристик проверить {type.attribute_review}</span>}
                    {type.attribute_unresolved > 0 && <span className="text-zinc-500"> · не определено {type.attribute_unresolved}</span>}
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
              {openId === type.id && (
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                  {(type.description_type_url || type.description_type_mirror_url) && (
                    <a className="text-indigo-300 hover:underline" href={type.description_type_mirror_url || type.description_type_url || undefined} target="_blank" rel="noreferrer">Описание типа (PDF)</a>
                  )}
                  <button type="button" className={BUTTON} disabled={!canEdit || jobsRunning} onClick={() => void startJob(`/meter-market/types/${type.id}/markup?refresh=true`)}>
                    Обновить описание и разобрать
                  </button>
                  <span className="text-zinc-600">{type.has_description ? "текст описания загружен" : "описание будет загружено из ФГИС"}</span>
                </div>
              )}
              {openId === type.id && <CodeNotes type={type} canEdit={canEdit} report={report} fail={fail} onSaved={load} />}
              {openId === type.id && <InterfaceCodes type={type} canEdit={canEdit} report={report} fail={fail} onSaved={load} />}
              {openId === type.id && <Modifications typeId={type.id} canEdit={canEdit} report={report} fail={fail} />}
            </div>
          ))}
        </div>
      )}
        </div>
      </div>
    </Panel>
  );
}

/** «Расшифровка кодов» (13.11.2026, раздел 108 ARCHITECTURE.md): то, чего нет в «Описании типа», но
 * есть в руководстве по эксплуатации («G3 — NB-IoT»). ИИ ставит её выше описания и расшифровывает по ней
 * каждое написание — и новые исполнения из «Аршина», которых нет в таблице. */
function CodeNotes({ type, canEdit, report, fail, onSaved }: EditProps & { type: MarketType; onSaved: () => void }) {
  const [value, setValue] = useState(type.code_notes ?? "");
  const [busy, setBusy] = useState(false);
  const dirty = value.trim() !== (type.code_notes ?? "").trim();
  const save = async () => {
    try {
      await api.patch(`/meter-market/types/${type.id}`, { code_notes: value });
      report(`Расшифровка кодов ${type.mit_number} сохранена`);
      onSaved();
    } catch (err) {
      fail(err, "Не удалось сохранить расшифровку кодов");
    }
  };
  const decode = async () => {
    setBusy(true);
    try {
      if (dirty) await api.patch(`/meter-market/types/${type.id}`, { code_notes: value });
      await api.post(`/meter-market/types/${type.id}/markup`, {});
      report(`ИИ расшифровывает написания ${type.mit_number} заново — результат появится через несколько минут`);
    } catch (err) {
      fail(err, "Не удалось запустить расшифровку");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 rounded-xl border border-white/[0.06] px-3 py-2">
      <label className="text-xs text-zinc-500" htmlFor={`code-notes-${type.id}`}>
        Расшифровка кодов обозначения (по руководству по эксплуатации) — для ИИ главнее описания типа
      </label>
      <textarea
        id={`code-notes-${type.id}`}
        className={`${INPUT} mt-1 min-h-[3.5rem] w-full`}
        placeholder="G3 — NB-IoT; G4E — GSM и RS; AS — только активная энергия"
        value={value}
        disabled={!canEdit}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <button type="button" className={BUTTON} disabled={!canEdit || !dirty} title={canEdit ? undefined : NO_RIGHT} onClick={() => void save()}>
          Сохранить
        </button>
        <button type="button" className={PRIMARY} disabled={!canEdit || busy} title={canEdit ? "ИИ заново расшифрует все написания типа, кроме размеченных вручную" : NO_RIGHT}
          onClick={() => void decode()}>
          Расшифровать заново
        </button>
        <span className="text-xs text-zinc-600">Ручная разметка не меняется</span>
      </div>
    </div>
  );
}

const DESIGN_LABEL = Object.fromEntries(DESIGNS.map((d) => [d.value, d.label]));

function Modifications({ typeId, canEdit, report, fail }: EditProps & { typeId: number }) {
  const [mods, setMods] = useState<MarketModification[] | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [attributeFilter, setAttributeFilter] = useState<"all" | "pending" | "unresolved">("all");
  const [variants, setVariants] = useState<Record<number, { id: number; raw: string; count: number; mapped_by: string | null }[]>>({});
  const load = useCallback(() => {
    api.get<MarketModification[]>(`/meter-market/types/${typeId}/modifications`).then(setMods).catch((err) => fail(err, "Не удалось загрузить модификации"));
  }, [typeId, fail]);
  useEffect(load, [load]);
  const done = (message: string) => {
    report(message);
    load();
  };
  const showVariants = (id: number) => {
    if (variants[id]) {
      setVariants(({ [id]: _, ...rest }) => rest);
      return;
    }
    api.get<(typeof variants)[number]>(`/meter-market/modifications/${id}/variants`).then((rows) => setVariants((prev) => ({ ...prev, [id]: rows })));
  };
  const markAll = async (value: boolean | null) => {
    try {
      const result = await api.post<{ changed: number }>(`/meter-market/types/${typeId}/smart`, { smart_890: value });
      report(
        value === null
          ? `Ручные отметки ПП 890 сняты (модификаций: ${result.changed})`
          : `Отмечено «${value ? "соответствует" : "не соответствует"} ПП 890»: модификаций ${result.changed}`,
      );
      load();
    } catch (err) {
      fail(err, "Не удалось отметить модификации");
    }
  };
  if (!mods) return <p className="py-3 text-xs text-zinc-600">Загружаю модификации…</p>;
  const visibleMods = attributeFilter === "all" ? mods : mods.filter((m) => Object.values(m.attributes).some((a) => a.status === attributeFilter));
  return (
    <div className="mt-2 overflow-x-auto rounded-xl border border-white/[0.06]">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs text-zinc-500">
        <span>ПП 890 для всех модификаций типа:</span>
        <button type="button" className={BUTTON} disabled={!canEdit} title={canEdit ? undefined : NO_RIGHT} onClick={() => void markAll(true)}>
          Интеллектуальные
        </button>
        <button type="button" className={BUTTON} disabled={!canEdit} title={canEdit ? undefined : NO_RIGHT} onClick={() => void markAll(false)}>
          Простейшие
        </button>
        <button type="button" className={BUTTON} disabled={!canEdit} title={canEdit ? "Снять ручные отметки — ИИ оценит заново" : NO_RIGHT} onClick={() => void markAll(null)}>
          Сбросить
        </button>
        <select className={`${INPUT} ml-auto py-1`} value={attributeFilter} onChange={(e) => setAttributeFilter(e.target.value as typeof attributeFilter)} aria-label="Фильтр характеристик">
          <option value="all" className="bg-zinc-900">Все характеристики</option>
          <option value="pending" className="bg-zinc-900">Требует проверки</option>
          <option value="unresolved" className="bg-zinc-900">Не определено</option>
        </select>
      </div>
      <table className="w-full min-w-[860px] text-xs">
        <thead>
          <tr className="text-zinc-500">
            <th className="px-3 py-2 text-left font-medium">Модификация</th>
            <th className="px-3 py-2 text-left font-medium">Тип</th>
            <th className="px-3 py-2 text-left font-medium">Исполнение</th>
            <th className="px-3 py-2 text-left font-medium">Интерфейс</th>
            <th className="px-3 py-2 text-left font-medium">МПИ</th>
            <th className="px-3 py-2 text-left font-medium">РПП</th>
            <th className="px-3 py-2 text-left font-medium">ПП 890</th>
            <th className="px-3 py-2 text-right font-medium">Написаний</th>
            <th className="px-3 py-2 text-right font-medium">Поверок</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {visibleMods.map((m) => (
            <Fragment key={m.id}>
              <tr className="border-t border-white/[0.05] text-zinc-300">
                <td className="px-3 py-1.5 text-zinc-100">{m.name}</td>
                <td className="px-3 py-1.5"><AttributeCell modificationId={m.id} field="phase" value={m.phase ?? "нет данных"} metadata={m.attributes.phase} canEdit={canEdit} onEdit={() => setEditing(m.id)} onDone={done} onError={fail} /></td>
                <td className="px-3 py-1.5"><AttributeCell modificationId={m.id} field="design" value={DESIGN_LABEL[m.design] ?? m.design} metadata={m.attributes.design} canEdit={canEdit} onEdit={() => setEditing(m.id)} onDone={done} onError={fail} /></td>
                <td className="px-3 py-1.5"><AttributeCell modificationId={m.id} field="interface" value={m.interface ?? "нет данных"} metadata={m.attributes.interface} canEdit={canEdit} onEdit={() => setEditing(m.id)} onDone={done} onError={fail} /></td>
                <td className="px-3 py-1.5"><AttributeCell modificationId={m.id} field="mpi_years" value={m.mpi_years === null ? "нет данных" : `${m.mpi_years} лет`} metadata={m.attributes.mpi_years} canEdit={canEdit} onEdit={() => setEditing(m.id)} onDone={done} onError={fail} /></td>
                <td className="px-3 py-1.5">
                  {m.in_rpp ? `${m.score ?? "?"} б.${m.rpp_included_at ? ` с ${m.rpp_included_at.split("-").reverse().join(".")}` : ""}` : "нет"}
                </td>
                <td className="px-3 py-1.5" title={m.smart_890_note ?? undefined}>
                  <AttributeCell modificationId={m.id} field="smart_890" value={m.smart_890 === null ? "нет данных" : m.smart_890 ? "да" : "нет"} metadata={m.attributes.smart_890} canEdit={canEdit} onEdit={() => setEditing(m.id)} onDone={done} onError={fail} />
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
                  <td colSpan={10} className="px-6 pb-2 text-zinc-500">
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
                  <td colSpan={10} className="px-3 pb-3">
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
