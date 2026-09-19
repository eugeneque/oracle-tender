import { useEffect, useState } from "react";
import { CheckCircle2, Clock, PlayCircle } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { AiProviderKey, RouterAiSettings } from "../../api/types";
import {
  chooseDefaultAiProvider,
  chooseMyAiProvider,
  refreshAiProvider,
  useAiProvider,
} from "../../hooks/useAiProvider";
import { formatDateTime } from "../../utils/format";
import { AI_PROVIDERS } from "../../utils/aiProviders";
import { AiProviderIcon } from "../AiProviderIcon";

/**
 * Переключатель модели ИИ и настройки Claude через RouterAI (18.09.2026).
 *
 * Выбор модели — персональный (просьба заказчика): один пользователь работает с Claude,
 * другой с YandexGPT, и смена у одного никого больше не касается. Тот же переключатель в
 * двух ролях (`scope`):
 * - `me` — своя модель (учётная запись, карточка тендера): все запросы этого пользователя
 *   и запущенные им фоновые задачи идут через неё;
 * - `default` — системная модель по умолчанию (страница «Интеграции», администратор): для
 *   задач по расписанию и пользователей, которые ничего не выбирали.
 *
 * Сервер не даст включить модель без ключа (400 с подсказкой), поэтому порядок действий на
 * экране — сначала карточка с ключом, потом переключатель, — и ошибка показывается прямо
 * под ним.
 */

export function AiProviderSwitcher({ scope }: { scope: "me" | "default" }) {
  const status = useAiProvider();
  const [isSwitching, setIsSwitching] = useState<AiProviderKey | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const current = scope === "default" ? status?.default_provider : status?.active_provider;

  const run = async (key: AiProviderKey | "reset", action: () => Promise<unknown>) => {
    setError(null);
    setIsSwitching(key);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось переключить модель");
    } finally {
      setIsSwitching(null);
    }
  };

  const handleSwitch = (provider: AiProviderKey) => {
    if (!status || isSwitching) return;
    if (scope === "default") {
      if (provider === status.default_provider) return;
      void run(provider, () => chooseDefaultAiProvider(provider));
      return;
    }
    // Свою модель можно «выбрать» и совпадающую с системной — тогда она закрепляется за
    // пользователем и не поменяется, если администратор сменит умолчание.
    if (provider === status.active_provider && status.source === "user") return;
    void run(provider, () => chooseMyAiProvider(provider));
  };

  const caption = (() => {
    if (!status) return null;
    if (scope === "default") {
      return `Действует для задач по расписанию и пользователей без собственного выбора. Свою модель каждый выбирает в учётной записи или прямо в карточке тендера.`;
    }
    if (status.source === "user") {
      return `Ваши запросы к ИИ идут через ${status.label}${status.model ? ` (${status.model})` : ""}. Другие пользователи это не затрагивает.`;
    }
    return `Вы ничего не выбирали — действует системная модель по умолчанию (${status.default_label}). Выберите свою, если хотите работать иначе.`;
  })();

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-zinc-400">
          {scope === "default" ? "Модель по умолчанию" : "Личная модель"}
        </span>
        <div
          role="radiogroup"
          aria-label={scope === "default" ? "Модель ИИ по умолчанию" : "Личная модель ИИ"}
          className="inline-flex rounded-lg border border-white/10 bg-black/20 p-1"
        >
          {AI_PROVIDERS.map((item) => {
            const isActive = current === item.key;
            const isConfigured = status?.configured_providers.includes(item.key) ?? false;
            const isClaude = item.key === "claude";
            const activeClasses = isClaude
              ? "bg-orange-500/20 text-orange-100 shadow-[inset_0_0_0_1px_rgba(251,146,60,0.45)]"
              : "bg-indigo-500/20 text-indigo-100 shadow-[inset_0_0_0_1px_rgba(129,140,248,0.45)]";
            return (
              <button
                key={item.key}
                type="button"
                role="radio"
                aria-checked={isActive}
                disabled={!status || isSwitching !== null || !isConfigured}
                title={isConfigured ? undefined : `${item.label}: учётные данные не заполнены`}
                onClick={() => handleSwitch(item.key)}
                className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-default ${
                  isActive
                    ? activeClasses
                    : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200 disabled:opacity-40 disabled:hover:bg-transparent"
                }`}
              >
                <AiProviderIcon provider={item.key} size={16} />
                <span className="flex flex-col items-start leading-tight">
                  <span>{isSwitching === item.key ? "Переключаю…" : item.label}</span>
                  <span className="text-[10px] font-normal opacity-70">
                    {isConfigured ? item.hint : "не настроено"}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
        {scope === "me" && status?.source === "user" && (
          <button
            type="button"
            disabled={isSwitching !== null}
            onClick={() => void run("reset", () => chooseMyAiProvider(null))}
            className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline disabled:opacity-50"
          >
            {isSwitching === "reset" ? "Сбрасываю…" : `Как в системе (${status.default_label})`}
          </button>
        )}
      </div>
      {caption && <p className="mt-2 text-xs text-zinc-500">{caption}</p>}
      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}
    </div>
  );
}

/** Карточка провайдера: подсвечивается, когда он — системная модель по умолчанию, в его же цвете. */
export function ProviderCard({
  provider,
  title,
  isActive,
  isConfigured,
  children,
}: {
  provider: AiProviderKey;
  title: string;
  isActive: boolean;
  isConfigured: boolean;
  children: React.ReactNode;
}) {
  const activeBorder =
    provider === "claude"
      ? "border-orange-400/30 bg-orange-500/[0.04]"
      : "border-indigo-400/30 bg-indigo-500/[0.04]";
  return (
    <div
      className={`rounded-xl border p-4 ${isActive ? activeBorder : "border-white/[0.08] bg-white/[0.02]"}`}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-zinc-200">
        <AiProviderIcon provider={provider} size={18} />
        {title}
        {isConfigured ? (
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
        {isActive && (
          <span
            className={`ml-auto rounded-full px-2.5 py-1 text-[11px] font-medium ${
              provider === "claude"
                ? "bg-orange-500/15 text-orange-200"
                : "bg-indigo-500/15 text-indigo-200"
            }`}
          >
            по умолчанию
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

export function RouterAiCard() {
  const status = useAiProvider();
  const [settings, setSettings] = useState<RouterAiSettings | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [modelInput, setModelInput] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);

  useEffect(() => {
    api
      .get<RouterAiSettings>("/integrations/routerai")
      .then((data) => {
        setSettings(data);
        setModelInput(data.model);
      })
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "Не удалось загрузить настройки RouterAI",
        ),
      );
  }, []);

  const handleSave = async () => {
    setError(null);
    setTestResult(null);
    setIsSaving(true);
    try {
      // Ключ уходит только если введён заново — PATCH-семантика, как у Yandex.
      const payload: { model: string; api_key?: string } = {
        model: modelInput,
      };
      if (apiKeyInput !== "") payload.api_key = apiKeyInput;
      const data = await api.patch<RouterAiSettings>(
        "/integrations/routerai",
        payload,
      );
      setSettings(data);
      setModelInput(data.model);
      setApiKeyInput("");
      // Подпись модели и список настроенных провайдеров в переключателях — из свежего статуса.
      void refreshAiProvider();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось сохранить настройки",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleTest = async () => {
    setError(null);
    setTestResult(null);
    setIsTesting(true);
    try {
      setTestResult(
        await api.post<{ success: boolean; message: string }>(
          "/integrations/routerai/test",
        ),
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось выполнить проверку подключения",
      );
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <ProviderCard
      provider="claude"
      title="Claude · RouterAI"
      isActive={status?.default_provider === "claude"}
      isConfigured={settings?.is_configured ?? false}
    >
      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block text-xs text-zinc-400">
          API-ключ RouterAI
          <input
            type="password"
            value={apiKeyInput}
            onChange={(e) => setApiKeyInput(e.target.value)}
            placeholder={settings?.api_key_masked ?? "sk-…"}
            autoComplete="off"
            className="mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-orange-400/50 focus:outline-none"
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Модель
          <input
            type="text"
            value={modelInput}
            onChange={(e) => setModelInput(e.target.value)}
            placeholder="anthropic/claude-opus-5"
            className="mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-orange-400/50 focus:outline-none"
          />
        </label>
      </div>
      <p className="mt-2 text-xs text-zinc-500">
        {settings ? `API: ${settings.base_url}/chat/completions` : ""}
        {settings?.updated_at
          ? ` · изменено ${formatDateTime(settings.updated_at)}${settings.updated_by ? `, ${settings.updated_by}` : ""}`
          : ""}
      </p>

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={() => void handleSave()}
          disabled={isSaving}
          className="rounded-lg bg-orange-500 px-3.5 py-2 text-xs font-medium text-white hover:bg-orange-400 disabled:opacity-50"
        >
          {isSaving ? "Сохраняю…" : "Сохранить"}
        </button>
        <button
          onClick={() => void handleTest()}
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
  );
}
