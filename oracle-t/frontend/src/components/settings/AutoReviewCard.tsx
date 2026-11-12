import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import type { AiProviderKey, AutoReviewStatus } from "../../api/types";
import { AiProviderIcon } from "../AiProviderIcon";
import { StatusPill, Toggle } from "./ui";

const REFRESH_MS = 15_000;
const countFormat = new Intl.NumberFormat("ru-RU");
const PROVIDERS: { key: AiProviderKey; label: string }[] = [
  { key: "qwen", label: "Qwen (RouterAI)" },
  { key: "deepseek", label: "DeepSeek (RouterAI)" },
  { key: "yandex", label: "YandexGPT" },
];

/**
 * Автоматический разбор (30.10.2026): открытые релевантные закупки без заключения сами
 * встают на полный разбор — по одной, ближайший срок подачи первым. Задумывался под
 * бесплатный Яндекс; с 30.10.2026 YandexGPT платная, поэтому с 12.11.2026 модель выбирает
 * администратор, а число закупок в сутки ограничено (раздел 104 ARCHITECTURE.md). Личный
 * выбор и модель по умолчанию на авторазбор не влияют.
 */
export function AutoReviewCard() {
  const [state, setState] = useState<AutoReviewStatus | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      api
        .get<AutoReviewStatus>("/integrations/auto-review")
        .then(setState)
        .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить авторазбор"));
    void load();
    // Счётчики и текущая закупка меняются каждые несколько минут — без перезагрузки страницы.
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  const save = async (patch: { enabled: boolean; provider?: AiProviderKey; daily_limit?: number }) => {
    setError(null);
    setIsSaving(true);
    try {
      setState(await api.put<AutoReviewStatus>("/integrations/auto-review", patch));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось переключить авторазбор");
    } finally {
      setIsSaving(false);
    }
  };

  const pill = !state?.enabled ? (
    <StatusPill tone="muted">выключен</StatusPill>
  ) : state.blocker ? (
    <StatusPill tone="bad">стоит</StatusPill>
  ) : (
    <StatusPill tone="ok">{state.current ? "разбирает" : "ждёт закупок"}</StatusPill>
  );

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
      <div className="flex flex-wrap items-start gap-3">
        <span className="flex h-11 shrink-0 items-center rounded-xl bg-white/[0.05] px-2.5">
          <AiProviderIcon provider={state?.provider ?? "qwen"} size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-semibold text-zinc-100">Автоматический разбор</span>
            {state && pill}
          </div>
          <div className="mt-0.5 max-w-2xl text-xs leading-relaxed text-zinc-500">
            Открытые закупки, прошедшие профили отбора, сами встают на полный разбор выбранной моделью —
            по одной, ближайший срок подачи первым, не больше дневного лимита. Уже разобранные вручную
            пропускаются. Один разбор — в среднем 1,3 млн токенов; YandexGPT с 30.10.2026 платная.
          </div>
        </div>
        <Toggle
          checked={state?.enabled ?? false}
          onChange={(value) => void save({ enabled: value })}
          disabled={!state || isSaving}
          label="Автоматический разбор"
        />
      </div>

      {state && (
        <div className="mt-4 flex flex-wrap items-end gap-4">
          <label className="text-xs text-zinc-500">
            Модель
            <select
              className="mt-1 block rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-sm text-zinc-100"
              value={state.provider}
              disabled={isSaving}
              onChange={(event) =>
                void save({ enabled: state.enabled, provider: event.target.value as AiProviderKey })
              }
            >
              {PROVIDERS.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-zinc-500">
            Закупок в сутки
            <input
              type="number"
              min={1}
              max={200}
              className="mt-1 block w-24 rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-sm tabular-nums text-zinc-100"
              defaultValue={state.daily_limit}
              key={state.daily_limit}
              disabled={isSaving}
              onBlur={(event) => {
                const value = Number(event.target.value);
                if (Number.isInteger(value) && value !== state.daily_limit) {
                  void save({ enabled: state.enabled, daily_limit: value });
                }
              }}
            />
          </label>
          <span className="pb-2 text-xs text-zinc-500">
            сегодня взято {countFormat.format(state.taken_today)} из {countFormat.format(state.daily_limit)}
          </span>
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}
      {state?.enabled && state.blocker && (
        <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-300">
          {state.blocker} — очередь продолжится, как только модель станет доступна.
        </div>
      )}

      {state && (
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {[
            { label: "Ждут разбора", value: state.pending },
            { label: "Разобрано", value: state.done },
            { label: "Не удалось", value: state.failed },
          ].map((item) => (
            <div key={item.label} className="rounded-xl bg-black/20 px-4 py-3">
              <div className="text-[11px] text-zinc-500">{item.label}</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-zinc-100">
                {countFormat.format(item.value)}
              </div>
            </div>
          ))}
        </div>
      )}

      {state?.current && (
        <p className="mt-3 text-xs text-zinc-400">
          {state.current.status === "running" ? "Сейчас разбирается" : "Следующая в очереди"}:{" "}
          <Link to={`/tenders/${state.current.tender_id}`} className="text-indigo-300 hover:text-indigo-200">
            {state.current.external_id}
          </Link>{" "}
          — {state.current.title}
          {state.current.message ? ` · ${state.current.message}` : ""}
        </p>
      )}
    </div>
  );
}
