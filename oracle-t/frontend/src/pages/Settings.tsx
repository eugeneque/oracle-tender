import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  PlayCircle,
  Plug,
  Settings as SettingsIcon,
  Wifi,
  WifiOff,
} from "lucide-react";

import { ApiError, api } from "../api/client";
import type { Source, SourcePollResult, YandexAiStudioSettings } from "../api/types";
import { MANUAL_SOURCE_TYPE } from "../api/types";
import { AppShell } from "../components/AppShell";
import { BitrixSection } from "../components/settings/BitrixSection";
import { CredentialsSection } from "../components/settings/CredentialsSection";
import { LogsSection } from "../components/settings/LogsSection";
import { NotificationsSection } from "../components/settings/NotificationsSection";
import { RegionResponsiblesSection } from "../components/settings/RegionResponsiblesSection";
import { RelevanceProfileSection } from "../components/settings/RelevanceProfileSection";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../context/useAuth";

const AVAILABILITY_REFRESH_MS = 20_000;

function formatDateTime(value: string | null): string {
  if (!value) return "никогда";
  return new Date(value).toLocaleString("ru-RU");
}

function AdapterStatusBadge({ source }: { source: Source }) {
  if (source.adapter_status === "implemented") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-400">
        <CheckCircle2 size={13} />
        подключён
      </span>
    );
  }
  if (source.adapter_status === "blocked") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-400">
        <AlertTriangle size={13} />
        заблокирован
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-400">
      <Clock size={13} />
      в очереди
    </span>
  );
}

function AvailabilityBadge({ source }: { source: Source }) {
  if (source.availability_status === "available") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-400"
        title={`Проверено: ${formatDateTime(source.availability_checked_at)}`}
      >
        <Wifi size={13} />
        Доступно
      </span>
    );
  }
  if (source.availability_status === "unavailable") {
    return (
      <div title={`Проверено: ${formatDateTime(source.availability_checked_at)}`}>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-400">
          <WifiOff size={13} />
          Недоступно
        </span>
        {source.availability_error && (
          <div className="mt-1 max-w-[220px] text-xs text-zinc-500">
            {source.availability_error}
          </div>
        )}
      </div>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-500">
      <Clock size={13} />
      проверяется…
    </span>
  );
}

function IntegrationsSection() {
  const [settings, setSettings] = useState<YandexAiStudioSettings | null>(null);
  const [isExpanded, setIsExpanded] = useState(true);
  const [folderIdInput, setFolderIdInput] = useState("");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const load = async () => {
    try {
      const data = await api.get<YandexAiStudioSettings>("/integrations/yandex-ai-studio");
      setSettings(data);
      setFolderIdInput(data.folder_id ?? "");
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить настройки интеграции");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleSave = async () => {
    setError(null);
    setTestResult(null);
    setIsSaving(true);
    try {
      // Ключ отправляем, только если админ реально ввёл новое значение — иначе PATCH не
      // трогает уже сохранённое (см. YandexAiStudioSettingsUpdate на бэкенде).
      const payload: { folder_id: string; api_key?: string } = { folder_id: folderIdInput };
      if (apiKeyInput !== "") {
        payload.api_key = apiKeyInput;
      }
      const data = await api.patch<YandexAiStudioSettings>("/integrations/yandex-ai-studio", payload);
      setSettings(data);
      setApiKeyInput("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить настройки");
    } finally {
      setIsSaving(false);
    }
  };

  const handleTest = async () => {
    setError(null);
    setTestResult(null);
    setIsTesting(true);
    try {
      const result = await api.post<{ success: boolean; message: string }>(
        "/integrations/yandex-ai-studio/test"
      );
      setTestResult(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить проверку подключения");
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <div className="mt-6 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">Интеграции</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Yandex AI Studio — ИИ-модуль (раздел 5.4 ТЗ) и OCR-fallback для сканов (раздел 5.2).
            Ключ хранится в БД, обновляется здесь, без правки .env на сервере.
          </p>
        </div>
        <ChevronDown
          size={18}
          className={`shrink-0 text-zinc-500 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {isExpanded && (
        <div className="border-t border-white/[0.08] px-5 py-4">
          {error && (
            <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
              {error}
            </div>
          )}

          <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
            <Plug size={15} className="text-indigo-400" />
            Yandex AI Studio
            {settings?.is_configured ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-400">
                <CheckCircle2 size={13} />
                настроено
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-400">
                <Clock size={13} />
                не настроено
              </span>
            )}
          </div>

          <div className="mt-4 grid max-w-xl gap-4 sm:grid-cols-2">
            <label className="block text-xs text-zinc-400">
              Folder ID
              <input
                type="text"
                value={folderIdInput}
                onChange={(e) => setFolderIdInput(e.target.value)}
                placeholder="b1g..."
                className="mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
              />
            </label>
            <label className="block text-xs text-zinc-400">
              API-ключ
              <input
                type="password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder={settings?.api_key_masked ?? "не задан"}
                autoComplete="off"
                className="mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
              />
            </label>
          </div>
          {settings?.updated_at && (
            <p className="mt-2 text-xs text-zinc-500">
              Изменено {formatDateTime(settings.updated_at)}
              {settings.updated_by ? ` · ${settings.updated_by}` : ""}
            </p>
          )}

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleSave}
              disabled={isSaving}
              className="rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              {isSaving ? "Сохраняю…" : "Сохранить"}
            </button>
            <button
              onClick={handleTest}
              disabled={isTesting}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
            >
              <PlayCircle size={13} />
              {isTesting ? "Проверяю…" : "Проверить подключение"}
            </button>
          </div>

          {testResult && (
            <div
              className={`mt-4 rounded-lg border px-4 py-2.5 text-sm ${
                testResult.success
                  ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
                  : "border-red-500/20 bg-red-500/10 text-red-400"
              }`}
            >
              {testResult.message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function SettingsPage() {
  const { user } = useAuth();
  const [sources, setSources] = useState<Source[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pollingKey, setPollingKey] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SourcePollResult | null>(null);
  const [isExpanded, setIsExpanded] = useState(true);

  const loadSources = async () => {
    try {
      // Источник ручных заявок — не площадка: ни адреса, ни опроса, ни доступности.
      setSources(
        (await api.get<Source[]>("/sources")).filter((s) => s.type !== MANUAL_SOURCE_TYPE),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить источники");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadSources();
  }, []);

  // Пинг доступности идёт на бэкенде раз в минуту (см. app/core/scheduler.py) — пока раздел
  // развёрнут, подтягиваем свежий статус, не дожидаясь ручного обновления страницы.
  useEffect(() => {
    if (!isExpanded) return;
    const interval = setInterval(() => void loadSources(), AVAILABILITY_REFRESH_MS);
    return () => clearInterval(interval);
  }, [isExpanded]);

  const handlePoll = async (source: Source) => {
    setError(null);
    setPollingKey(source.key);
    try {
      const result = await api.post<SourcePollResult>(`/sources/${source.id}/poll`);
      setLastResult(result);
      await loadSources();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось запустить опрос источника");
    } finally {
      setPollingKey(null);
    }
  };

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-8 py-8">
        <PageHeader
          breadcrumb={["ORACLE-T", "Настройки"]}
          title="Настройки"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <SettingsIcon size={18} />
            </span>
          }
        />

        <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
          <button
            onClick={() => setIsExpanded((v) => !v)}
            className="flex w-full items-center justify-between px-5 py-4 text-left"
          >
            <div>
              <h2 className="text-sm font-semibold text-zinc-100">Источники тендеров</h2>
              <p className="mt-0.5 text-xs text-zinc-500">
                12 источников из раздела 4.1 ТЗ (включая ЕИС) · опрос по расписанию 2 раза в день ·
                доступность площадок проверяется автоматически раз в минуту
              </p>
            </div>
            <ChevronDown
              size={18}
              className={`shrink-0 text-zinc-500 transition-transform ${isExpanded ? "rotate-180" : ""}`}
            />
          </button>

          {isExpanded && (
            <div className="border-t border-white/[0.08]">
              {error && (
                <div className="m-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
                  {error}
                </div>
              )}

              {lastResult && (
                <div className="mx-4 mt-4 rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-4 py-2.5 text-sm text-indigo-300">
                  Источник «{lastResult.source_key}»: найдено {lastResult.found}, создано{" "}
                  {lastResult.created}, обновлено {lastResult.updated}, ошибок{" "}
                  {lastResult.errors}
                </div>
              )}

              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-white/[0.08] text-zinc-500">
                    <tr>
                      <th className="px-5 py-3 font-medium">Источник</th>
                      <th className="px-5 py-3 font-medium">Доступ</th>
                      <th className="px-5 py-3 font-medium">Адаптер</th>
                      <th className="px-5 py-3 font-medium">Доступность</th>
                      <th className="px-5 py-3 font-medium">Последний опрос</th>
                      {user?.role === "admin" && (
                        <th className="px-5 py-3 font-medium">Действие</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {isLoading ? (
                      <tr>
                        <td className="px-5 py-4 text-zinc-500" colSpan={6}>
                          Загрузка…
                        </td>
                      </tr>
                    ) : (
                      sources.map((source) => (
                        <tr key={source.id} className="border-t border-white/[0.06] align-top">
                          <td className="px-5 py-3">
                            <div className="text-zinc-100">{source.name}</div>
                            <a
                              href={source.url}
                              target="_blank"
                              rel="noreferrer"
                              className="truncate text-xs text-indigo-400 hover:underline"
                            >
                              {source.url}
                            </a>
                            {source.note && (
                              <div className="mt-1 max-w-md text-xs text-zinc-500">
                                {source.note}
                              </div>
                            )}
                          </td>
                          <td className="px-5 py-3 text-zinc-400">
                            {source.status === "active" ? "активен" : source.status}
                          </td>
                          <td className="px-5 py-3">
                            <AdapterStatusBadge source={source} />
                          </td>
                          <td className="px-5 py-3">
                            <AvailabilityBadge source={source} />
                          </td>
                          <td className="px-5 py-3 text-zinc-400">
                            {formatDateTime(source.last_polled_at)}
                          </td>
                          {user?.role === "admin" && (
                            <td className="px-5 py-3">
                              <button
                                onClick={() => handlePoll(source)}
                                disabled={pollingKey === source.key}
                                className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                              >
                                <PlayCircle size={13} />
                                {pollingKey === source.key ? "Опрашиваю…" : "Опросить сейчас"}
                              </button>
                            </td>
                          )}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {user?.role === "admin" && <IntegrationsSection />}
        {user?.role === "admin" && <CredentialsSection />}
        <RelevanceProfileSection isAdmin={user?.role === "admin"} />
        {user?.role === "admin" && <RegionResponsiblesSection />}
        {/* Журнал операций и журнал уведомлений открыты всем пользователям (свёрнуты по
            умолчанию); настройки почтового ящика внутри раздела «Уведомления» видит
            только администратор. */}
        <NotificationsSection isAdmin={user?.role === "admin"} />
        {/* Выгрузка лидов доступна всем — это отчёт; ключи доступа выпускает администратор. */}
        <BitrixSection isAdmin={user?.role === "admin"} />
        <LogsSection />
      </div>
    </AppShell>
  );
}
