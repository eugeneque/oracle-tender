import { useEffect, useRef, useState } from "react";
import { ChevronDown, Pencil, RotateCcw, SlidersHorizontal } from "lucide-react";

import type { UserRelevanceProfile } from "../api/types";

/**
 * Выбор профилей отбора на странице тендеров (05.10.2026).
 *
 * Один механизм вместо двух: общие профили (бывший «профиль релевантности» из настроек) и
 * личные — в одном списке с галочками. По умолчанию выбраны действующие общие профили; это
 * то, что раньше делала скрытая галочка «Только прошедшие профиль». Несколько профилей на
 * одной площадке комбинируются — «любой» или «все».
 */
export function ProfilePicker({
  profiles,
  selectedIds,
  isDefaultSelection,
  label,
  mode,
  onChange,
  onResetToDefault,
  onModeChange,
  onManage,
}: {
  profiles: UserRelevanceProfile[];
  selectedIds: string[];
  /** Выбор не трогали — действуют общие профили по умолчанию. */
  isDefaultSelection: boolean;
  label: string;
  mode: "any" | "all";
  onChange: (ids: string[]) => void;
  onResetToDefault: () => void;
  onModeChange: (mode: "any" | "all") => void;
  onManage: () => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setIsOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [isOpen]);

  const toggle = (id: string) =>
    onChange(selectedIds.includes(id) ? selectedIds.filter((item) => item !== id) : [...selectedIds, id]);

  const shared = profiles.filter((profile) => profile.is_default);
  const personal = profiles.filter((profile) => !profile.is_default);

  const row = (profile: UserRelevanceProfile) => (
    <label
      key={profile.id}
      className={`flex cursor-pointer items-start gap-2.5 px-3 py-1.5 hover:bg-white/5 ${
        profile.is_active ? "" : "opacity-50"
      }`}
    >
      <input
        type="checkbox"
        checked={selectedIds.includes(profile.id)}
        onChange={() => toggle(profile.id)}
        className="mt-0.5 h-4 w-4 shrink-0 rounded border-white/20 bg-white/5 accent-indigo-500"
      />
      <span className="min-w-0">
        <span className="block truncate text-sm text-zinc-100">{profile.name}</span>
        <span className="block truncate text-[11px] text-zinc-500">
          {profile.source_keys.length === 0
            ? "на всех площадках"
            : `площадок: ${profile.source_keys.length}`}
          {!profile.is_default && profile.owner_name ? ` · ${profile.owner_name}` : ""}
          {!profile.is_active ? " · выключен" : ""}
        </span>
      </span>
    </label>
  );

  const groupTitle = (text: string) => (
    <div className="px-3 pb-1 pt-2 text-[10.5px] font-medium uppercase tracking-wide text-zinc-500">
      {text}
    </div>
  );

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        aria-expanded={isOpen}
        title="Профили отбора: какие собранные закупки показывать. Можно выбрать несколько."
        className={`flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm transition-colors ${
          isDefaultSelection
            ? "border-white/[0.08] bg-white/[0.03] text-zinc-300 hover:bg-white/5"
            : "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
        }`}
      >
        <SlidersHorizontal size={14} />
        <span className="max-w-[12rem] truncate">{label}</span>
        <ChevronDown size={13} className="text-zinc-500" />
      </button>
      {isOpen && (
        <div className="absolute left-0 top-full z-40 mt-1 w-[340px] overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-[0_20px_60px_rgba(0,0,0,0.35)]">
          <div className="max-h-80 overflow-y-auto pb-1">
            {groupTitle("Общие — действуют по умолчанию")}
            {shared.length === 0 && (
              <div className="px-3 py-1 text-xs text-zinc-500">Общих профилей нет.</div>
            )}
            {shared.map(row)}
            {groupTitle("Личные")}
            {personal.length === 0 && (
              <div className="px-3 py-1 text-xs text-zinc-500">
                Своих профилей пока нет — создайте в «Управлять профилями».
              </div>
            )}
            {personal.map(row)}
          </div>
          {selectedIds.length > 1 && (
            <div className="flex items-center gap-2 border-t border-white/[0.08] px-3 py-2 text-xs text-zinc-400">
              <span title="Как сочетаются профили, действующие на одной площадке">
                Сочетать на площадке:
              </span>
              {(
                [
                  { value: "any", label: "любой" },
                  { value: "all", label: "все" },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => onModeChange(option.value)}
                  aria-pressed={mode === option.value}
                  className={`rounded-full border px-2.5 py-0.5 transition-colors ${
                    mode === option.value
                      ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                      : "border-white/10 text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center justify-between gap-2 border-t border-white/[0.08] px-3 py-2">
            <button
              type="button"
              onClick={() => {
                setIsOpen(false);
                onManage();
              }}
              className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300"
            >
              <Pencil size={12} />
              Управлять профилями…
            </button>
            <span className="flex items-center gap-3">
              {!isDefaultSelection && (
                <button
                  type="button"
                  onClick={onResetToDefault}
                  className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
                >
                  <RotateCcw size={11} />
                  по умолчанию
                </button>
              )}
              {selectedIds.length > 0 && (
                <button
                  type="button"
                  onClick={() => onChange([])}
                  className="text-xs text-zinc-500 hover:text-zinc-300"
                >
                  без профилей
                </button>
              )}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
