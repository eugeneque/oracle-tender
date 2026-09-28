import { useEffect, useState } from "react";
import {
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
import { MANUAL_SOURCE_TYPE } from "../../api/types";
import {
  SettingCard,
  SettingsGroup,
  SettingsNotice,
  SettingsPanel,
  dangerButtonClass,
  primaryButtonClass,
  secondaryButtonClass,
  settingsInputClass,
} from "./ui";

const inputClass = settingsInputClass;

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
    <div className="rounded-2xl border border-indigo-500/25 bg-indigo-500/[0.05] p-5">
      <div className="mb-4 text-sm font-semibold text-indigo-300">
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
          className={primaryButtonClass}
        >
          {isSaving && <Loader2 size={14} className="animate-spin" />}
          Сохранить
        </button>
        <button
          onClick={onCancel}
          disabled={isSaving}
          className={secondaryButtonClass}
        >
          <X size={13} />
          Отмена
        </button>
      </div>
    </div>
  );
}

/**
 * Вкладка «Доступы к площадкам» (до 28.09.2026 — «Пользовательские данные») — логины и пароли к личным кабинетам площадок
 * (раздел 4.1, 5.1 ТЗ). Доступен только администратору.
 *
 * Пароль сюда можно только записать: сервер его не возвращает, поэтому в списке стоит маска,
 * а не значение. Хранится он зашифрованным в базе на этой же машине, ключ — в файле
 * `.credentials_key` в корне проекта.
 */
export function CredentialsSection() {
  const [credentials, setCredentials] = useState<SourceCredential[] | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
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
    // Доступы бывают только у площадок: у ручных заявок входить некуда.
    void api
      .get<Source[]>("/sources")
      .then((all) => setSources(all.filter((source) => source.type !== MANUAL_SOURCE_TYPE)));
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
    <SettingsPanel
      title="Доступы к площадкам"
      description="Логины и пароли к личным кабинетам площадок — для источников, которые отдают закупки только авторизованному пользователю. Здесь же хранится ключ платного тарифа Госплана."
      actions={
        <button
          onClick={() => {
            setEditing(null);
            setIsCreating(true);
          }}
          className={primaryButtonClass}
        >
          <Plus size={15} />
          Добавить доступ
        </button>
      }
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}

      <SettingsNotice tone="info">
        <span className="flex items-start gap-2">
          <ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400" />
          <span>
            Пароль шифруется ключом из файла <code className="text-zinc-300">.credentials_key</code>{" "}
            в корне проекта и обратно через интерфейс не отдаётся — его можно только заменить. Не
            удаляйте этот файл: без него сохранённые пароли не восстановить.
          </span>
        </span>
      </SettingsNotice>

      {(isCreating || editing) && (
        <CredentialForm
          key={editing?.id ?? "new"}
          sources={sources}
          credential={editing}
          onSubmit={editing ? handleUpdate : handleCreate}
          onCancel={() => {
            setIsCreating(false);
            setEditing(null);
          }}
        />
      )}

      <SettingsGroup id="credentials-list" label="Сохранённые доступы">
        {credentials === null ? (
          <div className="flex items-center gap-2 text-sm text-zinc-500">
            <Loader2 size={14} className="animate-spin" />
            Загрузка…
          </div>
        ) : credentials.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
            Доступы не заданы. Добавьте их для площадки, которая требует входа в личный кабинет.
          </div>
        ) : (
          credentials.map((credential) => (
            <SettingCard
              key={credential.id}
              active={credential.is_active}
              icon={<KeyRound size={18} />}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  {credential.label}
                  <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-xs font-normal text-zinc-400">
                    {credential.source_name}
                  </span>
                  {!credential.is_active && (
                    <span className="rounded-md border border-white/10 px-2 py-0.5 text-[11px] font-normal text-zinc-500">
                      выключен
                    </span>
                  )}
                </span>
              }
              description={
                <>
                  Логин: <span className="text-zinc-300">{credential.username}</span> · Пароль:{" "}
                  <span className="text-zinc-400">{credential.password_masked}</span>
                  {credential.notes ? ` · ${credential.notes}` : ""}
                  <span className="block text-[11px] text-zinc-600">
                    Изменено {formatDateTime(credential.updated_at)}
                    {credential.updated_by ? ` · ${credential.updated_by}` : ""}
                  </span>
                </>
              }
              control={
                <>
                  <button
                    onClick={() => {
                      setIsCreating(false);
                      setEditing(credential);
                    }}
                    className={secondaryButtonClass}
                  >
                    <Pencil size={13} />
                    Изменить
                  </button>
                  <button onClick={() => void handleDelete(credential)} className={dangerButtonClass}>
                    <Trash2 size={13} />
                    Удалить
                  </button>
                </>
              }
            />
          ))
        )}
      </SettingsGroup>
    </SettingsPanel>
  );
}
