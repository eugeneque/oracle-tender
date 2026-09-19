import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  ChevronDown,
  Clock,
  Mail,
  Megaphone,
  PlayCircle,
  Send,
  SendHorizonal,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { KnownRecipient, NotificationEntry, NotificationSettings } from "../../api/types";
import { RecipientPicker } from "./RecipientPicker";
import { RichTextEditor } from "./RichTextEditor";

const STATUS_LABELS: Record<NotificationEntry["status"], { text: string; className: string }> = {
  sent: { text: "отправлено", className: "bg-emerald-500/10 text-emerald-400" },
  failed: { text: "ошибка", className: "bg-red-500/10 text-red-400" },
  skipped: { text: "не отправлено", className: "bg-zinc-500/10 text-zinc-400" },
};

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU");
}

/**
 * Настройки почтового канала (раздел 5.8 ТЗ) — только для роли «Администратор».
 *
 * Поля SMTP, а не IMAP: IMAP читает почту, отправляет — SMTP. У почтового ящика обычно оба
 * доступа с одним логином и паролем, поэтому вводятся те же учётные данные, только сервер
 * указывается исходящий (`smtp.yandex.ru`, `smtp.mail.ru` и т.п.).
 */
function MailSettings() {
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [form, setForm] = useState<Partial<NotificationSettings> & { smtp_password?: string }>({});
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.get<NotificationSettings>("/notifications/settings");
      setSettings(data);
      setForm(data);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить настройки уведомлений");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const set = <K extends keyof NotificationSettings>(key: K, value: NotificationSettings[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSave = async () => {
    setError(null);
    setTestResult(null);
    setIsSaving(true);
    try {
      const payload = { ...form };
      // Пустой пароль означает «оставить прежний» — отправлять его значило бы стереть
      // сохранённый (см. PATCH-семантику в app/services/notification_service.py).
      if (!payload.smtp_password) delete payload.smtp_password;
      const data = await api.patch<NotificationSettings>("/notifications/settings", payload);
      setSettings(data);
      setForm({ ...data });
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
        await api.post<{ success: boolean; message: string }>("/notifications/test"),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отправить проверочное письмо");
    } finally {
      setIsTesting(false);
    }
  };

  if (!settings) {
    return <div className="px-5 py-4 text-sm text-zinc-500">Загружаю настройки…</div>;
  }

  const inputClass =
    "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

  return (
    <div className="border-t border-white/[0.08] px-5 py-4">
      {error && (
        <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 text-sm font-medium text-zinc-200">
        <span className="flex items-center gap-2">
          <Mail size={15} className="text-indigo-400" />
          Почтовый канал
        </span>
        {settings.is_configured ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-400">
            <CheckCircle2 size={13} />
            настроен
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-400">
            <Clock size={13} />
            не настроен
          </span>
        )}
        <label className="ml-auto flex items-center gap-2 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={form.is_enabled ?? false}
            onChange={(e) => set("is_enabled", e.target.checked)}
            className="h-4 w-4 rounded border-white/20 bg-black/30"
          />
          рассылка включена
        </label>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block text-xs text-zinc-400">
          SMTP-сервер
          <input
            type="text"
            value={form.smtp_host ?? ""}
            onChange={(e) => set("smtp_host", e.target.value)}
            placeholder="smtp.yandex.ru"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Порт
          <input
            type="number"
            value={form.smtp_port ?? 465}
            onChange={(e) => set("smtp_port", Number(e.target.value))}
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Шифрование
          <select
            value={form.smtp_security ?? "ssl"}
            onChange={(e) =>
              set("smtp_security", e.target.value as NotificationSettings["smtp_security"])
            }
            className={inputClass}
          >
            <option value="ssl">SSL/TLS (обычно порт 465)</option>
            <option value="starttls">STARTTLS (обычно порт 587)</option>
            <option value="none">без шифрования</option>
          </select>
        </label>
        <label className="block text-xs text-zinc-400">
          Адрес отправителя
          <input
            type="email"
            value={form.from_address ?? ""}
            onChange={(e) => set("from_address", e.target.value)}
            placeholder="tenders@company.ru"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Логин ящика
          <input
            type="text"
            value={form.smtp_username ?? ""}
            onChange={(e) => set("smtp_username", e.target.value)}
            autoComplete="off"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Пароль ящика
          <input
            type="password"
            value={form.smtp_password ?? ""}
            onChange={(e) => setForm((c) => ({ ...c, smtp_password: e.target.value }))}
            placeholder={settings.has_password ? "•••••••• (сохранён)" : "не задан"}
            autoComplete="new-password"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400 sm:col-span-2">
          Получатели
          <input
            type="text"
            value={form.recipients ?? ""}
            onChange={(e) => set("recipients", e.target.value)}
            placeholder="через запятую"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400 sm:col-span-2">
          Получатели сообщений об ошибках
          <input
            type="text"
            value={form.admin_recipients ?? ""}
            onChange={(e) => set("admin_recipients", e.target.value)}
            placeholder="администраторы, через запятую"
            className={inputClass}
          />
        </label>
      </div>

      <div className="mt-5 text-xs font-medium text-zinc-300">Триггеры (раздел 5.8 ТЗ)</div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {(
          [
            ["trigger_new_relevant", "Новый релевантный тендер"],
            ["trigger_high_ai_score", "Высокая AI-оценка по профилю"],
            ["trigger_deadline_soon", "Приём заявок скоро закрывается"],
            ["trigger_critical_error", "Критические ошибки системы"],
            ["trigger_documents_updated", "Обновление документов по СИ и руководств (еженедельный отчёт)"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={(form[key] as boolean) ?? true}
              onChange={(e) => set(key, e.target.checked)}
              className="h-4 w-4 rounded border-white/20 bg-black/30"
            />
            {label}
          </label>
        ))}
      </div>

      <div className="mt-4 grid max-w-md gap-4 sm:grid-cols-2">
        <label className="block text-xs text-zinc-400">
          Порог AI-оценки по профилю, %
          <input
            type="number"
            min={0}
            max={100}
            value={form.ai_score_threshold ?? 80}
            onChange={(e) => set("ai_score_threshold", Number(e.target.value))}
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          За сколько дней до окончания подачи
          <input
            type="number"
            min={1}
            max={60}
            value={form.deadline_days_threshold ?? 5}
            onChange={(e) => set("deadline_days_threshold", Number(e.target.value))}
            className={inputClass}
          />
        </label>
      </div>

      {settings.updated_at && (
        <p className="mt-3 text-xs text-zinc-500">
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
          disabled={isTesting}
          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
        >
          <PlayCircle size={13} />
          {isTesting ? "Отправляю…" : "Отправить проверочное письмо"}
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
    </div>
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
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Смена ключа очищает редактор: его содержимое живёт в DOM, а не в состоянии React.
  const [editorKey, setEditorKey] = useState(0);

  const loadSuggestions = useCallback(async () => {
    try {
      setSuggestions(await api.get<KnownRecipient[]>("/notifications/recipients"));
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
      setError(err instanceof ApiError ? err.message : "Не удалось отправить письмо");
    } finally {
      setIsSending(false);
    }
  };

  const inputClass =
    "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

  return (
    <div className="border-t border-white/[0.08] px-5 py-4">
      <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
        <Megaphone size={15} className="text-indigo-400" />
        Письмо пользователям
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        Произвольное сообщение о новостях платформы. Уходит с того же ящика, что и
        автоматические уведомления, и попадает в журнал ниже. Адреса, на которые письма уже
        отправлялись, подсказываются в поле получателей.
      </p>

      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="mt-4">
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
        <RichTextEditor value={bodyHtml} onChange={setBodyHtml} resetKey={editorKey} />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={handleSend}
          disabled={!canSend || isSending}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          <Send size={13} />
          {isSending ? "Отправляю…" : "Отправить письмо"}
        </button>
        <span className="text-xs text-zinc-600">
          {recipients.length > 0
            ? `получателей: ${recipients.length}`
            : "укажите хотя бы одного получателя"}
        </span>
      </div>

      {result && (
        <div
          className={`mt-4 rounded-lg border px-4 py-2.5 text-sm ${
            result.success
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
              : "border-red-500/20 bg-red-500/10 text-red-400"
          }`}
        >
          {result.message}
        </div>
      )}
    </div>
  );
}

/**
 * Раздел «Уведомления»: журнал отправок — всем пользователям, настройки почты — только
 * администратору. Свёрнут по умолчанию, как и «Логирование»: рассылка работает сама, и
 * заглядывают сюда, когда письмо не пришло или пришло не то.
 */
export function NotificationsSection({ isAdmin }: { isAdmin: boolean }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [entries, setEntries] = useState<NotificationEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const loadEntries = useCallback(() => {
    api
      .get<NotificationEntry[]>("/notifications?limit=100")
      .then((data) => {
        setEntries(data);
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить журнал уведомлений"),
      );
  }, []);

  useEffect(() => {
    if (!isExpanded) return;
    loadEntries();
  }, [isExpanded, loadEntries]);

  return (
    <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Bell size={15} className="text-indigo-400" />
            Уведомления
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Письма по триггерам раздела 5.8 ТЗ: новый релевантный тендер, высокий процент
            победителя, скорое закрытие приёма заявок, критические ошибки. Ниже — журнал
            отправок{isAdmin ? " и настройки почтового ящика" : ""}.
          </p>
        </div>
        <ChevronDown
          size={18}
          className={`shrink-0 text-zinc-500 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {isExpanded && (
        <>
          {isAdmin && <MailSettings />}
          {isAdmin && <BroadcastForm onSent={loadEntries} />}

          <div className="border-t border-white/[0.08] px-5 py-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-zinc-200">
              <SendHorizonal size={15} className="text-indigo-400" />
              Журнал отправок
            </div>

            {error && (
              <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
                {error}
              </div>
            )}

            {entries === null ? (
              <div className="text-sm text-zinc-500">Загружаю журнал уведомлений…</div>
            ) : entries.length === 0 ? (
              <div className="flex items-start gap-2 text-sm text-zinc-500">
                <AlertTriangle size={15} className="mt-0.5 shrink-0 text-zinc-600" />
                <span>
                  Уведомлений пока не было. Записи появятся, как только сработает любой из
                  триггеров — даже если почта ещё не настроена: такие события сохраняются со
                  статусом «не отправлено».
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
                        onClick={() => setOpenId(openId === entry.id ? null : entry.id)}
                        className="cursor-pointer border-t border-white/[0.06] align-top hover:bg-white/[0.02]"
                      >
                        <td className="py-2 pr-4 text-zinc-400">{formatDateTime(entry.created_at)}</td>
                        <td className="py-2 pr-4 text-zinc-300">{entry.trigger_title}</td>
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
                                  dangerouslySetInnerHTML={{ __html: entry.body_html }}
                                />
                              ) : (
                                <div className="whitespace-pre-wrap font-mono">{entry.body}</div>
                              )}
                              {entry.sent_by && (
                                <div className="mt-2 text-zinc-500">
                                  Отправил: {entry.sent_by}
                                </div>
                              )}
                              {entry.error && (
                                <div className="mt-2 text-red-400">Причина: {entry.error}</div>
                              )}
                            </div>
                          )}
                        </td>
                        <td className="py-2 pr-4 text-zinc-500">{entry.recipients ?? "—"}</td>
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
        </>
      )}
    </div>
  );
}
