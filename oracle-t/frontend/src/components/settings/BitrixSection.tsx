import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Copy, Download, KeyRound, Trash2 } from "lucide-react";

import { ApiError, api, downloadFile } from "../../api/client";
import type {
  ApiClient,
  ApiClientCreated,
  Bitrix24Settings,
  BitrixAiRequest,
  BitrixAiSettings,
} from "../../api/types";
import {
  FeatureCard,
  FeatureSummary,
  Step,
  type SummaryItem,
  notifyAppStatusChanged,
} from "../apps/AppKit";
import { BitrixAiBlock, bitrixAiState } from "./BitrixAiBlock";
import { BitrixDealsBlock, bitrixDealsState } from "./BitrixDealsBlock";
import {
  SettingsNotice,
  dangerButtonClass,
  secondaryButtonClass,
  settingsInputClass,
} from "./ui";

function formatDateTime(value: string | null): string {
  if (!value) return "не использовался";
  return new Date(value).toLocaleString("ru-RU");
}

/**
 * Страница приложения «Bitrix24» (Этап 13, раздел 5.10 ТЗ).
 *
 * Способы отдать данные наружу: анализ в сделку (`BitrixAiBlock`, им работает кнопка
 * «Загрузить в Битрикс» у закупок), сделки через вебхук (08.10.2026, `BitrixDealsBlock`), файл
 * лидов для ручного импорта и API для внешних систем.
 *
 * Редизайн 24.10.2026: каждая функция — карточка с переключателем в шапке, сверху сводка
 * «что работает». Раньше всё шло одной формой, и выключенная отправка терялась среди полей.
 * Настройки обеих интеграций грузятся здесь — сводке нужны состояния обеих.
 *
 * Ключ показывается ровно один раз, сразу после выпуска: в базе лежит только его хеш.
 * Поэтому выданный ключ выделен в отдельный блок с кнопкой копирования и предупреждением —
 * закрыв его, восстановить значение нельзя, можно только выпустить новый.
 */
export function BitrixSection({ isAdmin }: { isAdmin: boolean }) {
  const [aiSettings, setAiSettings] = useState<BitrixAiSettings | null>(null);
  const [aiRequests, setAiRequests] = useState<BitrixAiRequest[]>([]);
  const [dealSettings, setDealSettings] = useState<Bitrix24Settings | null>(
    null,
  );
  const [clients, setClients] = useState<ApiClient[] | null>(null);
  const [newName, setNewName] = useState("");
  const [issuedKey, setIssuedKey] = useState<ApiClientCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    if (!isAdmin) return;
    try {
      setClients(await api.get<ApiClient[]>("/integrations/api-clients"));
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось загрузить ключи доступа",
      );
    }
  }, [isAdmin]);

  useEffect(() => {
    void load();
    if (!isAdmin) return;
    api
      .get<BitrixAiSettings>("/integrations/bitrix24/ai")
      .then(setAiSettings)
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "Не удалось загрузить настройки Bitrix24",
        ),
      );
    api
      .get<BitrixAiRequest[]>("/integrations/bitrix24/ai/requests")
      .then(setAiRequests)
      .catch(() => undefined);
    api
      .get<Bitrix24Settings>("/integrations/bitrix24")
      .then(setDealSettings)
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "Не удалось загрузить настройки Bitrix24",
        ),
      );
  }, [isAdmin, load]);

  useEffect(() => {
    if (aiSettings || dealSettings) notifyAppStatusChanged();
  }, [aiSettings, dealSettings]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setIsBusy(true);
    setError(null);
    try {
      const created = await api.post<ApiClientCreated>(
        "/integrations/api-clients",
        {
          name: newName.trim(),
        },
      );
      setIssuedKey(created);
      setNewName("");
      setCopied(false);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Не удалось выпустить ключ",
      );
    } finally {
      setIsBusy(false);
    }
  };

  const handleRevoke = async (client: ApiClient) => {
    setIsBusy(true);
    setError(null);
    try {
      await api.delete(`/integrations/api-clients/${client.id}`);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Не удалось отозвать ключ",
      );
    } finally {
      setIsBusy(false);
    }
  };

  const handleExport = async () => {
    setError(null);
    setExportNote(null);
    try {
      const result = await downloadFile(
        "/export/bitrix24-leads.csv?limit=5000",
        "bitrix24_leads.csv",
      );
      setExportNote(
        `Файл ${result.fileName} готов${result.rows !== null ? `: строк ${result.rows}` : ""}`,
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось сформировать выгрузку",
      );
    }
  };

  const handleCopy = async () => {
    if (!issuedKey) return;
    try {
      await navigator.clipboard.writeText(issuedKey.key);
      setCopied(true);
    } catch {
      // Буфер обмена может быть недоступен (нет разрешения, не https) — ключ и так виден
      // на экране, его можно выделить руками.
      setCopied(false);
    }
  };

  const summary: SummaryItem[] = [];
  if (aiSettings) {
    const state = bitrixAiState(aiSettings);
    summary.push({
      id: "bitrix-analysis",
      title: "Анализ в сделку",
      state,
      hint:
        state === "on"
          ? "Кнопка «Загрузить в Битрикс» у закупок работает."
          : state === "paused"
            ? "Подключено, но отправка выключена — кнопка у закупок заблокирована."
            : "Укажите адрес обработчика и секрет.",
    });
  }
  if (dealSettings) {
    const state = bitrixDealsState(dealSettings);
    summary.push({
      id: "bitrix-deals",
      title: "Сделки через вебхук",
      state,
      hint:
        state === "on"
          ? `Сделки пишутся в воронку ${dealSettings.category_id}, стадия ${dealSettings.stage_id}.`
          : state === "paused"
            ? "Вебхук задан, запись сделок выключена."
            : "Вставьте входящий вебхук портала.",
    });
  }

  return (
    <div className="space-y-4">
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}

      {summary.length > 0 && <FeatureSummary items={summary} />}

      {aiSettings && (
        <BitrixAiBlock
          settings={aiSettings}
          onSettings={setAiSettings}
          requests={aiRequests}
          onRequests={setAiRequests}
        />
      )}
      {dealSettings && (
        <BitrixDealsBlock
          settings={dealSettings}
          onSettings={setDealSettings}
        />
      )}

      <FeatureCard
        id="bitrix-leads"
        icon={<Download size={18} />}
        title="Выгрузка лидов"
        description="Файл CSV для ручного импорта в Bitrix24: до 5000 последних закупок с заключением ИИ и ссылкой."
      >
        <Step title="Скачать файл">
          <div className="flex flex-wrap items-center gap-3">
            <button onClick={handleExport} className={secondaryButtonClass}>
              <Download size={13} />
              Выгрузить лиды в CSV
            </button>
            {exportNote && (
              <span className="text-xs text-emerald-400">{exportNote}</span>
            )}
          </div>
        </Step>
      </FeatureCard>

      {isAdmin && (
        <FeatureCard
          id="bitrix-api"
          icon={<KeyRound size={18} />}
          title="Ключи доступа к API"
          description={
            <>
              Внешняя система читает данные по адресам{" "}
              <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">
                /api/integration/v1/tenders
              </code>{" "}
              и{" "}
              <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">
                /api/integration/v1/leads
              </code>
              , передавая ключ в заголовке{" "}
              <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">
                X-API-Key
              </code>
              .
            </>
          }
        >
          <Step
            title="Выпустить ключ"
            hint="Ключ показывается один раз — сохраните его сразу."
          >
            {issuedKey && (
              <div className="mb-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
                <div className="flex items-start gap-2 text-xs text-amber-300">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>
                    Скопируйте ключ сейчас — он показывается один раз. В системе
                    хранится только его хеш, восстановить значение потом
                    невозможно.
                  </span>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <code className="flex-1 overflow-x-auto rounded bg-black/40 px-3 py-2 font-mono text-xs text-zinc-100">
                    {issuedKey.key}
                  </code>
                  <button onClick={handleCopy} className={secondaryButtonClass}>
                    <Copy size={13} />
                    {copied ? "Скопирован" : "Копировать"}
                  </button>
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Название системы, например «Bitrix24 портал»"
                aria-label="Название системы"
                className={`!mt-0 min-w-[260px] flex-1 ${settingsInputClass}`}
              />
              <button
                onClick={handleCreate}
                disabled={isBusy || !newName.trim()}
                className={secondaryButtonClass}
              >
                <KeyRound size={13} />
                Выпустить ключ
              </button>
            </div>
          </Step>
          <div className="overflow-x-auto px-5 py-4">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-white/[0.08] text-zinc-500">
                <tr>
                  <th className="py-2 pr-4 font-medium">Система</th>
                  <th className="py-2 pr-4 font-medium">Ключ</th>
                  <th className="py-2 pr-4 font-medium">Выпущен</th>
                  <th className="py-2 pr-4 font-medium">Последнее обращение</th>
                  <th className="py-2 font-medium">Состояние</th>
                </tr>
              </thead>
              <tbody>
                {clients === null ? (
                  <tr>
                    <td className="py-3 text-zinc-500" colSpan={5}>
                      Загружаю…
                    </td>
                  </tr>
                ) : clients.length === 0 ? (
                  <tr>
                    <td className="py-3 text-zinc-500" colSpan={5}>
                      Ключей пока нет. Выпустите ключ, когда появится система,
                      которой нужен доступ к данным.
                    </td>
                  </tr>
                ) : (
                  clients.map((client) => (
                    <tr
                      key={client.id}
                      className="border-t border-white/[0.06]"
                    >
                      <td className="py-2 pr-4 text-zinc-200">{client.name}</td>
                      <td className="py-2 pr-4 font-mono text-xs text-zinc-500">
                        {client.key_prefix}…
                      </td>
                      <td className="py-2 pr-4 text-zinc-400">
                        {formatDateTime(client.created_at)}
                      </td>
                      <td className="py-2 pr-4 text-zinc-400">
                        {formatDateTime(client.last_used_at)}
                      </td>
                      <td className="py-2">
                        {client.is_active ? (
                          <button
                            onClick={() => handleRevoke(client)}
                            disabled={isBusy}
                            className={dangerButtonClass}
                          >
                            <Trash2 size={12} />
                            Отозвать
                          </button>
                        ) : (
                          <span className="rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs text-zinc-500">
                            отозван
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </FeatureCard>
      )}
    </div>
  );
}
