import { useState } from "react";
import {
  CheckCircle2,
  Eye,
  RotateCcw,
  Save,
  Send,
  Sparkles,
  XCircle,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  BitrixAiPreview,
  BitrixAiRequest,
  BitrixAiSettings,
} from "../../api/types";
import { FeatureCard, type FeatureState, Step } from "../apps/AppKit";
import { SettingsNotice, secondaryButtonClass, settingsInputClass } from "./ui";

const SECTION_LABELS: Record<string, string> = {
  preparation: "Подготовка заявки",
  technical_specification: "ТЗ и номенклатура",
  contract: "Договор",
  participants_prices: "Участники и цены",
  fas: "ФАС",
  matching: "Сопоставление",
};

export function bitrixAiState(settings: BitrixAiSettings): FeatureState {
  if (!settings.receiver_url || !settings.secret_set) return "setup";
  return settings.push_enabled ? "on" : "paused";
}

/**
 * Пакет ИИ-анализа во вкладку сделки «ИИ анализ и решения» (20.10.2026).
 *
 * Обработчик `receive.php` приложения на портале: подпись HMAC общим секретом, сделку портал
 * находит или создаёт сам и возвращает её id. Порядок как у вебхука: адрес и секрет →
 * предпросмотр пакета (портал не вызывается) → включить отправку → отправить тендер.
 * В журнале — HTTP-код и ответ портала: их администратор передаёт администратору Bitrix24.
 *
 * Редизайн 24.10.2026: именно этот канал питает кнопку «Загрузить в Битрикс» у закупок, а его
 * флажок «Отправлять пакеты анализа» стоял посреди формы — Bitrix24 «подключён», кнопка
 * заблокирована, причину не находили. Теперь это главный переключатель карточки, и пока он
 * выключен, карточка прямо говорит, что кнопка у закупок не работает.
 */
export function BitrixAiBlock({
  settings,
  onSettings,
  requests,
  onRequests,
}: {
  settings: BitrixAiSettings;
  onSettings: (next: BitrixAiSettings) => void;
  requests: BitrixAiRequest[];
  onRequests: (next: BitrixAiRequest[]) => void;
}) {
  const [url, setUrl] = useState(settings.receiver_url ?? "");
  const [secret, setSecret] = useState("");
  const [tenderRef, setTenderRef] = useState("");
  const [fullSnapshot, setFullSnapshot] = useState(false);
  const [preview, setPreview] = useState<BitrixAiPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const run = async (action: () => Promise<void>) => {
    setIsBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Не удалось выполнить действие",
      );
    } finally {
      setIsBusy(false);
    }
  };

  const reloadRequests = async () =>
    onRequests(
      await api.get<BitrixAiRequest[]>("/integrations/bitrix24/ai/requests"),
    );

  const save = (patch: Record<string, unknown>) =>
    run(async () => {
      const next = await api.patch<BitrixAiSettings>(
        "/integrations/bitrix24/ai",
        patch,
      );
      onSettings(next);
      setUrl(next.receiver_url ?? "");
      if ("secret" in patch) setSecret("");
    });

  const handlePush = (enabled: boolean) => {
    if (
      enabled &&
      !window.confirm(
        "Включить отправку анализа в Bitrix24? Портал боевой: кнопка «Загрузить в Битрикс» у закупок начнёт создавать и обновлять сделки.",
      )
    )
      return;
    void save({ push_enabled: enabled });
  };

  const handlePreview = () =>
    run(async () => {
      const query = new URLSearchParams({
        tender: tenderRef.trim(),
        full_snapshot: String(fullSnapshot),
      });
      setPreview(
        await api.get<BitrixAiPreview>(
          `/integrations/bitrix24/ai/preview?${query}`,
        ),
      );
    });

  const handleSend = () => {
    if (
      !window.confirm(
        `Отправить пакет анализа тендера ${tenderRef.trim()} в Bitrix24?`,
      )
    )
      return;
    void run(async () => {
      await api.post<BitrixAiRequest>("/integrations/bitrix24/ai/send", {
        tender: tenderRef.trim(),
        full_snapshot: fullSnapshot,
      });
      await reloadRequests();
    });
  };

  const handleRetry = (item: BitrixAiRequest) =>
    run(async () => {
      await api.post<BitrixAiRequest>(
        `/integrations/bitrix24/ai/requests/${item.id}/retry`,
        {},
      );
      await reloadRequests();
    });

  const state = bitrixAiState(settings);
  const configured = state !== "setup";

  return (
    <FeatureCard
      id="bitrix-analysis"
      icon={<Sparkles size={18} />}
      title="Анализ в сделку"
      description={
        <>
          Кнопка «Загрузить в Битрикс» на странице закупки: разбор ИИ уходит во
          вкладку «ИИ анализ и решения» сделки. Сделку портал находит по номеру
          процедуры или создаёт сам.
        </>
      }
      state={state}
      toggle={{
        checked: settings.push_enabled,
        onChange: handlePush,
        disabled: isBusy || (!configured && !settings.push_enabled),
        label: "Отправка анализа в Bitrix24",
        blockedHint: configured ? null : "Сначала шаг 1: адрес и секрет",
      }}
      warning={
        state === "paused"
          ? "Пока выключено, кнопка «Загрузить в Битрикс» у закупок заблокирована. Портал боевой: после включения пакет может создать сделку и компанию."
          : state === "on"
            ? "Портал боевой: каждое нажатие кнопки у закупки создаёт или обновляет сделку."
            : null
      }
    >
      {error && (
        <div className="px-5 py-4">
          <SettingsNotice tone="error">{error}</SettingsNotice>
        </div>
      )}

      <Step
        number={1}
        title="Подключение к порталу"
        hint="Адрес обработчика и секрет HMAC — из настроек приложения «ИИ анализ и решения» на портале."
        done={configured}
      >
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://портал/local/tools/tender_ai_analysis/integration/receive.php"
              aria-label="Адрес обработчика"
              className={`!mt-0 min-w-[260px] flex-1 ${settingsInputClass}`}
            />
            <button
              onClick={() => void save({ receiver_url: url.trim() })}
              disabled={isBusy || url.trim() === (settings.receiver_url ?? "")}
              className={secondaryButtonClass}
            >
              <Save size={13} />
              Сохранить адрес
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder={
                settings.secret_set
                  ? `Секрет задан (${settings.secret_hint}) — введите новый, чтобы заменить`
                  : "Секрет HMAC"
              }
              aria-label="Секрет HMAC"
              className={`!mt-0 min-w-[260px] flex-1 ${settingsInputClass}`}
            />
            <button
              onClick={() => void save({ secret: secret.trim() })}
              disabled={isBusy || !secret.trim()}
              className={secondaryButtonClass}
            >
              <Save size={13} />
              {settings.secret_set ? "Заменить секрет" : "Сохранить секрет"}
            </button>
          </div>
        </div>
      </Step>

      <Step
        number={2}
        title="Проверка на одной закупке"
        hint="Предпросмотр собирает пакет, не обращаясь к порталу. «Отправить» работает, когда отправка включена."
      >
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={tenderRef}
            onChange={(e) => setTenderRef(e.target.value)}
            placeholder="Номер закупки"
            aria-label="Номер закупки"
            className={`!mt-0 w-56 ${settingsInputClass}`}
          />
          <label className="flex items-center gap-1.5 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={fullSnapshot}
              onChange={(e) => setFullSnapshot(e.target.checked)}
            />
            Полная версия разделов
          </label>
          <button
            onClick={() => void handlePreview()}
            disabled={isBusy || !tenderRef.trim()}
            className={secondaryButtonClass}
          >
            <Eye size={13} />
            Предпросмотр
          </button>
          <button
            onClick={handleSend}
            disabled={isBusy || !tenderRef.trim() || !settings.push_enabled}
            className={secondaryButtonClass}
            title={
              settings.push_enabled
                ? "Подписать и отправить пакет"
                : "Сначала включите отправку переключателем вверху карточки"
            }
          >
            <Send size={13} />
            Отправить
          </button>
        </div>

        {preview && (
          <div className="mt-3 rounded-xl border border-white/[0.08] bg-black/20 p-3 text-xs text-zinc-400">
            <div>
              Строк: {preview.items_count} ·{" "}
              {Math.round(preview.size_bytes / 1024)} КБ ·{" "}
              {Object.entries(preview.payload.sections)
                .map(
                  ([key, rows]) =>
                    `${SECTION_LABELS[key] ?? key}: ${rows.length}`,
                )
                .join(" · ")}
            </div>
            <details className="mt-2">
              <summary className="cursor-pointer text-zinc-300">
                Пакет целиком
              </summary>
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-[11px] text-zinc-400">
                {JSON.stringify(preview.payload, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </Step>

      {requests.length > 0 && (
        <details className="group px-5 py-4">
          <summary className="cursor-pointer list-none text-sm font-medium text-zinc-300 hover:text-zinc-100">
            Журнал отправок · {requests.length}
            <span className="ml-2 text-xs font-normal text-zinc-500 group-open:hidden">
              показать
            </span>
          </summary>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-white/[0.08] text-zinc-500">
                <tr>
                  <th className="py-2 pr-3 font-medium">Отправлен</th>
                  <th className="py-2 pr-3 font-medium">Тендер</th>
                  <th className="py-2 pr-3 font-medium">Ответ</th>
                  <th className="py-2 pr-3 font-medium">Сделка</th>
                  <th className="py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {requests.map((item) => (
                  <tr
                    key={item.id}
                    className="border-t border-white/[0.06] align-top"
                  >
                    <td className="py-2 pr-3 text-zinc-400">
                      {item.sent_at
                        ? new Date(item.sent_at).toLocaleString("ru-RU")
                        : "—"}
                      <div className="font-mono text-[10px] text-zinc-600">
                        {item.request_id}
                      </div>
                    </td>
                    <td className="py-2 pr-3 text-zinc-300">
                      {item.tender_number}
                      <div className="text-zinc-600">
                        строк {item.items_count}
                        {item.full_snapshot ? " · полная версия" : ""}
                      </div>
                    </td>
                    <td className="py-2 pr-3">
                      <span
                        className={`flex items-start gap-1.5 ${item.accepted ? "text-emerald-400" : "text-amber-300"}`}
                      >
                        {item.accepted ? (
                          <CheckCircle2 size={13} className="mt-0.5 shrink-0" />
                        ) : (
                          <XCircle size={13} className="mt-0.5 shrink-0" />
                        )}
                        {item.accepted
                          ? `HTTP ${item.http_status}${item.duplicate ? " · уже был принят" : ""}`
                          : item.error}
                      </span>
                      {item.response_body && (
                        <details className="mt-1 text-zinc-500">
                          <summary className="cursor-pointer">
                            Ответ портала
                          </summary>
                          <pre className="mt-1 max-w-md whitespace-pre-wrap text-[11px]">
                            {item.response_body}
                          </pre>
                        </details>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-zinc-300">
                      {item.deal_url ? (
                        <a
                          href={item.deal_url}
                          target="_blank"
                          rel="noreferrer"
                          className="underline"
                        >
                          {item.deal_id}
                        </a>
                      ) : (
                        (item.deal_id ?? "—")
                      )}
                      {item.deal_created && (
                        <div className="text-zinc-600">создана</div>
                      )}
                    </td>
                    <td className="py-2">
                      {item.retryable && (
                        <button
                          onClick={() => void handleRetry(item)}
                          disabled={isBusy}
                          className={secondaryButtonClass}
                        >
                          <RotateCcw size={12} />
                          Повторить
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </FeatureCard>
  );
}
