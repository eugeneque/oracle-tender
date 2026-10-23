import { useState } from "react";
import {
  CheckCircle2,
  FlaskConical,
  KanbanSquare,
  RefreshCw,
  Save,
  XCircle,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  Bitrix24CheckResult,
  Bitrix24PushResult,
  Bitrix24Settings,
} from "../../api/types";
import { FeatureCard, type FeatureState, Step } from "../apps/AppKit";
import {
  SettingsNotice,
  dangerButtonClass,
  secondaryButtonClass,
  settingsInputClass,
} from "./ui";

export function bitrixDealsState(settings: Bitrix24Settings): FeatureState {
  if (!settings.is_configured) return "setup";
  return settings.push_enabled ? "on" : "paused";
}

/**
 * Сделки в Bitrix24 через входящий вебхук (08.10.2026) — блок раздела «Интеграция с Bitrix24».
 *
 * Портал у заказчика один и боевой, поэтому порядок такой: вставить вебхук → «Проверить
 * подключение» (только чтение: стадии воронки и поля сделки) → и лишь потом включить отправку.
 * Переключатель отправки выключен по умолчанию и не включается, пока вебхук не задан.
 * Сам вебхук после сохранения не показывается — только портал и маска.
 *
 * Редизайн 24.10.2026: переключатель записи — в шапке карточки, шаги пронумерованы
 * (вебхук → воронка и стадия → проверка), как у «Анализа в сделку».
 */
export function BitrixDealsBlock({
  settings,
  onSettings,
}: {
  settings: Bitrix24Settings;
  onSettings: (next: Bitrix24Settings) => void;
}) {
  const [webhook, setWebhook] = useState("");
  const [categoryId, setCategoryId] = useState(String(settings.category_id));
  const [stageId, setStageId] = useState(settings.stage_id);
  const [check, setCheck] = useState<Bitrix24CheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [testDeal, setTestDeal] = useState<Bitrix24PushResult | null>(null);

  const apply = (next: Bitrix24Settings) => {
    onSettings(next);
    setCategoryId(String(next.category_id));
    setStageId(next.stage_id);
  };

  const save = async (patch: Record<string, unknown>) => {
    setIsBusy(true);
    setError(null);
    try {
      apply(await api.patch<Bitrix24Settings>("/integrations/bitrix24", patch));
      return true;
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось сохранить настройки",
      );
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

  const handleDisconnect = () => {
    if (
      !window.confirm("Удалить вебхук? Запись сделок через него прекратится.")
    )
      return;
    void save({ webhook_url: "" });
  };

  const handleSaveTarget = async () => {
    const category = Number(categoryId);
    if (!Number.isInteger(category) || category < 0 || !stageId.trim()) {
      setError(
        "Воронка — целое число, стадия — код стадии, например C5:PARSING",
      );
      return;
    }
    if (await save({ category_id: category, stage_id: stageId.trim() }))
      setCheck(null);
  };

  const handlePush = (enabled: boolean) => {
    if (
      enabled &&
      !window.confirm(
        "Разрешить запись сделок в CRM через вебхук? Портал боевой.",
      )
    )
      return;
    void save({ push_enabled: enabled });
  };

  const handleCheck = async () => {
    setIsBusy(true);
    setError(null);
    try {
      setCheck(
        await api.post<Bitrix24CheckResult>("/integrations/bitrix24/test", {}),
      );
      apply(await api.get<Bitrix24Settings>("/integrations/bitrix24"));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось проверить подключение",
      );
    } finally {
      setIsBusy(false);
    }
  };

  // Одна сделка «[ТЕСТ] …» из последнего тендера — посмотреть на портале, как легли поля.
  // Пишет в боевую CRM, поэтому с подтверждением.
  const handleTestDeal = async () => {
    if (
      !window.confirm(
        "Создать в Bitrix24 одну тестовую сделку «[ТЕСТ] …» из последнего тендера?",
      )
    )
      return;
    setIsBusy(true);
    setError(null);
    setTestDeal(null);
    try {
      setTestDeal(
        await api.post<Bitrix24PushResult>(
          "/integrations/bitrix24/test-deal",
          {},
        ),
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось создать тестовую сделку",
      );
    } finally {
      setIsBusy(false);
    }
  };

  const state = bitrixDealsState(settings);
  const checkOk = check ? check.success : settings.last_check_status === "ok";
  const targetSaved =
    categoryId === String(settings.category_id) &&
    stageId === settings.stage_id;

  return (
    <FeatureCard
      id="bitrix-deals"
      icon={<KanbanSquare size={18} />}
      title="Сделки через вебхук"
      description="Карточки закупок сделками в воронке CRM: название, сумма, НМЦК, сроки и ссылка в полях сделки."
      state={state}
      toggle={{
        checked: settings.push_enabled,
        onChange: handlePush,
        disabled: isBusy || (!settings.is_configured && !settings.push_enabled),
        label: "Запись сделок в CRM",
        blockedHint: settings.is_configured ? null : "Сначала шаг 1: вебхук",
      }}
      warning={
        state === "paused"
          ? "Пока выключено, в CRM через вебхук ничего не записывается. Включайте после проверки подключения — портал боевой."
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
        title="Входящий вебхук портала"
        hint={
          settings.is_configured ? (
            <>
              Задан:{" "}
              <code className="rounded bg-black/30 px-1 py-0.5 text-zinc-300">
                {settings.webhook_masked}
              </code>
              {settings.updated_by && settings.updated_at && (
                <>
                  {" "}
                  · {settings.updated_by},{" "}
                  {new Date(settings.updated_at).toLocaleString("ru-RU")}
                </>
              )}
            </>
          ) : (
            "Bitrix24 → Разработчикам → Входящий вебхук с правами на CRM."
          )
        }
        done={settings.is_configured}
      >
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="password"
            autoComplete="off"
            value={webhook}
            onChange={(e) => setWebhook(e.target.value)}
            placeholder={
              settings.is_configured
                ? "Новый вебхук, чтобы заменить"
                : "https://портал/rest/<пользователь>/<код>/"
            }
            aria-label="Вебхук"
            className={`!mt-0 min-w-[260px] flex-1 ${settingsInputClass}`}
          />
          <button
            onClick={handleSaveWebhook}
            disabled={isBusy || !webhook.trim()}
            className={secondaryButtonClass}
          >
            <Save size={13} />
            {settings.is_configured ? "Заменить" : "Сохранить"}
          </button>
          {settings.is_configured && (
            <button
              onClick={handleDisconnect}
              disabled={isBusy}
              className={dangerButtonClass}
            >
              Удалить
            </button>
          )}
        </div>
      </Step>

      <Step
        number={2}
        title="Куда класть сделки"
        hint="Воронка и стадия, на которой появляется новая сделка."
        done={settings.is_configured && targetSaved}
      >
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-zinc-500">
            Воронка (id)
            <input
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              className={`block w-24 ${settingsInputClass}`}
            />
          </label>
          <label className="text-xs text-zinc-500">
            Стадия
            {check && check.stages.length > 0 ? (
              <select
                value={stageId}
                onChange={(e) => setStageId(e.target.value)}
                className={`block min-w-[260px] ${settingsInputClass}`}
              >
                {!check.stages.some((s) => s.id === stageId) && (
                  <option value={stageId}>{stageId}</option>
                )}
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
                className={`block w-48 ${settingsInputClass}`}
              />
            )}
          </label>
          <button
            onClick={handleSaveTarget}
            disabled={isBusy || targetSaved}
            className={secondaryButtonClass}
          >
            <Save size={13} />
            Сохранить
          </button>
        </div>
      </Step>

      <Step
        number={3}
        title="Проверка подключения"
        hint="«Проверить» только читает стадии воронки и поля сделки. Тестовая сделка создаётся в CRM с пометкой «[ТЕСТ]»."
        done={settings.is_configured && checkOk}
      >
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleCheck}
            disabled={isBusy || !settings.is_configured}
            className={secondaryButtonClass}
          >
            <RefreshCw size={13} className={isBusy ? "animate-spin" : ""} />
            Проверить подключение
          </button>
          <button
            onClick={handleTestDeal}
            disabled={isBusy || !settings.is_configured}
            className={secondaryButtonClass}
          >
            <FlaskConical size={13} />
            Создать тестовую сделку
          </button>
        </div>

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
                <>
                  {" "}
                  Нет полей:{" "}
                  {check.missing_fields.map((f) => f.title).join(", ")}.
                </>
              )}
            </span>
          </div>
        )}

        {testDeal && (
          <div className="mt-3 flex items-start gap-2 text-xs text-emerald-400">
            <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
            <span>
              {testDeal.message}{" "}
              <a
                href={testDeal.url}
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                Открыть в Bitrix24
              </a>
            </span>
          </div>
        )}
      </Step>
    </FeatureCard>
  );
}
