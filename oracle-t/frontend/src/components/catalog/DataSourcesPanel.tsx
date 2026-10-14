import { useRef, useState } from "react";
import { ChevronDown, ExternalLink, Loader2, Upload } from "lucide-react";

import { ApiError, api, uploadFile } from "../../api/client";
import type {
  DataSource,
  DataSourceAction,
  DataSourceState,
  ExtractionOutcome,
  ManualIngestOutcome,
  ProductDocumentation,
  RegistryPoll,
} from "../../api/types";
import { AI_HINT, AiBadge } from "./AiBadge";

// Источники данных модели (замечание тендерного отдела 13.10.2026). Пустые П18–П28 и
// «СПОДЭС: —» выглядели как «ИИ не справился», хотя причина была в другом: руководство
// Энергомеры закрыто для роботов, реестры не опрашивались. Панель говорит по каждому
// источнику прямо — прочитан ли он, почему нет и что сделать; кнопки, которые вызывают
// модель ИИ, помечены. Состояние собирает сервер (`product_sources.py`).

const STATE_STYLE: Record<DataSourceState, { label: string; dot: string; text: string }> = {
  ok: { label: "прочитан", dot: "bg-emerald-400", text: "text-emerald-400" },
  pending: { label: "нужно опросить", dot: "bg-amber-400", text: "text-amber-400" },
  blocked: { label: "нужен файл", dot: "bg-amber-400", text: "text-amber-400" },
  error: { label: "ошибка опроса", dot: "bg-rose-400", text: "text-rose-400" },
  missing: { label: "нет источника", dot: "bg-zinc-600", text: "text-zinc-500" },
  running: { label: "опрашивается", dot: "bg-sky-400 animate-pulse", text: "text-sky-400" },
};

// Что требует действия человека: «нет источника» — не требует, сделать тут нечего.
const NEEDS_ATTENTION: DataSourceState[] = ["pending", "blocked", "error"];

const UPLOAD_ACCEPT: Record<string, string> = {
  upload_manual: ".pdf,.doc,.docx",
  gisp: ".xlsx",
  zak: ".pdf,.xlsx",
};

function summariseExtraction(outcome: ExtractionOutcome): string {
  const parts = [`сохранено ${outcome.saved}`];
  if (outcome.skipped_protected) parts.push(`не тронуто (ручные или источник выше рангом) ${outcome.skipped_protected}`);
  if (outcome.skipped_unknown_field) parts.push(`отброшено ${outcome.skipped_unknown_field}`);
  if (outcome.chunks_failed) parts.push(`ошибок разбора ${outcome.chunks_failed}`);
  return parts.join(", ");
}

function summariseManual(outcome: ManualIngestOutcome): string {
  const text = `Руководство прочитано: сохранено значений ${outcome.characteristics_saved}.`;
  return outcome.messages.length ? `${text} ${outcome.messages[0]}` : text;
}

function summarisePoll(poll: RegistryPoll): string {
  return (
    `${poll.file_name ?? "Выгрузка"}: строк ${poll.rows_total}, по счётчикам ${poll.rows_relevant}, ` +
    `моделей найдено ${poll.products_found} из ${poll.products_checked}.`
  );
}

export function DataSourcesPanel({
  productId,
  manufacturerId,
  sources,
  isAdmin,
  onChanged,
  onNotice,
  onError,
}: {
  productId: string;
  manufacturerId: string;
  sources: DataSource[] | null;
  isAdmin: boolean;
  onChanged: () => Promise<void>;
  onNotice: (text: string) => void;
  onError: (text: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const pendingUpload = useRef<{ source: DataSource; action: DataSourceAction } | null>(null);

  if (sources === null) return null;

  const attention = sources.filter((s) => NEEDS_ATTENTION.includes(s.state));
  // Раскрыта, пока есть что опросить; когда всё прочитано — свёрнута до одной строки.
  const open = expanded ?? attention.length > 0;

  const perform = async (source: DataSource, action: DataSourceAction, file?: File): Promise<string> => {
    switch (action.key) {
      case "autofill":
        await api.post(`/manufacturers/${manufacturerId}/autofill`);
        return "Производитель поставлен в очередь опроса. Карточка обновится, когда обход сайта дойдёт до модели.";
      case "extract_fgis":
        return `Из «Описания типа»: ${summariseExtraction(
          await api.post<ExtractionOutcome>(`/products/${productId}/extract-characteristics/from-si-type`)
        )}.`;
      case "read_manual":
        return summariseManual(await api.post<ManualIngestOutcome>(`/products/${productId}/read-manual`));
      case "find_docs":
        return (await api.post<ProductDocumentation>(`/products/${productId}/find-documentation`)).message;
      case "upload_manual":
        return summariseManual(await uploadFile<ManualIngestOutcome>(`/products/${productId}/manual-upload`, file!));
      case "poll_registry":
        await api.post<RegistryPoll>(`/catalog/registries/${source.registry}/poll`);
        return "Опрос реестра запущен в фоне — обычно несколько минут. Найденные записи появятся в «Реестрах допуска».";
      case "upload_registry":
        return summarisePoll(await uploadFile<RegistryPoll>(`/catalog/registries/${source.registry}/upload`, file!));
    }
  };

  const run = async (source: DataSource, action: DataSourceAction, file?: File) => {
    setBusy(`${source.key}/${action.key}`);
    try {
      onNotice(await perform(source, action, file));
      await onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Не удалось выполнить действие");
    } finally {
      setBusy(null);
    }
  };

  const click = (source: DataSource, action: DataSourceAction) => {
    if (action.key === "upload_manual" || action.key === "upload_registry") {
      pendingUpload.current = { source, action };
      if (fileInput.current) {
        fileInput.current.accept = UPLOAD_ACCEPT[action.key === "upload_manual" ? action.key : source.key] ?? "";
        fileInput.current.click();
      }
      return;
    }
    void run(source, action);
  };

  return (
    <section className="rounded-xl border border-white/[0.08]">
      <input
        ref={fileInput}
        type="file"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          const target = pendingUpload.current;
          event.target.value = "";
          if (file && target) void run(target.source, target.action, file);
        }}
      />
      <button
        onClick={() => setExpanded(!open)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
      >
        <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">Источники данных</span>
        <span className="flex items-center gap-2 text-right text-[12px]">
          {attention.length > 0 ? (
            <span className="text-amber-400">
              {attention.length} из {sources.length} нужно опросить — без них часть параметров пуста
            </span>
          ) : (
            <span className="text-zinc-500">все доступные источники прочитаны</span>
          )}
          <ChevronDown size={14} className={`shrink-0 text-zinc-500 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>

      {open && (
        <ul className="border-t border-white/[0.08]">
          {sources.map((source) => {
            const style = STATE_STYLE[source.state] ?? STATE_STYLE.missing;
            return (
              <li
                key={source.key}
                className="flex flex-col gap-2 border-t border-white/[0.06] px-4 py-2.5 first:border-t-0 sm:flex-row sm:items-start"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`} aria-hidden="true" />
                    <span className="text-sm text-zinc-200">{source.label}</span>
                    <span className={`text-[11px] ${style.text}`}>{style.label}</span>
                    {source.url && (
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-0.5 text-[11px] text-indigo-400 hover:text-indigo-300"
                      >
                        открыть <ExternalLink size={10} />
                      </a>
                    )}
                  </div>
                  <p className="mt-0.5 pl-3.5 text-[12px] leading-snug text-zinc-500">{source.detail}</p>
                </div>
                {isAdmin && source.actions.length > 0 && (
                  <div className="flex shrink-0 flex-wrap gap-1.5 pl-3.5 sm:pl-0">
                    {source.actions.map((action) => {
                      const key = `${source.key}/${action.key}`;
                      const upload = action.key === "upload_manual" || action.key === "upload_registry";
                      return (
                        <button
                          key={action.key}
                          onClick={() => click(source, action)}
                          disabled={busy !== null || source.state === "running"}
                          title={action.ai ? AI_HINT : "Без ИИ"}
                          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                        >
                          {busy === key ? <Loader2 size={12} className="animate-spin" /> : upload && <Upload size={12} />}
                          {action.label}
                          {action.ai && <AiBadge compact />}
                        </button>
                      );
                    })}
                  </div>
                )}
              </li>
            );
          })}
          {!isAdmin && attention.length > 0 && (
            <li className="border-t border-white/[0.06] px-4 py-2 text-[11px] text-zinc-500">
              Опросить источники или загрузить файл может администратор справочника.
            </li>
          )}
        </ul>
      )}
    </section>
  );
}
