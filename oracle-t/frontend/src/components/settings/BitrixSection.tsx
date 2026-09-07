import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  Copy,
  Download,
  KeyRound,
  Plug2,
  Trash2,
} from "lucide-react";

import { ApiError, api, downloadFile } from "../../api/client";
import type { ApiClient, ApiClientCreated } from "../../api/types";

function formatDateTime(value: string | null): string {
  if (!value) return "не использовался";
  return new Date(value).toLocaleString("ru-RU");
}

/**
 * Раздел «Интеграция с Bitrix24» (Этап 13, раздел 5.10 ТЗ).
 *
 * Два способа отдать данные наружу: файл для ручного импорта и API для будущей интеграции.
 * Реальных вызовов к Bitrix24 нет — ТЗ выносит их во вторую очередь.
 *
 * Ключ показывается ровно один раз, сразу после выпуска: в базе лежит только его хеш.
 * Поэтому выданный ключ выделен в отдельный блок с кнопкой копирования и предупреждением —
 * закрыв его, восстановить значение нельзя, можно только выпустить новый.
 */
export function BitrixSection({ isAdmin }: { isAdmin: boolean }) {
  const [isExpanded, setIsExpanded] = useState(false);
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
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить ключи доступа");
    }
  }, [isAdmin]);

  useEffect(() => {
    if (isExpanded) void load();
  }, [isExpanded, load]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setIsBusy(true);
    setError(null);
    try {
      const created = await api.post<ApiClientCreated>("/integrations/api-clients", {
        name: newName.trim(),
      });
      setIssuedKey(created);
      setNewName("");
      setCopied(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выпустить ключ");
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
      setError(err instanceof ApiError ? err.message : "Не удалось отозвать ключ");
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
      setError(err instanceof ApiError ? err.message : "Не удалось сформировать выгрузку");
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

  return (
    <div className="mt-6 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Plug2 size={15} className="text-indigo-400" />
            Интеграция с Bitrix24
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Выгрузка тендеров лидами для импорта в CRM и ключи доступа к API для внешних
            систем (раздел 5.10 ТЗ). Сами вызовы к Bitrix24 — следующий этап.
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

          <div className="mb-5">
            <div className="text-sm font-medium text-zinc-200">Выгрузка лидов</div>
            <p className="mt-1 text-xs text-zinc-500">
              CSV с полями лида: название, компания, сумма, ответственный по региону, номер
              закупки, ОКПД2, сроки, процент победителя и ссылка. Вторая строка файла —
              технические имена полей, по ним Bitrix24 сопоставляет колонки при импорте.
            </p>
            <div className="mt-3 flex items-center gap-3">
              <button
                onClick={handleExport}
                className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5"
              >
                <Download size={13} />
                Выгрузить лиды в CSV
              </button>
              {exportNote && <span className="text-xs text-emerald-400">{exportNote}</span>}
            </div>
          </div>

          {isAdmin && (
            <div className="border-t border-white/[0.08] pt-4">
              <div className="text-sm font-medium text-zinc-200">Ключи доступа к API</div>
              <p className="mt-1 text-xs text-zinc-500">
                Внешняя система читает данные по адресу{" "}
                <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">
                  /api/integration/v1/tenders
                </code>{" "}
                и{" "}
                <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">
                  /api/integration/v1/leads
                </code>
                , передавая ключ в заголовке{" "}
                <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">X-API-Key</code>.
              </p>

              {issuedKey && (
                <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
                  <div className="flex items-start gap-2 text-xs text-amber-300">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>
                      Скопируйте ключ сейчас — он показывается один раз. В системе хранится
                      только его хеш, восстановить значение потом невозможно.
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <code className="flex-1 overflow-x-auto rounded bg-black/40 px-3 py-2 font-mono text-xs text-zinc-100">
                      {issuedKey.key}
                    </code>
                    <button
                      onClick={handleCopy}
                      className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-2 text-xs text-zinc-300 hover:bg-white/5"
                    >
                      <Copy size={13} />
                      {copied ? "Скопирован" : "Копировать"}
                    </button>
                  </div>
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Название системы, например «Bitrix24 портал»"
                  className="min-w-[260px] flex-1 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
                />
                <button
                  onClick={handleCreate}
                  disabled={isBusy || !newName.trim()}
                  className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  <KeyRound size={13} />
                  Выпустить ключ
                </button>
              </div>

              <div className="mt-4 overflow-x-auto">
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
                          Ключей пока нет. Выпустите ключ, когда появится система, которой
                          нужен доступ к данным.
                        </td>
                      </tr>
                    ) : (
                      clients.map((client) => (
                        <tr key={client.id} className="border-t border-white/[0.06]">
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
                                className="flex items-center gap-1.5 rounded-lg border border-red-500/30 px-2.5 py-1.5 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-50"
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
            </div>
          )}
        </div>
      )}
    </div>
  );
}
