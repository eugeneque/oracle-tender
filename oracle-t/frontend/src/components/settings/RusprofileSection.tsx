import { useEffect, useState } from "react";
import { ChevronDown, Globe, PlayCircle } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { RusprofileSettings, YandexConnectionTestResult } from "../../api/types";
import { formatDateTime } from "../../utils/format";

const inputClass =
  "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

/**
 * Блок «Rusprofile» страницы «Интеграции» (18.09.2026).
 *
 * Учётная запись rusprofile.ru, под которой раздел «Моя компания» заполняется с сайта:
 * реквизиты, лицензии, реализованные проекты и история участий — в том числе проигрыши,
 * которых нет в реестре контрактов ЕИС. Сама синхронизация запускается кнопкой в
 * «Моей компании»; здесь — только логин, пароль, проверка входа и итог последнего запуска.
 *
 * Пароль отправляется только когда администратор ввёл новое значение: PATCH с одним логином
 * не трогает уже сохранённый пароль (см. RusprofileSettingsUpdate на бэкенде).
 */
export function RusprofileSection() {
  const [settings, setSettings] = useState<RusprofileSettings | null>(null);
  const [isExpanded, setIsExpanded] = useState(true);
  const [loginInput, setLoginInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<YandexConnectionTestResult | null>(null);

  const load = async () => {
    try {
      const data = await api.get<RusprofileSettings>("/integrations/rusprofile");
      setSettings(data);
      setLoginInput(data.login ?? "");
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить настройки rusprofile");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleSave = async () => {
    setError(null);
    setTestResult(null);
    setIsSaving(true);
    try {
      const payload: { login: string; password?: string } = { login: loginInput.trim() };
      if (passwordInput !== "") {
        payload.password = passwordInput;
      }
      const data = await api.patch<RusprofileSettings>("/integrations/rusprofile", payload);
      setSettings(data);
      setPasswordInput("");
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
      setTestResult(await api.post<YandexConnectionTestResult>("/integrations/rusprofile/test"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось проверить подключение");
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Globe size={15} className="text-indigo-400" />
            Rusprofile
            {settings && (
              <span
                className={`rounded-md border px-2 py-0.5 text-[11px] font-normal ${
                  settings.is_configured
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    : "border-white/10 text-zinc-500"
                }`}
              >
                {settings.is_configured ? "настроено" : "не настроено"}
              </span>
            )}
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Учётная запись rusprofile.ru: под ней «Моя компания» заполняется с сайта — реквизиты,
            лицензии, реализованные проекты и история участий с проигрышами, которых нет в ЕИС.
            Запуск — кнопкой «Обновить из rusprofile» в разделе «Моя компания».
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

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-xs text-zinc-400">
              Логин (e-mail учётной записи)
              <input
                type="email"
                value={loginInput}
                onChange={(e) => setLoginInput(e.target.value)}
                placeholder="user@company.ru"
                autoComplete="off"
                className={inputClass}
              />
            </label>
            <label className="block text-xs text-zinc-400">
              Пароль
              <input
                type="password"
                value={passwordInput}
                onChange={(e) => setPasswordInput(e.target.value)}
                placeholder={settings?.has_password ? "•••••••• (сохранён)" : "не задан"}
                autoComplete="new-password"
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
              onClick={handleSave}
              disabled={isSaving}
              className="rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              {isSaving ? "Сохраняю…" : "Сохранить"}
            </button>
            <button
              onClick={handleTest}
              disabled={isTesting || !settings?.is_configured}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
            >
              <PlayCircle size={13} />
              {isTesting ? "Проверяю…" : "Проверить вход"}
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

          {settings?.last_sync_at && (
            <div className="mt-4 rounded-lg border border-white/[0.06] bg-black/20 px-4 py-2.5 text-xs">
              <div className="text-zinc-400">
                Последняя синхронизация: {formatDateTime(settings.last_sync_at)} ·{" "}
                <span className={settings.last_sync_status === "ok" ? "text-emerald-400" : "text-red-400"}>
                  {settings.last_sync_status === "ok" ? "успешно" : "с ошибкой"}
                </span>
              </div>
              {settings.last_sync_message && (
                <div className="mt-1 text-zinc-500">{settings.last_sync_message}</div>
              )}
            </div>
          )}

          <p className="mt-4 text-[11px] leading-relaxed text-zinc-600">
            Если сайт после нескольких неудачных попыток входа потребует капчу, войдите один раз
            в браузере под этой учётной записью и повторите проверку. Значения, закрытые
            подпиской, система не сохраняет — при истёкшей подписке итог синхронизации об этом
            предупредит.
          </p>
        </div>
      )}
    </div>
  );
}
