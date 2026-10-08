import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, FlaskConical, RefreshCw, Save, XCircle } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { Bitrix24CheckResult, Bitrix24PushResult, Bitrix24Settings } from "../../api/types";

/**
 * Сделки в Bitrix24 через входящий вебхук (08.10.2026) — блок раздела «Интеграция с Bitrix24».
 *
 * Портал у заказчика один и боевой, поэтому порядок такой: вставить вебхук → «Проверить
 * подключение» (только чтение: стадии воронки и поля сделки) → и лишь потом включить отправку.
 * Переключатель отправки выключен по умолчанию и не включается, пока вебхук не задан.
 * Сам вебхук после сохранения не показывается — только портал и маска.
 */
export function BitrixDealsBlock() {
  const [settings, setSettings] = useState<Bitrix24Settings | null>(null);
  const [webhook, setWebhook] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [stageId, setStageId] = useState("");
  const [check, setCheck] = useState<Bitrix24CheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [testDeal, setTestDeal] = useState<Bitrix24PushResult | null>(null);

  const apply = (next: Bitrix24Settings) => {
    setSettings(next);
    setCategoryId(String(next.category_id));
    setStageId(next.stage_id);
  };

  const load = useCallback(async () => {
    try {
      apply(await api.get<Bitrix24Settings>("/integrations/bitrix24"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить настройки Bitrix24");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (patch: Record<string, unknown>) => {
    setIsBusy(true);
    setError(null);
    try {
      apply(await api.patch<Bitrix24Settings>("/integrations/bitrix24", patch));
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить настройки");
      return false;
    } finally {
      setIsBusy(false);
    }
  };

  const handleSaveWebhook = async () => {
    if (!webhook.trim()) return;
    if (await save({ webhook_url: webhook.trim() })) {
      setWebhook("");
      setCheck(null);
    }
  };

  const handleSaveTarget = async () => {
    const category = Number(categoryId);
    if (!Number.isInteger(category) || category < 0 || !stageId.trim()) {
      setError("Воронка — целое число, стадия — код стадии, например C5:PARSING");
      return;
    }
    if (await save({ category_id: category, stage_id: stageId.trim() })) setCheck(null);
  };

  const handleCheck = async () => {
    setIsBusy(true);
    setError(null);
    try {
      setCheck(await api.post<Bitrix24CheckResult>("/integrations/bitrix24/test", {}));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось проверить подключение");
    } finally {
      setIsBusy(false);
    }
  };

  // Одна сделка «[ТЕСТ] …» из последнего тендера — посмотреть на портале, как легли поля.
  // Пишет в боевую CRM, поэтому с подтверждением.
  const handleTestDeal = async () => {
    if (!window.confirm("Создать в Bitrix24 одну тестовую сделку «[ТЕСТ] …» из последнего тендера?")) return;
    setIsBusy(true);
    setError(null);
    setTestDeal(null);
    try {
      setTestDeal(await api.post<Bitrix24PushResult>("/integrations/bitrix24/test-deal", {}));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать тестовую сделку");
    } finally {
      setIsBusy(false);
    }
  };

  if (!settings) {
    return error ? <div className="text-sm text-red-400">{error}</div> : null;
  }

  const inputClass =
    "rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";
  const checkOk = check ? check.success : settings.last_check_status === "ok";

  return (
    <div className="mb-5 border-b border-white/[0.08] pb-5">
      <div className="text-sm font-medium text-zinc-200">Сделки в CRM</div>

      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="mt-3 text-xs text-zinc-400">
        Вебхук:{" "}
        {settings.is_configured ? (
          <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">{settings.webhook_masked}</code>
        ) : (
          <span className="text-zinc-500">не задан</span>
        )}
        {settings.updated_by && settings.updated_at && (
          <span className="text-zinc-600">
            {" "}· {settings.updated_by}, {new Date(settings.updated_at).toLocaleString("ru-RU")}
          </span>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          type="password"
          autoComplete="off"
          value={webhook}
          onChange={(e) => setWebhook(e.target.value)}
          placeholder="https://портал/rest/<пользователь>/<код>/"
          className={`min-w-[300px] flex-1 ${inputClass}`}
        />
        <button
          onClick={handleSaveWebhook}
          disabled={isBusy || !webhook.trim()}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-snow hover:bg-indigo-400 disabled:opacity-50"
        >
          <Save size={13} />
          {settings.is_configured ? "Заменить" : "Сохранить"}
        </button>
        {settings.is_configured && (
          <button
            onClick={() => void save({ webhook_url: "" })}
            disabled={isBusy}
            className="rounded-lg border border-red-500/30 px-3 py-2 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-50"
          >
            Отключить
          </button>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="text-xs text-zinc-500">
          Воронка (id)
          <input
            value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            className={`mt-1 block w-24 ${inputClass}`}
          />
        </label>
        <label className="text-xs text-zinc-500">
          Стадия
          {check && check.stages.length > 0 ? (
            <select
              value={stageId}
              onChange={(e) => setStageId(e.target.value)}
              className={`mt-1 block min-w-[260px] ${inputClass}`}
            >
              {!check.stages.some((s) => s.id === stageId) && <option value={stageId}>{stageId}</option>}
              {check.stages.map((stage) => (
                <option key={stage.id} value={stage.id}>
                  {stage.name} ({stage.id})
                </option>
              ))}
            </select>
          ) : (
            <input
              value={stageId}
              onChange={(e) => setStageId(e.target.value)}
              className={`mt-1 block w-48 ${inputClass}`}
            />
          )}
        </label>
        <button
          onClick={handleSaveTarget}
          disabled={
            isBusy ||
            (categoryId === String(settings.category_id) && stageId === settings.stage_id)
          }
          className="rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
        >
          Сохранить воронку и стадию
        </button>
        <button
          onClick={handleCheck}
          disabled={isBusy || !settings.is_configured}
          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          title="Читает стадии воронки и поля сделки — в CRM ничего не записывается"
        >
          <RefreshCw size={13} className={isBusy ? "animate-spin" : ""} />
          Проверить подключение
        </button>
        <button
          onClick={handleTestDeal}
          disabled={isBusy || !settings.is_configured}
          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          title="Создаёт в CRM одну сделку с пометкой «[ТЕСТ]» из последнего тендера"
        >
          <FlaskConical size={13} />
          Создать тестовую сделку
        </button>
      </div>

      {testDeal && (
        <div className="mt-3 flex items-start gap-2 text-xs text-emerald-400">
          <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
          <span>
            {testDeal.message}{" "}
            <a href={testDeal.url} target="_blank" rel="noreferrer" className="underline">
              Открыть в Bitrix24
            </a>
          </span>
        </div>
      )}

      {(check || settings.last_check_message) && (
        <div
          className={`mt-3 flex items-start gap-2 text-xs ${checkOk ? "text-emerald-400" : "text-amber-300"}`}
        >
          {checkOk ? (
            <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
          ) : (
            <XCircle size={14} className="mt-0.5 shrink-0" />
          )}
          <span>
            {check ? check.message : settings.last_check_message}
            {check && check.missing_fields.length > 0 && (
              <> Нет полей: {check.missing_fields.map((f) => f.title).join(", ")}.</>
            )}
          </span>
        </div>
      )}

      <label className="mt-4 flex items-start gap-2.5 text-sm text-zinc-200">
        <input
          type="checkbox"
          checked={settings.push_enabled}
          disabled={isBusy || !settings.is_configured}
          onChange={(e) => void save({ push_enabled: e.target.checked })}
          className="mt-0.5"
        />
        <span>
          Отправлять тендеры в CRM
          <span className="mt-0.5 flex items-start gap-1.5 text-xs text-zinc-500">
            <AlertTriangle size={12} className="mt-0.5 shrink-0 text-amber-400" />
            Портал боевой: включайте после проверки подключения. Пока выключено, в CRM ничего не
            записывается.
          </span>
        </span>
      </label>
    </div>
  );
}
