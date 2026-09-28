import { useEffect, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock,
  ExternalLink,
  Globe,
  Loader2,
  PlayCircle,
  Wifi,
  WifiOff,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { Source, SourcePollResult } from "../../api/types";
import { CATALOG_SOURCE_TYPES, GOSPLAN_SOURCE_TYPE, MANUAL_SOURCE_TYPE } from "../../api/types";
import {
  SettingCard,
  SettingsGroup,
  SettingsNotice,
  SettingsPanel,
  StatusPill,
  secondaryButtonClass,
} from "./ui";

const AVAILABILITY_REFRESH_MS = 20_000;

export function formatDateTime(value: string | null): string {
  if (!value) return "никогда";
  return new Date(value).toLocaleString("ru-RU");
}

export function AdapterStatusPill({ source }: { source: Source }) {
  if (source.adapter_status === "implemented") {
    return (
      <StatusPill tone="ok">
        <CheckCircle2 size={13} />
        подключён
      </StatusPill>
    );
  }
  if (source.adapter_status === "blocked") {
    return (
      <StatusPill tone="bad">
        <AlertTriangle size={13} />
        заблокирован
      </StatusPill>
    );
  }
  return (
    <StatusPill tone="muted">
      <Clock size={13} />в очереди
    </StatusPill>
  );
}

export function AvailabilityPill({ source }: { source: Source }) {
  const checked = `Проверено: ${formatDateTime(source.availability_checked_at)}`;
  if (source.availability_status === "available") {
    return (
      <StatusPill tone="ok" title={checked}>
        <Wifi size={13} />
        доступен
      </StatusPill>
    );
  }
  if (source.availability_status === "unavailable") {
    return (
      <StatusPill tone="bad" title={`${checked}${source.availability_error ? ` — ${source.availability_error}` : ""}`}>
        <WifiOff size={13} />
        недоступен
      </StatusPill>
    );
  }
  return (
    <StatusPill tone="muted">
      <Clock size={13} />
      проверяется…
    </StatusPill>
  );
}

/** Подгружает источники и держит статус доступности свежим, пока вкладка открыта: пинг идёт
 * на бэкенде раз в минуту (app/core/scheduler.py), страница только перечитывает результат. */
export function useSources(active: boolean) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    try {
      setSources(await api.get<Source[]>("/sources"));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить источники");
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  useEffect(() => {
    if (!active) return;
    const interval = setInterval(() => void reload(), AVAILABILITY_REFRESH_MS);
    return () => clearInterval(interval);
  }, [active]);

  return { sources, error, reload };
}

function SourceCard({
  source,
  isAdmin,
  isPolling,
  onPoll,
}: {
  source: Source;
  isAdmin: boolean;
  isPolling: boolean;
  onPoll: (source: Source) => void;
}) {
  const isCatalog = CATALOG_SOURCE_TYPES.includes(source.type);
  const canPoll = isAdmin && !isCatalog && source.adapter_status === "implemented";
  return (
    <SettingCard
      id={`source-${source.key}`}
      icon={isCatalog ? <BookOpen size={18} /> : <Globe size={18} />}
      title={
        <span className="flex flex-wrap items-center gap-2">
          {source.name}
          {source.status !== "active" && (
            <span className="rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[11px] font-normal text-zinc-500">
              {source.status === "disabled" ? "выключен" : "ожидает доступа"}
            </span>
          )}
        </span>
      }
      description={
        <>
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex max-w-full items-center gap-1 truncate text-indigo-400 hover:underline"
          >
            <span className="truncate">{source.url.replace(/^https?:\/\//, "")}</span>
            <ExternalLink size={11} className="shrink-0" />
          </a>
          <span className="block text-zinc-600">
            Последний опрос: {formatDateTime(source.last_polled_at)}
          </span>
          {source.note && <span className="mt-1 block text-zinc-500">{source.note}</span>}
          {source.availability_status === "unavailable" && source.availability_error && (
            <span className="mt-1 block text-red-400/80">{source.availability_error}</span>
          )}
        </>
      }
      control={
        <div className="flex flex-wrap items-center justify-end gap-2">
          <AvailabilityPill source={source} />
          <AdapterStatusPill source={source} />
          {canPoll && (
            <button
              onClick={() => onPoll(source)}
              disabled={isPolling}
              className={secondaryButtonClass}
            >
              {isPolling ? <Loader2 size={13} className="animate-spin" /> : <PlayCircle size={13} />}
              {isPolling ? "Опрашиваю…" : "Опросить"}
            </button>
          )}
        </div>
      }
    />
  );
}

/** Вкладка «Источники»: площадки закупок стандартного канала и источники справочника
 * продукции. Госплан сюда не входит — у него своя вкладка и свой канал на странице тендеров,
 * ручные заявки — не площадка. */
export function SourcesSection({ isAdmin, active }: { isAdmin: boolean; active: boolean }) {
  const { sources, error: loadError, reload } = useSources(active);
  const [pollingKey, setPollingKey] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SourcePollResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handlePoll = async (source: Source) => {
    setError(null);
    setPollingKey(source.key);
    try {
      setLastResult(await api.post<SourcePollResult>(`/sources/${source.id}/poll`));
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось запустить опрос источника");
    } finally {
      setPollingKey(null);
    }
  };

  const visible = (sources ?? []).filter(
    (s) => s.type !== MANUAL_SOURCE_TYPE && s.type !== GOSPLAN_SOURCE_TYPE,
  );
  const platforms = visible.filter((s) => !CATALOG_SOURCE_TYPES.includes(s.type));
  const connected = platforms.filter((s) => s.adapter_status === "implemented");
  const queued = platforms.filter((s) => s.adapter_status !== "implemented");
  const catalog = visible.filter((s) => CATALOG_SOURCE_TYPES.includes(s.type));
  const available = platforms.filter((s) => s.availability_status === "available").length;

  const renderCards = (items: Source[]) =>
    items.map((source) => (
      <SourceCard
        key={source.id}
        source={source}
        isAdmin={isAdmin}
        isPolling={pollingKey === source.key}
        onPoll={(s) => void handlePoll(s)}
      />
    ));

  return (
    <SettingsPanel
      title="Источники тендеров"
      description={
        <>
          Площадки, с которых система собирает закупки для режима «Стандартные ресурсы» на
          странице тендеров. Опрос идёт по расписанию два раза в день, доступность площадок
          проверяется автоматически раз в минуту.
          {sources && (
            <span className="mt-3 flex flex-wrap gap-2">
              <StatusPill tone="accent">подключено {connected.length}</StatusPill>
              <StatusPill tone={available === platforms.length ? "ok" : "muted"}>
                доступно сейчас {available} из {platforms.length}
              </StatusPill>
            </span>
          )}
        </>
      }
    >
      {(error || loadError) && <SettingsNotice tone="error">{error ?? loadError}</SettingsNotice>}
      {lastResult && (
        <SettingsNotice tone="success">
          Источник «{lastResult.source_key}»: найдено {lastResult.found}, создано{" "}
          {lastResult.created}, обновлено {lastResult.updated}, ошибок {lastResult.errors}
        </SettingsNotice>
      )}

      {sources === null ? (
        <div className="flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 size={14} className="animate-spin" />
          Загрузка…
        </div>
      ) : (
        <>
          <SettingsGroup
            id="sources-connected"
            label="Подключённые площадки"
            hint="Собирают закупки автоматически и по кнопке «Синхронизировать» на странице тендеров."
          >
            {renderCards(connected)}
          </SettingsGroup>
          {queued.length > 0 && (
            <SettingsGroup
              id="sources-queued"
              label="В очереди на подключение"
              hint="Доступность проверяется, но сбора пока нет."
            >
              {renderCards(queued)}
            </SettingsGroup>
          )}
          {catalog.length > 0 && (
            <SettingsGroup
              id="sources-catalog"
              label="Справочники продукции"
              hint="Госреестр СИ, сайты производителей, списки ПО верхнего уровня и реестры допуска. Тендеров не отдают — пополняют каталог."
            >
              {renderCards(catalog)}
            </SettingsGroup>
          )}
        </>
      )}
    </SettingsPanel>
  );
}
