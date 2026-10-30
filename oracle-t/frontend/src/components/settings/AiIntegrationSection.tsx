import { useEffect, useState } from "react";
import { PlayCircle } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { AiModelLimits, AiProviderKey, YandexAiStudioSettings } from "../../api/types";
import { refreshAiProvider, useAiProvider } from "../../hooks/useAiProvider";
import { formatDateTime } from "../../utils/format";
import { AiModelList, AiProviderSwitcher, ProviderCard, RouterAiCard } from "./AiProviderControls";
import { AutoReviewCard } from "./AutoReviewCard";

/**
 * Вкладка «Модели ИИ» страницы «Интеграции»: модель по умолчанию, доступность моделей и
 * учётные данные провайдеров — Yandex AI Studio и RouterAI (DeepSeek и Qwen).
 *
 * До 18.09.2026 жил внутри `/settings`; с 08.10.2026 — раскладка «список — карточка»: слева
 * все модели с состоянием и выключателем, справа ключи выбранной. Раньше три карточки
 * провайдеров и блок доступности стояли друг под другом, и раздел занимал несколько экранов.
 */
export function AiIntegrationSection() {
  const status = useAiProvider();
  const [picked, setPicked] = useState<AiProviderKey | null>(null);
  // Пока администратор ничего не выбрал, открыта модель по умолчанию — её чаще всего и правят.
  const selected: AiProviderKey = picked ?? status?.default_provider ?? "yandex";

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
        <AiProviderSwitcher scope="default" />
      </div>

      <AutoReviewCard />

      <div className="grid items-start gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <AiModelList selected={selected} onSelect={setPicked} />
        <div className="min-w-0 space-y-6">
          {selected === "yandex" && <YandexCard />}
          {(selected === "deepseek" || selected === "qwen") && <RouterAiCard />}
          <ModelLimitsCard key={selected} provider={selected} />
        </div>
      </div>
    </div>
  );
}

const tokensFormat = new Intl.NumberFormat("ru-RU");

/**
 * Окно контекста и потолок ответа модели (14.10.2026). От них считается, сколько текста
 * уходит в один запрос: куски документации и карточка производителя в матрице. Для модели
 * на своём ускорителе (окно 32 тыс.) потолок ответа надо опустить до 4–6 тыс. — вход и
 * ответ делят одно окно.
 */
function ModelLimitsCard({ provider }: { provider: AiProviderKey }) {
  const [limits, setLimits] = useState<AiModelLimits | null>(null);
  const [contextInput, setContextInput] = useState("");
  const [outputInput, setOutputInput] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const apply = (rows: AiModelLimits[]) => {
    const row = rows.find((item) => item.provider === provider) ?? null;
    setLimits(row);
    if (row) {
      setContextInput(String(row.context_tokens));
      setOutputInput(String(row.max_output_tokens));
    }
  };

  useEffect(() => {
    api
      .get<AiModelLimits[]>("/integrations/ai-limits")
      .then(apply)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить окно контекста"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const save = async (reset: boolean) => {
    setError(null);
    setSaved(false);
    setIsSaving(true);
    try {
      const payload = reset
        ? { context_tokens: null, max_output_tokens: null }
        : { context_tokens: Number(contextInput) || null, max_output_tokens: Number(outputInput) || null };
      apply(await api.put<AiModelLimits[]>(`/integrations/ai-limits/${provider}`, payload));
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить окно контекста");
    } finally {
      setIsSaving(false);
    }
  };

  const inputClass =
    "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm tabular-nums text-zinc-100 focus:border-indigo-500/50 focus:outline-none";

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[15px] font-semibold text-zinc-100">Окно контекста</div>
          <div className="mt-0.5 max-w-xl text-xs leading-relaxed text-zinc-500">
            Сколько токенов модель принимает за запрос и сколько оставлять на ответ. От этого
            зависит, какими кусками режется документация и сколько карточки производителя уходит
            в матрицу соответствия.
          </div>
        </div>
        {limits && (
          <span className="rounded-full bg-white/[0.06] px-2.5 py-1 text-[11px] text-zinc-400">
            {limits.is_default ? "по умолчанию" : "задано вручную"}
          </span>
        )}
      </div>

      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block text-xs text-zinc-400">
          Окно, токенов
          <input
            type="number"
            min={4096}
            value={contextInput}
            onChange={(event) => setContextInput(event.target.value)}
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Потолок ответа, токенов
          <input
            type="number"
            min={256}
            value={outputInput}
            onChange={(event) => setOutputInput(event.target.value)}
            className={inputClass}
          />
        </label>
      </div>
      {limits && (
        <p className="mt-2 text-xs text-zinc-500">
          На текст запроса остаётся ≈ {tokensFormat.format(limits.input_budget_chars)} символов. По умолчанию:
          окно {tokensFormat.format(limits.default_context_tokens)}, ответ{" "}
          {tokensFormat.format(limits.default_max_output_tokens)}.
        </p>
      )}

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={() => void save(false)}
          disabled={isSaving}
          className="rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-snow hover:bg-indigo-400 disabled:opacity-50"
        >
          {isSaving ? "Сохраняю…" : "Сохранить"}
        </button>
        {limits && !limits.is_default && (
          <button
            onClick={() => void save(true)}
            disabled={isSaving}
            className="rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          >
            Вернуть по умолчанию
          </button>
        )}
        {saved && <span className="text-xs text-emerald-400">Сохранено</span>}
      </div>
    </div>
  );
}

/** Yandex AI Studio: каталог и API-ключ. Он же даёт OCR-fallback для сканов (раздел 5.2 ТЗ). */
function YandexCard() {
  const status = useAiProvider();
  const [settings, setSettings] = useState<YandexAiStudioSettings | null>(null);
  const [folderIdInput, setFolderIdInput] = useState("");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  useEffect(() => {
    api
      .get<YandexAiStudioSettings>("/integrations/yandex-ai-studio")
      .then((data) => {
        setSettings(data);
        setFolderIdInput(data.folder_id ?? "");
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить настройки интеграции"),
      );
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
      setTestResult(
        await api.post<{ success: boolean; message: string }>("/integrations/yandex-ai-studio/test"),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить проверку подключения");
    } finally {
      setIsTesting(false);
    }
  };

  const inputClass =
    "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

  return (
    <ProviderCard
      provider="yandex"
      title="Yandex AI Studio"
      subtitle="YandexGPT и распознавание сканов (OCR)"
      isActive={status?.default_provider === "yandex"}
      isConfigured={settings?.is_configured ?? false}
    >
      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block text-xs text-zinc-400">
          Folder ID
          <input
            type="text"
            value={folderIdInput}
            onChange={(e) => setFolderIdInput(e.target.value)}
            placeholder="b1g..."
            className={inputClass}
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
            className={inputClass}
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
          onClick={() => void handleSave()}
          disabled={isSaving}
          className="rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-snow hover:bg-indigo-400 disabled:opacity-50"
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
