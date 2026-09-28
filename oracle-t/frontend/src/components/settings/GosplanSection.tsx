import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Database,
  FileText,
  Gauge,
  KeyRound,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  BackgroundJob,
  SourceCredential,
  TenderStats,
} from "../../api/types";
import { GOSPLAN_SOURCE_KEY } from "../../api/types";
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

/**
 * Вкладка «Госплан» (28.09.2026) — API данных ЕИС отдельным каналом сбора.
 *
 * Бесплатный тариф работает без ключа, но медленно (10 запросов в минуту), поэтому сбор
 * запускается фоновой задачей, как «Синхронизировать» на странице тендеров, а не запросом,
 * который держал бы страницу несколько минут. Ключ платного тарифа хранится как учётка
 * источника «Госплан» (пароль = ключ) — тем же шифрованием, что пароли площадок.
 */
export function GosplanSection({ isAdmin, active }: { isAdmin: boolean; active: boolean }) {
  const { sources, reload } = useSources(active);
  const source = sources?.find((s) => s.key === GOSPLAN_SOURCE_KEY) ?? null;
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
      .get<TenderStats>("/tenders/stats?feed=gosplan")
      .then((stats) => setCollected(stats.total))
      .catch(() => setCollected(null));

  const loadCredential = async () => {
    if (!isAdmin) return;
    try {
      const all = await api.get<SourceCredential[]>("/source-credentials");
      setCredential(all.find((c) => c.source_key === GOSPLAN_SOURCE_KEY) ?? null);
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
      setJob(await api.post<BackgroundJob>("/sources/poll", { source_keys: [GOSPLAN_SOURCE_KEY] }));
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
          label: "Ключ API",
          username: "apikey",
          password: apiKey.trim(),
          notes: "Платный тариф Госплана",
          is_active: true,
        });
      }
      setApiKey("");
      setNotice("Ключ сохранён — следующий сбор пойдёт через платный тариф.");
      await loadCredential();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить ключ");
    } finally {
      setIsSaving(false);
    }
  };

  const removeKey = async () => {
    if (!credential) return;
    if (!window.confirm("Удалить ключ API? Сбор вернётся на бесплатный тариф.")) return;
    try {
      await api.delete(`/source-credentials/${credential.id}`);
      setNotice("Ключ удалён — сбор идёт через бесплатный тариф.");
      await loadCredential();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить ключ");
    }
  };

  const isPaid = Boolean(credential && credential.is_active);

  return (
    <SettingsPanel
      title="Госплан"
      description={
        <>
          API данных ЕИС: закупки по 44-ФЗ и 223-ФЗ приходят готовыми полями — номер, объект,
          НМЦК, срок подачи, ОКПД2 и регион. Это отдельный канал: на странице тендеров его
          показывает режим «Госплан», и его закупки не смешиваются с собранными с площадок.
        </>
      }
      actions={
        <Link to="/tenders?feed=gosplan" className={secondaryButtonClass}>
          Тендеры Госплана
          <ArrowRight size={13} />
        </Link>
      }
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
      {notice && <SettingsNotice tone="success">{notice}</SettingsNotice>}

      <SettingsGroup id="gosplan-status" label="Подключение">
        <SettingCard
          id="gosplan-tariff"
          active={isPaid}
          icon={<Gauge size={18} />}
          title="Тариф"
          description={
            isPaid
              ? "Платный: запросы идут на v2.gosplan.info с ключом, без ограничения скорости."
              : "Бесплатный: v2test.gosplan.info без ключа, 10 запросов в минуту — сбор занимает несколько минут."
          }
          control={
            credential === undefined && isAdmin ? (
              <Loader2 size={14} className="animate-spin text-zinc-500" />
            ) : (
              <StatusPill tone={isPaid ? "accent" : "muted"}>
                {isPaid ? "платный" : isAdmin ? "бесплатный" : "см. администратора"}
              </StatusPill>
            )
          }
        />
        <SettingCard
          id="gosplan-availability"
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
              "Источник не найден — выполните миграции базы (0058)."
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
        <SettingsGroup
          id="gosplan-key"
          label="Ключ API"
          hint="Нужен только для платного тарифа. Хранится зашифрованным, как пароли площадок, и обратно не показывается."
        >
          <SettingCard
            icon={<KeyRound size={18} />}
            active={isPaid}
            title={credential ? "Ключ задан" : "Ключ не задан"}
            description={
              credential
                ? `${credential.password_masked} · изменён ${formatDateTime(credential.updated_at)}${
                    credential.updated_by ? ` · ${credential.updated_by}` : ""
                  }`
                : "Без ключа сбор идёт через бесплатный тариф."
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
                {credential ? "Новый ключ" : "Ключ платного тарифа"}
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="ключ из личного кабинета gosplan.info"
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
                Сохранить ключ
              </button>
            </div>
          </SettingCard>
        </SettingsGroup>
      )}

      <SettingsGroup id="gosplan-search" label="Как идёт сбор">
        <SettingCard
          icon={<Search size={18} />}
          title="Что запрашивается"
          description="Код ОКПД2 26.51.63 (приборы учёта) и слова «счетчик», «АСКУЭ», «АИИС» — по 44-ФЗ и 223-ФЗ. Поиск Госплана соединяет слова фразы через «ИЛИ», поэтому фразы профиля ему не передаются: лишнее отсеивает профиль релевантности при сохранении."
        />
        <SettingCard
          icon={<FileText size={18} />}
          title="Документы и карточка"
          description="Файлов у Госплана нет: документация и карточка закупки подтягиваются из ЕИС по тому же реестровому номеру."
        />
        <SettingCard
          icon={<Sparkles size={18} />}
          title="Проверка моделью"
          description="При сборе закупки Госплана моделью не проверяются — канал почти повторяет ЕИС, и проверка удвоила бы расход. Проверить накопленное можно на вкладке «Профиль отбора»."
        />
      </SettingsGroup>
    </SettingsPanel>
  );
}
