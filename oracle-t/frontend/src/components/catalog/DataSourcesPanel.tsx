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
import { AI_COST_NOTE, Tooltip } from "../ui/Tooltip";

// Источники данных модели (замечание тендерного отдела 13.10.2026). Пустые П18–П28 и
// «СПОДЭС: —» выглядели как «ИИ не справился», хотя причина была в другом: руководство
// Энергомеры закрыто для роботов, реестры не опрашивались. Панель говорит по каждому
// источнику прямо — прочитан ли он, почему нет и что сделать. Состояние собирает сервер
// (`product_sources.py`).
//
// 14.10.2026 (редизайн каталога): панель по умолчанию свёрнута в одну строку — она нужна,
// когда параметр пуст и хочется понять почему, а не при каждом открытии модели. Состояние —
// точкой, подпись нейтральная; что кнопка вызывает модель ИИ — в подсказке, а не значком.

const STATE_STYLE: Record<DataSourceState, { label: string; dot: string }> = {
  ok: { label: "прочитан", dot: "bg-emerald-400" },
  pending: { label: "не опрошен", dot: "bg-amber-400" },
  blocked: { label: "нужен файл", dot: "bg-amber-400" },
  error: { label: "ошибка опроса", dot: "bg-rose-400" },
  missing: { label: "нет источника", dot: "bg-zinc-600" },
  running: { label: "опрашивается", dot: "bg-indigo-400 animate-pulse" },
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
  // Итог опроса сервер формулирует сам: у ГИСП — сколько записей по нашим ОКПД2, у
  // Россетей — сколько записей в перечне (28.10.2026).
  if (poll.message) return poll.message;
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
  const open = expanded ?? false;

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
        return source.key === "gisp"
          ? "Опрос ГИСП запущен в фоне. Днём реестр отвечает очень медленно: на ответ даётся до 5 минут " +
              "и до 3 попыток, поэтому опрос может занять до 15 минут. Плановый опрос идёт ночью — " +
              "время указано в строке ГИСП. Найденные записи появятся в «Реестрах допуска»."
          : "Опрос перечня запущен в фоне — обычно минута-две. Найденные записи появятся в «Реестрах допуска».";
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
        <span className="text-sm text-zinc-300">Источники данных</span>
        <span className="flex items-center gap-2 text-right text-xs text-zinc-500">
          {attention.length > 0 && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden="true" />}
          {attention.length > 0 ? `не прочитано ${attention.length} из ${sources.length}` : "все прочитаны"}
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
                    <span className="text-xs text-zinc-500">{style.label}</span>
                    {source.url && (
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label="Открыть источник"
                        className="text-zinc-500 hover:text-zinc-200"
                      >
                        <ExternalLink size={12} />
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
                        <Tooltip key={action.key} content={action.ai ? AI_COST_NOTE : null}>
                          <button
                            onClick={() => click(source, action)}
                            disabled={busy !== null || source.state === "running"}
                            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                          >
                            {busy === key ? <Loader2 size={12} className="animate-spin" /> : upload && <Upload size={12} />}
                            {action.label}
                          </button>
                        </Tooltip>
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
