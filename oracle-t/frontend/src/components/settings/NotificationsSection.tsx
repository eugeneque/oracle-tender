import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Mail,
  Megaphone,
  PlayCircle,
  Send,
  SendHorizonal,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  KnownRecipient,
  NotificationEntry,
  NotificationSettings,
} from "../../api/types";
import { RecipientPicker } from "./RecipientPicker";
import { RichTextEditor } from "./RichTextEditor";
import {
  FeatureCard,
  type FeatureState,
  FeatureSummary,
  Step,
  type SummaryItem,
  ToggleRow,
  notifyAppStatusChanged,
} from "../apps/AppKit";
import {
  SettingsNotice,
  primaryButtonClass,
  secondaryButtonClass,
  settingsInputClass,
} from "./ui";

const STATUS_LABELS: Record<
  NotificationEntry["status"],
  { text: string; className: string }
> = {
  sent: { text: "отправлено", className: "bg-emerald-500/10 text-emerald-400" },
  failed: { text: "ошибка", className: "bg-red-500/10 text-red-400" },
  skipped: { text: "не отправлено", className: "bg-zinc-500/10 text-zinc-400" },
};

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU");
}

/** Состояние автоматических писем для сводки и шапки карточки. */
function mailState(settings: NotificationSettings): FeatureState {
  if (!settings.is_configured) return "setup";
  return settings.is_enabled ? "on" : "paused";
}

/** Чего не хватает каналу — словами, по порядку шагов. Ручная рассылка берёт адреса из формы
 * и работает и без них, а автоматические письма без общего списка получателей не уходят. */
function mailMissing(settings: NotificationSettings): string | null {
  if (!settings.smtp_host) return "Шаг 1: укажите SMTP-сервер";
  if (!settings.from_address && !settings.smtp_username)
    return "Шаг 1: укажите логин ящика";
  if (!settings.recipients) return "Шаг 2: укажите получателей";
  return null;
}

const TRIGGERS = [
  [
    "trigger_new_relevant",
    "Новый релевантный тендер",
    "Закупка прошла профили отбора и модель.",
  ],
  ["trigger_high_ai_score", "Высокая AI-оценка по профилю", null],
  ["trigger_deadline_soon", "Приём заявок скоро закрывается", null],
  [
    "trigger_critical_error",
    "Критические ошибки системы",
    "Уходят «Получателям сообщений об ошибках».",
  ],
  [
    "trigger_documents_updated",
    "Обновление документов по СИ и руководств",
    "Еженедельный отчёт.",
  ],
] as const;

const FORM_KEYS = [
  "smtp_host",
  "smtp_port",
  "smtp_security",
  "smtp_username",
  "from_address",
  "recipients",
  "admin_recipients",
  "ai_score_threshold",
  "deadline_days_threshold",
  ...TRIGGERS.map(([key]) => key),
] as const;

/**
 * Настройки почтового канала (раздел 5.8 ТЗ) — только для роли «Администратор».
 *
 * Поля SMTP, а не IMAP: IMAP читает почту, отправляет — SMTP. У почтового ящика обычно оба
 * доступа с одним логином и паролем, поэтому вводятся те же учётные данные, только сервер
 * указывается исходящий (`smtp.yandex.ru`, `smtp.mail.ru` и т.п.).
 *
 * Редизайн 24.10.2026: флажок «рассылка включена» стоял мелко в углу и сохранялся только
 * общей кнопкой — его не замечали и считали, что почта сломана. Теперь это переключатель в
 * шапке карточки, он сохраняется сразу; пока канал не настроен, рядом сказано, какого шага не
 * хватает. Поля разложены по шагам «ящик → кому → о чём», пороги стоят у своих триггеров, а у
 * кнопки «Сохранить» видно, есть ли несохранённые правки.
 */
function MailSettings({
  onSettings,
}: {
  onSettings: (next: NotificationSettings) => void;
}) {
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [form, setForm] = useState<
    Partial<NotificationSettings> & { smtp_password?: string }
  >({});
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);
  const [savedNote, setSavedNote] = useState(false);

  const apply = useCallback(
    (data: NotificationSettings) => {
      setSettings(data);
      onSettings(data);
    },
    [onSettings],
  );

  const load = useCallback(async () => {
    try {
      const data = await api.get<NotificationSettings>(
        "/notifications/settings",
      );
      apply(data);
      setForm(data);
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось загрузить настройки уведомлений",
      );
    }
  }, [apply]);

  useEffect(() => {
    void load();
  }, [load]);

  const set = <K extends keyof NotificationSettings>(
    key: K,
    value: NotificationSettings[K],
  ) => {
    setSavedNote(false);
    setForm((current) => ({ ...current, [key]: value }));
  };

  const handleSave = async () => {
    setError(null);
    setTestResult(null);
    setIsSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      for (const key of FORM_KEYS) payload[key] = form[key];
      // Пустой пароль означает «оставить прежний» — отправлять его значило бы стереть
      // сохранённый (см. PATCH-семантику в app/services/notification_service.py).
      if (form.smtp_password) payload.smtp_password = form.smtp_password;
      const data = await api.patch<NotificationSettings>(
        "/notifications/settings",
        payload,
      );
      apply(data);
      setForm({ ...data });
      setSavedNote(true);
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

  // Переключатель рассылки сохраняется сразу и только он — несохранённые правки формы не
  // уходят вместе с ним и не теряются.
  const handleEnabled = async (enabled: boolean) => {
    setError(null);
    try {
      const data = await api.patch<NotificationSettings>(
        "/notifications/settings",
        { is_enabled: enabled },
      );
      apply(data);
      setForm((current) => ({ ...current, is_enabled: data.is_enabled }));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось переключить рассылку",
      );
    }
  };

  const handleTest = async () => {
    setError(null);
    setTestResult(null);
    setIsTesting(true);
    try {
      setTestResult(
        await api.post<{ success: boolean; message: string }>(
          "/notifications/test",
        ),
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось отправить проверочное письмо",
      );
    } finally {
      setIsTesting(false);
    }
  };

  if (!settings) {
    return error ? (
      <SettingsNotice tone="error">{error}</SettingsNotice>
    ) : (
      <div className="px-1 text-sm text-zinc-500">Загружаю настройки…</div>
    );
  }

  const state = mailState(settings);
  const missing = mailMissing(settings);
  const dirty =
    FORM_KEYS.some((key) => (form[key] ?? null) !== (settings[key] ?? null)) ||
    Boolean(form.smtp_password);
  const mailboxDone = Boolean(
    settings.smtp_host && (settings.from_address || settings.smtp_username),
  );

  return (
    <FeatureCard
      id="mail-auto"
      icon={<Mail size={18} />}
      title="Автоматические письма"
      description="Письма о новых релевантных закупках, сроках подачи и ошибках — сами, по триггерам ниже."
      state={state}
      toggle={{
        checked: settings.is_enabled,
        onChange: (value) => void handleEnabled(value),
        disabled: !settings.is_configured && !settings.is_enabled,
        label: "Автоматические письма",
        blockedHint: settings.is_configured ? null : missing,
      }}
      warning={
        state === "paused"
          ? "Канал настроен, но рассылка выключена — автоматические письма не уходят."
          : settings.is_enabled && missing
            ? `Рассылка включена, но письма не уйдут. ${missing}.`
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
        title="Почтовый ящик"
        hint="Исходящий сервер (SMTP) и учётная запись ящика, от имени которого уходят письма."
        done={mailboxDone}
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label className="block text-xs text-zinc-400">
            SMTP-сервер
            <input
              type="text"
              value={form.smtp_host ?? ""}
              onChange={(e) => set("smtp_host", e.target.value)}
              placeholder="smtp.yandex.ru"
              className={settingsInputClass}
            />
          </label>
          <label className="block text-xs text-zinc-400">
            Порт
            <input
              type="number"
              value={form.smtp_port ?? 465}
              onChange={(e) => set("smtp_port", Number(e.target.value))}
              className={settingsInputClass}
            />
          </label>
          <label className="block text-xs text-zinc-400">
            Шифрование
            <select
              value={form.smtp_security ?? "ssl"}
              onChange={(e) =>
                set(
                  "smtp_security",
                  e.target.value as NotificationSettings["smtp_security"],
                )
              }
              className={settingsInputClass}
            >
              <option value="ssl">SSL/TLS (обычно порт 465)</option>
              <option value="starttls">STARTTLS (обычно порт 587)</option>
              <option value="none">без шифрования</option>
            </select>
          </label>
          <label className="block text-xs text-zinc-400">
            Логин ящика
            <input
              type="text"
              value={form.smtp_username ?? ""}
              onChange={(e) => set("smtp_username", e.target.value)}
              autoComplete="off"
              className={settingsInputClass}
            />
          </label>
          <label className="block text-xs text-zinc-400">
            Пароль ящика
            <input
              type="password"
              value={form.smtp_password ?? ""}
              onChange={(e) => {
                setSavedNote(false);
                setForm((c) => ({ ...c, smtp_password: e.target.value }));
              }}
              placeholder={
                settings.has_password ? "•••••••• (сохранён)" : "не задан"
              }
              autoComplete="new-password"
              className={settingsInputClass}
            />
          </label>
          <label className="block text-xs text-zinc-400">
            Адрес отправителя{" "}
            <span className="text-zinc-600">— необязательно</span>
            <input
              type="email"
              value={form.from_address ?? ""}
              onChange={(e) => set("from_address", e.target.value)}
              // Пустое поле — письма уходят от логина ящика (24.10.2026).
              placeholder={
                form.smtp_username
                  ? `${form.smtp_username} (логин ящика)`
                  : "tenders@company.ru"
              }
              className={settingsInputClass}
            />
          </label>
        </div>
      </Step>

      <Step
        number={2}
        title="Кому писать"
        hint="Адреса через запятую. Без получателей автоматические письма не уходят."
        done={Boolean(settings.recipients)}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-xs text-zinc-400">
            Получатели
            <input
              type="text"
              value={form.recipients ?? ""}
              onChange={(e) => set("recipients", e.target.value)}
              placeholder="tenders@company.ru, manager@company.ru"
              className={settingsInputClass}
            />
          </label>
          <label className="block text-xs text-zinc-400">
            Получатели сообщений об ошибках{" "}
            <span className="text-zinc-600">— пусто: общий список</span>
            <input
              type="text"
              value={form.admin_recipients ?? ""}
              onChange={(e) => set("admin_recipients", e.target.value)}
              placeholder="администраторы"
              className={settingsInputClass}
            />
          </label>
        </div>
      </Step>

      <Step
        number={3}
        title="О чём писать"
        hint="Выключенный триггер не шлёт писем, даже когда рассылка включена."
      >
        <div className="divide-y divide-white/[0.06]">
          {TRIGGERS.map(([key, label, hint]) => (
            <ToggleRow
              key={key}
              title={label}
              hint={hint ?? undefined}
              checked={(form[key] as boolean) ?? true}
              onChange={(value) => set(key, value)}
            >
              {key === "trigger_high_ai_score" && (
                <label className="flex items-center gap-2 text-xs text-zinc-500">
                  от
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={form.ai_score_threshold ?? 80}
                    onChange={(e) =>
                      set("ai_score_threshold", Number(e.target.value))
                    }
                    aria-label="Порог AI-оценки, %"
                    className={`!mt-0 w-20 tabular-nums ${settingsInputClass}`}
                  />
                  %
                </label>
              )}
              {key === "trigger_deadline_soon" && (
                <label className="flex items-center gap-2 text-xs text-zinc-500">
                  за
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={form.deadline_days_threshold ?? 5}
                    onChange={(e) =>
                      set("deadline_days_threshold", Number(e.target.value))
                    }
                    aria-label="За сколько дней до окончания подачи"
                    className={`!mt-0 w-20 tabular-nums ${settingsInputClass}`}
                  />
                  дн. до окончания
                </label>
              )}
            </ToggleRow>
          ))}
        </div>
      </Step>

      <div className="flex flex-wrap items-center gap-3 px-5 py-4">
        <button
          onClick={handleSave}
          disabled={isSaving || !dirty}
          className={primaryButtonClass}
        >
          {isSaving ? "Сохраняю…" : "Сохранить"}
        </button>
        <button
          onClick={handleTest}
          disabled={isTesting || dirty}
          title={
            dirty
              ? "Письмо уходит по сохранённым настройкам — сначала сохраните"
              : "Работает и при выключенной рассылке"
          }
          className={secondaryButtonClass}
        >
          <PlayCircle size={13} />
          {isTesting ? "Отправляю…" : "Отправить проверочное письмо"}
        </button>
        <span className="text-xs text-zinc-500">
          {dirty
            ? "Есть несохранённые изменения"
            : savedNote
              ? "Сохранено"
              : settings.updated_at
                ? `Изменено ${formatDateTime(settings.updated_at)}${settings.updated_by ? ` · ${settings.updated_by}` : ""}`
                : null}
        </span>
        {testResult && (
          <div className="w-full">
            <SettingsNotice tone={testResult.success ? "success" : "error"}>
              {testResult.message}
            </SettingsNotice>
          </div>
        )}
      </div>
    </FeatureCard>
  );
}

/**
 * Произвольное письмо администратора: новости платформы, регламентные работы, объявления.
 *
 * Отдельно от четырёх триггеров раздела 5.8 ТЗ и от проверочного письма: те система шлёт
 * сама и по своим адресам, это — человек и по адресам, которые выбирает здесь же. В журнал
 * попадает наравне с остальными, с пометкой автора.
 *
 * Флаг «рассылка включена» на эту форму не влияет — он выключает автоматические письма.
 * Нажатие кнопки здесь всегда означает отправку.
 */
function BroadcastForm({ onSent }: { onSent: () => void }) {
  const [subject, setSubject] = useState("");
  const [bodyHtml, setBodyHtml] = useState("");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<KnownRecipient[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [result, setResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Смена ключа очищает редактор: его содержимое живёт в DOM, а не в состоянии React.
  const [editorKey, setEditorKey] = useState(0);

  const loadSuggestions = useCallback(async () => {
    try {
      setSuggestions(
        await api.get<KnownRecipient[]>("/notifications/recipients"),
      );
    } catch {
      // Подсказки — удобство: без них поле остаётся полностью рабочим, ошибку показывать
      // не за что.
    }
  }, []);

  useEffect(() => {
    void loadSuggestions();
  }, [loadSuggestions]);

  const hasText = bodyHtml.replace(/<[^>]*>/g, "").trim().length > 0;
  const canSend = subject.trim().length > 0 && hasText && recipients.length > 0;

  const handleSend = async () => {
    setError(null);
    setResult(null);
    setIsSending(true);
    try {
      const response = await api.post<{ success: boolean; message: string }>(
        "/notifications/broadcast",
        { subject: subject.trim(), body_html: bodyHtml, recipients },
      );
      setResult(response);
      if (response.success) {
        setSubject("");
        setBodyHtml("");
        setRecipients([]);
        setEditorKey((key) => key + 1);
        // Адресаты этого письма должны сразу оказаться в подсказках следующего.
        void loadSuggestions();
        onSent();
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Не удалось отправить письмо",
      );
    } finally {
      setIsSending(false);
    }
  };

  const inputClass = settingsInputClass;

  return (
    <FeatureCard
      id="mail-broadcast"
      icon={<Megaphone size={18} />}
      title="Письмо пользователям"
      description="Новости платформы, плановые работы, объявления — вручную, по адресам из формы. Работает и при выключенной автоматической рассылке."
    >
      <div className="px-5 py-5">
        {error && <SettingsNotice tone="error">{error}</SettingsNotice>}

        <div className={error ? "mt-4" : ""}>
          <label className="block text-xs text-zinc-400">
            Получатели
            <RecipientPicker
              value={recipients}
              onChange={setRecipients}
              suggestions={suggestions}
            />
          </label>
        </div>

        <label className="mt-4 block text-xs text-zinc-400">
          Тема письма
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            maxLength={500}
            placeholder="Например: плановые работы в субботу"
            className={inputClass}
          />
        </label>

        <div className="mt-4 text-xs text-zinc-400">
          Текст письма
          <RichTextEditor
            value={bodyHtml}
            onChange={setBodyHtml}
            resetKey={editorKey}
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            onClick={handleSend}
            disabled={!canSend || isSending}
            className={primaryButtonClass}
          >
            <Send size={14} />
            {isSending ? "Отправляю…" : "Отправить письмо"}
          </button>
          <span className="text-xs text-zinc-600">
            {recipients.length > 0
              ? `получателей: ${recipients.length}`
              : "укажите хотя бы одного получателя"}
          </span>
        </div>

        {result && (
          <div className="mt-4">
            <SettingsNotice tone={result.success ? "success" : "error"}>
              {result.message}
            </SettingsNotice>
          </div>
        )}
      </div>
    </FeatureCard>
  );
}

/**
 * Страница приложения «Почта и уведомления»: журнал отправок — всем пользователям, настройки
 * почты и ручная рассылка — только администратору.
 *
 * Редизайн 24.10.2026: сводка «что работает» сверху и карточки функций с переключателем в
 * шапке, как у Bitrix24 (`components/apps/AppKit.tsx`). Свёрнутый режим для старой страницы
 * «Настройки» убран — блок живёт только на странице приложения.
 */
export function NotificationsSection({ isAdmin }: { isAdmin: boolean }) {
  const [entries, setEntries] = useState<NotificationEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [mail, setMail] = useState<NotificationSettings | null>(null);

  const loadEntries = useCallback(() => {
    api
      .get<NotificationEntry[]>("/notifications?limit=100")
      .then((data) => {
        setEntries(data);
        setError(null);
      })
      .catch((err) =>
        setError(
          err instanceof ApiError
            ? err.message
            : "Не удалось загрузить журнал уведомлений",
        ),
      );
  }, []);

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  const summary: SummaryItem[] = [];
  if (mail) {
    const state = mailState(mail);
    const missing = mailMissing(mail);
    summary.push({
      id: "mail-auto",
      title: "Автоматические письма",
      state,
      hint:
        state === "on"
          ? `Уходят от ${mail.from_address || mail.smtp_username} на ${mail.recipients}.`
          : state === "paused"
            ? "Канал настроен, рассылка выключена — включите переключатель."
            : `${missing ?? "Настройте почтовый ящик"}.`,
    });
    summary.push({
      id: "mail-broadcast",
      title: "Письмо пользователям",
      state:
        mail.smtp_host && (mail.from_address || mail.smtp_username)
          ? "on"
          : "setup",
      hint: "Ручная рассылка по адресам из формы.",
    });
  }
  useEffect(() => {
    if (mail) notifyAppStatusChanged();
  }, [mail]);

  const lastFailure = entries?.find((entry) => entry.status !== "sent");

  return (
    <div className="space-y-4">
      {summary.length > 0 && <FeatureSummary items={summary} />}
      {isAdmin && <MailSettings onSettings={setMail} />}
      {isAdmin && <BroadcastForm onSent={loadEntries} />}

      <FeatureCard
        id="mail-log"
        icon={<SendHorizonal size={18} />}
        title="Журнал отправок"
        description={
          lastFailure &&
          entries?.[0]?.id === lastFailure.id &&
          lastFailure.error
            ? `Последнее письмо не ушло: ${lastFailure.error}`
            : "Что система отправила, кому и с каким итогом. Нажмите строку, чтобы увидеть письмо."
        }
      >
        <div className="px-5 py-4">
          {error && (
            <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
              {error}
            </div>
          )}

          {entries === null ? (
            <div className="text-sm text-zinc-500">
              Загружаю журнал уведомлений…
            </div>
          ) : entries.length === 0 ? (
            <div className="flex items-start gap-2 text-sm text-zinc-500">
              <AlertTriangle
                size={15}
                className="mt-0.5 shrink-0 text-zinc-600"
              />
              <span>
                Уведомлений пока не было. Записи появятся, как только сработает
                любой из триггеров — даже если почта ещё не настроена: такие
                события сохраняются со статусом «не отправлено».
              </span>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-white/[0.08] text-zinc-500">
                  <tr>
                    <th className="py-2 pr-4 font-medium">Время</th>
                    <th className="py-2 pr-4 font-medium">Триггер</th>
                    <th className="py-2 pr-4 font-medium">Тема</th>
                    <th className="py-2 pr-4 font-medium">Кому</th>
                    <th className="py-2 font-medium">Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => (
                    <tr
                      key={entry.id}
                      onClick={() =>
                        setOpenId(openId === entry.id ? null : entry.id)
                      }
                      className="cursor-pointer border-t border-white/[0.06] align-top hover:bg-white/[0.02]"
                    >
                      <td className="py-2 pr-4 text-zinc-400">
                        {formatDateTime(entry.created_at)}
                      </td>
                      <td className="py-2 pr-4 text-zinc-300">
                        {entry.trigger_title}
                      </td>
                      <td className="py-2 pr-4 text-zinc-300">
                        {entry.subject}
                        {openId === entry.id && (
                          <div className="mt-2 rounded-lg border border-white/[0.06] bg-black/30 p-3 text-xs text-zinc-400">
                            {entry.body_html ? (
                              // Разметка письма, а не текст: администратор должен видеть
                              // то же, что получил адресат. HTML очищен на сервере по
                              // белому списку тегов (app/services/email_html.py) — в базу
                              // попадает уже безопасный, и другого источника у него нет.
                              <div
                                className="letter-body text-zinc-300"
                                dangerouslySetInnerHTML={{
                                  __html: entry.body_html,
                                }}
                              />
                            ) : (
                              <div className="whitespace-pre-wrap font-mono">
                                {entry.body}
                              </div>
                            )}
                            {entry.sent_by && (
                              <div className="mt-2 text-zinc-500">
                                Отправил: {entry.sent_by}
                              </div>
                            )}
                            {entry.error && (
                              <div className="mt-2 text-red-400">
                                Причина: {entry.error}
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-zinc-500">
                        {entry.recipients ?? "—"}
                      </td>
                      <td className="py-2">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${
                            STATUS_LABELS[entry.status].className
                          }`}
                        >
                          {STATUS_LABELS[entry.status].text}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </FeatureCard>
    </div>
  );
}
