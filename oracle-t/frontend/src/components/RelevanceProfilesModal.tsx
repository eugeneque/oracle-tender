import { useEffect, useState } from "react";
import { ChevronDown, Filter, Loader2, Pencil, Plus, Trash2, Users, X } from "lucide-react";
import { motion } from "motion/react";

import { ApiError, api } from "../api/client";
import type {
  ProfilePreview,
  ProfileRules,
  Source,
  UserRelevanceProfile,
  UserRelevanceProfileInput,
} from "../api/types";
import { useAuth } from "../context/useAuth";
import { dialogVariants, scrimVariants } from "../utils/motion";
import { plural } from "../utils/format";
import { OkpdTreePicker } from "./OkpdTreePicker";
import { TermInput } from "./TermInput";

/**
 * Профили отбора — единственный механизм отбора закупок (05.10.2026).
 *
 * Общие профили (бывшие группы «профиля релевантности» из настроек) действуют у всех по
 * умолчанию, их ведёт администратор. Личные заводит любой специалист: «щитовые приборы»,
 * «поверка и монтаж», «ветка 26.51 без воды и газа». Логика у тех и других одна, и в
 * редакторе сразу видно, сколько закупок профиль отберёт и какие — термин добавляют, видя
 * результат, а не наугад.
 */

const EMPTY_DRAFT: UserRelevanceProfileInput = {
  name: "",
  description: null,
  keywords: [],
  exclusion_keywords: [],
  okpd2_codes: [],
  match_mode: "any",
  okpd2_mode: "narrow",
  source_keys: [],
  is_default: false,
  is_active: true,
};

const inputClass =
  "w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500";

function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <span className="inline-flex gap-1">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          aria-pressed={value === option.value}
          className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
            value === option.value
              ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
              : "border-white/10 text-zinc-500 hover:text-zinc-300"
          }`}
        >
          {option.label}
        </button>
      ))}
    </span>
  );
}

/** Что отберёт профиль с текущими правилами — пересчитывается через полсекунды после правки. */
function PreviewPanel({ rules }: { rules: ProfileRules }) {
  const [preview, setPreview] = useState<ProfilePreview | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const empty = rules.keywords.length === 0 && rules.okpd2_codes.length === 0;
  const key = JSON.stringify(rules);

  useEffect(() => {
    if (empty) {
      setPreview(null);
      return;
    }
    setIsLoading(true);
    const timer = setTimeout(() => {
      void api
        .post<ProfilePreview>("/relevance/profiles/preview", JSON.parse(key))
        .then(setPreview)
        .catch(() => setPreview(null))
        .finally(() => setIsLoading(false));
    }, 500);
    return () => clearTimeout(timer);
  }, [key, empty]);

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-zinc-400">
        <span className="font-medium text-zinc-200">Что отберёт профиль</span>
        {isLoading && <Loader2 size={12} className="animate-spin text-zinc-500" />}
      </div>
      {empty ? (
        <p className="text-xs text-zinc-500">
          Добавьте слово или код ОКПД2 — здесь появится, сколько собранных закупок подходит и
          какие именно.
        </p>
      ) : preview ? (
        <>
          <p className="text-xs text-zinc-400">
            Подходит{" "}
            <b className="font-semibold tabular-nums text-zinc-100">
              {preview.count.toLocaleString("ru-RU")}
            </b>{" "}
            {plural(preview.count, "закупка", "закупки", "закупок")} из{" "}
            {preview.total.toLocaleString("ru-RU")} собранных
            {rules.source_keys.length > 0 ? " на выбранных площадках" : ""}. Свежие:
          </p>
          <ul className="mt-1.5 space-y-1">
            {preview.samples.map((item) => (
              <li key={item.id} className="flex gap-2 text-xs">
                <span className="w-[4.5rem] shrink-0 tabular-nums text-zinc-600">
                  {item.publish_date ? new Date(item.publish_date).toLocaleDateString("ru-RU") : "—"}
                </span>
                <span className="min-w-0 flex-1 truncate text-zinc-300" title={item.title}>
                  {item.title}
                </span>
                {item.okpd2_code && (
                  <span className="shrink-0 tabular-nums text-zinc-600">{item.okpd2_code}</span>
                )}
              </li>
            ))}
          </ul>
          {preview.count > 0 && (
            <p className="mt-2 text-[11px] text-zinc-500">
              Видите чужое — добавьте исключение. Нет нужного — добавьте слово или код.
            </p>
          )}
        </>
      ) : (
        <p className="text-xs text-zinc-500">Считаем…</p>
      )}
    </div>
  );
}

function ProfileEditor({
  profile,
  sources,
  isAdmin,
  onCancel,
  onSaved,
}: {
  profile: UserRelevanceProfile | null;
  sources: Source[];
  isAdmin: boolean;
  onCancel: () => void;
  onSaved: (saved: UserRelevanceProfile, isNew: boolean) => void;
}) {
  const [draft, setDraft] = useState<UserRelevanceProfileInput>(
    profile
      ? {
          name: profile.name,
          description: profile.description,
          keywords: profile.keywords,
          exclusion_keywords: profile.exclusion_keywords,
          okpd2_codes: profile.okpd2_codes,
          match_mode: profile.match_mode,
          okpd2_mode: profile.okpd2_mode,
          source_keys: profile.source_keys,
          is_default: profile.is_default,
          is_active: profile.is_active,
        }
      : EMPTY_DRAFT,
  );
  const [isOkpdOpen, setIsOkpdOpen] = useState(draft.okpd2_codes.length > 0);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (patch: Partial<UserRelevanceProfileInput>) =>
    setDraft((prev) => ({ ...prev, ...patch }));

  const save = async () => {
    const body: UserRelevanceProfileInput = {
      ...draft,
      name: draft.name.trim(),
      description: draft.description?.trim() ? draft.description.trim() : null,
    };
    if (!body.name) {
      setError("Укажите название профиля");
      return;
    }
    if (body.keywords.length === 0 && body.okpd2_codes.length === 0) {
      setError("Добавьте хотя бы одно ключевое слово или код ОКПД2");
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const saved = profile
        ? await api.put<UserRelevanceProfile>(`/relevance/profiles/${profile.id}`, body)
        : await api.post<UserRelevanceProfile>("/relevance/profiles", body);
      onSaved(saved, profile === null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить профиль");
      setIsSaving(false);
    }
  };

  const rules: ProfileRules = {
    keywords: draft.keywords,
    exclusion_keywords: draft.exclusion_keywords,
    okpd2_codes: draft.okpd2_codes,
    match_mode: draft.match_mode,
    okpd2_mode: draft.okpd2_mode,
    source_keys: draft.source_keys,
  };

  return (
    <>
      <div className="grid gap-4 overflow-y-auto px-5 py-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <div className="min-w-0 space-y-4">
          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {error}
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-zinc-500">Название</label>
              <input
                value={draft.name}
                onChange={(e) => set({ name: e.target.value })}
                maxLength={200}
                placeholder="Например: Щитовые приборы и АСКУЭ"
                className={inputClass}
                autoFocus
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-500">Описание (необязательно)</label>
              <input
                value={draft.description ?? ""}
                onChange={(e) => set({ description: e.target.value })}
                maxLength={1000}
                placeholder="Для кого и для чего этот профиль"
                className={inputClass}
              />
            </div>
          </div>

          <div>
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <label className="text-xs text-zinc-500">
                Ключевые слова — напечатайте и нажмите Enter
              </label>
              {draft.keywords.length > 1 && (
                <span className="flex items-center gap-2 text-xs text-zinc-500">
                  должно совпасть:
                  <Segmented
                    value={draft.match_mode}
                    options={[
                      { value: "any", label: "любое" },
                      { value: "all", label: "все" },
                    ]}
                    onChange={(value) => set({ match_mode: value })}
                  />
                </span>
              )}
            </div>
            <TermInput
              value={draft.keywords}
              onChange={(keywords) => set({ keywords })}
              placeholder="счетчик*  ·  прибор* учет*  ·  (поверк* счетчик*)~5"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-500">
              Исключения — встретилось, и закупка не подходит
            </label>
            <TermInput
              value={draft.exclusion_keywords}
              onChange={(exclusion_keywords) => set({ exclusion_keywords })}
              placeholder="вод*  ·  газ*  ·  тепл*"
              tone="negative"
            />
          </div>
          <p className="-mt-2 text-xs text-zinc-500">
            <code>слово*</code> — все формы слова; <code>(слово1* слово2*)~N</code> — слова не
            дальше N слов друг от друга. Ищется в названии закупки, заказчике и способе закупки.
          </p>

          <div className="rounded-lg border border-white/[0.08]">
            <button
              type="button"
              onClick={() => setIsOkpdOpen((v) => !v)}
              aria-expanded={isOkpdOpen}
              className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left"
            >
              <span className="text-sm text-zinc-200">
                Коды ОКПД2
                <span className="ml-2 text-xs text-zinc-500">
                  {draft.okpd2_codes.length > 0
                    ? `выбрано: ${draft.okpd2_codes.length}`
                    : "не ограничены"}
                </span>
              </span>
              <ChevronDown
                size={14}
                className={`text-zinc-500 transition-transform ${isOkpdOpen ? "rotate-180" : ""}`}
              />
            </button>
            {isOkpdOpen && (
              <div className="border-t border-white/[0.06] p-3">
                <OkpdTreePicker
                  value={draft.okpd2_codes}
                  onChange={(codes) => set({ okpd2_codes: codes })}
                />
              </div>
            )}
          </div>
          {draft.keywords.length > 0 && draft.okpd2_codes.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
              Слова и коды:
              <Segmented
                value={draft.okpd2_mode}
                options={[
                  { value: "narrow", label: "нужно и то и другое" },
                  { value: "either", label: "достаточно одного" },
                ]}
                onChange={(value) => set({ okpd2_mode: value })}
              />
            </div>
          )}

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs text-zinc-500">
                Площадки, на которые действует профиль
                <span className="ml-2 text-zinc-600">
                  {draft.source_keys.length === 0
                    ? "все площадки"
                    : `выбрано: ${draft.source_keys.length}`}
                </span>
              </span>
              {draft.source_keys.length > 0 && (
                <button
                  type="button"
                  onClick={() => set({ source_keys: [] })}
                  className="text-xs text-zinc-500 hover:text-zinc-300"
                >
                  на все
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {sources.map((source) => {
                const active = draft.source_keys.includes(source.key);
                return (
                  <button
                    key={source.key}
                    type="button"
                    aria-pressed={active}
                    onClick={() =>
                      set({
                        source_keys: active
                          ? draft.source_keys.filter((key) => key !== source.key)
                          : [...draft.source_keys, source.key],
                      })
                    }
                    className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                      active
                        ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                        : "border-white/10 text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    {source.name}
                  </button>
                );
              })}
            </div>
            <p className="mt-1.5 text-xs text-zinc-500">
              Профиль сужает закупки только этих площадок; остальные не трогает.
            </p>
          </div>

          {isAdmin && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-white/[0.08] px-3 py-2.5">
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  checked={draft.is_default}
                  onChange={(e) => set({ is_default: e.target.checked })}
                  className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
                />
                Общий профиль — действует у всех по умолчанию
              </label>
              {draft.is_default && (
                <label className="flex items-center gap-2 text-sm text-zinc-300">
                  <input
                    type="checkbox"
                    checked={draft.is_active}
                    onChange={(e) => set({ is_active: e.target.checked })}
                    className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
                  />
                  Включён
                </label>
              )}
            </div>
          )}
        </div>
        <div className="min-w-0 lg:sticky lg:top-0 lg:self-start">
          <PreviewPanel rules={rules} />
        </div>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-white/[0.08] px-5 py-3">
        <button
          onClick={onCancel}
          disabled={isSaving}
          className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-zinc-300 hover:bg-white/5"
        >
          Отмена
        </button>
        <button
          onClick={() => void save()}
          disabled={isSaving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-snow hover:bg-indigo-400 disabled:opacity-60"
        >
          {isSaving && <Loader2 size={13} className="animate-spin" />}
          {profile ? "Сохранить" : "Создать"}
        </button>
      </div>
    </>
  );
}

function Chips({ items, tone }: { items: string[]; tone: "positive" | "negative" | "muted" }) {
  const className =
    tone === "positive"
      ? "border-indigo-500/25 bg-indigo-500/[0.07] text-indigo-200/90"
      : tone === "negative"
        ? "border-red-500/25 bg-red-500/[0.06] text-red-300/90"
        : "border-white/10 bg-white/[0.03] text-zinc-400";
  return (
    <>
      {items.slice(0, 8).map((item) => (
        <span key={item} className={`rounded-md border px-1.5 py-0.5 text-[11px] ${className}`}>
          {item}
        </span>
      ))}
      {items.length > 8 && <span className="text-[11px] text-zinc-500">+{items.length - 8}</span>}
    </>
  );
}

/** Список профилей с правкой — и в окне со страницы тендеров, и в настройках. */
export function ProfilesManager({
  profiles,
  sources,
  activeIds,
  onApply,
  onChanged,
  onEditingChange,
}: {
  profiles: UserRelevanceProfile[];
  sources: Source[];
  /** Выбранные на странице тендеров; без `onApply` кнопок «Применить» нет. */
  activeIds?: string[];
  onApply?: (ids: string[]) => void;
  onChanged: () => void;
  onEditingChange?: (title: string | null) => void;
}) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [editing, setEditing] = useState<UserRelevanceProfile | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const ids = activeIds ?? [];

  useEffect(() => {
    onEditingChange?.(
      editing === null ? null : editing === "new" ? "Новый профиль" : `Профиль «${editing.name}»`,
    );
  }, [editing, onEditingChange]);

  const remove = async (profile: UserRelevanceProfile) => {
    const warning = profile.is_default
      ? `Удалить общий профиль «${profile.name}»? Он перестанет отбирать закупки у всех.`
      : `Удалить профиль «${profile.name}»? Он пропадёт у всех сотрудников.`;
    if (!window.confirm(warning)) return;
    setDeletingId(profile.id);
    setError(null);
    try {
      await api.delete(`/relevance/profiles/${profile.id}`);
      if (ids.includes(profile.id)) onApply?.(ids.filter((id) => id !== profile.id));
      setNotice(`Профиль «${profile.name}» удалён`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить профиль");
    } finally {
      setDeletingId(null);
    }
  };

  const handleSaved = (saved: UserRelevanceProfile, isNew: boolean) => {
    setEditing(null);
    const count = saved.matched_count;
    setNotice(
      `Профиль «${saved.name}» ${isNew ? "создан" : "сохранён"}` +
        (count === null
          ? ""
          : `: подходит ${count.toLocaleString("ru-RU")} ${plural(count, "закупка", "закупки", "закупок")}`) +
        (saved.is_default ? ". Отметки общих профилей пересчитаны." : ""),
    );
    // Новый личный профиль сразу применяется: его создают, чтобы посмотреть на результат.
    if (isNew && !saved.is_default) onApply?.([...ids, saved.id]);
    onChanged();
  };

  if (editing !== null) {
    return (
      <ProfileEditor
        profile={editing === "new" ? null : editing}
        sources={sources}
        isAdmin={isAdmin}
        onCancel={() => setEditing(null)}
        onSaved={handleSaved}
      />
    );
  }

  const card = (profile: UserRelevanceProfile) => {
    const isActive = ids.includes(profile.id);
    return (
      <div
        key={profile.id}
        className={`rounded-xl border px-4 py-3 ${
          isActive
            ? "border-indigo-500/40 bg-indigo-500/[0.06]"
            : "border-white/[0.08] bg-white/[0.03]"
        } ${profile.is_active ? "" : "opacity-60"}`}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium text-zinc-100">
              {profile.name}
              {!profile.is_active && (
                <span className="ml-2 text-xs font-normal text-zinc-500">выключен</span>
              )}
            </div>
            <div className="text-xs text-zinc-500">
              {profile.is_default ? "общий" : profile.owner_name ?? "автор удалён"}
              {profile.description && ` · ${profile.description}`}
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            {onApply && (
              <button
                onClick={() =>
                  onApply(isActive ? ids.filter((id) => id !== profile.id) : [...ids, profile.id])
                }
                className={`rounded-lg border px-2.5 py-1 text-xs transition-colors ${
                  isActive
                    ? "border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10"
                    : "border-white/10 text-zinc-300 hover:bg-white/5"
                }`}
              >
                {isActive ? "Снять" : "Применить"}
              </button>
            )}
            {profile.can_edit && (
              <>
                <button
                  onClick={() => {
                    setNotice(null);
                    setEditing(profile);
                  }}
                  title="Изменить"
                  aria-label={`Изменить «${profile.name}»`}
                  className="rounded-lg p-1.5 text-zinc-500 hover:bg-white/10 hover:text-zinc-100"
                >
                  <Pencil size={14} />
                </button>
                <button
                  onClick={() => void remove(profile)}
                  disabled={deletingId === profile.id}
                  title="Удалить"
                  aria-label={`Удалить «${profile.name}»`}
                  className="rounded-lg p-1.5 text-zinc-500 hover:bg-red-500/10 hover:text-red-400"
                >
                  {deletingId === profile.id ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Trash2 size={14} />
                  )}
                </button>
              </>
            )}
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <Chips items={profile.keywords} tone="positive" />
          <Chips items={profile.exclusion_keywords.map((k) => `− ${k}`)} tone="negative" />
          <Chips items={profile.okpd2_codes.map((c) => `ОКПД2 ${c}`)} tone="muted" />
          <span className="rounded-md border border-white/10 px-1.5 py-0.5 text-[11px] text-zinc-500">
            {profile.source_keys.length === 0
              ? "все площадки"
              : profile.source_keys
                  .map((key) => sources.find((s) => s.key === key)?.name ?? key)
                  .join(", ")}
          </span>
        </div>
      </div>
    );
  };

  const shared = profiles.filter((profile) => profile.is_default);
  const personal = profiles.filter((profile) => !profile.is_default);

  return (
    <>
      <div className="space-y-2 overflow-y-auto px-5 py-4">
        {notice && (
          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            {notice}
          </div>
        )}
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}
        <div className="flex items-center gap-1.5 pt-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
          <Users size={12} />
          Общие — действуют у всех по умолчанию{isAdmin ? "" : ", меняет администратор"}
        </div>
        {shared.length === 0 && <div className="text-xs text-zinc-500">Общих профилей нет.</div>}
        {shared.map(card)}
        <div className="pt-3 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
          Личные
        </div>
        {personal.length === 0 && (
          <div className="rounded-lg border border-dashed border-white/10 px-4 py-6 text-center text-sm text-zinc-500">
            Своих профилей пока нет. Создайте, например, «Щитовые приборы» со словом «счетчик*» и
            веткой ОКПД2 26.51.
          </div>
        )}
        {personal.map(card)}
      </div>
      <div className="flex items-center justify-end gap-3 border-t border-white/[0.08] px-5 py-3">
        <button
          onClick={() => {
            setNotice(null);
            setEditing("new");
          }}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-snow hover:bg-indigo-400"
        >
          <Plus size={14} />
          Новый профиль
        </button>
      </div>
    </>
  );
}

export function RelevanceProfilesModal({
  profiles,
  sources,
  activeIds,
  onApply,
  onChanged,
  onClose,
}: {
  profiles: UserRelevanceProfile[];
  sources: Source[];
  activeIds: string[];
  /** Применить набор профилей к списку тендеров. */
  onApply: (ids: string[]) => void;
  /** Список изменился (создан, правлен, удалён) — родителю нужно перечитать и обновить выдачу. */
  onChanged: () => void;
  onClose: () => void;
}) {
  const [editingTitle, setEditingTitle] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && editingTitle === null) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, editingTitle]);

  return (
    <motion.div
      variants={scrimVariants}
      initial="initial"
      animate="animate"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 px-4 py-8"
      onClick={onClose}
    >
      <motion.div
        variants={dialogVariants}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Профили отбора"
        className={`flex max-h-full w-full flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-xl ${
          editingTitle === null ? "max-w-3xl" : "max-w-5xl"
        }`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] px-5 py-4">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <Filter size={15} />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-white">
                {editingTitle ?? "Профили отбора"}
              </h2>
              <p className="mt-0.5 text-xs text-zinc-500">
                Слова, исключения и ветки ОКПД2, которыми отбираются собранные закупки. Профиль
                привязывается к площадкам; несколько профилей сочетаются.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded-md p-1 text-zinc-500 hover:bg-white/10 hover:text-zinc-100"
          >
            <X size={16} />
          </button>
        </div>
        <ProfilesManager
          profiles={profiles}
          sources={sources}
          activeIds={activeIds}
          onApply={onApply}
          onChanged={onChanged}
          onEditingChange={setEditingTitle}
        />
      </motion.div>
    </motion.div>
  );
}
