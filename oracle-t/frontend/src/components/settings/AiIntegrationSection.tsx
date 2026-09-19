import { useEffect, useState } from "react";
import { BrainCircuit, ChevronDown, PlayCircle } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { YandexAiStudioSettings } from "../../api/types";
import { refreshAiProvider, useAiProvider } from "../../hooks/useAiProvider";
import { formatDateTime } from "../../utils/format";
import { AiProviderSwitcher, ProviderCard, RouterAiCard } from "./AiProviderControls";

/**
 * Блок «Искусственный интеллект» страницы «Интеграции»: переключатель активной модели и
 * учётные данные обоих провайдеров — Yandex AI Studio и Claude через RouterAI.
 *
 * До 18.09.2026 жил внутри `/settings` как раздел «Интеграции»; вынесен на отдельную
 * страницу вместе с Bitrix и почтой — внешние подключения теперь в одном месте, а
 * «Настройки» остались про источники, профиль релевантности и справочники.
 */
export function AiIntegrationSection() {
  const providerStatus = useAiProvider();
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
      void refreshAiProvider();
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
    <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <BrainCircuit size={15} className="text-indigo-400" />
            Искусственный интеллект
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Модели ИИ-модуля (раздел 5.4 ТЗ): YandexGPT или Claude через RouterAI. Каждый пользователь
            выбирает модель сам; здесь — ключи обоих провайдеров и модель по умолчанию. Yandex AI
            Studio также даёт OCR-fallback для сканов (раздел 5.2). Ключи хранятся в БД, без .env.
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

          <AiProviderSwitcher scope="default" />

          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            <ProviderCard
              provider="yandex"
              title="Yandex AI Studio"
              isActive={providerStatus?.default_provider === "yandex"}
              isConfigured={settings?.is_configured ?? false}
            >
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
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
            </ProviderCard>

            <RouterAiCard />
          </div>
        </div>
      )}
    </div>
  );
}
