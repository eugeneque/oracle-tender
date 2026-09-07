import { useEffect, useState } from "react";
import {
  ChevronDown,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { Source, SourceCredential, SourceCredentialInput } from "../../api/types";

const inputClass =
  "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("ru-RU");
}

/**
 * Форма блока учётных данных.
 *
 * При редактировании поле пароля остаётся пустым и отправляется, только если админ ввёл
 * новое значение: старый пароль сервер не отдаёт (и не должен), а требовать вводить его
 * заново ради переименования блока — лишняя работа и лишний повод записать его куда-нибудь рядом.
 */
function CredentialForm({
  sources,
  credential,
  onSubmit,
  onCancel,
}: {
  sources: Source[];
  credential: SourceCredential | null;
  onSubmit: (payload: SourceCredentialInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [sourceId, setSourceId] = useState(credential?.source_id ?? sources[0]?.id ?? "");
  const [label, setLabel] = useState(credential?.label ?? "");
  const [username, setUsername] = useState(credential?.username ?? "");
  const [password, setPassword] = useState("");
  const [notes, setNotes] = useState(credential?.notes ?? "");
  const [isActive, setIsActive] = useState(credential?.is_active ?? true);
  const [isSaving, setIsSaving] = useState(false);

  const isEdit = credential !== null;
  const canSubmit =
    sourceId !== "" && label.trim() !== "" && username.trim() !== "" && (isEdit || password !== "");

  const submit = async () => {
    setIsSaving(true);
    try {
      const payload: SourceCredentialInput = {
        label: label.trim(),
        username: username.trim(),
        notes: notes.trim() || null,
        is_active: isActive,
      };
      if (!isEdit) payload.source_id = sourceId;
      if (password !== "") payload.password = password;
      await onSubmit(payload);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="mb-4 rounded-xl border border-indigo-500/25 bg-indigo-500/[0.04] p-4">
      <div className="mb-3 text-sm font-semibold text-indigo-300">
        {isEdit ? `Блок «${credential.label}»` : "Новый блок учётных данных"}
      </div>

      <div className="grid max-w-3xl gap-4 sm:grid-cols-2">
        <label className="block text-xs text-zinc-400">
          Площадка
          <select
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            disabled={isEdit}
            className={`${inputClass} bg-zinc-900 disabled:opacity-60`}
          >
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-zinc-400">
          Название блока
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Основная учётка"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Логин
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Пароль
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={isEdit ? "оставьте пустым, чтобы не менять" : "пароль от личного кабинета"}
            autoComplete="new-password"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400 sm:col-span-2">
          Примечание
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="например: доступ к закрытому разделу 223-ФЗ"
            className={inputClass}
          />
        </label>
      </div>

      <label className="mt-3 flex items-center gap-2 text-sm text-zinc-300">
        <input
          type="checkbox"
          checked={isActive}
          onChange={(e) => setIsActive(e.target.checked)}
          className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
        />
        Использовать при опросе площадки
      </label>

      <div className="mt-4 flex items-center gap-2">
        <button
          onClick={() => void submit()}
          disabled={!canSubmit || isSaving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          {isSaving && <Loader2 size={13} className="animate-spin" />}
          Сохранить
        </button>
        <button
          onClick={onCancel}
          disabled={isSaving}
          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-zinc-300 hover:bg-white/5"
        >
          <X size={13} />
          Отмена
        </button>
      </div>
    </div>
  );
}

/**
 * Раздел «Пользовательские данные» — логины и пароли к личным кабинетам площадок
 * (раздел 4.1, 5.1 ТЗ). Доступен только администратору.
 *
 * Пароль сюда можно только записать: сервер его не возвращает, поэтому в списке стоит маска,
 * а не значение. Хранится он зашифрованным в базе на этой же машине, ключ — в файле
 * `.credentials_key` в корне проекта.
 */
export function CredentialsSection() {
  const [credentials, setCredentials] = useState<SourceCredential[] | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [isExpanded, setIsExpanded] = useState(true);
  const [editing, setEditing] = useState<SourceCredential | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setCredentials(await api.get<SourceCredential[]>("/source-credentials"));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить учётные данные");
    }
  };

  useEffect(() => {
    void load();
    void api.get<Source[]>("/sources").then(setSources);
  }, []);

  const handleCreate = async (payload: SourceCredentialInput) => {
    try {
      await api.post("/source-credentials", payload);
      setIsCreating(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить учётные данные");
    }
  };

  const handleUpdate = async (payload: SourceCredentialInput) => {
    if (!editing) return;
    try {
      await api.patch(`/source-credentials/${editing.id}`, payload);
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить изменения");
    }
  };

  const handleDelete = async (credential: SourceCredential) => {
    // Удаление пароля необратимо — восстановить его из базы нельзя даже администратору,
    // поэтому спрашиваем подтверждение.
    if (!window.confirm(`Удалить блок «${credential.label}» (${credential.source_name})?`)) return;
    try {
      await api.delete(`/source-credentials/${credential.id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить учётные данные");
    }
  };

  return (
    <div className="mt-6 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">Пользовательские данные</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Логины и пароли к личным кабинетам площадок — для источников, которые отдают
            закупки только авторизованному пользователю. Хранятся на этом компьютере в
            зашифрованном виде и переживают перезагрузку.
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

          <div className="mb-4 flex items-start gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-xs text-zinc-500">
            <ShieldCheck size={14} className="mt-0.5 shrink-0 text-emerald-400" />
            <span>
              Пароль шифруется ключом из файла <code className="text-zinc-400">.credentials_key</code>{" "}
              в корне проекта и обратно через интерфейс не отдаётся — его можно только заменить.
              Не удаляйте этот файл: без него сохранённые пароли не восстановить.
            </span>
          </div>

          {isCreating && (
            <CredentialForm
              sources={sources}
              credential={null}
              onSubmit={handleCreate}
              onCancel={() => setIsCreating(false)}
            />
          )}
          {editing && (
            <CredentialForm
              sources={sources}
              credential={editing}
              onSubmit={handleUpdate}
              onCancel={() => setEditing(null)}
            />
          )}

          {credentials === null ? (
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <Loader2 size={14} className="animate-spin" />
              Загрузка…
            </div>
          ) : credentials.length === 0 ? (
            <div className="rounded-lg border border-dashed border-white/10 p-5 text-center text-sm text-zinc-500">
              Учётные данные не заданы. Добавьте блок для площадки, которая требует входа в
              личный кабинет.
            </div>
          ) : (
            <div className="space-y-2">
              {credentials.map((credential) => (
                <div
                  key={credential.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <KeyRound size={14} className="shrink-0 text-indigo-400" />
                      <span className="text-sm text-zinc-100">{credential.label}</span>
                      <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs text-zinc-400">
                        {credential.source_name}
                      </span>
                      {!credential.is_active && (
                        <span className="rounded-md border border-white/10 px-2 py-0.5 text-[11px] text-zinc-500">
                          выключен
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-xs text-zinc-500">
                      Логин: <span className="text-zinc-300">{credential.username}</span> · Пароль:{" "}
                      <span className="text-zinc-400">{credential.password_masked}</span>
                      {credential.notes ? ` · ${credential.notes}` : ""}
                    </div>
                    <div className="mt-0.5 text-[11px] text-zinc-600">
                      Изменено {formatDateTime(credential.updated_at)}
                      {credential.updated_by ? ` · ${credential.updated_by}` : ""}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      onClick={() => {
                        setIsCreating(false);
                        setEditing(credential);
                      }}
                      className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5"
                    >
                      <Pencil size={13} />
                      Изменить
                    </button>
                    <button
                      onClick={() => void handleDelete(credential)}
                      className="flex items-center gap-1.5 rounded-lg border border-red-500/20 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10"
                    >
                      <Trash2 size={13} />
                      Удалить
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <button
            onClick={() => {
              setEditing(null);
              setIsCreating(true);
            }}
            className="mt-4 flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-medium text-white hover:bg-indigo-400"
          >
            <Plus size={14} />
            Добавить блок
          </button>
        </div>
      )}
    </div>
  );
}
