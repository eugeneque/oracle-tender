import { Check, Pencil, Plus, Tag as TagIcon, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { TagColor, TenderTag } from "../../api/types";
import { TAG_COLORS } from "../../api/types";
import { tagColorClasses } from "../../utils/tagColors";

/**
 * Выпадающий выбор тегов закупки (замечание 17.09.2026): отметить существующие, завести
 * новый, переименовать или удалить. Один компонент и для карточки закупки, и для фильтров —
 * чтобы справочник тегов везде правился одинаково.
 *
 * Справочник грузится при каждом открытии: тег, который сосед завёл минуту назад, должен
 * быть виден без перезагрузки страницы.
 */
export function TagPicker({
  selectedIds,
  onChange,
  onClose,
  anchorClassName = "",
  allowManage = true,
}: {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  onClose: () => void;
  anchorClassName?: string;
  /** Правка справочника (переименовать/удалить/создать) — в фильтрах её прячем: там
   * выбирают, а не ведут справочник. */
  allowManage?: boolean;
}) {
  const [tags, setTags] = useState<TenderTag[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [newColor, setNewColor] = useState<TagColor>("indigo");
  const [editing, setEditing] = useState<TenderTag | null>(null);
  const [editName, setEditName] = useState("");
  const [editColor, setEditColor] = useState<TagColor>("zinc");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      setTags(await api.get<TenderTag[]>("/tags"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить теги");
      setTags([]);
    }
  };

  useEffect(() => {
    void load();
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const normalized = query.trim().toLowerCase();
  const visible = (tags ?? []).filter(
    (tag) => !normalized || tag.name.toLowerCase().includes(normalized),
  );
  const exactExists = (tags ?? []).some((tag) => tag.name.toLowerCase() === normalized);

  const toggle = (id: string) => {
    onChange(
      selectedIds.includes(id) ? selectedIds.filter((item) => item !== id) : [...selectedIds, id],
    );
  };

  const create = async () => {
    const name = query.trim();
    if (!name || exactExists) return;
    setError(null);
    try {
      const tag = await api.post<TenderTag>("/tags", { name, color: newColor });
      setTags((prev) => [...(prev ?? []), tag].sort((a, b) => a.name.localeCompare(b.name, "ru")));
      setQuery("");
      onChange([...selectedIds, tag.id]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать тег");
    }
  };

  const startEdit = (tag: TenderTag) => {
    setEditing(tag);
    setEditName(tag.name);
    setEditColor(tag.color);
  };

  const saveEdit = async () => {
    if (!editing) return;
    setError(null);
    try {
      const updated = await api.patch<TenderTag>(`/tags/${editing.id}`, {
        name: editName.trim(),
        color: editColor,
      });
      setTags((prev) => (prev ?? []).map((tag) => (tag.id === updated.id ? updated : tag)));
      setEditing(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить тег");
    }
  };

  const remove = async (tag: TenderTag) => {
    if (!window.confirm(`Удалить тег «${tag.name}»? Он будет снят со всех закупок.`)) return;
    setError(null);
    try {
      await api.delete(`/tags/${tag.id}`);
      setTags((prev) => (prev ?? []).filter((item) => item.id !== tag.id));
      if (selectedIds.includes(tag.id)) onChange(selectedIds.filter((id) => id !== tag.id));
      if (editing?.id === tag.id) setEditing(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить тег");
    }
  };

  const colorDots = (value: TagColor, setValue: (c: TagColor) => void) => (
    <div className="flex flex-wrap items-center gap-1">
      {TAG_COLORS.map((color) => (
        <button
          key={color}
          type="button"
          onClick={() => setValue(color)}
          className={`flex h-5 w-5 items-center justify-center rounded-full border transition-colors ${
            value === color ? "border-white/60" : "border-transparent hover:border-white/30"
          }`}
          title={color}
        >
          <span className={`h-3 w-3 rounded-full ${tagColorClasses(color).dot}`} />
        </button>
      ))}
    </div>
  );

  return (
    <div
      ref={rootRef}
      className={`absolute z-40 mt-1 w-72 rounded-xl border border-white/10 bg-zinc-900 p-2 shadow-2xl ${anchorClassName}`}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="relative">
        <TagIcon size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-600" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && allowManage) void create();
          }}
          maxLength={60}
          placeholder={allowManage ? "Найти или создать тег…" : "Найти тег…"}
          className="w-full rounded-lg border border-white/10 bg-white/[0.03] py-1.5 pl-8 pr-2 text-xs text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
        />
      </div>

      {error && (
        <div className="mt-2 rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">
          {error}
        </div>
      )}

      <div className="mt-2 max-h-60 overflow-y-auto">
        {tags === null ? (
          <div className="px-2 py-2 text-xs text-zinc-600">Загрузка…</div>
        ) : visible.length === 0 && !normalized ? (
          <div className="px-2 py-2 text-xs leading-relaxed text-zinc-600">
            Тегов пока нет.{allowManage ? " Введите название и нажмите Enter." : ""}
          </div>
        ) : (
          visible.map((tag) => {
            const checked = selectedIds.includes(tag.id);
            const colors = tagColorClasses(tag.color);
            if (editing?.id === tag.id) {
              return (
                <div key={tag.id} className="rounded-lg border border-white/10 bg-white/[0.03] p-2">
                  <input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveEdit();
                    }}
                    maxLength={60}
                    autoFocus
                    className="mb-2 w-full rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-xs text-white outline-none focus:border-indigo-500"
                  />
                  <div className="flex items-center justify-between gap-2">
                    {colorDots(editColor, setEditColor)}
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => void saveEdit()}
                        className="rounded-md p-1 text-emerald-300 hover:bg-white/5"
                        title="Сохранить"
                      >
                        <Check size={13} />
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditing(null)}
                        className="rounded-md p-1 text-zinc-500 hover:bg-white/5"
                        title="Отмена"
                      >
                        <X size={13} />
                      </button>
                    </div>
                  </div>
                </div>
              );
            }
            return (
              <div
                key={tag.id}
                className="group flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-white/[0.05]"
              >
                <button
                  type="button"
                  onClick={() => toggle(tag.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <span
                    className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                      checked ? "border-indigo-400 bg-indigo-500 text-white" : "border-white/20"
                    }`}
                  >
                    {checked && <Check size={11} />}
                  </span>
                  <span className={`h-2 w-2 shrink-0 rounded-full ${colors.dot}`} />
                  <span className="truncate text-xs text-zinc-200">{tag.name}</span>
                </button>
                {allowManage && (
                  <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={() => startEdit(tag)}
                      className="rounded-md p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
                      title="Переименовать или сменить цвет"
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      type="button"
                      onClick={() => void remove(tag)}
                      className="rounded-md p-1 text-zinc-500 hover:bg-red-500/10 hover:text-red-300"
                      title="Удалить тег"
                    >
                      <Trash2 size={12} />
                    </button>
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>

      {allowManage && normalized && !exactExists && (
        <div className="mt-2 border-t border-white/[0.06] pt-2">
          <button
            type="button"
            onClick={() => void create()}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-indigo-300 hover:bg-white/[0.05]"
          >
            <Plus size={13} />
            Создать тег «{query.trim()}»
          </button>
          <div className="px-2 pt-1">{colorDots(newColor, setNewColor)}</div>
        </div>
      )}
    </div>
  );
}
