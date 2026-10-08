import { useEffect, useState } from "react";
import { CheckCircle2, Clock, PlayCircle } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { AiProviderKey, GigaChatSettings, RouterAiSettings } from "../../api/types";
import {
  chooseDefaultAiProvider,
  chooseMyAiProvider,
  refreshAiProvider,
  setAiProviderEnabled,
  useAiProvider,
} from "../../hooks/useAiProvider";
import { formatDateTime } from "../../utils/format";
import { AI_PROVIDER_ACCENT, AI_PROVIDERS } from "../../utils/aiProviders";
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
    if (scope === "default" && status.disabled_providers?.includes(status.default_provider)) {
      return `${status.default_label} отключена администратором — задачи по расписанию и пользователи без своего выбора идут через другую включённую модель, пока её не включат.`;
    }
    const requested = status.requested_provider;
    if (
      scope === "me" &&
      requested &&
      requested !== status.active_provider &&
      status.disabled_providers?.includes(requested)
    ) {
      const label = AI_PROVIDERS.find((item) => item.key === requested)?.label ?? requested;
      return `${label} отключена администратором — запросы идут через ${status.label}. Когда её включат, ваш выбор вернётся сам.`;
    }
    // 08.10.2026: постоянные пояснения убраны — подпись остаётся только для предупреждений.
    return null;
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
            const isDisabled = status?.disabled_providers?.includes(item.key) ?? false;
            const activeClasses = AI_PROVIDER_ACCENT[item.key].segment;
            return (
              <button
                key={item.key}
                type="button"
                role="radio"
                aria-checked={isActive}
                disabled={!status || isSwitching !== null || !isConfigured || isDisabled}
                title={
                  isDisabled
                    ? `${item.label} отключена администратором`
                    : isConfigured
                      ? undefined
                      : `${item.label}: учётные данные не заполнены`
                }
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
                    {isDisabled ? "отключена" : isConfigured ? item.hint : "не настроено"}
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

/**
 * Карточка провайдера — правая половина раздела «Модели ИИ» (редизайн 08.10.2026): шапка со
 * знаком, подписью и статусом, под ней форма ключей. Подсвечивается в цвете провайдера, когда
 * он — системная модель по умолчанию. `providers` — знаки в шапке: у RouterAI их два, один
 * ключ обслуживает и Claude, и DeepSeek.
 */
export function ProviderCard({
  provider,
  providers,
  title,
  subtitle,
  isActive,
  isConfigured,
  children,
}: {
  provider: AiProviderKey;
  providers?: AiProviderKey[];
  title: string;
  subtitle?: string;
  isActive: boolean;
  isConfigured: boolean;
  children: React.ReactNode;
}) {
  const activeBorder = AI_PROVIDER_ACCENT[provider].card;
  const icons = providers ?? [provider];
  return (
    <div
      className={`rounded-2xl border p-5 ${isActive ? activeBorder : "border-white/[0.08] bg-white/[0.03]"}`}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-11 shrink-0 items-center gap-1.5 rounded-xl bg-white/[0.05] px-2.5">
          {icons.map((key) => (
            <AiProviderIcon key={key} provider={key} size={22} />
          ))}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold text-zinc-100">{title}</div>
          {subtitle && <div className="mt-0.5 text-xs text-zinc-500">{subtitle}</div>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isActive && (
            <span
              className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${AI_PROVIDER_ACCENT[provider].pill}`}
            >
              по умолчанию
            </span>
          )}
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
        </div>
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
  const [deepseekModelInput, setDeepseekModelInput] = useState("");
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
        setDeepseekModelInput(data.deepseek_model);
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
      const payload: { model: string; deepseek_model: string; api_key?: string } = {
        model: modelInput,
        deepseek_model: deepseekModelInput,
      };
      if (apiKeyInput !== "") payload.api_key = apiKeyInput;
      const data = await api.patch<RouterAiSettings>(
        "/integrations/routerai",
        payload,
      );
      setSettings(data);
      setModelInput(data.model);
      setDeepseekModelInput(data.deepseek_model);
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

  // Один ключ RouterAI обслуживает две модели — Claude и DeepSeek (28.09.2026). Карточка
  // подсвечивается цветом той из них, что выбрана по умолчанию.
  const defaultRouterAi =
    status?.default_provider === "claude" || status?.default_provider === "deepseek"
      ? status.default_provider
      : null;

  return (
    <ProviderCard
      provider={defaultRouterAi ?? "claude"}
      providers={["claude", "deepseek"]}
      title="RouterAI · Claude и DeepSeek"
      subtitle="Один ключ RouterAI обслуживает обе модели"
      isActive={defaultRouterAi !== null}
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
          Модель Claude
          <input
            type="text"
            value={modelInput}
            onChange={(e) => setModelInput(e.target.value)}
            placeholder="anthropic/claude-opus-5"
            className="mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-orange-400/50 focus:outline-none"
          />
        </label>
        <label className="block text-xs text-zinc-400 sm:col-start-2">
          Модель DeepSeek
          <input
            type="text"
            value={deepseekModelInput}
            onChange={(e) => setDeepseekModelInput(e.target.value)}
            placeholder="deepseek/deepseek-v4-pro-0813"
            className="mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-blue-400/50 focus:outline-none"
          />
        </label>
      </div>
      <p className="mt-2 text-xs text-zinc-500">
        {settings?.updated_at
          ? `Изменено ${formatDateTime(settings.updated_at)}${settings.updated_by ? `, ${settings.updated_by}` : ""}`
          : ""}
      </p>

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={() => void handleSave()}
          disabled={isSaving}
          className="rounded-lg bg-orange-500 px-3.5 py-2 text-xs font-medium text-snow hover:bg-orange-400 disabled:opacity-50"
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
          <p className="whitespace-pre-line">{testResult.message}</p>
        </div>
      )}
    </ProviderCard>
  );
}

/** Версии API GigaChat — по договору со Сбером (физлицо, юрлицо по предоплате, по постоплате). */
const GIGACHAT_SCOPES: { value: string; label: string }[] = [
  { value: "GIGACHAT_API_B2B", label: "Юрлицо, предоплата (B2B)" },
  { value: "GIGACHAT_API_CORP", label: "Юрлицо, постоплата (CORP)" },
  { value: "GIGACHAT_API_PERS", label: "Физлицо (PERS)" },
];

/**
 * GigaChat (Сбер, 08.10.2026) — задел: ключ у заказчика ещё оформляется. Карточка уже здесь,
 * чтобы после получения ключа осталось вписать его и нажать «Проверить подключение».
 * Ключ авторизации — из личного кабинета Сбера (Base64 от Client ID:Client Secret).
 */
export function GigaChatCard() {
  const status = useAiProvider();
  const [settings, setSettings] = useState<GigaChatSettings | null>(null);
  const [authKeyInput, setAuthKeyInput] = useState("");
  const [scopeInput, setScopeInput] = useState("GIGACHAT_API_B2B");
  const [modelInput, setModelInput] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const apply = (data: GigaChatSettings) => {
    setSettings(data);
    setScopeInput(data.scope);
    setModelInput(data.model);
  };

  useEffect(() => {
    api
      .get<GigaChatSettings>("/integrations/gigachat")
      .then(apply)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить настройки GigaChat"),
      );
  }, []);

  const handleSave = async () => {
    setError(null);
    setTestResult(null);
    setIsSaving(true);
    try {
      // Ключ уходит только если введён заново — PATCH-семантика, как у RouterAI.
      const payload: { scope: string; model: string; auth_key?: string } = {
        scope: scopeInput,
        model: modelInput,
      };
      if (authKeyInput !== "") payload.auth_key = authKeyInput;
      apply(await api.patch<GigaChatSettings>("/integrations/gigachat", payload));
      setAuthKeyInput("");
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
      setTestResult(
        await api.post<{ success: boolean; message: string }>("/integrations/gigachat/test"),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить проверку подключения");
    } finally {
      setIsTesting(false);
    }
  };

  const inputClass =
    "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-400/50 focus:outline-none";

  return (
    <ProviderCard
      provider="gigachat"
      title="GigaChat · Сбер"
      subtitle="Ключ авторизации из личного кабинета Сбера"
      isActive={status?.default_provider === "gigachat"}
      isConfigured={settings?.is_configured ?? false}
    >
      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block text-xs text-zinc-400">
          Ключ авторизации
          <input
            type="password"
            value={authKeyInput}
            onChange={(e) => setAuthKeyInput(e.target.value)}
            placeholder={settings?.auth_key_masked ?? "Base64 из личного кабинета"}
            autoComplete="off"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Версия API
          <select
            value={scopeInput}
            onChange={(e) => setScopeInput(e.target.value)}
            className={inputClass}
          >
            {GIGACHAT_SCOPES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-zinc-400 sm:col-start-2">
          Модель
          <input
            type="text"
            value={modelInput}
            onChange={(e) => setModelInput(e.target.value)}
            placeholder="GigaChat-2-Max"
            className={inputClass}
          />
        </label>
      </div>
      <p className="mt-2 text-xs text-zinc-500">
        {settings?.updated_at
          ? `Изменено ${formatDateTime(settings.updated_at)}${settings.updated_by ? `, ${settings.updated_by}` : ""}`
          : ""}
      </p>

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={() => void handleSave()}
          disabled={isSaving}
          className="rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-medium text-snow hover:bg-emerald-500 disabled:opacity-50"
        >
          {isSaving ? "Сохраняю…" : "Сохранить"}
        </button>
        <button
          onClick={() => void handleTest()}
          disabled={isTesting || !settings?.is_configured}
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
          <p className="whitespace-pre-line">{testResult.message}</p>
        </div>
      )}
    </ProviderCard>
  );
}

/**
 * Отключение моделей администратором (29.09.2026) — «намертво»: в отключённую модель не
 * уходит ни одного запроса ни от кого, разборы, начатые через неё, останавливаются на
 * следующем обращении к модели (их ответы не используются), а новые идут через замену —
 * модель по умолчанию или первую включённую. Личный выбор пользователей сохраняется: после
 * включения каждый вернётся к своей модели.
 *
 * С 08.10.2026 это ещё и навигация раздела «Модели ИИ»: строка выбирает, чьи ключи показать
 * справа (Claude и DeepSeek открывают общую карточку RouterAI), переключатель в строке —
 * доступность. Раньше доступность была отдельным блоком, а все карточки стояли друг под
 * другом, и страница росла вниз с каждым новым провайдером.
 */
export function AiModelList({
  selected,
  onSelect,
}: {
  selected: AiProviderKey;
  onSelect: (provider: AiProviderKey) => void;
}) {
  const status = useAiProvider();
  const [busy, setBusy] = useState<AiProviderKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async (provider: AiProviderKey, enable: boolean) => {
    const label = AI_PROVIDERS.find((item) => item.key === provider)?.label ?? provider;
    if (
      !enable &&
      !window.confirm(
        `Отключить ${label} для всех?\n\nРазборы, которые сейчас идут через неё, будут остановлены, новые запросы в неё отправляться не будут.`,
      )
    ) {
      return;
    }
    setError(null);
    setBusy(provider);
    try {
      await setAiProviderEnabled(provider, enable);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить доступность модели");
    } finally {
      setBusy(null);
    }
  };

  const disabled = status?.disabled_providers ?? [];
  const allOff = status !== null && AI_PROVIDERS.every((item) => disabled.includes(item.key));

  return (
    <div>
      <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
        Модели
      </div>
      <div className="space-y-1.5" role="listbox" aria-label="Модели ИИ">
        {AI_PROVIDERS.map((item) => {
          const isOff = disabled.includes(item.key);
          const isConfigured = status?.configured_providers.includes(item.key) ?? false;
          const isDefault = status?.default_provider === item.key;
          const isSelected = selected === item.key;
          const info = status?.disabled_info?.[item.key];
          const state = isOff
            ? `отключена${info?.at ? ` ${formatDateTime(info.at)}` : ""}${info?.by ? `, ${info.by}` : ""}`
            : isConfigured
              ? "настроено"
              : "не настроено";
          return (
            <div
              key={item.key}
              className={`flex items-center gap-3 rounded-xl border pr-3 transition-colors ${
                isSelected
                  ? "border-white/[0.14] bg-white/[0.07]"
                  : "border-transparent hover:bg-white/[0.04]"
              }`}
            >
              <button
                type="button"
                role="option"
                aria-selected={isSelected}
                onClick={() => onSelect(item.key)}
                className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-2.5 text-left"
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/[0.05]">
                  <AiProviderIcon
                    provider={item.key}
                    size={22}
                    className={isOff ? "opacity-40 grayscale" : undefined}
                  />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span
                      className={`truncate text-sm font-medium ${isOff ? "text-zinc-500 line-through" : "text-zinc-100"}`}
                    >
                      {item.label}
                    </span>
                    {isDefault && (
                      <span
                        className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${AI_PROVIDER_ACCENT[item.key].pill}`}
                      >
                        по умолчанию
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 truncate text-[11px] text-zinc-500">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        isOff ? "bg-zinc-600" : isConfigured ? "bg-emerald-400" : "bg-amber-400/70"
                      }`}
                    />
                    <span className="truncate">
                      {item.hint} · {state}
                    </span>
                  </span>
                </span>
              </button>
              <button
                type="button"
                role="switch"
                aria-checked={!isOff}
                aria-label={`${item.label}: ${isOff ? "включить" : "отключить"}`}
                title={isOff ? "Включить модель" : "Отключить модель для всех"}
                disabled={!status || busy !== null}
                onClick={() => void toggle(item.key, isOff)}
                className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
                  isOff ? "bg-zinc-700" : "bg-emerald-500"
                }`}
              >
                <span
                  className={`absolute top-0.5 h-4 w-4 rounded-full bg-snow transition-all ${
                    isOff ? "left-0.5" : "left-[18px]"
                  }`}
                />
              </button>
            </div>
          );
        })}
      </div>
      {allOff && (
        <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          Все модели отключены — разбор закупок и заключения ИИ недоступны.
        </div>
      )}
      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>
      )}
    </div>
  );
}
