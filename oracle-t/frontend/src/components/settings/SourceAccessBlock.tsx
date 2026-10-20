import { useState } from "react";
import { ChevronDown, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { Source, SourceCredential, SourceCredentialInput } from "../../api/types";
import {
  dangerButtonClass,
  primaryButtonClass,
  secondaryButtonClass,
  settingsInputClass,
} from "./ui";

const DEFAULT_LABEL = "Основная учётка";
// Поля стоят в строку с кнопками — верхний отступ подписи им не нужен.
const inputClass = settingsInputClass.replace("mt-1.5 ", "");

/** Название для ещё одной учётки той же площадки: сервер не даст два блока с одним именем. */
function nextLabel(existing: SourceCredential[]): string {
  if (!existing.some((c) => c.label === DEFAULT_LABEL)) return DEFAULT_LABEL;
  let n = 2;
  while (existing.some((c) => c.label === `Учётка ${n}`)) n += 1;
  return `Учётка ${n}`;
}

/**
 * Поля логина и пароля одной учётки прямо в карточке площадки.
 *
 * Пароль при правке пустой и уходит на сервер, только если введён новый: старый сервер не
 * отдаёт (см. `CredentialsSection`). Новая учётка без пароля не сохраняется.
 */
function AccessRow({
  source,
  credential,
  label,
  onSaved,
  onCancel,
}: {
  source: Source;
  credential: SourceCredential | null;
  label: string;
  onSaved: () => Promise<void>;
  onCancel?: () => void;
}) {
  const [username, setUsername] = useState(credential?.username ?? "");
  const [password, setPassword] = useState("");
  const [isActive, setIsActive] = useState(credential?.is_active ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isEdit = credential !== null;
  const dirty =
    !isEdit ||
    username.trim() !== credential.username ||
    password !== "" ||
    isActive !== credential.is_active;
  const canSave = username.trim() !== "" && (isEdit || password !== "") && dirty;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload: SourceCredentialInput = { username: username.trim(), is_active: isActive };
      if (password !== "") payload.password = password;
      if (isEdit) {
        await api.patch(`/source-credentials/${credential.id}`, payload);
      } else {
        await api.post("/source-credentials", { ...payload, source_id: source.id, label });
      }
      setPassword("");
      await onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить доступ");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!credential) return;
    // Пароль из базы не восстановить — спрашиваем, как и на вкладке «Доступы».
    if (!window.confirm(`Удалить доступ «${credential.label}» к площадке «${source.name}»?`)) return;
    setBusy(true);
    try {
      await api.delete(`/source-credentials/${credential.id}`);
      await onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить доступ");
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      {isEdit && credential.label !== DEFAULT_LABEL && (
        <div className="text-[11px] text-zinc-500">{credential.label}</div>
      )}
      <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Логин"
          aria-label={`Логин для ${source.name}`}
          autoComplete="off"
          className={inputClass}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={isEdit ? `${credential.password_masked} сохранён` : "Пароль"}
          title={isEdit ? "Оставьте пустым, чтобы не менять пароль" : undefined}
          aria-label={`Пароль для ${source.name}`}
          autoComplete="new-password"
          className={inputClass}
        />
        <div className="flex items-center gap-2">
          <button onClick={() => void save()} disabled={!canSave || busy} className={primaryButtonClass}>
            {busy && <Loader2 size={13} className="animate-spin" />}
            Сохранить
          </button>
          {isEdit ? (
            <button
              onClick={() => void remove()}
              disabled={busy}
              className={dangerButtonClass}
              title="Удалить доступ"
              aria-label="Удалить доступ"
            >
              <Trash2 size={13} />
            </button>
          ) : (
            onCancel && (
              <button onClick={onCancel} disabled={busy} className={secondaryButtonClass}>
                Отмена
              </button>
            )
          )}
        </div>
      </div>
      {isEdit && (
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-white/20 bg-white/5 accent-indigo-500"
          />
          Использовать при опросе площадки
        </label>
      )}
      {error && <div className="text-xs text-red-400">{error}</div>}
    </div>
  );
}

/**
 * Блок «Доступ к личному кабинету» в карточке площадки (20.10.2026): логин и пароль вводятся
 * прямо у площадки, а не через общую форму вкладки «Доступы» с выбором площадки из списка.
 * Хранилище то же — `/source-credentials`, пароль шифруется на сервере.
 */
export function SourceAccessBlock({
  source,
  credentials,
  onChanged,
}: {
  source: Source;
  credentials: SourceCredential[];
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);

  const summary =
    credentials.length === 0
      ? "не задан"
      : credentials.map((c) => c.username + (c.is_active ? "" : " (выключен)")).join(", ");
  const showNew = credentials.length === 0 || adding;

  const saved = async () => {
    setAdding(false);
    await onChanged();
  };

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left text-xs text-zinc-400 hover:text-zinc-200"
      >
        <KeyRound size={13} className={credentials.length ? "text-emerald-400" : "text-zinc-500"} />
        <span>
          Доступ к личному кабинету:{" "}
          <span className={credentials.length ? "text-zinc-200" : "text-zinc-500"}>{summary}</span>
        </span>
        <ChevronDown
          size={13}
          className={`ml-auto shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="mt-3 space-y-3">
          {credentials.map((credential) => (
            <AccessRow
              key={`${credential.id}-${credential.updated_at}`}
              source={source}
              credential={credential}
              label={credential.label}
              onSaved={saved}
            />
          ))}
          {showNew && (
            <AccessRow
              source={source}
              credential={null}
              label={nextLabel(credentials)}
              onSaved={saved}
              onCancel={credentials.length ? () => setAdding(false) : undefined}
            />
          )}
          {!showNew && (
            <button onClick={() => setAdding(true)} className={secondaryButtonClass}>
              <Plus size={13} />
              Ещё одна учётка
            </button>
          )}
        </div>
      )}
    </div>
  );
}
