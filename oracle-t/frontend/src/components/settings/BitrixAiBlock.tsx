import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Eye, RotateCcw, Save, Send, XCircle } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { BitrixAiPreview, BitrixAiRequest, BitrixAiSettings } from "../../api/types";

const SECTION_LABELS: Record<string, string> = {
  preparation: "Подготовка заявки",
  technical_specification: "ТЗ и номенклатура",
  contract: "Договор",
  participants_prices: "Участники и цены",
  fas: "ФАС",
  matching: "Сопоставление",
};

/**
 * Пакет ИИ-анализа во вкладку сделки «ИИ анализ и решения» (20.10.2026).
 *
 * Обработчик `receive.php` приложения на портале: подпись HMAC общим секретом, сделку портал
 * находит или создаёт сам и возвращает её id. Порядок как у вебхука: адрес и секрет →
 * предпросмотр пакета (портал не вызывается) → включить отправку → отправить тендер.
 * В журнале — HTTP-код и ответ портала: их администратор передаёт администратору Bitrix24.
 */
export function BitrixAiBlock() {
  const [settings, setSettings] = useState<BitrixAiSettings | null>(null);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [tenderRef, setTenderRef] = useState("");
  const [fullSnapshot, setFullSnapshot] = useState(false);
  const [preview, setPreview] = useState<BitrixAiPreview | null>(null);
  const [requests, setRequests] = useState<BitrixAiRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.get<BitrixAiSettings>("/integrations/bitrix24/ai");
      setSettings(next);
      setUrl(next.receiver_url ?? "");
      setRequests(await api.get<BitrixAiRequest[]>("/integrations/bitrix24/ai/requests"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить настройки обработчика");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<void>) => {
    setIsBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить действие");
    } finally {
      setIsBusy(false);
    }
  };

  const save = (patch: Record<string, unknown>) =>
    run(async () => {
      setSettings(await api.patch<BitrixAiSettings>("/integrations/bitrix24/ai", patch));
      if ("secret" in patch) setSecret("");
    });

  const handlePreview = () =>
    run(async () => {
      const query = new URLSearchParams({ tender: tenderRef.trim(), full_snapshot: String(fullSnapshot) });
      setPreview(await api.get<BitrixAiPreview>(`/integrations/bitrix24/ai/preview?${query}`));
    });

  const handleSend = () => {
    if (!window.confirm(`Отправить пакет анализа тендера ${tenderRef.trim()} в Bitrix24?`)) return;
    void run(async () => {
      await api.post<BitrixAiRequest>("/integrations/bitrix24/ai/send", {
        tender: tenderRef.trim(),
        full_snapshot: fullSnapshot,
      });
      setRequests(await api.get<BitrixAiRequest[]>("/integrations/bitrix24/ai/requests"));
    });
  };

  const handleRetry = (item: BitrixAiRequest) =>
    run(async () => {
      await api.post<BitrixAiRequest>(`/integrations/bitrix24/ai/requests/${item.id}/retry`, {});
      setRequests(await api.get<BitrixAiRequest[]>("/integrations/bitrix24/ai/requests"));
    });

  if (!settings) {
    return error ? <div className="mb-5 text-sm text-red-400">{error}</div> : null;
  }

  const configured = Boolean(settings.receiver_url && settings.secret_set);
  const inputClass =
    "rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";
  const ghostButton =
    "flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50";

  return (
    <div className="mb-5 border-b border-white/[0.08] pb-5">
      <div className="text-sm font-medium text-zinc-200">Анализ во вкладку сделки</div>
      <p className="mt-1 text-xs text-zinc-500">
        Приложение «ИИ анализ и решения»: короткие поля сделки и строки шести разделов вкладки. Сделку
        портал находит по номеру процедуры или создаёт сам.
      </p>

      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://портал/local/tools/tender_ai_analysis/integration/receive.php"
          className={`min-w-[300px] flex-1 ${inputClass}`}
        />
        <button
          onClick={() => void save({ receiver_url: url.trim() })}
          disabled={isBusy || url.trim() === (settings.receiver_url ?? "")}
          className={ghostButton}
        >
          <Save size={13} />
          Сохранить адрес
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          type="password"
          autoComplete="off"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder={settings.secret_set ? `Секрет HMAC задан (${settings.secret_hint})` : "Секрет HMAC"}
          className={`min-w-[300px] flex-1 ${inputClass}`}
        />
        <button
          onClick={() => void save({ secret: secret.trim() })}
          disabled={isBusy || !secret.trim()}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-snow hover:bg-indigo-400 disabled:opacity-50"
        >
          <Save size={13} />
          {settings.secret_set ? "Заменить секрет" : "Сохранить секрет"}
        </button>
      </div>

      <label className="mt-3 flex items-start gap-2.5 text-sm text-zinc-200">
        <input
          type="checkbox"
          checked={settings.push_enabled}
          disabled={isBusy || !configured}
          onChange={(e) => void save({ push_enabled: e.target.checked })}
          className="mt-0.5"
        />
        <span>
          Отправлять пакеты анализа
          <span className="mt-0.5 flex items-start gap-1.5 text-xs text-zinc-500">
            <AlertTriangle size={12} className="mt-0.5 shrink-0 text-amber-400" />
            Портал боевой: пакет может создать сделку и компанию. Предпросмотр работает и без отправки.
          </span>
        </span>
      </label>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <input
          value={tenderRef}
          onChange={(e) => setTenderRef(e.target.value)}
          placeholder="Номер закупки"
          className={`w-56 ${inputClass}`}
        />
        <label className="flex items-center gap-1.5 text-xs text-zinc-400">
          <input type="checkbox" checked={fullSnapshot} onChange={(e) => setFullSnapshot(e.target.checked)} />
          Полная версия разделов
        </label>
        <button onClick={() => void handlePreview()} disabled={isBusy || !tenderRef.trim()} className={ghostButton}>
          <Eye size={13} />
          Предпросмотр
        </button>
        <button
          onClick={handleSend}
          disabled={isBusy || !tenderRef.trim() || !settings.push_enabled}
          className={ghostButton}
          title={settings.push_enabled ? "Подписать и отправить пакет" : "Сначала включите отправку"}
        >
          <Send size={13} />
          Отправить
        </button>
      </div>

      {preview && (
        <div className="mt-3 rounded-lg border border-white/[0.08] bg-black/20 p-3 text-xs text-zinc-400">
          <div>
            Строк: {preview.items_count} · {Math.round(preview.size_bytes / 1024)} КБ ·{" "}
            {Object.entries(preview.payload.sections)
              .map(([key, rows]) => `${SECTION_LABELS[key] ?? key}: ${rows.length}`)
              .join(" · ")}
          </div>
          <details className="mt-2">
            <summary className="cursor-pointer text-zinc-300">Пакет целиком</summary>
            <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-[11px] text-zinc-400">
              {JSON.stringify(preview.payload, null, 2)}
            </pre>
          </details>
        </div>
      )}

      {requests.length > 0 && (
        <div className="mt-4 overflow-x-auto">
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
                <tr key={item.id} className="border-t border-white/[0.06] align-top">
                  <td className="py-2 pr-3 text-zinc-400">
                    {item.sent_at ? new Date(item.sent_at).toLocaleString("ru-RU") : "—"}
                    <div className="font-mono text-[10px] text-zinc-600">{item.request_id}</div>
                  </td>
                  <td className="py-2 pr-3 text-zinc-300">
                    {item.tender_number}
                    <div className="text-zinc-600">строк {item.items_count}{item.full_snapshot ? " · полная версия" : ""}</div>
                  </td>
                  <td className="py-2 pr-3">
                    <span className={`flex items-start gap-1.5 ${item.accepted ? "text-emerald-400" : "text-amber-300"}`}>
                      {item.accepted ? <CheckCircle2 size={13} className="mt-0.5 shrink-0" /> : <XCircle size={13} className="mt-0.5 shrink-0" />}
                      {item.accepted
                        ? `HTTP ${item.http_status}${item.duplicate ? " · уже был принят" : ""}`
                        : item.error}
                    </span>
                    {item.response_body && (
                      <details className="mt-1 text-zinc-500">
                        <summary className="cursor-pointer">Ответ портала</summary>
                        <pre className="mt-1 max-w-md whitespace-pre-wrap text-[11px]">{item.response_body}</pre>
                      </details>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-zinc-300">
                    {item.deal_url ? (
                      <a href={item.deal_url} target="_blank" rel="noreferrer" className="underline">
                        {item.deal_id}
                      </a>
                    ) : (
                      item.deal_id ?? "—"
                    )}
                    {item.deal_created && <div className="text-zinc-600">создана</div>}
                  </td>
                  <td className="py-2">
                    {item.retryable && (
                      <button onClick={() => void handleRetry(item)} disabled={isBusy} className={ghostButton}>
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
      )}
    </div>
  );
}
