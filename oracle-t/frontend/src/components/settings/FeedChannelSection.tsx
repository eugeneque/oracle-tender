import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Database,
  Gauge,
  KeyRound,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  BackgroundJob,
  SourceCredential,
  TenderStats,
} from "../../api/types";
import type { ExternalFeed } from "../../api/types";
import { AvailabilityPill, formatDateTime, useSources } from "./SourcesSection";
import {
  SettingCard,
  SettingsGroup,
  SettingsNotice,
  SettingsPanel,
  StatusPill,
  dangerButtonClass,
  primaryButtonClass,
  secondaryButtonClass,
  settingsInputClass,
} from "./ui";

/** Всё, чем вкладки внешних каналов отличаются друг от друга. */
interface FeedChannelConfig {
  feed: ExternalFeed;
  title: string;
  /** Подпись ссылки на страницу тендеров, отфильтрованную по каналу. */
  tendersLink: string;
  /** Работает ли канал без ключа (у Госплана — бесплатный тариф). */
  keyOptional: boolean;
  keyName: string;
  keyPlaceholder: string;
  keyHint: string;
  keyNotes: string;
  /** Строка «Доступ» при заданном ключе и без него. */
  accessWithKey: string;
  accessWithoutKey: string;
  pillWithKey: string;
  pillWithoutKey: string;
  savedNotice: string;
  removeConfirm: string;
  removedNotice: string;
  migration: string;
}

const FEED_CHANNELS: Record<ExternalFeed, FeedChannelConfig> = {
  gosplan: {
    feed: "gosplan",
    title: "Госплан",
    tendersLink: "Тендеры Госплана",
    keyOptional: true,
    keyName: "Ключ API",
    keyPlaceholder: "ключ из личного кабинета gosplan.info",
    keyHint:
      "Нужен только для платного тарифа. Хранится зашифрованным, как пароли площадок, и обратно не показывается.",
    keyNotes: "Платный тариф Госплана",
    accessWithKey: "Платный: запросы идут на v2.gosplan.info с ключом, без ограничения скорости.",
    accessWithoutKey:
      "Бесплатный: v2test.gosplan.info без ключа, 10 запросов в минуту — сбор занимает несколько минут.",
    pillWithKey: "платный",
    pillWithoutKey: "бесплатный",
    savedNotice: "Ключ сохранён — следующий сбор пойдёт через платный тариф.",
    removeConfirm: "Удалить ключ API? Сбор вернётся на бесплатный тариф.",
    removedNotice: "Ключ удалён — сбор идёт через бесплатный тариф.",
    migration: "0058",
  },
  tenderplan: {
    feed: "tenderplan",
    title: "Тендерплан",
    tendersLink: "Тендеры Тендерплана",
    keyOptional: false,
    keyName: "Токен API",
    keyPlaceholder: "персональный токен из личного кабинета tenderplan.ru",
    keyHint:
      "Персональный токен, не «сервисный ключ»: настройки пользователя в Тендерплане → «Интеграции с сервисами» → Open API → «Настроить». Права: resources (personal и external), relations:read, keys:read. Хранится зашифрованным и обратно не показывается.",
    keyNotes: "Персональный токен Тендерплана",
    accessWithKey: "Подключено: запросы идут на tenderplan.ru с токеном, секунда между запросами.",
    accessWithoutKey: "Не подключено: без токена API Тендерплана не отвечает.",
    pillWithKey: "подключено",
    pillWithoutKey: "нет токена",
    savedNotice: "Токен сохранён — можно собирать.",
    removeConfirm: "Удалить токен Тендерплана? Сбор остановится.",
    removedNotice: "Токен удалён — сбор из Тендерплана остановлен.",
    migration: "0062",
  },
};

/**
 * Вкладка внешнего канала сбора — Госплан (28.09.2026) и Тендерплан (30.09.2026).
 *
 * Сбор запускается фоновой задачей, как «Синхронизировать» на странице тендеров, а не
 * запросом, который держал бы страницу несколько минут. Ключ (токен) хранится как учётка
 * источника канала (пароль = ключ) — тем же шифрованием, что пароли площадок.
 */
export function FeedChannelSection({
  feed,
  isAdmin,
  active,
}: {
  feed: ExternalFeed;
  isAdmin: boolean;
  active: boolean;
}) {
  const config = FEED_CHANNELS[feed];
  // Имя канала — это и ключ его источника.
  const sourceKey = feed;
  const { sources, reload } = useSources(active);
  const source = sources?.find((s) => s.key === sourceKey) ?? null;
  const [credential, setCredential] = useState<SourceCredential | null | undefined>(undefined);
  const [collected, setCollected] = useState<number | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [job, setJob] = useState<BackgroundJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const isPolling = job?.status === "queued" || job?.status === "running";

  const loadStats = () =>
    api
      .get<TenderStats>(`/tenders/stats?feed=${feed}`)
      .then((stats) => setCollected(stats.total))
      .catch(() => setCollected(null));

  const loadCredential = async () => {
    if (!isAdmin) return;
    try {
      const all = await api.get<SourceCredential[]>("/source-credentials");
      setCredential(all.find((c) => c.source_key === sourceKey) ?? null);
    } catch {
      setCredential(null);
    }
  };

  useEffect(() => {
    void loadStats();
    void loadCredential();
    // Сбор мог запустить кто-то другой или эта же вкладка до перезагрузки.
    void api
      .get<BackgroundJob | null>("/sources/poll/current")
      .then((current) => {
        if (current && (current.status === "queued" || current.status === "running")) setJob(current);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- загрузка один раз при входе
  }, []);

  useEffect(() => {
    if (!isPolling) return;
    const timer = setInterval(() => {
      void api
        .get<BackgroundJob | null>("/sources/poll/current")
        .then((current) => {
          if (!current) return;
          setJob(current);
          if (current.status === "success") {
            setNotice(current.message ?? "Сбор завершён");
            void loadStats();
            void reload();
          } else if (current.status === "error") {
            setError(`Сбор прерван: ${current.message ?? "неизвестная ошибка"}`);
          }
        })
        .catch(() => undefined);
    }, 3_000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- перезапуск только по статусу задачи
  }, [isPolling]);

  const startPoll = async () => {
    setError(null);
    setNotice(null);
    try {
      setJob(await api.post<BackgroundJob>("/sources/poll", { source_keys: [sourceKey] }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось запустить сбор");
    }
  };

  const saveKey = async () => {
    if (!source || !apiKey.trim()) return;
    setIsSaving(true);
    setError(null);
    setNotice(null);
    try {
      if (credential) {
        await api.patch(`/source-credentials/${credential.id}`, {
          password: apiKey.trim(),
          is_active: true,
        });
      } else {
        await api.post("/source-credentials", {
          source_id: source.id,
          label: config.keyName,
          username: "apikey",
          password: apiKey.trim(),
          notes: config.keyNotes,
          is_active: true,
        });
      }
      setApiKey("");
      setNotice(config.savedNotice);
      await loadCredential();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить ключ");
    } finally {
      setIsSaving(false);
    }
  };

  const removeKey = async () => {
    if (!credential) return;
    if (!window.confirm(config.removeConfirm)) return;
    try {
      await api.delete(`/source-credentials/${credential.id}`);
      setNotice(config.removedNotice);
      await loadCredential();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить ключ");
    }
  };

  const hasKey = Boolean(credential && credential.is_active);

  return (
    <SettingsPanel
      title={config.title}
      actions={
        <Link to={`/tenders?feed=${feed}`} className={secondaryButtonClass}>
          {config.tendersLink}
          <ArrowRight size={13} />
        </Link>
      }
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
      {notice && <SettingsNotice tone="success">{notice}</SettingsNotice>}

      <SettingsGroup id={`${feed}-status`} label="Подключение">
        <SettingCard
          id={`${feed}-tariff`}
          active={hasKey}
          icon={<Gauge size={18} />}
          title={config.keyOptional ? "Тариф" : "Доступ"}
          description={hasKey ? config.accessWithKey : config.accessWithoutKey}
          control={
            credential === undefined && isAdmin ? (
              <Loader2 size={14} className="animate-spin text-zinc-500" />
            ) : (
              <StatusPill tone={hasKey ? "accent" : "muted"}>
                {hasKey ? config.pillWithKey : isAdmin ? config.pillWithoutKey : "см. администратора"}
              </StatusPill>
            )
          }
        />
        <SettingCard
          id={`${feed}-availability`}
          icon={<Database size={18} />}
          title="Состояние"
          description={
            source ? (
              <>
                Последний сбор: {formatDateTime(source.last_polled_at)}
                {collected !== null && <> · в канале {collected.toLocaleString("ru-RU")} закупок</>}
                {isPolling && job?.message && <span className="block text-indigo-300">{job.message}</span>}
              </>
            ) : (
              `Источник не найден — выполните миграции базы (${config.migration}).`
            )
          }
          control={
            source && (
              <>
                <AvailabilityPill source={source} />
                {isAdmin && (
                  <button
                    onClick={() => void startPoll()}
                    disabled={isPolling}
                    className={secondaryButtonClass}
                  >
                    <RefreshCw size={13} className={isPolling ? "animate-spin" : ""} />
                    {isPolling ? "Собираю…" : "Собрать сейчас"}
                  </button>
                )}
              </>
            )
          }
        />
      </SettingsGroup>

      {isAdmin && (
        <SettingsGroup id={`${feed}-key`} label={config.keyName} hint={config.keyHint}>
          <SettingCard
            icon={<KeyRound size={18} />}
            active={hasKey}
            title={credential ? "Задан" : "Не задан"}
            description={
              credential
                ? `${credential.password_masked} · изменён ${formatDateTime(credential.updated_at)}${
                    credential.updated_by ? ` · ${credential.updated_by}` : ""
                  }`
                : config.accessWithoutKey
            }
            control={
              credential && (
                <button onClick={() => void removeKey()} className={dangerButtonClass}>
                  <Trash2 size={13} />
                  Удалить
                </button>
              )
            }
          >
            <div className="flex flex-wrap items-end gap-3">
              <label className="block min-w-[260px] flex-1 text-xs text-zinc-400">
                {credential ? `Новый: ${config.keyName.toLowerCase()}` : config.keyName}
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={config.keyPlaceholder}
                  autoComplete="new-password"
                  className={settingsInputClass}
                />
              </label>
              <button
                onClick={() => void saveKey()}
                disabled={!apiKey.trim() || isSaving || !source}
                className={primaryButtonClass}
              >
                {isSaving && <Loader2 size={14} className="animate-spin" />}
                Сохранить
              </button>
            </div>
          </SettingCard>
        </SettingsGroup>
      )}

    </SettingsPanel>
  );
}
