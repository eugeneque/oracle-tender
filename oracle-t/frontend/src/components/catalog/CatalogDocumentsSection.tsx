import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, FileClock, Loader2 } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { CatalogDocument, CatalogDocumentsSummary, CatalogTask } from "../../api/types";
import { formatDate, formatDateTime } from "../../utils/format";

// Справочник документов по СИ и руководств по эксплуатации с датой актуальности (правка по
// итогам показа 15.09.2026). Раз в неделю система сверяет каждый документ с источником —
// сайтом производителя или реестром ФГИС — и обо всём, что переиздано, появилось или
// пропало, присылает отдельный отчёт на почту. Здесь — тот же справочник целиком, чтобы
// перед подачей заявки видеть, какой редакции документ лежит в основе характеристик.

const DATE_SOURCE_LABELS: Record<NonNullable<CatalogDocument["document_date_source"]>, string> = {
  last_modified: "дата файла на сайте",
  fgis_version: "редакция в ФГИС",
  observed: "дата фиксации системой",
};

const CHECK_STATUS_LABELS: Record<CatalogDocument["check_status"], string> = {
  ok: "проверен",
  unavailable: "недоступен",
  forbidden_by_robots: "закрыт robots.txt",
  not_checked: "не проверялся",
};

function isRecentlyChanged(document: CatalogDocument, lastCheckedAt: string | null): boolean {
  // «Свежее» — изменившееся при последней сверке: то, что попало в последний отчёт.
  if (!document.changed_at || !lastCheckedAt) return false;
  return new Date(document.changed_at).toDateString() === new Date(lastCheckedAt).toDateString();
}

export function CatalogDocumentsSection({
  manufacturerId,
  isAdmin,
}: {
  manufacturerId: string | null;
  isAdmin: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [documents, setDocuments] = useState<CatalogDocument[]>([]);
  const [summary, setSummary] = useState<CatalogDocumentsSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string) => {
    const [docs, sum] = await Promise.all([
      api.get<CatalogDocument[]>(`/catalog/documents?manufacturer_id=${id}`),
      api.get<CatalogDocumentsSummary>(`/catalog/documents/summary?manufacturer_id=${id}`),
    ]);
    setDocuments(docs);
    setSummary(sum);
  }, []);

  useEffect(() => {
    setDocuments([]);
    setSummary(null);
    setNotice(null);
    setError(null);
    if (!manufacturerId) return;
    load(manufacturerId).catch((err) =>
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить документы")
    );
  }, [manufacturerId, load]);

  const recentlyChanged = useMemo(
    () => documents.filter((d) => d.is_active && isRecentlyChanged(d, summary?.last_checked_at ?? null)).length,
    [documents, summary]
  );

  // Сверка ставится в очередь, а не выполняется в запросе: это сотни запросов к чужим
  // сайтам с паузами между ними — минуты, и держать на них открытую вкладку нельзя.
  const handleCheck = async () => {
    if (!manufacturerId) return;
    setBusy(true);
    setError(null);
    try {
      await api.post<CatalogTask>(`/catalog/documents/check?manufacturer_id=${manufacturerId}`);
      setNotice(
        "Сверка документов запущена в фоне — сайты производителей отвечают медленно. " +
          "Если что-то изменилось, отчёт уйдёт на почту и появится в журнале уведомлений; " +
          "обновите страницу через несколько минут."
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось запустить сверку");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <div
        className={`flex flex-wrap items-center justify-between gap-2 px-5 py-3 ${
          open ? "border-b border-white/[0.08]" : ""
        }`}
      >
        <button onClick={() => setOpen((v) => !v)} className="flex items-start gap-2 text-left" aria-expanded={open}>
          <span className="mt-0.5 text-zinc-500">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
          <span>
            <h2 className="text-sm font-semibold text-zinc-100">
              Документы по СИ и руководства
              {summary && summary.total > 0 && <span className="ml-1.5 text-zinc-500">({summary.total})</span>}
              {recentlyChanged > 0 && (
                <span className="ml-1.5 text-indigo-300" title="Изменились при последней сверке">
                  ● обновлено {recentlyChanged}
                </span>
              )}
              {summary && summary.unavailable > 0 && (
                <span className="ml-1.5 text-amber-400" title="Источник не отвечает">
                  ⚠ недоступно {summary.unavailable}
                </span>
              )}
            </h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              Актуальные даты руководств, паспортов, описаний типа и сертификатов. Сверка с источниками —
              раз в неделю, отчёт об изменениях — на почту.
              {summary?.last_checked_at && ` Последняя сверка: ${formatDateTime(summary.last_checked_at)}.`}
            </p>
          </span>
        </button>
        {isAdmin && manufacturerId && (
          <button
            onClick={handleCheck}
            disabled={busy}
            title="Сверить документы производителя с источниками сейчас, не дожидаясь еженедельной проверки"
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <FileClock size={13} />}
            Проверить актуальность
          </button>
        )}
      </div>

      <div className="px-5 py-3" hidden={!open}>
        {error && (
          <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>
        )}
        {notice && (
          <div className="mb-3 flex items-start justify-between gap-3 rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-300">
            <span>{notice}</span>
            <button onClick={() => setNotice(null)} className="shrink-0 text-indigo-400 hover:text-indigo-200">
              ×
            </button>
          </div>
        )}
        {documents.length === 0 ? (
          <p className="text-xs text-zinc-500">
            Документов пока нет — они появятся после обхода сайта производителя и первой сверки.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-zinc-500">
                  <th className="py-1.5 pr-4 font-medium">Документ</th>
                  <th className="py-1.5 pr-4 font-medium">Модель / код СИ</th>
                  <th className="py-1.5 pr-4 font-medium">Актуальная дата</th>
                  <th className="py-1.5 pr-4 font-medium">Проверено</th>
                  <th className="py-1.5 font-medium">Состояние</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((d) => {
                  const fresh = d.is_active && isRecentlyChanged(d, summary?.last_checked_at ?? null);
                  return (
                    <tr key={d.id} className={`border-t border-white/[0.06] ${d.is_active ? "" : "opacity-50"}`}>
                      <td className="py-1.5 pr-4">
                        <a
                          href={d.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-zinc-100 hover:text-indigo-300"
                          title={d.url}
                        >
                          {d.kind_title}
                          <ExternalLink size={11} className="opacity-50" />
                        </a>
                        {d.version_label && <span className="ml-1.5 text-[11px] text-zinc-500">ред. {d.version_label}</span>}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-4 text-zinc-300">
                        {d.model_name ?? (d.si_code ? <span className="font-mono">{d.si_code}</span> : "—")}
                        {d.execution && <span className="ml-1.5 text-[10px] text-zinc-500">{d.execution}</span>}
                      </td>
                      <td className="py-1.5 pr-4">
                        <span className={fresh ? "text-indigo-300" : "text-zinc-100"}>{formatDate(d.document_date) ?? "—"}</span>
                        {d.document_date_source && (
                          <span className="ml-1.5 text-[11px] text-zinc-500">{DATE_SOURCE_LABELS[d.document_date_source]}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-4 text-[11px] text-zinc-500">{formatDateTime(d.last_checked_at) ?? "—"}</td>
                      <td className="py-1.5">
                        {!d.is_active ? (
                          <span className="text-[11px] text-zinc-500">пропал из справочника</span>
                        ) : fresh ? (
                          <span className="rounded-full bg-indigo-500/10 px-2 py-0.5 text-[11px] font-medium text-indigo-300">
                            обновлён {formatDate(d.changed_at)}
                          </span>
                        ) : d.check_status === "unavailable" ? (
                          <span
                            className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-400"
                            title={d.check_error ?? undefined}
                          >
                            <AlertTriangle size={11} /> недоступен
                          </span>
                        ) : (
                          <span className="text-[11px] text-zinc-500">
                            {CHECK_STATUS_LABELS[d.check_status]}
                            {d.change_count > 0 && ` · изменялся ${d.change_count} раз`}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
