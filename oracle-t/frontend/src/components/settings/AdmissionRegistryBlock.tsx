import { useEffect, useRef, useState } from "react";
import { FileUp, Loader2, PlayCircle } from "lucide-react";

import { ApiError, api, uploadFile } from "../../api/client";
import type { AdmissionRegistryKey, RegistryPoll, RegistryPollStatus, Source } from "../../api/types";
import { primaryButtonClass, secondaryButtonClass, SettingsNotice } from "./ui";
import { formatDateTime } from "./SourcesSection";

// Источник «Справочников продукции» → реестр, по которому идёт опрос (`registry_polling.POLLABLE`).
const REGISTRY_BY_ADAPTER: Record<string, AdmissionRegistryKey> = {
  gisp: "industrial_products",
  rosseti_zak: "rosseti_attestation",
};

const POLL_REFRESH_MS = 5_000;

export function registryForSource(source: Source): AdmissionRegistryKey | null {
  return source.adapter_key ? REGISTRY_BY_ADAPTER[source.adapter_key] ?? null : null;
}

function describePoll(poll: RegistryPoll): string {
  if (poll.status === "running") return `идёт с ${formatDateTime(poll.started_at)}…`;
  const when = formatDateTime(poll.finished_at ?? poll.started_at);
  const how = poll.method === "upload" ? "загружен файл" : "опрос";
  return `${when}, ${how}${poll.message ? ` — ${poll.message}` : ""}`;
}

/**
 * Опрос реестра допуска прямо в карточке источника (28.10.2026). Раньше опросить ГИСП или
 * загрузить перечень Россетей можно было только из карточки модели каталога, а в «Источниках»
 * висело «Последний опрос: никогда» — сверка реестров пишет свои опросы, не `last_polled_at`.
 *
 * ГИСП с сервера отвечает (браузерный User-Agent, раздел 77) — кнопка опроса. Сайт Россетей
 * закрыт JavaScript-проверкой браузера, которую сервер не проходит, — перечень загружается
 * вручную: плашка объясняет, где взять PDF, и рядом поле файла.
 */
export function AdmissionRegistryBlock({
  source,
  registry,
  isAdmin,
}: {
  source: Source;
  registry: AdmissionRegistryKey;
  isAdmin: boolean;
}) {
  const [status, setStatus] = useState<RegistryPollStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const manual = registry === "rosseti_attestation";

  const reload = async () => {
    try {
      const polls = await api.get<RegistryPollStatus[]>("/catalog/registries/polls");
      setStatus(polls.find((p) => p.registry === registry) ?? null);
    } catch {
      // Статус опроса — подсказка; без него карточка источника остаётся рабочей.
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registry]);

  const running = status?.last_poll?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), POLL_REFRESH_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const startPoll = async () => {
    setBusy(true);
    setNotice(null);
    try {
      await api.post<RegistryPoll>(`/catalog/registries/${registry}/poll`);
      setNotice({
        tone: "success",
        text: "Опрос запущен в фоне. Днём ГИСП отвечает медленно — до 15 минут. Записи появятся в «Реестрах допуска» моделей.",
      });
      await reload();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : "Не удалось запустить опрос" });
    } finally {
      setBusy(false);
    }
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setNotice(null);
    try {
      const poll = await uploadFile<RegistryPoll>(`/catalog/registries/${registry}/upload`, file);
      setNotice({ tone: "success", text: poll.message ?? `Разобрано записей: ${poll.records_saved}` });
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      await reload();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : "Не удалось разобрать файл" });
    } finally {
      setBusy(false);
    }
  };

  const lastPoll = status?.last_poll ?? null;
  const lastOk = status?.last_successful_poll ?? null;

  return (
    <div className="space-y-3">
      {manual && (
        <SettingsNotice tone="info">
          Перечень загружается вручную: сайт Россетей закрыт проверкой браузера, сервер его не
          скачивает. Раз в две недели откройте{" "}
          <a href={source.url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:underline">
            «Проверку качества оборудования»
          </a>
          , скачайте PDF «Раздел II. Вторичное оборудование…» и загрузите его сюда.
        </SettingsNotice>
      )}

      <div className="text-xs text-zinc-500">
        <div>
          Последняя сверка:{" "}
          <span className={lastPoll?.status === "error" ? "text-red-400/80" : undefined}>
            {lastPoll ? describePoll(lastPoll) : "никогда"}
          </span>
        </div>
        {lastOk && lastOk.id !== lastPoll?.id && (
          <div>Последняя удачная: {describePoll(lastOk)}</div>
        )}
      </div>

      {isAdmin &&
        (manual ? (
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInput}
              type="file"
              accept=".pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <button type="button" onClick={() => fileInput.current?.click()} disabled={busy} className={secondaryButtonClass}>
              <FileUp size={13} />
              {file ? "Другой файл" : "Выбрать PDF"}
            </button>
            {file && <span className="max-w-[16rem] truncate text-xs text-zinc-400">{file.name}</span>}
            <button type="button" onClick={() => void upload()} disabled={!file || busy} className={primaryButtonClass}>
              {busy && <Loader2 size={13} className="animate-spin" />}
              {busy ? "Разбираю…" : "Разбор"}
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => void startPoll()} disabled={busy || running} className={secondaryButtonClass}>
            {busy || running ? <Loader2 size={13} className="animate-spin" /> : <PlayCircle size={13} />}
            {running ? "Опрос идёт…" : "Опросить реестр"}
          </button>
        ))}

      {notice && <SettingsNotice tone={notice.tone}>{notice.text}</SettingsNotice>}
    </div>
  );
}
