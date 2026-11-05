import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Check,
  Info,
  ChevronDown,
  ChevronRight,
  FileText,
  History,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Undo2,
  Upload,
} from "lucide-react";

import { ApiError, api, getToken, uploadFile } from "../../api/client";
import type {
  IntegrationDocument,
  IntegrationDraft,
  IntegrationField,
  IntegrationFieldStatus,
  IntegrationFunction,
  IntegrationVerdict,
  SoftwareIntegration,
  SoftwareIntegrationsOverview,
} from "../../api/types";
import { formatDate } from "../../utils/format";

// Интеграции МИРТЕК в ПО верхнего уровня — данные специалиста (05.11.2026).
//
// Старший тендерный специалист ведёт таблицу интеграций и официальные письма разработчиков
// ПО и ручается за них. Правки здесь — черновик: в анализ тендеров и в каталог они попадают
// только по общей кнопке «Сохранить» (это и есть «дообучение» — опубликованное подмешивается
// в сопоставление требований сразу, без переобучения). Поля, заполненные системой (по
// публичному списку или по скану), помечены «авто» и до проверки специалистом не
// публикуются.

const VERDICT_LABEL: Record<IntegrationVerdict, string> = {
  yes: "Да",
  partial: "Частично",
  no: "Нет",
  unknown: "Не выяснено",
};

const VERDICT_STYLE: Record<IntegrationVerdict, string> = {
  yes: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  partial: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  no: "border-white/10 bg-white/[0.04] text-zinc-400",
  unknown: "border-white/10 bg-white/[0.04] text-zinc-500",
};

const STATUS_LABEL: Record<IntegrationFieldStatus, string> = {
  auto: "авто · не проверено",
  confirmed: "подтверждено",
  edited: "исправлено",
  manual: "внесено вручную",
};

const SOURCE_LABEL: Record<string, string> = {
  table_v6: "таблица интеграций V6",
  public_list: "публичный список",
  scan: "скан",
  manual: "вручную",
};

const EMPTY_FIELD: IntegrationField = {
  value: "",
  verdict: null,
  status: "auto",
  source: "manual",
  updated_at: null,
  updated_by: null,
};

// crypto.randomUUID есть только в защищённом контексте (https/localhost); установка по http
// на сервере его лишена, поэтому запасной вариант.
function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// Подсказка по наведению. Рисуется в body с fixed-позицией: внутри таблицы с прокруткой
// обычный absolute-блок обрезался бы краем контейнера.
function HoverHint({ text, children }: { text: string; children: React.ReactNode }) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  if (!text) return <>{children}</>;
  const width = 340;
  const left = rect ? Math.min(Math.max(8, rect.left), window.innerWidth - width - 8) : 0;
  const below = rect ? rect.bottom + 6 : 0;
  const flip = rect ? below + 200 > window.innerHeight : false;
  return (
    <span
      className="inline-flex"
      onMouseEnter={(e) => setRect(e.currentTarget.getBoundingClientRect())}
      onMouseLeave={() => setRect(null)}
    >
      {children}
      {rect &&
        createPortal(
          <div
            style={{
              position: "fixed",
              left,
              width,
              ...(flip ? { bottom: window.innerHeight - rect.top + 6 } : { top: below }),
              zIndex: 1000,
            }}
            className="pointer-events-none whitespace-pre-wrap rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-[12px] leading-snug text-zinc-200 shadow-xl"
          >
            {text}
          </div>,
          document.body
        )}
    </span>
  );
}

function VerdictBadge({ verdict, text }: { verdict: IntegrationVerdict | null; text?: string }) {
  if (!verdict && !text) return <span className="text-zinc-600">—</span>;
  const style = VERDICT_STYLE[verdict ?? "unknown"];
  const label = verdict ? VERDICT_LABEL[verdict] : "";
  // Если вердикт передаёт весь текст («Да»), уточнения нет и значка нет.
  const hasNote = Boolean(text) && text!.toLowerCase().replace(/\.$/, "") !== label.toLowerCase();
  return (
    <span className="inline-flex items-center gap-1.5">
      {verdict && <span className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[11px] ${style}`}>{label}</span>}
      {hasNote && (
        <HoverHint text={text!}>
          <Info size={13} className="cursor-help text-zinc-500 hover:text-indigo-300" />
        </HoverHint>
      )}
    </span>
  );
}

export function SoftwareIntegrationsSection({ canDelete }: { canDelete: boolean }) {
  const [data, setData] = useState<SoftwareIntegrationsOverview | null>(null);
  const [edits, setEdits] = useState<Record<string, IntegrationDraft>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [comment, setComment] = useState("");
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");

  const load = useCallback(async () => {
    setData(await api.get<SoftwareIntegrationsOverview>("/software-integrations"));
  }, []);

  useEffect(() => {
    load().catch((err) => setError(errorText(err, "Не удалось загрузить таблицу интеграций")));
  }, [load]);

  const items = data?.items ?? [];
  const fieldMeta = data?.fields ?? [];

  const editedIds = Object.keys(edits);
  const dirtyCount = useMemo(
    () => items.filter((item) => item.dirty || edits[item.id]).length,
    [items, edits]
  );
  const pendingReview = items.reduce((sum, item) => sum + item.pending_review, 0);

  const draftOf = (item: SoftwareIntegration): IntegrationDraft => edits[item.id] ?? item.draft;
  const setDraft = (item: SoftwareIntegration, draft: IntegrationDraft) =>
    setEdits((prev) => ({ ...prev, [item.id]: draft }));

  const persistEdits = async (ids: string[]) => {
    for (const id of ids) {
      const draft = edits[id];
      if (!draft) continue;
      await api.put<SoftwareIntegration>(`/software-integrations/${id}`, { draft });
    }
  };

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(errorText(err, "Операция не удалась"));
    } finally {
      setBusy(null);
    }
  };

  const handlePublish = () =>
    run("publish", async () => {
      await persistEdits(editedIds);
      const outcome = await api.post<{ published: boolean; message: string }>(
        "/software-integrations/publish",
        { comment: comment.trim() || null }
      );
      setEdits({});
      setComment("");
      setNotice(outcome.message);
      await load();
    });

  const handleSaveDraft = (item: SoftwareIntegration) =>
    run(`draft:${item.id}`, async () => {
      await persistEdits([item.id]);
      setEdits((prev) => {
        const { [item.id]: _removed, ...rest } = prev;
        return rest;
      });
      setNotice(`Черновик «${item.name}» сохранён. В анализ тендеров он попадёт после кнопки «Сохранить».`);
      await load();
    });

  const handleReset = (item: SoftwareIntegration) =>
    run(`reset:${item.id}`, async () => {
      if (!window.confirm(`Вернуть «${item.name}» к последней сохранённой версии? Несохранённые правки пропадут.`)) return;
      await api.post(`/software-integrations/${item.id}/reset`);
      setEdits((prev) => {
        const { [item.id]: _removed, ...rest } = prev;
        return rest;
      });
      await load();
    });

  const handleDelete = (item: SoftwareIntegration) =>
    run(`delete:${item.id}`, async () => {
      if (!window.confirm(`Удалить «${item.name}» вместе со сканами? Данные перестанут учитываться сразу.`)) return;
      await api.delete(`/software-integrations/${item.id}`);
      setExpanded(null);
      await load();
    });

  const handleAutofill = () =>
    run("autofill", async () => {
      await persistEdits(editedIds);
      setEdits({});
      const outcome = await api.post<{ message: string }>("/software-integrations/autofill");
      setNotice(outcome.message);
      await load();
    });

  const handleAdd = () =>
    run("add", async () => {
      const created = await api.post<SoftwareIntegration>("/software-integrations", { name: newName });
      setNewName("");
      setAdding(false);
      await load();
      setExpanded(created.id);
    });

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.08] px-5 py-3">
        <div className="max-w-2xl">
          <h2 className="text-sm font-semibold text-zinc-100">
            Интеграции МИРТЕК в ПО верхнего уровня
            <span className="ml-1.5 text-zinc-500">· данные специалиста</span>
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Правки — черновик: в анализ тендеров они попадают после кнопки «Сохранить».
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setShowHistory((v) => !v)}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5"
          >
            <History size={13} /> История
          </button>
          <button
            onClick={handleAutofill}
            disabled={busy !== null}
            title="Предложить значения по публичным спискам поддерживаемого оборудования (в пустые поля, как «авто»)"
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          >
            {busy === "autofill" ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            Автозаполнить по спискам
          </button>
          <button
            onClick={() => setAdding((v) => !v)}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5"
          >
            <Plus size={13} /> Добавить ПО
          </button>
        </div>
      </div>

      {adding && (
        <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-5 py-3">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Название ПО, например «АСКУЭ Сфера»"
            className="w-72 rounded-lg border border-white/10 bg-transparent px-3 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500"
          />
          <button
            onClick={handleAdd}
            disabled={!newName.trim() || busy !== null}
            className="rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            Добавить
          </button>
        </div>
      )}

      {(notice || error) && (
        <div className="space-y-2 px-5 pt-3">
          {error && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>
          )}
          {notice && (
            <div className="flex items-start justify-between gap-3 rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-300">
              <span>{notice}</span>
              <button onClick={() => setNotice(null)} className="shrink-0 text-indigo-400 hover:text-indigo-200">
                ×
              </button>
            </div>
          )}
        </div>
      )}

      {showHistory && data && (
        <div className="border-b border-white/[0.06] px-5 py-3">
          <div className="mb-1.5 text-xs font-medium text-zinc-400">Журнал сохранений</div>
          {data.publications.length === 0 ? (
            <div className="text-xs text-zinc-500">Сохранений ещё не было</div>
          ) : (
            <ul className="space-y-1.5">
              {data.publications.map((p) => (
                <li key={p.id} className="text-[12px] text-zinc-400">
                  <span className="text-zinc-300">{formatDate(p.published_at)}</span>
                  {p.published_by && <span> · {p.published_by}</span>}
                  {p.comment && <span className="text-zinc-300"> · {p.comment}</span>}
                  <span className="text-zinc-500">
                    {" "}
                    — {p.changes.map((c) => c.name).slice(0, 6).join(", ")}
                    {p.changes.length > 6 ? ` и ещё ${p.changes.length - 6}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="px-5 py-3">
        {data === null && !error && (
          <div className="flex items-center gap-2 py-6 text-sm text-zinc-500">
            <Loader2 size={14} className="animate-spin" /> Загрузка…
          </div>
        )}
        {data && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-zinc-500">
                  <th className="w-6 py-1.5" />
                  <th className="py-1.5 pr-4 font-medium">ПО</th>
                  <th className="py-1.5 pr-4 font-medium">Есть ли ПУ МИРТЕК?</th>
                  <th className="py-1.5 pr-4 font-medium">Протокол МИРТЕК</th>
                  <th className="py-1.5 pr-4 font-medium">СПОДЭС</th>
                  <th className="py-1.5 font-medium">Состояние</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const draft = draftOf(item);
                  const isOpen = expanded === item.id;
                  const unsaved = Boolean(edits[item.id]);
                  const pending = unsaved
                    ? Object.values(draft.fields).filter((f) => f.status === "auto" && (f.value || f.verdict)).length +
                      draft.functions.filter((f) => !f.verified).length
                    : item.pending_review;
                  const f = (key: string) => draft.fields[key] ?? EMPTY_FIELD;
                  return (
                    <ItemRows
                      key={item.id}
                      isOpen={isOpen}
                      onToggle={() => setExpanded(isOpen ? null : item.id)}
                      summary={
                        <>
                          <td className="py-2 pr-4 font-medium text-zinc-100">
                            {item.name}
                            {item.platform_name && (
                              <span className="ml-1.5 text-[11px] font-normal text-zinc-500">
                                публичный список: {item.platform_name}
                              </span>
                            )}
                          </td>
                          <td className="py-2 pr-4">
                            <VerdictBadge verdict={f("has_mirtek").verdict} text={f("has_mirtek").value} />
                          </td>
                          <td className="py-2 pr-4">
                            <VerdictBadge verdict={f("protocol_mirtek").verdict} text={f("protocol_mirtek").value} />
                          </td>
                          <td className="py-2 pr-4">
                            <VerdictBadge verdict={f("protocol_spodes").verdict} text={f("protocol_spodes").value} />
                          </td>
                          <td className="py-2 text-[11px]">
                            <div className="flex flex-wrap gap-1">
                              {(unsaved || item.dirty) && (
                                <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-1.5 py-0.5 text-indigo-300">
                                  не сохранено
                                </span>
                              )}
                              {pending > 0 && (
                                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-300">
                                  проверить: {pending}
                                </span>
                              )}
                              {!item.dirty && !unsaved && pending === 0 && item.published_at && (
                                <span className="text-zinc-500">сохранено {formatDate(item.published_at)}</span>
                              )}
                            </div>
                          </td>
                        </>
                      }
                      detail={
                        isOpen && (
                          <IntegrationEditor
                            item={item}
                            draft={draft}
                            fieldMeta={fieldMeta}
                            unsaved={unsaved}
                            busy={busy}
                            canDelete={canDelete}
                            onChange={(next) => setDraft(item, next)}
                            onSaveDraft={() => handleSaveDraft(item)}
                            onReset={() => handleReset(item)}
                            onDelete={() => handleDelete(item)}
                            onReload={load}
                            onError={setError}
                            onNotice={setNotice}
                          />
                        )
                      }
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 rounded-b-xl border-t border-white/[0.08] bg-zinc-950/90 px-5 py-3 backdrop-blur">
        <div className="text-xs text-zinc-500">
          {dirtyCount > 0 ? (
            <span className="text-indigo-300">Не сохранено записей: {dirtyCount}</span>
          ) : (
            "Все изменения сохранены"
          )}
          {pendingReview > 0 && (
            <span className="ml-2 text-amber-300">
              · не проверено системных значений: {pendingReview} (в анализ не попадут)
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Комментарий к сохранению (необязательно)"
            className="w-64 rounded-lg border border-white/10 bg-transparent px-3 py-1.5 text-xs text-zinc-100 outline-none focus:border-indigo-500"
          />
          <button
            onClick={handlePublish}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            {busy === "publish" ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}

function ItemRows({
  isOpen,
  onToggle,
  summary,
  detail,
}: {
  isOpen: boolean;
  onToggle: () => void;
  summary: React.ReactNode;
  detail: React.ReactNode;
}) {
  return (
    <>
      <tr onClick={onToggle} className="cursor-pointer border-t border-white/[0.06] hover:bg-white/[0.03]">
        <td className="py-2 text-zinc-500">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
        {summary}
      </tr>
      {isOpen && (
        <tr className="border-t border-white/[0.04] bg-white/[0.02]">
          <td colSpan={6} className="px-1 py-3">
            {detail}
          </td>
        </tr>
      )}
    </>
  );
}

function IntegrationEditor({
  item,
  draft,
  fieldMeta,
  unsaved,
  busy,
  canDelete,
  onChange,
  onSaveDraft,
  onReset,
  onDelete,
  onReload,
  onError,
  onNotice,
}: {
  item: SoftwareIntegration;
  draft: IntegrationDraft;
  fieldMeta: { key: string; label: string; with_verdict: boolean }[];
  unsaved: boolean;
  busy: string | null;
  canDelete: boolean;
  onChange: (draft: IntegrationDraft) => void;
  onSaveDraft: () => void;
  onReset: () => void;
  onDelete: () => void;
  onReload: () => Promise<void>;
  onError: (text: string | null) => void;
  onNotice: (text: string | null) => void;
}) {
  const setField = (key: string, patch: Partial<IntegrationField>) => {
    const old = draft.fields[key] ?? EMPTY_FIELD;
    const next = { ...old, ...patch };
    const changed = patch.value !== undefined || patch.verdict !== undefined;
    if (changed) next.status = old.value || old.verdict ? "edited" : "manual";
    onChange({ ...draft, fields: { ...draft.fields, [key]: next } });
  };

  const confirmField = (key: string) => {
    const old = draft.fields[key] ?? EMPTY_FIELD;
    onChange({ ...draft, fields: { ...draft.fields, [key]: { ...old, status: "confirmed" } } });
  };

  const setFunction = (index: number, patch: Partial<IntegrationFunction>) =>
    onChange({
      ...draft,
      functions: draft.functions.map((fn, i) => (i === index ? { ...fn, ...patch } : fn)),
    });

  const addFunction = () =>
    onChange({
      ...draft,
      functions: [
        ...draft.functions,
        {
          id: newId(),
          model: draft.models[0] ?? "",
          protocol: "СПОДЭС",
          name: "",
          status: "yes",
          comment: "",
          verified: true,
          source: "manual",
          document_id: null,
        },
      ],
    });

  const [modelInput, setModelInput] = useState("");
  const [openNotes, setOpenNotes] = useState<Set<string>>(new Set());
  const toggleNote = (key: string) =>
    setOpenNotes((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  const unverifiedFunctions = draft.functions.filter((fn) => !fn.verified).length;

  return (
    <div className="space-y-4 px-3">
      <DocumentsPanel integrationId={item.id} onReload={onReload} onError={onError} onNotice={onNotice} />

      <div className="grid gap-3 md:grid-cols-2">
        {fieldMeta.map((meta) => {
          const field = draft.fields[meta.key] ?? EMPTY_FIELD;
          const filled = Boolean(field.value || field.verdict);
          const validated = field.status !== "auto";
          // У полей с вердиктом уточнение — во всплывающей подсказке над значком; по клику
          // оно открывается для правки. Остальные поля — обычный текст.
          const noteOpen = openNotes.has(meta.key);
          return (
            <div key={meta.key} className={meta.key === "software_version" || meta.key === "note" ? "md:col-span-2" : ""}>
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <label className="text-[12px] font-medium text-zinc-300">{meta.label}</label>
                {filled &&
                  (validated ? (
                    <span
                      title={`${STATUS_LABEL[field.status]}${field.source !== "manual" ? ` · ${SOURCE_LABEL[field.source] ?? field.source}` : ""}`}
                      className="text-emerald-400"
                    >
                      <Check size={14} />
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5 text-[11px] text-amber-300">
                      {STATUS_LABEL.auto}
                      <button
                        onClick={() => confirmField(meta.key)}
                        className="flex items-center gap-0.5 rounded-md border border-emerald-500/30 px-1.5 py-0.5 text-emerald-300 hover:bg-emerald-500/10"
                      >
                        <Check size={11} /> Подтвердить
                      </button>
                    </span>
                  ))}
              </div>
              {meta.with_verdict ? (
                <div>
                  <div className="flex items-center gap-2">
                    <select
                      value={field.verdict ?? ""}
                      onChange={(e) => setField(meta.key, { verdict: (e.target.value || null) as IntegrationVerdict | null })}
                      className="h-9 rounded-lg border border-white/10 bg-zinc-900 px-2 text-xs text-zinc-200"
                    >
                      <option value="">—</option>
                      {(Object.keys(VERDICT_LABEL) as IntegrationVerdict[]).map((v) => (
                        <option key={v} value={v}>
                          {VERDICT_LABEL[v]}
                        </option>
                      ))}
                    </select>
                    <HoverHint text={noteOpen ? "" : field.value}>
                      <button
                        onClick={() => toggleNote(meta.key)}
                        className={`flex h-9 items-center gap-1 rounded-lg border px-2 text-xs ${
                          field.value
                            ? "border-indigo-500/40 text-indigo-300"
                            : "border-white/10 text-zinc-500 hover:text-zinc-300"
                        }`}
                      >
                        <Info size={13} />
                        {field.value ? "Уточнение" : "Добавить уточнение"}
                      </button>
                    </HoverHint>
                  </div>
                  {noteOpen && (
                    <textarea
                      autoFocus
                      value={field.value}
                      onChange={(e) => setField(meta.key, { value: e.target.value })}
                      rows={3}
                      className="mt-2 w-full resize-y rounded-lg border border-white/10 bg-transparent px-3 py-1.5 text-[13px] text-zinc-100 outline-none focus:border-indigo-500"
                    />
                  )}
                </div>
              ) : (
                <textarea
                  value={field.value}
                  onChange={(e) => setField(meta.key, { value: e.target.value })}
                  rows={meta.key === "software_version" ? 3 : 2}
                  className="min-h-9 w-full resize-y rounded-lg border border-white/10 bg-transparent px-3 py-1.5 text-[13px] text-zinc-100 outline-none focus:border-indigo-500"
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Модели */}
      <div>
        <div className="mb-1 text-[12px] font-medium text-zinc-300">
          Модели МИРТЕК, интегрированные в ПО
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {draft.models.map((model) => (
            <span key={model} className="flex items-center gap-1 rounded-full border border-white/10 px-2 py-0.5 text-[12px] text-zinc-200">
              {model}
              <button
                onClick={() => onChange({ ...draft, models: draft.models.filter((m) => m !== model) })}
                className="text-zinc-500 hover:text-red-400"
                title="Убрать"
              >
                ×
              </button>
            </span>
          ))}
          <input
            value={modelInput}
            onChange={(e) => setModelInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && modelInput.trim()) {
                e.preventDefault();
                if (!draft.models.includes(modelInput.trim()))
                  onChange({ ...draft, models: [...draft.models, modelInput.trim()] });
                setModelInput("");
              }
            }}
            placeholder="МИРТЕК-32-РУ + Enter"
            className="w-44 rounded-lg border border-white/10 bg-transparent px-2 py-1 text-xs text-zinc-100 outline-none focus:border-indigo-500"
          />
        </div>
      </div>

      {/* Функции */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <div className="text-[12px] font-medium text-zinc-300">
            Интегрированные функции
            {unverifiedFunctions > 0 && (
              <span className="ml-1.5 font-normal text-amber-300">не проверено: {unverifiedFunctions}</span>
            )}
          </div>
          <button
            onClick={addFunction}
            className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-xs text-zinc-300 hover:bg-white/5"
          >
            <Plus size={12} /> Функция
          </button>
        </div>
        {draft.functions.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-xs">
              <thead>
                <tr className="text-left text-[11px] text-zinc-500">
                  <th className="py-1 pr-2 font-medium">Модель</th>
                  <th className="py-1 pr-2 font-medium">Протокол</th>
                  <th className="py-1 pr-2 font-medium">Функция</th>
                  <th className="py-1 pr-2 font-medium">Соответствие</th>
                  <th className="py-1 pr-2 font-medium">Комментарий</th>
                  <th className="py-1 font-medium" />
                </tr>
              </thead>
              <tbody>
                {draft.functions.map((fn, index) => (
                  <tr key={fn.id} className="border-t border-white/[0.05] align-top">
                    <td className="py-1 pr-2">
                      <input
                        value={fn.model}
                        onChange={(e) => setFunction(index, { model: e.target.value })}
                        className="w-32 rounded border border-white/10 bg-transparent px-1.5 py-1 text-zinc-200"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <select
                        value={fn.protocol}
                        onChange={(e) => setFunction(index, { protocol: e.target.value })}
                        className="rounded border border-white/10 bg-zinc-900 px-1 py-1 text-zinc-200"
                      >
                        <option value="">—</option>
                        <option value="СПОДЭС">СПОДЭС</option>
                        <option value="МИРТЕК">МИРТЕК</option>
                      </select>
                    </td>
                    <td className="py-1 pr-2">
                      <input
                        value={fn.name}
                        onChange={(e) => setFunction(index, { name: e.target.value })}
                        className="w-64 rounded border border-white/10 bg-transparent px-1.5 py-1 text-zinc-200"
                      />
                    </td>
                    <td className="py-1 pr-2">
                      <select
                        value={fn.status}
                        onChange={(e) => setFunction(index, { status: e.target.value as IntegrationFunction["status"] })}
                        className="rounded border border-white/10 bg-zinc-900 px-1 py-1 text-zinc-200"
                      >
                        <option value="yes">+ поддержано</option>
                        <option value="partial">частично</option>
                        <option value="no">− нет</option>
                      </select>
                    </td>
                    <td className="py-1 pr-2">
                      <input
                        value={fn.comment}
                        onChange={(e) => setFunction(index, { comment: e.target.value })}
                        className="w-48 rounded border border-white/10 bg-transparent px-1.5 py-1 text-zinc-200"
                      />
                    </td>
                    <td className="whitespace-nowrap py-1">
                      {fn.verified ? (
                        <span className="text-[11px] text-zinc-500">проверено</span>
                      ) : (
                        <button
                          onClick={() => setFunction(index, { verified: true })}
                          title={fn.source === "scan" ? "Предложено по скану" : "Не проверено"}
                          className="rounded-md border border-emerald-500/30 px-1.5 py-0.5 text-[11px] text-emerald-300 hover:bg-emerald-500/10"
                        >
                          Подтвердить
                        </button>
                      )}
                      <button
                        onClick={() => onChange({ ...draft, functions: draft.functions.filter((_, i) => i !== index) })}
                        className="ml-1.5 text-zinc-500 hover:text-red-400"
                        title="Удалить строку"
                      >
                        <Trash2 size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-3">
        <div />
        <div className="flex items-center gap-2">
          {canDelete && (
            <button
              onClick={onDelete}
              disabled={busy !== null}
              className="flex items-center gap-1 rounded-lg border border-red-500/30 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10 disabled:opacity-50"
            >
              <Trash2 size={12} /> Удалить ПО
            </button>
          )}
          <button
            onClick={onReset}
            disabled={busy !== null}
            title="Вернуть последнюю сохранённую версию"
            className="flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          >
            <Undo2 size={12} /> Отменить правки
          </button>
          <button
            onClick={onSaveDraft}
            disabled={busy !== null || !unsaved}
            className="flex items-center gap-1 rounded-lg border border-indigo-500/40 px-2.5 py-1.5 text-xs text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-40"
          >
            {busy === `draft:${item.id}` ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            Сохранить в черновик
          </button>
        </div>
      </div>
    </div>
  );
}

function DocumentsPanel({
  integrationId,
  onReload,
  onError,
  onNotice,
}: {
  integrationId: string;
  onReload: () => Promise<void>;
  onError: (text: string | null) => void;
  onNotice: (text: string | null) => void;
}) {
  const [documents, setDocuments] = useState<IntegrationDocument[]>([]);
  const [uploading, setUploading] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setDocuments(await api.get<IntegrationDocument[]>(`/software-integrations/${integrationId}/documents`));
  }, [integrationId]);

  useEffect(() => {
    load().catch(() => setDocuments([]));
  }, [load]);

  // Разбор идёт в фоне (OCR + модель): пока есть документ в работе, опрашиваем список; по
  // завершении перечитываем таблицу — в черновике появились предложения.
  const pending = documents.some((d) => d.analysis_status === "pending");
  const wasPending = useRef(false);
  useEffect(() => {
    if (!pending) {
      if (wasPending.current) {
        wasPending.current = false;
        void onReload();
      }
      return;
    }
    wasPending.current = true;
    const timer = window.setInterval(() => void load().catch(() => undefined), 3000);
    return () => window.clearInterval(timer);
  }, [pending, load, onReload]);

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    onError(null);
    try {
      for (const file of Array.from(files)) {
        await uploadFile<IntegrationDocument>(`/software-integrations/${integrationId}/documents`, file);
      }
      onNotice("Скан загружен и разбирается: предложенные значения появятся в форме как «авто» — проверьте их.");
      await load();
    } catch (err) {
      onError(errorText(err, "Не удалось загрузить скан"));
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  };

  const openFile = async (doc: IntegrationDocument) => {
    // Файл отдаётся под авторизацией — открываем через blob, а не прямой ссылкой.
    try {
      const response = await fetch(`/api/software-integrations/documents/${doc.id}/file`, {
        headers: { Authorization: `Bearer ${getToken() ?? ""}` },
      });
      if (!response.ok) throw new Error(response.statusText);
      const url = URL.createObjectURL(await response.blob());
      window.open(url, "_blank");
    } catch {
      onError("Не удалось открыть файл");
    }
  };

  const reanalyze = async (doc: IntegrationDocument) => {
    try {
      await api.post(`/software-integrations/documents/${doc.id}/analyze`);
      await load();
    } catch (err) {
      onError(errorText(err, "Не удалось запустить разбор"));
    }
  };

  const remove = async (doc: IntegrationDocument) => {
    if (!window.confirm(`Удалить скан «${doc.file_name}»?`)) return;
    try {
      await api.delete(`/software-integrations/documents/${doc.id}`);
      await load();
    } catch (err) {
      onError(errorText(err, "Не удалось удалить скан"));
    }
  };

  return (
    <div>
      <div className="mb-2 flex items-center gap-3">
        <button
          onClick={() => input.current?.click()}
          disabled={uploading}
          title="PDF, PNG, JPG: платформа прочитает скан и предложит значения"
          className="flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          Сканы писем и протоколов
        </button>
        {documents.length === 0 && <span className="text-xs text-zinc-600">Сканов нет</span>}
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg,.webp"
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
        />
      </div>
      {documents.length > 0 && (
        <ul className="space-y-1.5">
          {documents.map((doc) => (
            <li key={doc.id} className="rounded-lg border border-white/[0.06] px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <button onClick={() => void openFile(doc)} className="flex items-center gap-1.5 text-zinc-200 hover:text-indigo-300">
                  <FileText size={13} /> {doc.file_name}
                  <span className="text-zinc-500">· {formatDate(doc.created_at)}</span>
                </button>
                <span className="flex items-center gap-2">
                  {doc.analysis_status === "pending" && (
                    <span className="flex items-center gap-1 text-indigo-300">
                      <Loader2 size={12} className="animate-spin" /> разбирается…
                    </span>
                  )}
                  {doc.analysis_status === "done" && (
                    <span className="text-emerald-300">
                      разобран: полей {doc.analysis_result?.fields_filled?.length ?? 0}, функций{" "}
                      {doc.analysis_result?.functions_added ?? 0}
                    </span>
                  )}
                  {doc.analysis_status === "failed" && <span className="text-red-400">не разобран</span>}
                  <button onClick={() => void reanalyze(doc)} title="Разобрать заново" className="text-zinc-500 hover:text-zinc-200">
                    <RefreshCw size={12} />
                  </button>
                  <button onClick={() => void remove(doc)} title="Удалить" className="text-zinc-500 hover:text-red-400">
                    <Trash2 size={12} />
                  </button>
                </span>
              </div>
              {doc.analysis_status === "failed" && doc.analysis_error && (
                <div className="mt-1 text-red-400/80">{doc.analysis_error}</div>
              )}
              {doc.analysis_status === "done" && doc.analysis_result?.reading?.summary && (
                <div className="mt-1 text-zinc-500">{doc.analysis_result.reading.summary}</div>
              )}
              {doc.analysis_status === "done" && (doc.analysis_result?.conflicts?.length ?? 0) > 0 && (
                <div className="mt-1 text-amber-300/90">
                  Расхождения со внесёнными данными:{" "}
                  {doc.analysis_result?.conflicts?.map((c) => `${c.field} — в скане «${c.from_scan}»`).join("; ")}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
